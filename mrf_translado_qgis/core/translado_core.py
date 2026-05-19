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
    session_start: Optional[str] = None
    session_end: Optional[str] = None
    reference_epoch: Optional[str] = None
    orbit_type: Optional[str] = None
    processed_frequency: Optional[str] = None
    normal_height: Optional[float] = None
    geoid_model: Optional[str] = None
    geoid_factor: Optional[float] = None
    geoid_uncertainty: Optional[float] = None
    lat_20004: Optional[str] = None
    lon_20004: Optional[str] = None
    alt_20004: Optional[float] = None
    utm_n_20004: Optional[float] = None
    utm_e_20004: Optional[float] = None
    lat_survey: Optional[str] = None
    lon_survey: Optional[str] = None
    alt_survey: Optional[float] = None
    utm_n_survey: Optional[float] = None
    utm_e_survey: Optional[float] = None
    dn_epoch: Optional[float] = None
    de_epoch: Optional[float] = None
    dh_epoch: Optional[float] = None


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
    full_text_parts = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages[:2]:
            full_text_parts.append(page.extract_text() or "")
    full_text = "\n".join(full_text_parts)
    lines = [line.strip() for line in full_text.splitlines() if line.strip()]

    def _find(pattern: str, default: Optional[str] = None) -> Optional[str]:
        match = re.search(pattern, full_text, flags=re.IGNORECASE)
        return match.group(1).strip() if match else default

    def _first_line_contains(token: str) -> Optional[str]:
        for line in lines:
            if token.lower() in line.lower():
                return line
        return None

    def _extract_coord_line(line: str):
        coord_pattern = re.compile(
            r"(?P<lat>-?\d+°\s*\d+[´'’]\s*\d+(?:[,.]\d+)?[˝\"”]?)\s+"
            r"(?P<lon>-?\d+°\s*\d+[´'’]\s*\d+(?:[,.]\d+)?[˝\"”]?)\s+"
            r"(?P<alt>-?\d+(?:[,.]\d+)?)\s+"
            r"(?P<north>\d{7,8}(?:[,.]\d+)?)\s+"
            r"(?P<east>\d{5,6}(?:[,.]\d+)?)"
        )
        match = coord_pattern.search(line)
        if match:
            return {
                "lat": match.group("lat").replace("´", "'").replace("˝", '"'),
                "lon": match.group("lon").replace("´", "'").replace("˝", '"'),
                "alt": normalize_number(match.group("alt")),
                "north": normalize_number(match.group("north")),
                "east": normalize_number(match.group("east")),
            }
        values = [normalize_number(item) for item in re.findall(r"-?\d+[\.,]\d+", line)]
        norths = [value for value in values if 7000000 < value < 10000000]
        easts = [value for value in values if 100000 < value < 900000]
        smalls = [value for value in values if 0 < value < 10000 and abs(value - 2000.4) > 1e-6]
        if not norths or not easts or not smalls:
            raise ValueError("Não foi possível interpretar linha de coordenadas PPP.")
        return {"lat": "", "lon": "", "alt": smalls[-1], "north": norths[-1], "east": easts[-1]}

    target_line = _first_line_contains("Em 2000.4")
    survey_line = _first_line_contains("Na data do levantamento")
    sigma_line = _first_line_contains("Sigma(95%)")
    if not target_line:
        raise ValueError('Linha "Em 2000.4" não encontrada no PPP.')

    coord_20004 = _extract_coord_line(target_line)
    coord_survey = _extract_coord_line(survey_line) if survey_line else None

    sigma_n = sigma_e = sigma_h = None
    if sigma_line:
        sigmas = [normalize_number(item) for item in re.findall(r"\d+[\.,]\d+", sigma_line)]
        if len(sigmas) >= 3:
            sigma_n, sigma_e, sigma_h = sigmas[0], sigmas[1], sigmas[2]

    inicio = _find(r"Início:[^\n]*?(\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2})")
    fim = _find(r"Fim:[^\n]*?(\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2})")
    orbit = _find(r"Órbitas dos satélites:\s*\d*\s*([A-ZÁÉÍÓÚÂÊÔÃÕÇ]+)")
    freq = _find(r"Frequência processada:\s*([A-Z0-9]+)")
    geoid_model = _find(r"Modelo:\s*([A-Za-z0-9_]+)")
    geoid_factor = _find(r"Fator para Conversão \(m\):\s*([-\d,.]+)")
    geoid_unc = _find(r"Incerteza \(m\):\s*([-\d,.]+)")
    normal_height = _find(r"Altitude Normal \(m\):\s*([-\d,.]+)")

    dn_epoch = de_epoch = dh_epoch = None
    if coord_survey:
        dn_epoch = coord_survey["north"] - coord_20004["north"]
        de_epoch = coord_survey["east"] - coord_20004["east"]
        dh_epoch = coord_survey["alt"] - coord_20004["alt"]

    return PPPData(
        east=coord_20004["east"],
        north=coord_20004["north"],
        h=coord_20004["alt"],
        sigma_e=sigma_e,
        sigma_n=sigma_n,
        sigma_h=sigma_h,
        source_pdf=path,
        source_kind="PPP_IBGE",
        session_start=inicio,
        session_end=fim,
        reference_epoch="2000.4",
        orbit_type=orbit,
        processed_frequency=freq,
        normal_height=normalize_number(normal_height) if normal_height else None,
        geoid_model=geoid_model,
        geoid_factor=normalize_number(geoid_factor) if geoid_factor else None,
        geoid_uncertainty=normalize_number(geoid_unc) if geoid_unc else None,
        lat_20004=coord_20004["lat"],
        lon_20004=coord_20004["lon"],
        alt_20004=coord_20004["alt"],
        utm_n_20004=coord_20004["north"],
        utm_e_20004=coord_20004["east"],
        lat_survey=coord_survey["lat"] if coord_survey else None,
        lon_survey=coord_survey["lon"] if coord_survey else None,
        alt_survey=coord_survey["alt"] if coord_survey else None,
        utm_n_survey=coord_survey["north"] if coord_survey else None,
        utm_e_survey=coord_survey["east"] if coord_survey else None,
        dn_epoch=dn_epoch,
        de_epoch=de_epoch,
        dh_epoch=dh_epoch,
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
    full_text_parts = []
    pattern = re.compile(
        r'^(?P<codigo>[A-Z0-9\-]+)\s+'
        r'(?P<lon>-?\d+°\d+[\'’]\d+(?:[.,]\d+)?"?)\s+'
        r'(?P<lat>-?\d+°\d+[\'’]\d+(?:[.,]\d+)?"?)\s+'
        r'(?P<alt>\d+(?:[.,]\d+)?)'
    )

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            full_text_parts.append(text)
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

    full_text = "\n".join(full_text_parts)

    def _find(pattern_text: str, default: str = "") -> str:
        match = re.search(pattern_text, full_text, flags=re.IGNORECASE)
        return match.group(1).strip() if match else default

    denominacao = _find(r"Denominação:\s*(.+)")
    sistema = _find(r"Sistema Geodésico de referência:\s*([^\n]+?)(?:\s+Documento|\n|$)", "SIRGAS 2000")
    credenciado = _find(r"Código de credenciamento:\s*([A-Z0-9]+)")
    documento_rt = _find(r"Documento de RT:\s*([^\n]+)")
    data_cert = _find(r"Data Certificação:\s*([^\n]+)")
    status = ""
    if "Certificada - Sem Confirmação de Registro em Cartório" in full_text:
        status = "Certificada - Sem Confirmação de Registro em Cartório"
    elif "certificada" in full_text.lower():
        status = "Certificada"

    metadata = {
        "denominacao": denominacao,
        "sistema_geodesico": sistema,
        "credenciado": credenciado,
        "documento_rt": documento_rt,
        "data_certificacao": data_cert,
        "status_certificacao": status,
    }
    for vertex in vertices:
        vertex.update(metadata)

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
