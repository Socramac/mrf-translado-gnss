from __future__ import annotations

import json
from datetime import datetime
import os
from pathlib import Path
from typing import Optional

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton,
    QRadioButton, QSplitter, QStyle, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget
)

from .core.translado_core import (
    Emitente, PointData, PPPData, apply_translation, export_adjusted_txt,
    format_pt, load_points_txt, memorial_vertex_to_pppdata, parse_base_txt,
    parse_memorial_sigef_pdf, parse_ppp_pdf
)
from .core.report_core import create_pdf

CONFIG_FILE = Path.home() / ".mrf_translado_qgis_emitente.json"


class EmitenteDialog(QDialog):
    def __init__(self, emitente: Emitente, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cadastro Empresa/Profissional")
        self.resize(620, 440)
        self.result = None
        self.vars = {k: QLineEdit(getattr(emitente, k, '')) for k in emitente.__dataclass_fields__.keys()}

        root = QVBoxLayout(self)
        form = QFormLayout()
        fields = [
            ('Logo', 'logo'), ('Empresa', 'empresa'), ('CNPJ', 'cnpj'), ('Endereço', 'endereco'),
            ('E-mail', 'email'), ('Telefone', 'telefone'), ('Projeto', 'projeto'),
            ('Resp. Técnico', 'responsavel_tecnico'), ('Cód. Credenciado', 'codigo_credenciado'),
            ('Equip. Base', 'equipamento_base'), ('Equip. Rover', 'equipamento_rover'), ('Data', 'data_relatorio')
        ]
        for label, key in fields:
            if key == 'logo':
                row = QHBoxLayout()
                row.addWidget(self.vars[key], 1)
                btn = QPushButton("...")
                btn.clicked.connect(self.pick_logo)
                row.addWidget(btn)
                wrap = QWidget()
                wrap.setLayout(row)
                form.addRow(label, wrap)
            else:
                form.addRow(label, self.vars[key])
        root.addLayout(form)

        buttons = QHBoxLayout()
        btn_save = QPushButton("Salvar")
        btn_cancel = QPushButton("Cancelar")
        btn_save.clicked.connect(self.on_save)
        btn_cancel.clicked.connect(self.reject)
        buttons.addStretch()
        buttons.addWidget(btn_save)
        buttons.addWidget(btn_cancel)
        root.addLayout(buttons)

    def pick_logo(self):
        path, _ = QFileDialog.getOpenFileName(self, "Escolher logo", "", "Imagens (*.png *.jpg *.jpeg *.gif)")
        if path:
            self.vars['logo'].setText(path)

    def on_save(self):
        self.result = Emitente(**{k: v.text().strip() for k, v in self.vars.items()})
        self.accept()


class VertexSelectorDialog(QDialog):
    def __init__(self, vertices, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Selecionar vértice da base")
        self.setMinimumWidth(420)
        self.vertices = vertices
        self.selected_vertex = None

        layout = QVBoxLayout(self)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Buscar vértice...")
        self.search_edit.textChanged.connect(self.filter_list)
        layout.addWidget(self.search_edit)

        self.list_widget = QListWidget()
        for v in vertices:
            self.list_widget.addItem(v["codigo"])
        layout.addWidget(self.list_widget)

        row = QHBoxLayout()
        btn_ok = QPushButton("Selecionar")
        btn_cancel = QPushButton("Cancelar")
        btn_ok.clicked.connect(self.accept_selection)
        btn_cancel.clicked.connect(self.reject)
        row.addStretch()
        row.addWidget(btn_ok)
        row.addWidget(btn_cancel)
        layout.addLayout(row)

    def filter_list(self, text):
        text = text.strip().upper()
        self.list_widget.clear()
        for v in self.vertices:
            if text in v["codigo"].upper():
                self.list_widget.addItem(v["codigo"])

    def accept_selection(self):
        item = self.list_widget.currentItem()
        if not item:
            QMessageBox.warning(self, "MRF Translado GNSS", "Selecione um vértice.")
            return
        codigo = item.text()
        for v in self.vertices:
            if v["codigo"] == codigo:
                self.selected_vertex = v
                break
        self.accept()


class MRFTransladoDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle("MRF Translado GNSS")
        self.resize(1260, 780)
        self.df = None
        self.result_df = None
        self.coord_line = 'Sistema de Coordenadas: SIRGAS 2000 / UTM zone 19S'
        self.base_data: Optional[PointData] = None
        self.ppp_data: Optional[PPPData] = None
        self.emitente = self.load_emitente()
        self._build_ui()
        self.apply_theme_styles()

    def load_emitente(self) -> Emitente:
        if CONFIG_FILE.exists():
            try:
                return Emitente(**json.loads(CONFIG_FILE.read_text(encoding='utf-8')))
            except Exception:
                pass
        return Emitente()

    def save_emitente(self):
        CONFIG_FILE.write_text(json.dumps(self.emitente.__dict__, ensure_ascii=False, indent=2), encoding='utf-8')

    def _is_dark_theme(self) -> bool:
        bg = self.palette().window().color()
        return bg.lightness() < 128

    def _style_button(self, btn: QPushButton, variant="light"):
        dark_theme = self._is_dark_theme()
        common = "padding:8px 12px; border-radius:8px; font-weight:600;"
        if variant == "dark":
            if dark_theme:
                btn.setStyleSheet(common + "background:#2f6b9f; color:white; border:1px solid #4a82b2;")
            else:
                btn.setStyleSheet(common + "background:#1f4e79; color:white; border:1px solid #214a73;")
        elif variant == "warn":
            if dark_theme:
                btn.setStyleSheet(common + "background:#c99a1a; color:white; border:1px solid #dfb53c;")
            else:
                btn.setStyleSheet(common + "background:#f4c542; color:#1f1f1f; border:1px solid #c59f2c;")
        else:
            if dark_theme:
                btn.setStyleSheet(common + "background:#3b4a57; color:white; border:1px solid #556575;")
            else:
                btn.setStyleSheet(common + "background:#d6eaf8; color:#1f1f1f; border:1px solid #b7cadb;")

    def _set_btn_icon(self, btn, icon_name):
        from qgis.PyQt.QtGui import QIcon
        icon_path = os.path.join(os.path.dirname(__file__), 'icons', icon_name)
        btn.setIcon(QIcon(icon_path))

    def apply_theme_styles(self):
        dark_theme = self._is_dark_theme()
        if dark_theme:
            self.delta_label.setStyleSheet("font-size:16px; font-weight:700; color:#f5f7fa; padding:6px;")
        else:
            self.delta_label.setStyleSheet("font-size:16px; font-weight:700; color:#1b2430; padding:6px;")
        for b in [self.btn_points, self.btn_base, self.btn_pdf, self.btn_emitente, self.btn_layers, self.btn_export]:
            self._style_button(b, "light")
        for b in [self.btn_calc, self.btn_report]:
            self._style_button(b, "dark")
        self._style_button(self.btn_clear, "warn")

    def _build_ui(self):
        root = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        self.btn_points = QPushButton("Importar TXT Pontos")
        self.btn_base = QPushButton("Importar Base")
        self.btn_pdf = QPushButton("Importar PPP/Memorial")
        self.btn_emitente = QPushButton("Cadastro Empresa")
        self.btn_calc = QPushButton("Calcular Translado")
        self.btn_layers = QPushButton("Criar Camadas")
        self.btn_export = QPushButton("Exportar TXT Ajustado")
        self.btn_report = QPushButton("Gerar PDF")
        self.btn_clear = QPushButton("Limpar Dados")

        self._set_btn_icon(self.btn_points, "import_down.svg")
        self._set_btn_icon(self.btn_base, "base_receiver.svg")
        self._set_btn_icon(self.btn_pdf, "pdf_import.svg")
        self._set_btn_icon(self.btn_emitente, "base_receiver.svg")
        self._set_btn_icon(self.btn_calc, "calculate.svg")
        self._set_btn_icon(self.btn_layers, "layers.svg")
        self._set_btn_icon(self.btn_export, "export_up.svg")
        self._set_btn_icon(self.btn_report, "pdf_file.svg")
        self._set_btn_icon(self.btn_clear, "alert.svg")

        for b in [self.btn_points, self.btn_base, self.btn_pdf, self.btn_emitente, self.btn_calc, self.btn_layers, self.btn_export, self.btn_report, self.btn_clear]:
            toolbar.addWidget(b)
        toolbar.addStretch()
        root.addLayout(toolbar)

        self.btn_points.clicked.connect(self.import_points)
        self.btn_base.clicked.connect(self.import_base)
        self.btn_pdf.clicked.connect(self.import_pdf)
        self.btn_emitente.clicked.connect(self.edit_emitente)
        self.btn_calc.clicked.connect(self.calculate)
        self.btn_layers.clicked.connect(self.create_layers)
        self.btn_export.clicked.connect(self.export_txt)
        self.btn_report.clicked.connect(self.generate_report)
        self.btn_clear.clicked.connect(self.clear_data)

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        splitter.addWidget(left)

        grp_base = QGroupBox("Base levantada")
        base_form = QFormLayout(grp_base)
        self.base_name = QLineEdit()
        self.base_e = QLineEdit()
        self.base_n = QLineEdit()
        self.base_h = QLineEdit()
        base_form.addRow("Nome", self.base_name)
        base_form.addRow("Este", self.base_e)
        base_form.addRow("Norte", self.base_n)
        base_form.addRow("Altitude", self.base_h)
        left_layout.addWidget(grp_base)

        grp_ppp = QGroupBox("PPP / Base conhecida")
        ppp_grid = QGridLayout(grp_ppp)
        self.radio_pdf = QRadioButton("PDF")
        self.radio_manual = QRadioButton("Manual")
        self.radio_manual.setChecked(True)
        self.tipo_pdf_label = QLabel("Tipo de PDF")
        self.tipo_pdf = QComboBox()
        self.tipo_pdf.addItems(["PPP Ibge", "Memorial Sigef"])
        self.ppp_e = QLineEdit()
        self.ppp_n = QLineEdit()
        self.ppp_h = QLineEdit()

        self.radio_pdf.toggled.connect(self.update_mode_states)
        self.radio_manual.toggled.connect(self.update_mode_states)

        ppp_grid.addWidget(self.tipo_pdf_label, 0, 0)
        ppp_grid.addWidget(self.tipo_pdf, 0, 1)
        ppp_grid.addWidget(self.radio_pdf, 1, 0)
        ppp_grid.addWidget(self.radio_manual, 1, 1)
        ppp_grid.addWidget(QLabel("Este"), 2, 0)
        ppp_grid.addWidget(self.ppp_e, 2, 1)
        ppp_grid.addWidget(QLabel("Norte"), 3, 0)
        ppp_grid.addWidget(self.ppp_n, 3, 1)
        ppp_grid.addWidget(QLabel("Altitude"), 4, 0)
        ppp_grid.addWidget(self.ppp_h, 4, 1)
        left_layout.addWidget(grp_ppp)
        left_layout.addStretch()

        right = QWidget()
        right_layout = QVBoxLayout(right)
        splitter.addWidget(right)

        self.delta_label = QLabel("ΔN: - | ΔE: - | ΔH: -")
        right_layout.addWidget(self.delta_label)

        self.table = QTableWidget()
        right_layout.addWidget(self.table, 1)
        splitter.setSizes([320, 920])
        self.update_mode_states()

    def update_mode_states(self):
        pdf_mode = self.radio_pdf.isChecked()
        self.tipo_pdf_label.setVisible(pdf_mode)
        self.tipo_pdf.setVisible(pdf_mode)
        for widget in [self.ppp_e, self.ppp_n, self.ppp_h]:
            widget.setEnabled(not pdf_mode)

    def edit_emitente(self):
        dlg = EmitenteDialog(self.emitente, self)
        if dlg.exec_():
            self.emitente = dlg.result
            self.save_emitente()
            self.iface.messageBar().pushSuccess("MRF Translado GNSS", "Cadastro salvo.")

    def import_points(self):
        path, _ = QFileDialog.getOpenFileName(self, "Importar TXT de pontos", "", "TXT (*.txt);;CSV (*.csv)")
        if not path:
            return
        try:
            self.df, self.coord_line = load_points_txt(path)
            self.populate_table(self.df)
            self.iface.messageBar().pushSuccess("MRF Translado GNSS", f"{len(self.df)} pontos importados.")
        except Exception as e:
            QMessageBox.critical(self, "MRF Translado GNSS", str(e))

    def import_base(self):
        path, _ = QFileDialog.getOpenFileName(self, "Importar base TXT", "", "TXT (*.txt);;CSV (*.csv)")
        if not path:
            return
        try:
            b = parse_base_txt(path)
            self.base_data = b
            self.base_name.setText(b.name)
            self.base_e.setText(format_pt(b.east))
            self.base_n.setText(format_pt(b.north))
            self.base_h.setText(format_pt(b.h))
            self.iface.messageBar().pushSuccess("MRF Translado GNSS", "Base importada.")
        except Exception as e:
            QMessageBox.critical(self, "MRF Translado GNSS", str(e))

    def import_pdf(self):
        path, _ = QFileDialog.getOpenFileName(self, "Importar PDF", "", "PDF (*.pdf)")
        if not path:
            return
        try:
            if self.tipo_pdf.currentText() == "PPP Ibge":
                p = parse_ppp_pdf(path)
            else:
                vertices = parse_memorial_sigef_pdf(path)
                dlg = VertexSelectorDialog(vertices, self)
                if not dlg.exec_():
                    return
                p = memorial_vertex_to_pppdata(dlg.selected_vertex, source_pdf=path)
            self.ppp_data = p
            self.ppp_e.setText(format_pt(p.east))
            self.ppp_n.setText(format_pt(p.north))
            self.ppp_h.setText(format_pt(p.h))
            self.radio_pdf.setChecked(True)
            self.iface.messageBar().pushSuccess("MRF Translado GNSS", "PDF importado.")
        except Exception as e:
            QMessageBox.critical(self, "MRF Translado GNSS", str(e))

    def _collect_base(self) -> PointData:
        return PointData(
            self.base_name.text().strip() or "BASE",
            float(self.base_e.text().replace(",", ".")),
            float(self.base_n.text().replace(",", ".")),
            float(self.base_h.text().replace(",", ".")),
        )

    def _collect_ppp(self) -> PPPData:
        return PPPData(
            float(self.ppp_e.text().replace(",", ".")),
            float(self.ppp_n.text().replace(",", ".")),
            float(self.ppp_h.text().replace(",", ".")),
            None, None, None,
            self.ppp_data.source_pdf if self.ppp_data else None,
            self.ppp_data.source_kind if self.ppp_data else None,
            self.ppp_data.source_code if self.ppp_data else None,
        )

    def calculate(self):
        if self.df is None:
            QMessageBox.warning(self, "MRF Translado GNSS", "Importe primeiro o TXT dos pontos.")
            return
        try:
            self.base_data = self._collect_base()
            self.ppp_data = self._collect_ppp()
            self.result_df, (dn, de, dh) = apply_translation(self.df, self.base_data, self.ppp_data)
            self.delta_label.setText(f"ΔN: {format_pt(dn)} | ΔE: {format_pt(de)} | ΔH: {format_pt(dh)}")
            self.populate_table(self.result_df)
            self.iface.messageBar().pushSuccess("MRF Translado GNSS", "Translado calculado.")
        except Exception as e:
            QMessageBox.critical(self, "MRF Translado GNSS", str(e))

    def populate_table(self, df):
        cols = list(df.columns)
        self.table.setColumnCount(len(cols))
        self.table.setRowCount(len(df))
        self.table.setHorizontalHeaderLabels(cols)
        for r in range(len(df)):
            for c, col in enumerate(cols):
                valor = df.iloc[r][col]
                if isinstance(valor, float):
                    if col in ['DP E', 'DP N', 'DP U']:
                        valor = f"{valor:.4f}".replace(".", ",")
                    else:
                        valor = f"{valor:.3f}".replace(".", ",")
                self.table.setItem(r, c, QTableWidgetItem(str(valor)))
        self.table.resizeColumnsToContents()

    def infer_epsg(self):
        text = (self.coord_line or "").lower()
        if "zone 19s" in text or "19s" in text:
            return "EPSG:31979"
        if "zone 20s" in text or "20s" in text:
            return "EPSG:31980"
        if "zone 18s" in text or "18s" in text:
            return "EPSG:31978"
        return "EPSG:31979"

    def create_layers(self):
        from qgis.PyQt.QtCore import QVariant
        from qgis.core import QgsFeature, QgsField, QgsGeometry, QgsProject, QgsVectorLayer, QgsPointXY
        if self.df is None or self.result_df is None:
            QMessageBox.warning(self, "MRF Translado GNSS", "Calcule o translado antes de criar camadas.")
            return
        crs = self.infer_epsg()
        original = QgsVectorLayer(f"Point?crs={crs}", "Pontos Originais", "memory")
        adjusted = QgsVectorLayer(f"Point?crs={crs}", "Pontos Ajustados", "memory")
        vectors = QgsVectorLayer(f"LineString?crs={crs}", "Vetores de Deslocamento", "memory")
        for layer in [original, adjusted, vectors]:
            pr = layer.dataProvider()
            pr.addAttributes([QgsField("Nome", QVariant.String), QgsField("Status", QVariant.String)])
            layer.updateFields()
        feats = []
        for _, row in self.df.iterrows():
            ft = QgsFeature(original.fields())
            ft["Nome"] = str(row["Nome"])
            ft["Status"] = str(row["Status"])
            ft.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(float(row["Este"]), float(row["Norte"]))))
            feats.append(ft)
        original.dataProvider().addFeatures(feats)
        feats = []
        for _, row in self.result_df.iterrows():
            ft = QgsFeature(adjusted.fields())
            ft["Nome"] = str(row["Nome"])
            ft["Status"] = str(row["Status"])
            ft.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(float(row["Este Ajustado"]), float(row["Norte Ajustado"]))))
            feats.append(ft)
        adjusted.dataProvider().addFeatures(feats)
        feats = []
        for _, row in self.result_df.iterrows():
            ft = QgsFeature(vectors.fields())
            ft["Nome"] = str(row["Nome"])
            ft["Status"] = str(row["Status"])
            line = [QgsPointXY(float(row["Este"]), float(row["Norte"])), QgsPointXY(float(row["Este Ajustado"]), float(row["Norte Ajustado"]))]
            ft.setGeometry(QgsGeometry.fromPolylineXY(line))
            feats.append(ft)
        vectors.dataProvider().addFeatures(feats)
        QgsProject.instance().addMapLayer(original)
        QgsProject.instance().addMapLayer(adjusted)
        QgsProject.instance().addMapLayer(vectors)
        self.iface.messageBar().pushSuccess("MRF Translado GNSS", "Camadas criadas no projeto.")

    def export_txt(self):
        if self.result_df is None:
            QMessageBox.warning(self, "MRF Translado GNSS", "Calcule o translado antes de exportar.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Exportar TXT ajustado", "pontos_ajustados.txt", "TXT (*.txt)")
        if not path:
            return
        try:
            export_adjusted_txt(path, self.result_df, self.coord_line)
            self.iface.messageBar().pushSuccess("MRF Translado GNSS", "TXT ajustado exportado.")
        except Exception as e:
            QMessageBox.critical(self, "MRF Translado GNSS", str(e))

    def generate_report(self):
        if self.result_df is None or self.base_data is None or self.ppp_data is None:
            QMessageBox.warning(self, "MRF Translado GNSS", "Calcule o translado antes de gerar o PDF.")
            return

        if not self.emitente.empresa:
            QMessageBox.warning(self, "MRF Translado GNSS", "Preencha o Cadastro Empresa antes de gerar o relatório.")
            return

        if not self.emitente.data_relatorio:
            self.emitente.data_relatorio = datetime.now().strftime("%d/%m/%Y")
            self.save_emitente()

        path, _ = QFileDialog.getSaveFileName(self, "Gerar relatório PDF", "relatorio_translado.pdf", "PDF (*.pdf)")
        if not path:
            return
        try:
            _, deltas = apply_translation(self.df, self.base_data, self.ppp_data)
            create_pdf(path, self.emitente, self.base_data, self.ppp_data, self.result_df, deltas, self.emitente.logo or None)
            self.iface.messageBar().pushSuccess("MRF Translado GNSS", "PDF gerado com sucesso.")
        except Exception as e:
            QMessageBox.critical(self, "MRF Translado GNSS", str(e))

    def clear_data(self):
        if QMessageBox.question(self, "MRF Translado GNSS", "Tem certeza que deseja limpar todos os dados?") != QMessageBox.Yes:
            return
        self.df = None
        self.result_df = None
        self.base_data = None
        self.ppp_data = None
        for w in [self.base_name, self.base_e, self.base_n, self.base_h, self.ppp_e, self.ppp_n, self.ppp_h]:
            w.clear()
        self.radio_manual.setChecked(True)
        self.update_mode_states()
        self.delta_label.setText("ΔN: - | ΔE: - | ΔH: -")
        self.table.clear()
        self.table.setRowCount(0)
        self.table.setColumnCount(0)
        self.iface.messageBar().pushSuccess("MRF Translado GNSS", "Dados limpos.")
