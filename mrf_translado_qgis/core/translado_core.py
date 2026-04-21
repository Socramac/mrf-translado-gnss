from __future__ import annotations
import re
from dataclasses import dataclass
from io import StringIO
from typing import Optional, Tuple
import pandas as pd
import pdfplumber
from pyproj import CRS, Transformer

REQUIRED_COLUMNS = ['Nome', 'Status', 'Este', 'Norte', 'Altitude Elipsoidal', 'DP E', 'DP N', 'DP U']

@dataclass
class PointData:
    name: str
    east: float
    north: float
    h: float
    sigma_e: float = 0.0
    sigma_n: float = 0.0
    sigma_h: float = 0.0

@dataclass
class PPPData:
    east: float
    north: float
    h: float
    sigma_e: Optional[float] = None
    sigma_n: Optional[float] = None
    sigma_h: Optional[float] = None
    source_pdf: Optional[str] = None
    source_kind: Optional[str] = None
    source_code: Optional[str] = None

@dataclass
class Emitente:
    logo: str = ''
    empresa: str = ''
    cnpj: str = ''
    endereco: str = ''
    email: str = ''
    telefone: str = ''
    projeto: str = ''
    responsavel_tecnico: str = ''
    codigo_credenciado: str = ''
    equipamento_base: str = ''
    equipamento_rover: str = ''
    data_relatorio: str = ''

def normalize_number(value) -> float:
    s = str(value).strip().replace(' ', '')
    if not s:
        raise ValueError('Campo numérico vazio.')
    if ',' in s and '.' in s:
        if s.rfind(',') > s.rfind('.'):
            s = s.replace('.', '').replace(',', '.')
        else:
            s = s.replace(',', '')
    else:
        s = s.replace(',', '.')
    return float(s)

def format_pt(value, dec=3) -> str:
    if value is None or pd.isna(value):
        return ''
    return f"{float(value):.{dec}f}".replace('.', ',')

def load_points_txt(path: str) -> Tuple[pd.DataFrame, str]:
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        lines = [line.rstrip('\n') for line in f]
    coord_line = next((ln.strip() for ln in lines if 'Sistema de Coordenadas:' in ln), 'Sistema de Coordenadas: SIRGAS 2000 / UTM zone 19S')
    header_idx = None
    for i, line in enumerate(lines):
        if all(col in line for col in ['Nome', 'Status', 'Este', 'Norte']):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError('Cabeçalho do TXT não encontrado.')
    df = pd.read_csv(StringIO('\n'.join(lines[header_idx:])), sep=';')
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f'Colunas obrigatórias ausentes: {missing}')
    for col in ['Este', 'Norte', 'Altitude Elipsoidal', 'DP E', 'DP N', 'DP U']:
        df[col] = df[col].map(normalize_number)
    return df.reset_index(drop=True), coord_line

def parse_base_txt(path: str) -> PointData:
    df, _ = load_points_txt(path)
    row = df.iloc[0]
    return PointData(str(row['Nome']), float(row['Este']), float(row['Norte']), float(row['Altitude Elipsoidal']))

def parse_ppp_pdf(path: str) -> PPPData:
    target_line = None
    sigma_line = None
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages[:2]:
            page_text = page.extract_text() or ''
            for ln in page_text.splitlines():
                s = ln.strip()
                if not s:
                    continue
                if target_line is None and 'Em 2000.4' in s:
                    target_line = s
                if sigma_line is None and 'Sigma(95%)' in s:
                    sigma_line = s
                if target_line and sigma_line:
                    break
            if target_line and sigma_line:
                break
    if not target_line:
        raise ValueError('Linha "Em 2000.4" não encontrada no PPP.')
    vals = [normalize_number(x) for x in re.findall(r'-?\d+[\.,]\d+', target_line)]
    norths = [x for x in vals if 7000000 < x < 10000000]
    easts = [x for x in vals if 100000 < x < 900000]
    smalls = [x for x in vals if 0 < x < 10000]
    if not norths or not easts:
        raise ValueError('Não foi possível localizar UTM Norte/Este no PPP.')
    north = norths[-1]
    east = easts[-1]
    h_candidates = [x for x in smalls if abs(x - 2000.4) > 1e-6 and x < 1000]
    if not h_candidates:
        raise ValueError('Não foi possível localizar a altitude no PPP.')
    h = h_candidates[-1]
    sigma_n = sigma_e = sigma_h = None
    if sigma_line:
        sigs = [normalize_number(x) for x in re.findall(r'\d+[\.,]\d+', sigma_line)]
        if len(sigs) >= 3:
            sigma_n, sigma_e, sigma_h = sigs[0], sigs[1], sigs[2]
    return PPPData(east=east, north=north, h=h, sigma_e=sigma_e, sigma_n=sigma_n, sigma_h=sigma_h, source_pdf=path, source_kind='PPP_IBGE')

def gms_para_decimal(txt: str) -> float:
    s = str(txt).strip().replace("º", "°").replace("’", "'").replace("”", '"').replace("″", '"').replace("′", "'")
    m = re.search(r'(-?\d+)[°]\s*(\d+)[\'’]?\s*(\d+(?:[.,]\d+)?)["”]?', s)
    if not m:
        raise ValueError(f'Coordenada GMS inválida: {txt}')
    g = float(m.group(1)); mi = float(m.group(2)); se = float(m.group(3).replace(',', '.'))
    sign = -1 if g < 0 else 1
    g = abs(g)
    return sign * (g + mi / 60 + se / 3600)

def obter_fuso_utm(lon: float) -> int:
    return int((lon + 180) // 6) + 1

def converter_gms_para_utm_sirgas2000(long_gms: str, lat_gms: str):
    lon = gms_para_decimal(long_gms)
    lat = gms_para_decimal(lat_gms)
    fuso = obter_fuso_utm(lon)
    crs_origem = CRS.from_epsg(4674)
    crs_destino = CRS.from_proj4(f"+proj=utm +zone={fuso} {'+south' if lat < 0 else ''} +ellps=GRS80 +units=m +no_defs")
    transformer = Transformer.from_crs(crs_origem, crs_destino, always_xy=True)
    este, norte = transformer.transform(lon, lat)
    return este, norte, fuso

def parse_memorial_sigef_pdf(path: str):
    vertices = []
    padrao = re.compile(r'^(?P<codigo>[A-Z0-9\-]+)\s+(?P<lon>-?\d+°\d+[\'’]\d+(?:[.,]\d+)?"?)\s+(?P<lat>-?\d+°\d+[\'’]\d+(?:[.,]\d+)?"?)\s+(?P<alt>\d+(?:[.,]\d+)?)')
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            txt = page.extract_text() or ''
            for ln in txt.splitlines():
                s = ln.strip()
                if not s:
                    continue
                m = padrao.search(s)
                if m:
                    vertices.append({"codigo": m.group("codigo"), "longitude_gms": m.group("lon"), "latitude_gms": m.group("lat"), "altitude": normalize_number(m.group("alt"))})
    if not vertices:
        raise ValueError("Nenhum vértice encontrado no Memorial SIGEF.")
    return vertices

def memorial_vertex_to_pppdata(vertex: dict, source_pdf: Optional[str] = None) -> PPPData:
    este, norte, _ = converter_gms_para_utm_sirgas2000(vertex['longitude_gms'], vertex['latitude_gms'])
    return PPPData(east=este, north=norte, h=vertex['altitude'], source_pdf=source_pdf, source_kind='BASE_CONHECIDA', source_code=vertex['codigo'])

def apply_translation(df: pd.DataFrame, base: PointData, ppp: PPPData) -> Tuple[pd.DataFrame, Tuple[float, float, float]]:
    dn, de, dh = ppp.north - base.north, ppp.east - base.east, ppp.h - base.h
    out = df.copy()
    out['Este Ajustado'] = out['Este'] + de
    out['Norte Ajustado'] = out['Norte'] + dn
    out['Altitude Ajustada'] = out['Altitude Elipsoidal'] + dh
    return out, (dn, de, dh)

def export_adjusted_txt(path: str, df: pd.DataFrame, coord_line: str):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(coord_line + '\n')
        f.write('Nome;Status;Este;Norte;Altitude Elipsoidal;DP E;DP N;DP U\n')
        for _, r in df.iterrows():
            f.write(f"{r['Nome']};{r['Status']};{r['Este Ajustado']:.3f};{r['Norte Ajustado']:.3f};{r['Altitude Ajustada']:.3f};{r['DP E']:.4f};{r['DP N']:.4f};{r['DP U']:.4f}\n")
