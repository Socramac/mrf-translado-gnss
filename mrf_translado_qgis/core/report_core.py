from __future__ import annotations
import os, tempfile
from typing import Optional, Tuple
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import ScalarFormatter, MaxNLocator
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak
from .translado_core import Emitente, PointData, PPPData, format_pt
GREEN_SOFT = '#E8F5E9'

def _apply_plain_coordinate_axes(ax):
    fmtx = ScalarFormatter(useOffset=False); fmty = ScalarFormatter(useOffset=False)
    fmtx.set_scientific(False); fmty.set_scientific(False)
    ax.xaxis.set_major_formatter(fmtx); ax.yaxis.set_major_formatter(fmty)
    ax.ticklabel_format(style='plain', axis='both', useOffset=False)
    ax.xaxis.set_major_locator(MaxNLocator(nbins=7)); ax.yaxis.set_major_locator(MaxNLocator(nbins=7))

def figure_vetorizacao(df, ppp, figsize=(6.6, 3.8), dpi=120):
    fig = plt.Figure(figsize=figsize, dpi=dpi); ax = fig.add_subplot(111); ax.set_facecolor('white')
    xs = [ppp.east] + df['Este Ajustado'].tolist(); ys = [ppp.north] + df['Norte Ajustado'].tolist()
    for _, r in df.iterrows():
        ax.plot([ppp.east, r['Este Ajustado']], [ppp.north, r['Norte Ajustado']], color='#c7cdd4', linewidth=0.9)
        ax.scatter([r['Este Ajustado']], [r['Norte Ajustado']], s=18, c='#2e7d32', edgecolors='white', linewidths=0.25)
        ax.annotate(str(r['Nome']), (r['Este Ajustado'], r['Norte Ajustado']), xytext=(4, 2), textcoords='offset points', fontsize=7)
    ax.scatter([ppp.east], [ppp.north], marker='^', s=52, c='#d62828')
    ax.annotate('BASE PPP', (ppp.east, ppp.north), xytext=(6, 3), textcoords='offset points', fontsize=7.2, fontweight='bold')
    ax.set_title('VETORIZAÇÃO DOS PONTOS AJUSTADOS - BASE PPP/CONHECIDA', fontsize=9.5, fontweight='bold', color='#1f4e79', pad=8)
    ax.set_xlabel('Este (m)', fontsize=8.5); ax.set_ylabel('Norte (m)', fontsize=8.5)
    ax.grid(True, color='#e5e7eb', linewidth=0.65); _apply_plain_coordinate_axes(ax)
    ax.tick_params(axis='x', labelrotation=22, labelsize=8); ax.tick_params(axis='y', labelsize=8)
    dx = max(xs) - min(xs) if max(xs) > min(xs) else 10; dy = max(ys) - min(ys) if max(ys) > min(ys) else 10
    mx = dx * 0.18; my = dy * 0.18; ax.set_xlim(min(xs)-mx, max(xs)+mx); ax.set_ylim(min(ys)-my, max(ys)+my)
    fig.subplots_adjust(left=0.11, right=0.97, top=0.82, bottom=0.20); return fig

def save_fig(fig, path: str):
    fig.savefig(path, dpi=300, bbox_inches='tight', facecolor='white'); plt.close(fig)

def create_pdf(output_pdf: str, emitente: Emitente, base: PointData, ppp: PPPData, result_df, deltas: Tuple[float, float, float], logo_path: Optional[str]):
    dn, de, dh = deltas
    tmpdir = tempfile.mkdtemp(prefix='mrf_trans_qgis_'); vet_path = os.path.join(tmpdir, 'vetorizacao.png'); save_fig(figure_vetorizacao(result_df, ppp), vet_path)
    doc = SimpleDocTemplate(output_pdf, pagesize=A4, leftMargin=1.4*cm, rightMargin=1.4*cm, topMargin=1.4*cm, bottomMargin=1.2*cm)
    styles = getSampleStyleSheet(); styles['Normal'].fontName = 'Helvetica'; styles['Heading2'].textColor = colors.HexColor('#1f4e79')
    title_style = ParagraphStyle('main_title', parent=styles['Heading2'], alignment=1, fontSize=16, leading=18, spaceAfter=10, textColor=colors.Color(31/255,78/255,121/255))
    normal = styles['Normal']; normal.leading = 10
    header_style = ParagraphStyle('header_style', parent=normal, fontSize=12, leading=12, alignment=1)
    body10 = ParagraphStyle('body10', parent=normal, fontSize=10, leading=10)
    body_base = ParagraphStyle('body_base', parent=body10, leading=15)
    section_style = ParagraphStyle('section_style', parent=body10, fontSize=10, leading=10, textColor=colors.Color(31/255,78/255,121/255), spaceAfter=2)
    elems = []
    header_inner = []
    if logo_path and os.path.exists(logo_path): header_inner.append([Image(logo_path, width=4.6*cm, height=1.7*cm)])
    else: header_inner.append([Paragraph('<b>MRF CONSULTORIA</b>', styles['Heading2'])])
    header_inner.extend([[Paragraph(f"<b>{emitente.empresa or '-'}</b>", header_style)],[Paragraph(f"<b>CNPJ:</b> {emitente.cnpj or '-'}", header_style)],[Paragraph(f"{emitente.endereco or '-'}", header_style)],[Paragraph(f"<b>E-mail:</b> {emitente.email or '-'}", header_style)],[Paragraph(f"<b>Telefone:</b> {emitente.telefone or '-'}", header_style)]])
    header_tbl = Table(header_inner, colWidths=[17.7*cm]); header_tbl.setStyle(TableStyle([('BOX',(0,0),(-1,-1),0.8,colors.HexColor('#b7c9d6')),('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#f0f0f0')),('ALIGN',(0,0),(-1,-1),'CENTER'),('VALIGN',(0,0),(-1,-1),'MIDDLE')]))
    elems.append(header_tbl); elems.append(Spacer(1,0.25*cm))
    ident_lines = [
        [Paragraph(f"<b>Projeto:</b> {emitente.projeto or '-'}", normal)],
        [Paragraph(f"<b>Resp. Técnico:</b> {emitente.responsavel_tecnico or '-'}", normal)],
        [Paragraph(f"<b>Cód. Credenciado:</b> {emitente.codigo_credenciado or '-'}", normal)],
        [Paragraph(f"<b>Equip. Base:</b> {emitente.equipamento_base or '-'}", normal)],
        [Paragraph(f"<b>Equip. Rover:</b> {emitente.equipamento_rover or '-'}", normal)],
        [Paragraph(f"<b>Data:</b> {emitente.data_relatorio or '-'}", normal)],
    ]
    ident_tbl = Table(ident_lines, colWidths=[17.7*cm]); ident_tbl.setStyle(TableStyle([('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),('TOPPADDING',(0,0),(-1,-1),1),('BOTTOMPADDING',(0,0),(-1,-1),1)]))
    elems.append(ident_tbl); elems.append(Spacer(1,0.2*cm))
    elems.append(Paragraph('RELATÓRIO TÉCNICO DE TRANSLADO DE COORDENADAS', title_style))
    base_title = f"BASE LEVANTADA - {base.name or 'BASE'}"
    ppp_title = 'PPP - IBGE' if ppp.source_kind == 'PPP_IBGE' else (f"BASE CONHECIDA - {(ppp.source_code or '').strip()}" if ppp.source_kind == 'BASE_CONHECIDA' else 'PPP (IBGE/CONHECIDA)')
    info_table = Table([[Paragraph(f'<b>{base_title}</b>', normal), Paragraph(f'<b>{ppp_title}</b>', normal)],[Paragraph(f"Este: {format_pt(base.east)}<br/>Norte: {format_pt(base.north)}<br/>Altitude: {format_pt(base.h)}", body_base), Paragraph(f"Este: {format_pt(ppp.east)}<br/>Norte: {format_pt(ppp.north)}<br/>Altitude: {format_pt(ppp.h)}", body_base)]], colWidths=[8.85*cm,8.85*cm])
    info_table.setStyle(TableStyle([('BOX',(0,0),(-1,-1),0.8,colors.HexColor('#b7c9d6')),('INNERGRID',(0,0),(-1,-1),0.35,colors.HexColor('#d9e3ea')),('BACKGROUND',(0,0),(-1,0),colors.Color(218/255,236/255,246/255))]))
    elems.append(info_table); elems.append(Spacer(1,0.18*cm))
    elems.append(Paragraph('<b>Tabela de Deltas</b>', section_style))
    delta_tbl = Table([[Paragraph('<b>ΔN</b>', body10), Paragraph('<b>ΔE</b>', body10), Paragraph('<b>ΔH</b>', body10)],[Paragraph(format_pt(dn), body10), Paragraph(format_pt(de), body10), Paragraph(format_pt(dh), body10)]], colWidths=[2.2*cm,2.2*cm,2.2*cm], hAlign='CENTER')
    delta_tbl.setStyle(TableStyle([('BOX',(0,0),(-1,-1),0.8,colors.HexColor('#b7c9d6')),('BACKGROUND',(0,0),(-1,0),colors.Color(218/255,236/255,246/255)),('INNERGRID',(0,0),(-1,-1),0.35,colors.HexColor('#d9e3ea')),('ALIGN',(0,0),(-1,-1),'CENTER')]))
    elems.append(delta_tbl); elems.append(Spacer(1,0.28*cm))
    elems.append(Paragraph('<b>Coordenadas Ajustadas</b>', section_style)); elems.append(Spacer(1,0.12*cm))
    adj_data = [['Nome','Este','Norte','Altitude']]
    for _, r in result_df.iterrows(): adj_data.append([str(r['Nome']), format_pt(r['Este Ajustado']), format_pt(r['Norte Ajustado']), format_pt(r['Altitude Ajustada'])])
    adj_tbl = Table(adj_data, repeatRows=1, colWidths=[4.5*cm,4*cm,4*cm,4*cm]); adj_tbl.setStyle(TableStyle([('BOX',(0,0),(-1,-1),0.8,colors.HexColor('#b7c9d6')),('BACKGROUND',(0,0),(-1,0),colors.Color(218/255,236/255,246/255)),('BACKGROUND',(0,1),(-1,-1),colors.HexColor(GREEN_SOFT)),('INNERGRID',(0,0),(-1,-1),0.35,colors.HexColor('#d9e3ea'))]))
    elems.append(adj_tbl); elems.append(PageBreak()); elems.append(Paragraph('Vetorização dos Pontos', title_style)); elems.append(Image(vet_path, width=17.7*cm, height=11.2*cm))
    doc.build(elems)
