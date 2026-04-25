from __future__ import annotations

import json
import os
from datetime import datetime
from urllib.request import urlopen
from pathlib import Path
from typing import Optional

from qgis.PyQt.QtCore import QTimer, Qt, QVariant
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .core.report_core import create_pdf
from .core.translado_core import (
    Emitente,
    PPPData,
    PointData,
    apply_translation,
    export_adjusted_txt,
    format_pt,
    load_points_txt,
    memorial_vertex_to_pppdata,
    parse_base_txt,
    parse_memorial_sigef_pdf,
    parse_ppp_pdf,
)

CONFIG_FILE = Path.home() / ".mrf_translado_qgis_emitente.json"
CURRENT_VERSION = "1.2.0"
VERSION_URL = "https://raw.githubusercontent.com/Socramac/mrf-translado-gnss/main/version.txt"


class EmitenteDialog(QDialog):
    def __init__(self, emitente: Emitente, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cadastro Empresa/Profissional")
        self.resize(620, 440)
        self.result = None
        self.vars = {k: QLineEdit(getattr(emitente, k, "")) for k in emitente.__dataclass_fields__.keys()}

        root = QVBoxLayout(self)
        form = QFormLayout()
        fields = [
            ("Logo", "logo"), ("Empresa", "empresa"), ("CNPJ", "cnpj"), ("Endereço", "endereco"),
            ("E-mail", "email"), ("Telefone", "telefone"), ("Projeto", "projeto"),
            ("Resp. Técnico", "responsavel_tecnico"), ("Cód. Credenciado", "codigo_credenciado"),
            ("Equip. Base", "equipamento_base"), ("Equip. Rover", "equipamento_rover"), ("Data", "data_relatorio"),
        ]
        for label, key in fields:
            if key == "logo":
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
            self.vars["logo"].setText(path)

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
        for vertex in vertices:
            self.list_widget.addItem(vertex["codigo"])
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
        for vertex in self.vertices:
            if text in vertex["codigo"].upper():
                self.list_widget.addItem(vertex["codigo"])

    def accept_selection(self):
        item = self.list_widget.currentItem()
        if not item:
            QMessageBox.warning(self, "MRF Translado GNSS", "Selecione um vértice.")
            return

        codigo = item.text()
        for vertex in self.vertices:
            if vertex["codigo"] == codigo:
                self.selected_vertex = vertex
                break
        self.accept()


class MRFTransladoDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle("MRF Translado GNSS")
        self.resize(1360, 820)

        self.df = None
        self.result_df = None
        self.coord_line = "Sistema de Coordenadas: SIRGAS 2000 / UTM zone 19S"
        self.base_data: Optional[PointData] = None
        self.ppp_data: Optional[PPPData] = None
        self.emitente = self.load_emitente()

        self._build_ui()
        self.apply_theme_styles()
        QTimer.singleShot(1200, self.check_for_updates)

    def load_emitente(self) -> Emitente:
        if CONFIG_FILE.exists():
            try:
                return Emitente(**json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
            except Exception:
                pass
        return Emitente()

    def save_emitente(self):
        CONFIG_FILE.write_text(
            json.dumps(self.emitente.__dict__, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _is_dark_theme(self) -> bool:
        return self.palette().window().color().lightness() < 128

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
        icon_path = os.path.join(os.path.dirname(__file__), "icons", icon_name)
        btn.setIcon(QIcon(icon_path))

    def apply_theme_styles(self):
        if self._is_dark_theme():
            self.delta_label.setStyleSheet("font-size:16px; font-weight:700; color:#f5f7fa; padding:6px;")
        else:
            self.delta_label.setStyleSheet("font-size:16px; font-weight:700; color:#1b2430; padding:6px;")

        for button in [self.btn_points, self.btn_base, self.btn_pdf, self.btn_emitente, self.btn_model, self.btn_layers, self.btn_export]:
            self._style_button(button, "light")
        for button in [self.btn_calc, self.btn_report]:
            self._style_button(button, "dark")
        self._style_button(self.btn_clear, "warn")

    def _variance_help_text(self) -> str:
        return (
            "A propagação de variância recalcula os sigmas das coordenadas ajustadas "
            "considerando os sigmas originais dos pontos e a incerteza da base PPP. "
            "Use essa opção quando quiser que o resultado final reflita também a "
            "precisão da base de referência."
        )

    def _show_variance_help(self):
        QMessageBox.information(self, "Propagação de Variância", self._variance_help_text())

    def _build_ui(self):
        root = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        self.btn_points = QPushButton("Importar TXT Pontos")
        self.btn_base = QPushButton("Importar Base")
        self.btn_pdf = QPushButton("Importar PPP/Memorial")
        self.btn_emitente = QPushButton("Cadastro Empresa")
        self.btn_model = QPushButton("Baixar TXT Modelo")
        self.btn_calc = QPushButton("Calcular Translado")
        self.btn_layers = QPushButton("Criar Camadas")
        self.btn_export = QPushButton("Exportar TXT Ajustado")
        self.btn_report = QPushButton("Gerar PDF")
        self.btn_clear = QPushButton("Limpar Dados")

        self._set_btn_icon(self.btn_points, "import_down.svg")
        self._set_btn_icon(self.btn_base, "base_receiver.svg")
        self._set_btn_icon(self.btn_pdf, "pdf_import.svg")
        self._set_btn_icon(self.btn_emitente, "base_receiver.svg")
        self._set_btn_icon(self.btn_model, "export_up.svg")
        self._set_btn_icon(self.btn_calc, "calculate.svg")
        self._set_btn_icon(self.btn_layers, "layers.svg")
        self._set_btn_icon(self.btn_export, "export_up.svg")
        self._set_btn_icon(self.btn_report, "pdf_file.svg")
        self._set_btn_icon(self.btn_clear, "alert.svg")

        for button in [self.btn_points, self.btn_base, self.btn_pdf, self.btn_emitente, self.btn_model, self.btn_calc, self.btn_layers, self.btn_export, self.btn_report, self.btn_clear]:
            toolbar.addWidget(button)

        self.chk_delete_rows = QCheckBox("Permitir excluir linhas")
        self.chk_delete_rows.setChecked(False)
        self.chk_delete_rows.toggled.connect(self.toggle_row_delete_mode)
        toolbar.addWidget(self.chk_delete_rows)

        variance_wrap = QVBoxLayout()
        variance_top = QHBoxLayout()
        self.chk_variance = QCheckBox("Utilizar Propagação de Variância")
        self.chk_variance.setChecked(True)
        self.chk_variance.toggled.connect(self.update_mode_states)
        variance_top.addWidget(self.chk_variance)

        self.btn_variance_help = QToolButton()
        self.btn_variance_help.setText("i")
        self.btn_variance_help.setToolTip(self._variance_help_text())
        self.btn_variance_help.setAutoRaise(False)
        self.btn_variance_help.setFixedSize(22, 22)
        self.btn_variance_help.clicked.connect(self._show_variance_help)
        self.btn_variance_help.setStyleSheet("QToolButton { border:1px solid #7f8c8d; border-radius:11px; font-weight:bold; }")
        variance_top.addWidget(self.btn_variance_help)
        variance_top.addStretch()

        variance_wrap.addLayout(variance_top)
        toolbar.addStretch()
        toolbar.addLayout(variance_wrap)
        root.addLayout(toolbar)

        self.btn_points.clicked.connect(self.import_points)
        self.btn_base.clicked.connect(self.import_base)
        self.btn_pdf.clicked.connect(self.import_pdf)
        self.btn_emitente.clicked.connect(self.edit_emitente)
        self.btn_model.clicked.connect(self.export_template_txt)
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
        self.tipo_pdf.currentIndexChanged.connect(self.update_mode_states)

        self.ppp_e = QLineEdit()
        self.ppp_n = QLineEdit()
        self.ppp_h = QLineEdit()
        self.ppp_sigma_e_label = QLabel("Sigma E")
        self.ppp_sigma_e = QLineEdit()
        self.ppp_sigma_n_label = QLabel("Sigma N")
        self.ppp_sigma_n = QLineEdit()
        self.ppp_sigma_h_label = QLabel("Sigma H")
        self.ppp_sigma_h = QLineEdit()

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
        ppp_grid.addWidget(self.ppp_sigma_e_label, 5, 0)
        ppp_grid.addWidget(self.ppp_sigma_e, 5, 1)
        ppp_grid.addWidget(self.ppp_sigma_n_label, 6, 0)
        ppp_grid.addWidget(self.ppp_sigma_n, 6, 1)
        ppp_grid.addWidget(self.ppp_sigma_h_label, 7, 0)
        ppp_grid.addWidget(self.ppp_sigma_h, 7, 1)

        self.ppp_sigma_widgets = [
            self.ppp_sigma_e_label, self.ppp_sigma_e,
            self.ppp_sigma_n_label, self.ppp_sigma_n,
            self.ppp_sigma_h_label, self.ppp_sigma_h,
        ]
        left_layout.addWidget(grp_ppp)
        left_layout.addStretch()

        right = QWidget()
        right_layout = QVBoxLayout(right)
        splitter.addWidget(right)

        self.delta_label = QLabel("ΔN: - | ΔE: - | ΔH: -")
        right_layout.addWidget(self.delta_label)

        self.table = QTableWidget()
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.handle_table_right_click)
        right_layout.addWidget(self.table, 1)
        splitter.setSizes([360, 980])

        self.update_mode_states()

    def _version_tuple(self, version_text: str):
        parts = []
        for item in version_text.strip().split("."):
            try:
                parts.append(int(item))
            except ValueError:
                parts.append(0)
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts[:3])

    def check_for_updates(self):
        try:
            latest = urlopen(VERSION_URL, timeout=4)  # nosec.read().decode("utf-8").strip()
            if not latest:
                return

            if self._version_tuple(latest) > self._version_tuple(CURRENT_VERSION):
                QMessageBox.information(
                    self,
                    "MRF Translado GNSS",
                    "Nova versão disponível do MRF Translado GNSS.\n\n"
                    f"Versão instalada: {CURRENT_VERSION}\n"
                    f"Última versão: {latest}\n\n"
                    "Abra o Gerenciador de Complementos do QGIS para atualizar.",
                )
        except Exception:
            return

    def toggle_row_delete_mode(self, enabled: bool):
        if enabled:
            QMessageBox.information(
                self,
                "MRF Translado GNSS",
                "Modo de exclusão de linhas ativado.\n\n"
                "Clique com o botão direito sobre uma linha da tabela para excluí-la.\n"
                "Uma confirmação será solicitada antes da exclusão.",
            )

    def handle_table_right_click(self, position):
        if not self.chk_delete_rows.isChecked():
            return

        row = self.table.rowAt(position.y())
        if row < 0:
            return

        point_name = ""
        item = self.table.item(row, 0)
        if item is not None:
            point_name = item.text()

        question = "Deseja realmente excluir esta linha?"
        if point_name:
            question += f"\n\nPonto: {point_name}"

        answer = QMessageBox.question(
            self,
            "MRF Translado GNSS",
            question,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if answer != QMessageBox.Yes:
            return

        self.delete_imported_row(row)

    def delete_imported_row(self, row: int):
        if self.df is not None and 0 <= row < len(self.df):
            self.df = self.df.drop(self.df.index[row]).reset_index(drop=True)

        if self.result_df is not None and 0 <= row < len(self.result_df):
            self.result_df = self.result_df.drop(self.result_df.index[row]).reset_index(drop=True)

        current_df = self.result_df if self.result_df is not None else self.df

        if current_df is None or current_df.empty:
            self.result_df = None
            self.populate_table(None)
            self.delta_label.setText("ΔN: - | ΔE: - | ΔH: -")
            self.iface.messageBar().pushSuccess("MRF Translado GNSS", "Linha excluída. Nenhum ponto restante.")
            return

        self.populate_table(current_df)
        self.iface.messageBar().pushSuccess("MRF Translado GNSS", "Linha excluída com sucesso.")

    def update_mode_states(self):
        pdf_mode = self.radio_pdf.isChecked()
        self.tipo_pdf_label.setVisible(pdf_mode)
        self.tipo_pdf.setVisible(pdf_mode)

        manual_ppp = self.radio_manual.isChecked()
        is_memorial = pdf_mode and self.tipo_pdf.currentText() == "Memorial Sigef"

        if is_memorial:
            self.chk_variance.setChecked(False)
            self.chk_variance.setEnabled(False)
            self.btn_variance_help.setEnabled(False)
            self.chk_variance.setToolTip("No Memorial SIGEF não há sigmas disponíveis para propagação.")
        else:
            self.chk_variance.setEnabled(True)
            self.btn_variance_help.setEnabled(True)
            self.chk_variance.setToolTip("")

        for widget in [self.ppp_e, self.ppp_n, self.ppp_h]:
            widget.setEnabled(manual_ppp)

        show_sigma = self.chk_variance.isChecked() and not is_memorial
        for widget in self.ppp_sigma_widgets:
            widget.setVisible(show_sigma)

        for widget in [self.ppp_sigma_e, self.ppp_sigma_n, self.ppp_sigma_h]:
            widget.setEnabled(show_sigma and manual_ppp)

    def export_template_txt(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Salvar TXT modelo",
            "modelo_importacao_mrf.txt",
            "TXT (*.txt)",
        )
        if not path:
            return

        content = (
            "Sistema de Coordenadas: SIRGAS 2000 / UTM zone 19S\n"
            "Nome;Status;Este;Norte;Altitude Elipsoidal;DP E;DP N;DP U\n"
            "PONTO-01;FIXO;569204.950;8807268.244;253.826;0.0100;0.0100;0.0120\n"
            "PONTO-02;FIXO;568862.595;8807434.493;255.017;0.0100;0.0100;0.0130\n"
        )
        try:
            with open(path, "w", encoding="utf-8") as file_obj:
                file_obj.write(content)
            QMessageBox.information(
                self,
                "MRF Translado GNSS",
                "TXT modelo salvo com sucesso.\n\n"
                "Use esse mesmo padrão para os pontos rover.\n"
                "Para a base, utilize o mesmo layout com apenas 1 linha.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "MRF Translado GNSS", str(exc))

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
        except Exception as exc:
            QMessageBox.critical(self, "MRF Translado GNSS", str(exc))

    def import_base(self):
        path, _ = QFileDialog.getOpenFileName(self, "Importar base TXT", "", "TXT (*.txt);;CSV (*.csv)")
        if not path:
            return
        try:
            base = parse_base_txt(path)
            self.base_data = base
            self.base_name.setText(base.name)
            self.base_e.setText(format_pt(base.east))
            self.base_n.setText(format_pt(base.north))
            self.base_h.setText(format_pt(base.h))
            self.iface.messageBar().pushSuccess("MRF Translado GNSS", "Base importada.")
        except Exception as exc:
            QMessageBox.critical(self, "MRF Translado GNSS", str(exc))

    def import_pdf(self):
        path, _ = QFileDialog.getOpenFileName(self, "Importar PDF", "", "PDF (*.pdf)")
        if not path:
            return
        try:
            if self.tipo_pdf.currentText() == "PPP Ibge":
                ppp = parse_ppp_pdf(path)
            else:
                vertices = parse_memorial_sigef_pdf(path)
                dlg = VertexSelectorDialog(vertices, self)
                if not dlg.exec_():
                    return
                ppp = memorial_vertex_to_pppdata(dlg.selected_vertex, source_pdf=path)

            self.ppp_data = ppp
            self.ppp_e.setText(format_pt(ppp.east))
            self.ppp_n.setText(format_pt(ppp.north))
            self.ppp_h.setText(format_pt(ppp.h))
            self.ppp_sigma_e.setText(format_pt(ppp.sigma_e, 4) if ppp.sigma_e is not None else "")
            self.ppp_sigma_n.setText(format_pt(ppp.sigma_n, 4) if ppp.sigma_n is not None else "")
            self.ppp_sigma_h.setText(format_pt(ppp.sigma_h, 4) if ppp.sigma_h is not None else "")
            self.radio_pdf.setChecked(True)
            self.update_mode_states()
            self.iface.messageBar().pushSuccess("MRF Translado GNSS", "PDF importado.")
        except Exception as exc:
            QMessageBox.critical(self, "MRF Translado GNSS", str(exc))

    def _collect_base(self) -> PointData:
        return PointData(
            self.base_name.text().strip() or "BASE",
            float(self.base_e.text().replace(",", ".")),
            float(self.base_n.text().replace(",", ".")),
            float(self.base_h.text().replace(",", ".")),
        )

    def _collect_ppp(self) -> PPPData:
        use_variance = self.chk_variance.isChecked()
        sigma_e = float(self.ppp_sigma_e.text().replace(",", ".")) if use_variance and self.ppp_sigma_e.text().strip() else None
        sigma_n = float(self.ppp_sigma_n.text().replace(",", ".")) if use_variance and self.ppp_sigma_n.text().strip() else None
        sigma_h = float(self.ppp_sigma_h.text().replace(",", ".")) if use_variance and self.ppp_sigma_h.text().strip() else None

        return PPPData(
            float(self.ppp_e.text().replace(",", ".")),
            float(self.ppp_n.text().replace(",", ".")),
            float(self.ppp_h.text().replace(",", ".")),
            sigma_e,
            sigma_n,
            sigma_h,
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
            self.result_df, (dn, de, dh) = apply_translation(
                self.df,
                self.base_data,
                self.ppp_data,
                use_variance=self.chk_variance.isChecked(),
            )
            self.delta_label.setText(f"ΔN: {format_pt(dn)} | ΔE: {format_pt(de)} | ΔH: {format_pt(dh)}")
            self.populate_table(self.result_df)
            self.iface.messageBar().pushSuccess("MRF Translado GNSS", "Translado calculado.")
        except Exception as exc:
            QMessageBox.critical(self, "MRF Translado GNSS", str(exc))

    def populate_table(self, df):
        if df is None or not hasattr(df, "columns"):
            self.table.clear()
            self.table.setColumnCount(0)
            self.table.setRowCount(0)
            return

        cols = list(df.columns)
        self.table.setColumnCount(len(cols))
        self.table.setRowCount(len(df))
        self.table.setHorizontalHeaderLabels(cols)
        for row_index in range(len(df)):
            for col_index, col in enumerate(cols):
                value = df.iloc[row_index][col]
                if isinstance(value, float):
                    value = f"{value:.4f}".replace(".", ",") if "DP " in col else f"{value:.3f}".replace(".", ",")
                self.table.setItem(row_index, col_index, QTableWidgetItem(str(value)))
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
        from qgis.core import QgsFeature, QgsField, QgsGeometry, QgsPointXY, QgsProject, QgsVectorLayer

        if self.df is None or self.result_df is None:
            QMessageBox.warning(self, "MRF Translado GNSS", "Calcule o translado antes de criar camadas.")
            return

        crs = self.infer_epsg()
        original = QgsVectorLayer(f"Point?crs={crs}", "Pontos Originais", "memory")
        adjusted = QgsVectorLayer(f"Point?crs={crs}", "Pontos Ajustados", "memory")
        vectors = QgsVectorLayer(f"LineString?crs={crs}", "Vetores de Deslocamento", "memory")

        for layer in [original, adjusted, vectors]:
            provider = layer.dataProvider()
            provider.addAttributes([QgsField("Nome", QVariant.String), QgsField("Status", QVariant.String)])
            layer.updateFields()

        feats = []
        for _, row in self.df.iterrows():
            feature = QgsFeature(original.fields())
            feature["Nome"] = str(row["Nome"])
            feature["Status"] = str(row["Status"])
            feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(float(row["Este"]), float(row["Norte"]))))
            feats.append(feature)
        original.dataProvider().addFeatures(feats)

        feats = []
        for _, row in self.result_df.iterrows():
            feature = QgsFeature(adjusted.fields())
            feature["Nome"] = str(row["Nome"])
            feature["Status"] = str(row["Status"])
            feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(float(row["Este Ajustado"]), float(row["Norte Ajustado"]))))
            feats.append(feature)
        adjusted.dataProvider().addFeatures(feats)

        feats = []
        for _, row in self.result_df.iterrows():
            feature = QgsFeature(vectors.fields())
            feature["Nome"] = str(row["Nome"])
            feature["Status"] = str(row["Status"])
            line = [
                QgsPointXY(float(row["Este"]), float(row["Norte"])),
                QgsPointXY(float(row["Este Ajustado"]), float(row["Norte Ajustado"])),
            ]
            feature.setGeometry(QgsGeometry.fromPolylineXY(line))
            feats.append(feature)
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
        except Exception as exc:
            QMessageBox.critical(self, "MRF Translado GNSS", str(exc))

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
            _, deltas = apply_translation(
                self.df,
                self.base_data,
                self.ppp_data,
                use_variance=self.chk_variance.isChecked(),
            )
            create_pdf(
                path,
                self.emitente,
                self.base_data,
                self.ppp_data,
                self.result_df,
                deltas,
                self.emitente.logo or None,
                use_variance=self.chk_variance.isChecked(),
            )
            self.iface.messageBar().pushSuccess("MRF Translado GNSS", "PDF gerado com sucesso.")
        except Exception as exc:
            QMessageBox.critical(self, "MRF Translado GNSS", str(exc))

    def clear_data(self):
        if QMessageBox.question(self, "MRF Translado GNSS", "Tem certeza que deseja limpar todos os dados?") != QMessageBox.Yes:
            return

        self.df = None
        self.result_df = None
        self.base_data = None
        self.ppp_data = None

        for widget in [
            self.base_name, self.base_e, self.base_n, self.base_h,
            self.ppp_e, self.ppp_n, self.ppp_h,
            self.ppp_sigma_e, self.ppp_sigma_n, self.ppp_sigma_h,
        ]:
            widget.clear()

        self.radio_manual.setChecked(True)
        self.chk_delete_rows.setChecked(False)
        self.chk_variance.setChecked(True)
        self.delta_label.setText("ΔN: - | ΔE: - | ΔH: -")
        self.table.clear()
        self.table.setRowCount(0)
        self.table.setColumnCount(0)
        self.update_mode_states()
        self.iface.messageBar().pushSuccess("MRF Translado GNSS", "Dados limpos.")
