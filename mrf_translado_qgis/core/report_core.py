from __future__ import annotations

import os
import tempfile
from typing import Optional, Tuple

import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator, ScalarFormatter
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .translado_core import Emitente, PPPData, PointData, format_pt

GREEN_SOFT = "#E8F5E9"


def _apply_plain_coordinate_axes(ax):
    fmtx = ScalarFormatter(useOffset=False)
    fmty = ScalarFormatter(useOffset=False)
    fmtx.set_scientific(False)
    fmty.set_scientific(False)
    ax.xaxis.set_major_formatter(fmtx)
    ax.yaxis.set_major_formatter(fmty)
    ax.ticklabel_format(style="plain", axis="both", useOffset=False)
    ax.xaxis.set_major_locator(MaxNLocator(nbins=7))
    ax.yaxis.set_major_locator(MaxNLocator(nbins=7))


def figure_vetorizacao(df, ppp, figsize=(6.6, 3.8), dpi=120):
    fig = plt.Figure(figsize=figsize, dpi=dpi)
    ax = fig.add_subplot(111)
    ax.set_facecolor("white")
    xs = [ppp.east] + df["Este Ajustado"].tolist()
    ys = [ppp.north] + df["Norte Ajustado"].tolist()

    for _, row in df.iterrows():
        ax.plot(
            [ppp.east, row["Este Ajustado"]],
            [ppp.north, row["Norte Ajustado"]],
            color="#c7cdd4",
            linewidth=0.9,
        )
        ax.scatter(
            [row["Este Ajustado"]],
            [row["Norte Ajustado"]],
            s=18,
            c="#2e7d32",
            edgecolors="white",
            linewidths=0.25,
        )
        ax.annotate(
            str(row["Nome"]),
            (row["Este Ajustado"], row["Norte Ajustado"]),
            xytext=(4, 2),
            textcoords="offset points",
            fontsize=7,
        )

    ax.scatter([ppp.east], [ppp.north], marker="^", s=52, c="#d62828")
    ax.annotate(
        "BASE PPP",
        (ppp.east, ppp.north),
        xytext=(6, 3),
        textcoords="offset points",
        fontsize=7.2,
        fontweight="bold",
    )
    ax.set_title(
        "VETORIZAÇÃO DOS PONTOS AJUSTADOS - BASE PPP/CONHECIDA",
        fontsize=9.5,
        fontweight="bold",
        color="#1f4e79",
        pad=8,
    )
    ax.set_xlabel("Este (m)", fontsize=8.5)
    ax.set_ylabel("Norte (m)", fontsize=8.5)
    ax.grid(True, color="#e5e7eb", linewidth=0.65)
    _apply_plain_coordinate_axes(ax)
    ax.tick_params(axis="x", labelrotation=22, labelsize=8)
    ax.tick_params(axis="y", labelsize=8)

    dx = max(xs) - min(xs) if max(xs) > min(xs) else 10
    dy = max(ys) - min(ys) if max(ys) > min(ys) else 10
    mx = dx * 0.18
    my = dy * 0.18
    ax.set_xlim(min(xs) - mx, max(xs) + mx)
    ax.set_ylim(min(ys) - my, max(ys) + my)
    fig.subplots_adjust(left=0.11, right=0.97, top=0.82, bottom=0.20)
    return fig


def save_fig(fig, path: str):
    fig.savefig(path, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _styles():
    styles = getSampleStyleSheet()
    styles["Normal"].fontName = "Helvetica"
    styles["Heading2"].textColor = colors.HexColor("#1f4e79")
    normal = styles["Normal"]
    normal.leading = 10

    title_style = ParagraphStyle(
        "main_title",
        parent=styles["Heading2"],
        alignment=1,
        fontSize=16,
        leading=18,
        spaceAfter=10,
        textColor=colors.Color(31 / 255, 78 / 255, 121 / 255),
    )
    header_style = ParagraphStyle(
        "header_style",
        parent=normal,
        fontSize=12,
        leading=12,
        alignment=1,
    )
    body10 = ParagraphStyle(
        "body10",
        parent=normal,
        fontSize=10,
        leading=10,
    )
    body_justified = ParagraphStyle(
        "body_justified",
        parent=body10,
        alignment=TA_JUSTIFY,
        fontSize=10,
        leading=12,
    )
    body_base = ParagraphStyle(
        "body_base",
        parent=body10,
        leading=15,
    )
    section_style = ParagraphStyle(
        "section_style",
        parent=body10,
        fontSize=10,
        leading=10,
        textColor=colors.Color(31 / 255, 78 / 255, 121 / 255),
        spaceAfter=2,
    )
    centered_style = ParagraphStyle(
        "centered_style",
        parent=body10,
        alignment=TA_CENTER,
        fontSize=10,
        leading=10,
        textColor=colors.Color(31 / 255, 78 / 255, 121 / 255),
        spaceAfter=3,
    )
    return (
        styles,
        normal,
        title_style,
        header_style,
        body10,
        body_justified,
        body_base,
        section_style,
        centered_style,
    )


def _standard_table_style(header=True, green_body=False):
    style = [
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#b7c9d6")),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#d9e3ea")),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
    ]
    if header:
        style.append(
            ("BACKGROUND", (0, 0), (-1, 0), colors.Color(218 / 255, 236 / 255, 246 / 255))
        )
    if green_body:
        style.append(("BACKGROUND", (0, 1), (-1, -1), colors.HexColor(GREEN_SOFT)))
    return TableStyle(style)


def create_pdf(
    output_pdf: str,
    emitente: Emitente,
    base: PointData,
    ppp: PPPData,
    result_df,
    deltas: Tuple[float, float, float],
    logo_path: Optional[str],
    use_variance: bool = True,
):
    dn, de, dh = deltas
    tmpdir = tempfile.mkdtemp(prefix="mrf_trans_qgis_")
    vet_path = os.path.join(tmpdir, "vetorizacao.png")
    save_fig(figure_vetorizacao(result_df, ppp), vet_path)

    doc = SimpleDocTemplate(
        output_pdf,
        pagesize=A4,
        leftMargin=1.4 * cm,
        rightMargin=1.4 * cm,
        topMargin=1.4 * cm,
        bottomMargin=1.2 * cm,
    )
    (
        styles,
        normal,
        title_style,
        header_style,
        body10,
        body_justified,
        body_base,
        section_style,
        centered_style,
    ) = _styles()
    elems = []

    header_inner = []
    if logo_path and os.path.exists(logo_path):
        header_inner.append([Image(logo_path, width=4.6 * cm, height=1.7 * cm)])
    else:
        header_inner.append([Paragraph("<b>MRF CONSULTORIA</b>", styles["Heading2"])])

    header_inner.extend(
        [
            [Paragraph(f"<b>{emitente.empresa or '-'}</b>", header_style)],
            [Paragraph(f"<b>CNPJ:</b> {emitente.cnpj or '-'}", header_style)],
            [Paragraph(f"{emitente.endereco or '-'}", header_style)],
            [Paragraph(f"<b>E-mail:</b> {emitente.email or '-'}", header_style)],
            [Paragraph(f"<b>Telefone:</b> {emitente.telefone or '-'}", header_style)],
        ]
    )
    header_tbl = Table(header_inner, colWidths=[17.7 * cm])
    header_tbl.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#b7c9d6")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f0f0f0")),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    elems.append(header_tbl)
    elems.append(Spacer(1, 0.25 * cm))

    ident_lines = [
        [Paragraph(f"<b>Projeto:</b> {emitente.projeto or '-'}", normal)],
        [Paragraph(f"<b>Resp. Técnico:</b> {emitente.responsavel_tecnico or '-'}", normal)],
        [Paragraph(f"<b>Cód. Credenciado:</b> {emitente.codigo_credenciado or '-'}", normal)],
        [Paragraph(f"<b>Equip. Base:</b> {emitente.equipamento_base or '-'}", normal)],
        [Paragraph(f"<b>Equip. Rover:</b> {emitente.equipamento_rover or '-'}", normal)],
        [Paragraph(f"<b>Data:</b> {emitente.data_relatorio or '-'}", normal)],
    ]
    ident_tbl = Table(ident_lines, colWidths=[17.7 * cm])
    ident_tbl.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )
    elems.append(ident_tbl)
    elems.append(Spacer(1, 0.2 * cm))
    elems.append(Paragraph("RELATÓRIO TÉCNICO DE TRANSLADO DE COORDENADAS", title_style))

    base_title = f"BASE LEVANTADA - {base.name or 'BASE'}"
    if ppp.source_kind == "PPP_IBGE":
        ppp_title = "PPP - IBGE"
    elif ppp.source_kind == "BASE_CONHECIDA":
        codigo = (ppp.source_code or "").strip()
        ppp_title = f"BASE CONHECIDA - {codigo}" if codigo else "BASE CONHECIDA"
    else:
        ppp_title = "PPP (IBGE/CONHECIDA)"

    ppp_text = (
        f"Este: {format_pt(ppp.east)}<br/>"
        f"Norte: {format_pt(ppp.north)}<br/>"
        f"Altitude: {format_pt(ppp.h)}"
    )
    if use_variance:
        ppp_text += (
            f"<br/>σE: {format_pt(ppp.sigma_e, 4) if ppp.sigma_e is not None else '-'} | "
            f"σN: {format_pt(ppp.sigma_n, 4) if ppp.sigma_n is not None else '-'} | "
            f"σH: {format_pt(ppp.sigma_h, 4) if ppp.sigma_h is not None else '-'}"
        )

    base_text = (
        f"Este: {format_pt(base.east)}<br/>"
        f"Norte: {format_pt(base.north)}<br/>"
        f"Altitude: {format_pt(base.h)}"
    )

    info_table = Table(
        [
            [
                Paragraph(f"<b>{base_title}</b>", normal),
                Paragraph(f"<b>{ppp_title}</b>", normal),
            ],
            [Paragraph(base_text, body_base), Paragraph(ppp_text, body_base)],
        ],
        colWidths=[8.85 * cm, 8.85 * cm],
    )
    info_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#b7c9d6")),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#d9e3ea")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.Color(218 / 255, 236 / 255, 246 / 255)),
            ]
        )
    )
    elems.append(info_table)
    elems.append(Spacer(1, 0.18 * cm))

    elems.append(Paragraph("<b>CORREÇÕES APLICADAS</b>", section_style))
    elems.append(Paragraph("<b>REPRESENTAÇÃO TOPOCÊNTRICA LOCAL</b>", centered_style))
    delta_tbl = Table(
        [
            [Paragraph("<b>ΔN</b>", body10), Paragraph("<b>ΔE</b>", body10), Paragraph("<b>ΔH</b>", body10)],
            [Paragraph(format_pt(dn), body10), Paragraph(format_pt(de), body10), Paragraph(format_pt(dh), body10)],
        ],
        colWidths=[2.2 * cm, 2.2 * cm, 2.2 * cm],
        hAlign="CENTER",
    )
    delta_tbl.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#b7c9d6")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.Color(218 / 255, 236 / 255, 246 / 255)),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#d9e3ea")),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ]
        )
    )
    elems.append(delta_tbl)
    elems.append(Spacer(1, 0.28 * cm))

    elems.append(Paragraph("<b>COORDENADAS ORIGINAIS</b>", section_style))
    elems.append(Spacer(1, 0.12 * cm))
    orig_data = [["Nome", "Este", "Norte", "Altitude", "σE", "σN", "σH"]]
    for _, row in result_df.iterrows():
        orig_data.append(
            [
                str(row["Nome"]),
                format_pt(row["Este"]),
                format_pt(row["Norte"]),
                format_pt(row["Altitude Elipsoidal"]),
                format_pt(row["DP E"], 4),
                format_pt(row["DP N"], 4),
                format_pt(row["DP U"], 4),
            ]
        )
    orig_tbl = Table(
        orig_data,
        repeatRows=1,
        colWidths=[3.4 * cm, 2.35 * cm, 2.8 * cm, 2.4 * cm, 2.15 * cm, 2.15 * cm, 2.15 * cm],
    )
    orig_tbl.setStyle(_standard_table_style())
    elems.append(orig_tbl)
    elems.append(Spacer(1, 0.32 * cm))

    elems.append(Paragraph("<b>COORDENADAS AJUSTADAS</b>", section_style))
    elems.append(Spacer(1, 0.12 * cm))
    adj_data = [["Nome", "Este", "Norte", "Altitude", "σE", "σN", "σH"]]
    for _, row in result_df.iterrows():
        adj_data.append(
            [
                str(row["Nome"]),
                format_pt(row["Este Ajustado"]),
                format_pt(row["Norte Ajustado"]),
                format_pt(row["Altitude Ajustada"]),
                format_pt(row["DP E Ajustado"], 4),
                format_pt(row["DP N Ajustado"], 4),
                format_pt(row["DP U Ajustado"], 4),
            ]
        )
    adj_tbl = Table(
        adj_data,
        repeatRows=1,
        colWidths=[3.4 * cm, 2.35 * cm, 2.8 * cm, 2.4 * cm, 2.15 * cm, 2.15 * cm, 2.15 * cm],
    )
    adj_tbl.setStyle(_standard_table_style(green_body=True))
    elems.append(adj_tbl)
    elems.append(Spacer(1, 0.25 * cm))

    if use_variance:
        metodologia = (
            "Os pontos rover foram ajustados por meio da aplicação de uma translação cartesiana no sistema UTM, "
            "utilizando como referência a diferença entre a base levantada em campo e a base conhecida obtida via "
            "PPP IBGE ou Memorial SIGEF. O vetor de correção (ΔE, ΔN, ΔH) foi calculado diretamente no sistema plano "
            "e aplicado de forma uniforme a todos os pontos levantados, garantindo consistência geométrica entre as "
            "coordenadas ajustadas. Para fins de interpretação, o vetor de correção é apresentado na representação "
            "topocêntrica local (E, N, U)."
        )
        propagacao = (
            "A propagação das variâncias foi realizada considerando a incerteza das coordenadas observadas e a "
            "incerteza da base conhecida utilizada na correção. Os desvios padrão ajustados foram obtidos pela "
            "combinação quadrática das variâncias, conforme o modelo: σ_final = √(σ_rover² + σ_correção²). "
            "Dessa forma, a precisão final dos pontos ajustados reflete simultaneamente a qualidade do levantamento "
            "original e a precisão da base de referência adotada."
        )
        observacoes = (
            "Os resultados apresentados são válidos para as condições do levantamento informado. Recomenda-se a "
            "verificação dos desvios padrão e da consistência geométrica antes da utilização em processos técnicos, "
            "legais ou certificação junto aos órgãos competentes."
        )

        elems.append(Paragraph("<b>METODOLOGIA</b>", section_style))
        elems.append(Paragraph(metodologia, body_justified))
        elems.append(Spacer(1, 0.16 * cm))
        elems.append(Paragraph("<b>PROPAGAÇÃO DAS VARIÂNCIAS</b>", section_style))
        elems.append(Paragraph(propagacao, body_justified))
        elems.append(Spacer(1, 0.16 * cm))
        elems.append(Paragraph("<b>OBSERVAÇÕES</b>", section_style))
        elems.append(Paragraph(observacoes, body_justified))
        elems.append(PageBreak())

    elems.append(Paragraph("Vetorização dos Pontos", title_style))
    elems.append(Image(vet_path, width=17.7 * cm, height=11.2 * cm))
    elems.append(Spacer(1, 0.8 * cm))

    assinatura_nome = emitente.responsavel_tecnico or "-"
    assinatura_cpf = getattr(emitente, "cpf_profissional", "") or "-"
    conselho = getattr(emitente, "conselho_classe", "") or ""
    registro = getattr(emitente, "numero_registro", "") or "-"

    if conselho:
        conselho_registro = f"{conselho} nº {registro}"
    else:
        conselho_registro = f"Nº de Registro: {registro}"

    signature_style = ParagraphStyle(
        "signature_style",
        parent=body10,
        alignment=TA_CENTER,
        fontSize=10,
        leading=13,
    )

    assinatura_tbl = Table(
        [
            [Paragraph("__________________________________________", signature_style)],
            [Paragraph(f"<b>{assinatura_nome}</b>", signature_style)],
            [Paragraph(f"CPF: {assinatura_cpf}", signature_style)],
            [Paragraph(conselho_registro, signature_style)],
        ],
        colWidths=[17.7 * cm],
    )
    assinatura_tbl.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    elems.append(assinatura_tbl)

    doc.build(elems)
