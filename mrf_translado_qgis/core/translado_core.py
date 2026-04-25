from __future__ import annotations

import re
from dataclasses import dataclass
from io import StringIO
from typing import Optional, Tuple

import pandas as pd
from pyproj import CRS, Transformer

REQUIRED_COLUMNS = [
    "Nome",
    "Status",
    "Este",
    "Norte",
    "Altitude Elipsoidal",
    "DP E",
    "DP N",
    "DP U",
]


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
    logo: str = ""
    empresa: str = ""
    cnpj: str = ""
    endereco: str = ""
    email: str = ""
    telefone: str = ""
    projeto: str = ""
    responsavel_tecnico: str = ""
    cpf_profissional: str = ""
    conselho_classe: str = ""
    numero_registro: str = ""
    codigo_credenciado: str = ""
    equipamento_base: str = ""
    equipamento_rover: str = ""
    data_relatorio: str = ""


def _require_pdfplumber():
    try:
        import pdfplumber  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "A biblioteca 'pdfplumber' não está instalada no Python do QGIS.\n\n"
            "Instale no ambiente do QGIS com:\n"
            "pip install pdfplumber"
        ) from exc
    return pdfplumber


def normalize_number(value) -> float:
    s = str(value).strip().replace(" ", "")
    if not s:
        raise ValueError("Campo numérico vazio.")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    else:
        s = s.replace(",", ".")
    return float(s)


def format_pt(value, dec=3) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{float(value):.{dec}f}".replace(".", ",")


def load_points_txt(path: str) -> Tuple[pd.DataFrame, str]:
    with open(path, "r", encoding="utf-8", errors="ignore") as file_obj:
        lines = [line.rstrip("\n") for line in file_obj]

    coord_line = next(
        (line.strip() for line in lines if "Sistema de Coordenadas:" in line),
        "Sistema de Coordenadas: SIRGAS 2000 / UTM zone 19S",
    )

    header_idx = None
    for index, line in enumerate(lines):
        if all(col in line for col in ["Nome", "Status", "Este", "Norte"]):
            header_idx = index
            break

    if header_idx is None:
        raise ValueError("Cabeçalho do TXT não encontrado.")

    df = pd.read_csv(StringIO("\n".join(lines[header_idx:])), sep=";")

    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"Colunas obrigatórias ausentes: {missing}")

    for column in REQUIRED_COLUMNS[2:]:
        df[column] = df[column].map(normalize_number)

    return df.reset_index(drop=True), coord_line


def parse_base_txt(path: str) -> PointData:
    df, _ = load_points_txt(path)
    row = df.iloc[0]
    return PointData(
        str(row["Nome"]),
        float(row["Este"]),
        float(row["Norte"]),
        float(row["Altitude Elipsoidal"]),
    )


def parse_ppp_pdf(path: str) -> PPPData:
    pdfplumber = _require_pdfplumber()

    target_line = None
    sigma_line = None
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages[:2]:
            page_text = page.extract_text() or ""
            for raw_line in page_text.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                if target_line is None and "Em 2000.4" in line:
                    target_line = line
                if sigma_line is None and "Sigma(95%)" in line:
                    sigma_line = line
                if target_line and sigma_line:
                    break
            if target_line and sigma_line:
                break

    if not target_line:
        raise ValueError('Linha "Em 2000.4" não encontrada no PPP.')

    values = [normalize_number(item) for item in re.findall(r"-?\d+[\.,]\d+", target_line)]
    norths = [value for value in values if 7000000 < value < 10000000]
    easts = [value for value in values if 100000 < value < 900000]
    smalls = [value for value in values if 0 < value < 10000]

    if not norths or not easts:
        raise ValueError("Não foi possível localizar UTM Norte/Este no PPP.")

    north = norths[-1]
    east = easts[-1]
    h_candidates = [value for value in smalls if abs(value - 2000.4) > 1e-6 and value < 1000]

    if not h_candidates:
        raise ValueError("Não foi possível localizar a altitude no PPP.")

    h = h_candidates[-1]
    sigma_n = sigma_e = sigma_h = None

    if sigma_line:
        sigmas = [normalize_number(item) for item in re.findall(r"\d+[\.,]\d+", sigma_line)]
        if len(sigmas) >= 3:
            sigma_n, sigma_e, sigma_h = sigmas[0], sigmas[1], sigmas[2]

    return PPPData(
        east=east,
        north=north,
        h=h,
        sigma_e=sigma_e,
        sigma_n=sigma_n,
        sigma_h=sigma_h,
        source_pdf=path,
        source_kind="PPP_IBGE",
    )


def gms_para_decimal(texto: str) -> float:
    normalized = (
        str(texto)
        .strip()
        .replace("º", "°")
        .replace("’", "'")
        .replace("”", '"')
        .replace("″", '"')
        .replace("′", "'")
    )

    match = re.search(
        r'(-?\d+)[°]\s*(\d+)[\'’]?\s*(\d+(?:[.,]\d+)?)["”]?',
        normalized,
    )
    if not match:
        raise ValueError(f"Coordenada GMS inválida: {texto}")

    graus = float(match.group(1))
    minutos = float(match.group(2))
    segundos = float(match.group(3).replace(",", "."))
    sign = -1 if graus < 0 else 1
    graus = abs(graus)
    return sign * (graus + minutos / 60 + segundos / 3600)


def obter_fuso_utm(longitude: float) -> int:
    return int((longitude + 180) // 6) + 1


def converter_gms_para_utm_sirgas2000(long_gms: str, lat_gms: str):
    lon = gms_para_decimal(long_gms)
    lat = gms_para_decimal(lat_gms)
    fuso = obter_fuso_utm(lon)

    crs_origem = CRS.from_epsg(4674)
    crs_destino = CRS.from_proj4(
        (
            f"+proj=utm +zone={fuso} "
            f"{'+south' if lat < 0 else ''} "
            "+ellps=GRS80 +units=m +no_defs"
        ).strip()
    )
    transformer = Transformer.from_crs(crs_origem, crs_destino, always_xy=True)
    este, norte = transformer.transform(lon, lat)

    return este, norte, fuso


def parse_memorial_sigef_pdf(path: str):
    pdfplumber = _require_pdfplumber()

    vertices = []
    pattern = re.compile(
        r'^(?P<codigo>[A-Z0-9\-]+)\s+'
        r'(?P<lon>-?\d+°\d+[\'’]\d+(?:[.,]\d+)?"?)\s+'
        r'(?P<lat>-?\d+°\d+[\'’]\d+(?:[.,]\d+)?"?)\s+'
        r'(?P<alt>\d+(?:[.,]\d+)?)'
    )

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                match = pattern.search(line)
                if match:
                    vertices.append(
                        {
                            "codigo": match.group("codigo"),
                            "longitude_gms": match.group("lon"),
                            "latitude_gms": match.group("lat"),
                            "altitude": normalize_number(match.group("alt")),
                        }
                    )

    if not vertices:
        raise ValueError("Nenhum vértice encontrado no Memorial SIGEF.")

    return vertices


def memorial_vertex_to_pppdata(vertex: dict, source_pdf: Optional[str] = None) -> PPPData:
    este, norte, _ = converter_gms_para_utm_sirgas2000(
        vertex["longitude_gms"],
        vertex["latitude_gms"],
    )
    return PPPData(
        east=este,
        north=norte,
        h=vertex["altitude"],
        source_pdf=source_pdf,
        source_kind="BASE_CONHECIDA",
        source_code=vertex["codigo"],
    )


def apply_translation(
    df: pd.DataFrame,
    base: PointData,
    ppp: PPPData,
    use_variance: bool = True,
) -> Tuple[pd.DataFrame, Tuple[float, float, float]]:
    dn = ppp.north - base.north
    de = ppp.east - base.east
    dh = ppp.h - base.h

    out = df.copy()
    out["Este Ajustado"] = out["Este"] + de
    out["Norte Ajustado"] = out["Norte"] + dn
    out["Altitude Ajustada"] = out["Altitude Elipsoidal"] + dh

    if use_variance:
        sigma_de = float(ppp.sigma_e or 0.0)
        sigma_dn = float(ppp.sigma_n or 0.0)
        sigma_dh = float(ppp.sigma_h or 0.0)

        out["DP E Ajustado"] = (out["DP E"].astype(float) ** 2 + sigma_de ** 2) ** 0.5
        out["DP N Ajustado"] = (out["DP N"].astype(float) ** 2 + sigma_dn ** 2) ** 0.5
        out["DP U Ajustado"] = (out["DP U"].astype(float) ** 2 + sigma_dh ** 2) ** 0.5
    else:
        out["DP E Ajustado"] = out["DP E"].astype(float)
        out["DP N Ajustado"] = out["DP N"].astype(float)
        out["DP U Ajustado"] = out["DP U"].astype(float)

    return out, (dn, de, dh)


def export_adjusted_txt(path: str, df: pd.DataFrame, coord_line: str):
    with open(path, "w", encoding="utf-8") as file_obj:
        file_obj.write(coord_line + "\n")
        file_obj.write("Nome;Status;Este;Norte;Altitude Elipsoidal;DP E;DP N;DP U\n")
        for _, row in df.iterrows():
            dp_e = row["DP E Ajustado"] if "DP E Ajustado" in df.columns else row["DP E"]
            dp_n = row["DP N Ajustado"] if "DP N Ajustado" in df.columns else row["DP N"]
            dp_u = row["DP U Ajustado"] if "DP U Ajustado" in df.columns else row["DP U"]

            file_obj.write(
                f"{row['Nome']};{row['Status']};{row['Este Ajustado']:.3f};"
                f"{row['Norte Ajustado']:.3f};{row['Altitude Ajustada']:.3f};"
                f"{float(dp_e):.4f};{float(dp_n):.4f};{float(dp_u):.4f}\n"
            )
