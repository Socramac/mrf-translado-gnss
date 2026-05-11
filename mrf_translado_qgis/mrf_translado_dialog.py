from __future__ import annotations

import json
import os
from configparser import ConfigParser
from datetime import datetime
from urllib.request import urlopen
from pathlib import Path
from typing import Optional

from qgis.PyQt.QtCore import QSize, QTimer, Qt, QVariant
from qgis.PyQt.QtGui import QColor, QPainter, QPen, QBrush, QFont
from qgis.core import QgsCoordinateReferenceSystem, QgsProject
from qgis.gui import QgsMapCanvas, QgsProjectionSelectionWidget
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QFrame,
    QSizePolicy,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .core.report_core import create_pdf
from .core.visualization_core import (
    apply_layers_to_canvas,
    build_visualization_layers,
)
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

PLUGIN_DIR = Path(__file__).resolve().parent
METADATA_FILE = PLUGIN_DIR / "metadata.txt"
CONFIG_FILE = Path.home() / ".mrf_translado_qgis_emitente.json"
VERSION_URL = "https://raw.githubusercontent.com/Socramac/mrf-translado-gnss/main/version.txt"
CRS_CONFIG_FILE = Path.home() / ".mrf_translado_qgis_crs.txt"


def get_plugin_version() -> str:
    try:
        parser = ConfigParser()
        parser.read(METADATA_FILE, encoding="utf-8")
        return parser.get("general", "version")
    except Exception:
        return "0.0.0"


CURRENT_VERSION = get_plugin_version()


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


class ScaleBarOverlay(QFrame):
    """Escala gráfica dinâmica desenhada com QPainter sobre o QgsMapCanvas."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.total_m = 1000.0
        self.setObjectName("mrfScaleOverlay")
        self.setMinimumSize(330, 58)
        self.setMaximumHeight(64)
        self.setAttribute(Qt.WA_TranslucentBackground, False)

    def set_total_distance(self, total_m: float):
        try:
            self.total_m = max(float(total_m), 1.0)
            self.update()
        except Exception:
            pass

    def _format_label(self, meters: float) -> str:
        if meters >= 1000:
            value = meters / 1000.0
            if abs(value - round(value)) < 1e-6:
                return f"{int(round(value))} km"
            return f"{value:.1f} km".replace(".", ",")
        return f"{int(round(meters))} m"

    def sizeHint(self):
        return QSize(330, 58)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = self.rect().adjusted(0, 0, -1, -1)
        bg = QColor(32, 38, 43, 242)
        border = QColor(79, 89, 99)
        white = QColor(245, 247, 250)
        black = QColor(0, 0, 0)

        painter.setPen(QPen(border, 1))
        painter.setBrush(QBrush(bg))
        painter.drawRoundedRect(rect, 8, 8)

        left = 22
        top_text = 11
        bar_top = 34
        bar_h = 8
        seg_w = 54
        segments = 4

        font = QFont()
        font.setPointSize(8)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(white)

        step = self.total_m / segments
        for idx in range(segments + 1):
            x = left + idx * seg_w
            label = "0" if idx == 0 else self._format_label(step * idx)
            painter.drawText(x - 4, top_text + 8, label)

        for idx in range(segments):
            x = left + idx * seg_w
            color = black if idx % 2 == 0 else white
            painter.setPen(QPen(border, 0.5))
            painter.setBrush(QBrush(color))
            painter.drawRect(x, bar_top, seg_w, bar_h)

        painter.setPen(QPen(white, 1.4))
        for idx in range(segments + 1):
            x = left + idx * seg_w
            painter.drawLine(x, bar_top - 5, x, bar_top + bar_h + 5)

        painter.end()


class MapCanvasContainer(QFrame):
    """Container para sobrepor controles, HUD e escala sobre o QgsMapCanvas."""

    def __init__(self, canvas: QgsMapCanvas, parent=None):
        super().__init__(parent)
        self.setObjectName("mrfMapCanvasHost")
        self.canvas = canvas
        self.nav_box = None
        self.scale_label = None
        self.hud_widgets = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.canvas)

    def set_overlays(self, nav_box: QFrame, scale_label: QLabel, hud_widgets: list[QLabel]):
        self.nav_box = nav_box
        self.scale_label = scale_label
        self.hud_widgets = hud_widgets

        for widget in [self.nav_box, self.scale_label, *self.hud_widgets]:
            widget.setParent(self)
            widget.raise_()
            widget.show()

        self._position_overlays()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_overlays()

    def _position_overlays(self):
        margin = 14
        width = self.width()
        height = self.height()
        if width <= 0 or height <= 0:
            return

        if self.nav_box is not None:
            nav_size = self.nav_box.sizeHint()
            nav_w = max(42, nav_size.width())
            nav_h = max(150, nav_size.height())
            self.nav_box.setGeometry(
                width - nav_w - margin,
                max(margin, int((height - nav_h) / 2)),
                nav_w,
                nav_h,
            )
            self.nav_box.raise_()

        if self.scale_label is not None:
            scale_w = max(330, self.scale_label.sizeHint().width())
            scale_h = max(58, self.scale_label.sizeHint().height())
            self.scale_label.setGeometry(
                margin + 10,
                height - scale_h - margin,
                scale_w,
                scale_h,
            )
            self.scale_label.raise_()

        if self.hud_widgets:
            gap = 8
            hud_h = 30
            widths = [max(145, widget.sizeHint().width() + 18) for widget in self.hud_widgets]
            total_w = sum(widths) + gap * (len(widths) - 1)
            x = max(margin, width - total_w - margin - 8)
            y = height - hud_h - margin
            for widget, widget_w in zip(self.hud_widgets, widths):
                widget.setGeometry(x, y, widget_w, hud_h)
                widget.raise_()
                x += widget_w + gap


class MRFTransladoDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle("MRF Translado GNSS")
        self.resize(1520, 860)

        self.df = None
        self.result_df = None
        self.coord_line = "Sistema de Coordenadas: não definido"
        self.selected_crs = QgsCoordinateReferenceSystem()
        self.base_data: Optional[PointData] = None
        self.ppp_data: Optional[PPPData] = None
        self.visual_layers = []
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
        self.delta_label.setStyleSheet("font-size:16px; font-weight:800; padding:6px;")

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

    def _mk_group(self, title: str) -> QGroupBox:
        group = QGroupBox(title)
        group.setObjectName("mrfBlock")
        return group

    def _mk_hud_box(self, title: str, value: str = "-") -> QLabel:
        label = QLabel(f"<b>{title}</b>  {value}")
        label.setObjectName("mrfHudBox")
        label.setMinimumHeight(28)
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet(
            "QLabel {"
            "border: 1px solid #4f5963;"
            "border-radius: 8px;"
            "padding: 4px 10px;"
            "background-color: #20262b;"
            "color: #f5f7fa;"
            "font-size: 11px;"
            "font-weight: 600;"
            "}"
        )
        return label

    def _delta_html(self, dn: str, de: str, dh: str) -> str:
        return (
            "<span style='color:#22c55e; font-weight:800;'>ΔN:</span> "
            f"<span style='color:#f5f7fa; font-weight:700;'>{dn}</span>"
            " <span style='color:#f5f7fa;'>|</span> "
            "<span style='color:#22c55e; font-weight:800;'>ΔE:</span> "
            f"<span style='color:#f5f7fa; font-weight:700;'>{de}</span>"
            " <span style='color:#f5f7fa;'>|</span> "
            "<span style='color:#22c55e; font-weight:800;'>ΔH:</span> "
            f"<span style='color:#f5f7fa; font-weight:700;'>{dh}</span>"
        )

    def _build_ui(self):
        self.setMinimumSize(1450, 820)
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # ------------------------------------------------------------------
        # Barra principal de comandos
        # ------------------------------------------------------------------
        command_box = self._mk_group("Comandos")
        command_layout = QHBoxLayout(command_box)
        command_layout.setContentsMargins(10, 12, 10, 10)
        command_layout.setSpacing(6)

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

        button_widths = {
            self.btn_points: 145,
            self.btn_base: 120,
            self.btn_pdf: 170,
            self.btn_emitente: 140,
            self.btn_model: 145,
            self.btn_calc: 145,
            self.btn_layers: 125,
            self.btn_export: 165,
            self.btn_report: 110,
            self.btn_clear: 125,
        }
        for button in [
            self.btn_points, self.btn_base, self.btn_pdf, self.btn_emitente,
            self.btn_model, self.btn_calc, self.btn_layers, self.btn_export,
            self.btn_report, self.btn_clear,
        ]:
            button.setMinimumHeight(34)
            button.setIconSize(QSize(16, 16))
            button.setMinimumWidth(button_widths.get(button, 110))
            command_layout.addWidget(button)
        root.addWidget(command_box)

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

        # ------------------------------------------------------------------
        # Corpo principal
        # ------------------------------------------------------------------
        main = QHBoxLayout()
        main.setSpacing(10)
        root.addLayout(main, 1)

        # Painel esquerdo com blocos técnicos
        left_panel = QFrame()
        left_panel.setObjectName("mrfPanel")
        left_panel.setMinimumWidth(350)
        left_panel.setMaximumWidth(430)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(10)
        main.addWidget(left_panel, 0)

        grp_crs = self._mk_group("Sistema de Coordenadas (CRS)")
        crs_layout = QVBoxLayout(grp_crs)
        crs_layout.setContentsMargins(10, 18, 10, 10)
        crs_layout.setSpacing(8)
        self.crs_selector = QgsProjectionSelectionWidget()
        self.crs_selector.setOptionVisible(QgsProjectionSelectionWidget.CrsNotSet, True)
        self.crs_selector.crsChanged.connect(self.on_crs_changed)
        self.crs_status = QLabel("Selecione um CRS projetado UTM em metros para habilitar o plugin.")
        self.crs_status.setWordWrap(True)
        crs_layout.addWidget(self.crs_selector)
        crs_layout.addWidget(self.crs_status)
        left_layout.addWidget(grp_crs)

        grp_base = self._mk_group("Base levantada")
        base_form = QFormLayout(grp_base)
        base_form.setContentsMargins(10, 20, 10, 10)
        base_form.setHorizontalSpacing(10)
        base_form.setVerticalSpacing(8)
        self.base_name = QLineEdit()
        self.base_e = QLineEdit()
        self.base_n = QLineEdit()
        self.base_h = QLineEdit()
        for w in [self.base_name, self.base_e, self.base_n, self.base_h]:
            w.setMinimumHeight(28)
        base_form.addRow("Nome", self.base_name)
        base_form.addRow("Este", self.base_e)
        base_form.addRow("Norte", self.base_n)
        base_form.addRow("Altitude", self.base_h)
        left_layout.addWidget(grp_base)

        grp_ppp = self._mk_group("PPP / Base conhecida")
        ppp_grid = QGridLayout(grp_ppp)
        ppp_grid.setContentsMargins(10, 20, 10, 10)
        ppp_grid.setHorizontalSpacing(10)
        ppp_grid.setVerticalSpacing(8)
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
        for w in [self.ppp_e, self.ppp_n, self.ppp_h, self.ppp_sigma_e, self.ppp_sigma_n, self.ppp_sigma_h]:
            w.setMinimumHeight(28)

        self.radio_pdf.toggled.connect(self.update_mode_states)
        self.radio_manual.toggled.connect(self.update_mode_states)

        radio_row = QHBoxLayout()
        radio_row.setSpacing(12)
        radio_row.addWidget(self.radio_pdf)
        radio_row.addWidget(self.radio_manual)
        radio_row.addStretch()
        radio_wrap = QWidget()
        radio_wrap.setLayout(radio_row)

        ppp_grid.addWidget(self.tipo_pdf_label, 0, 0)
        ppp_grid.addWidget(self.tipo_pdf, 0, 1)
        ppp_grid.addWidget(radio_wrap, 1, 0, 1, 2)
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
        left_layout.addStretch(1)

        self.delta_label = QLabel(self._delta_html("-", "-", "-"))
        self.delta_label.setObjectName("mrfDeltaLabel")
        self.delta_label.setAlignment(Qt.AlignCenter)
        delta_box = self._mk_group("Correções aplicadas")
        delta_layout = QVBoxLayout(delta_box)
        delta_layout.setContentsMargins(10, 18, 10, 10)
        delta_layout.addWidget(self.delta_label)
        left_layout.addWidget(delta_box)

        # Painel direito
        right_panel = QFrame()
        right_panel.setObjectName("mrfPanel")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(10, 10, 10, 10)
        right_layout.setSpacing(10)
        main.addWidget(right_panel, 1)

        # Bloco separado para opções de conferência/edição
        options_box = self._mk_group("Opções")
        options_layout = QGridLayout(options_box)
        options_layout.setContentsMargins(10, 18, 10, 10)
        options_layout.setHorizontalSpacing(14)
        options_layout.setVerticalSpacing(6)

        self.chk_delete_rows = QCheckBox("Permitir excluir linhas")
        self.chk_delete_rows.setChecked(False)
        self.chk_delete_rows.toggled.connect(self.toggle_row_delete_mode)

        self.chk_visualizacao = QCheckBox("Visualização")
        self.chk_visualizacao.setChecked(False)
        self.chk_visualizacao.toggled.connect(self.toggle_visualizacao)

        self.chk_variance = QCheckBox("Utilizar Propagação de Variância")
        self.chk_variance.setChecked(True)
        self.chk_variance.toggled.connect(self.update_mode_states)

        self.btn_variance_help = QToolButton()
        self.btn_variance_help.setText("i")
        self.btn_variance_help.setToolTip(self._variance_help_text())
        self.btn_variance_help.setAutoRaise(False)
        self.btn_variance_help.setFixedSize(22, 22)
        self.btn_variance_help.clicked.connect(self._show_variance_help)
        self.btn_variance_help.setObjectName("mrfInfoButton")

        options_layout.addWidget(self.chk_delete_rows, 0, 0)
        options_layout.addWidget(self.chk_visualizacao, 1, 0)
        options_layout.addWidget(self.chk_variance, 2, 0)
        options_layout.addWidget(self.btn_variance_help, 2, 1)
        options_layout.setColumnStretch(0, 1)
        right_layout.addWidget(options_box)

        # Bloco da tabela/mapa
        view_box = self._mk_group("Importação do TXT / Visualização do mapa")
        view_layout = QVBoxLayout(view_box)
        view_layout.setContentsMargins(10, 18, 10, 10)
        view_layout.setSpacing(8)

        visual_header = QGroupBox("Visualização")
        visual_header.setObjectName("mrfBlock")
        visual_header_layout = QHBoxLayout(visual_header)
        visual_header_layout.setContentsMargins(10, 18, 10, 10)
        visual_header_layout.setSpacing(10)

        self.satellite_group = QGroupBox("Satélite")
        self.satellite_group.setObjectName("mrfInnerBlock")
        self.satellite_group.setMaximumWidth(330)
        satellite_layout = QHBoxLayout(self.satellite_group)
        satellite_layout.setContentsMargins(10, 16, 10, 8)
        satellite_layout.setSpacing(12)
        self.radio_sat_esri = QRadioButton("Esri")
        self.radio_sat_google = QRadioButton("Google")
        self.radio_sat_bing = QRadioButton("Bing")
        self.radio_sat_esri.setChecked(True)
        for radio in [self.radio_sat_esri, self.radio_sat_google, self.radio_sat_bing]:
            radio.toggled.connect(self._on_satellite_changed)
            satellite_layout.addWidget(radio)
        satellite_layout.addStretch()
        self.satellite_group.setVisible(False)

        visual_header_layout.addWidget(self.satellite_group, 0, Qt.AlignLeft)
        visual_header_layout.addStretch(1)
        view_layout.addWidget(visual_header)

        legend = QHBoxLayout()
        legend.setSpacing(16)
        self.legend_base = QLabel("▲ Base PPP/Conhecida")
        self.legend_original = QLabel("● Pontos originais")
        self.legend_adjusted = QLabel("● Pontos ajustados")
        self.legend_vectors = QLabel("━ Vetores base → ajustados")
        self.legend_base.setObjectName("legendBase")
        self.legend_original.setObjectName("legendOriginal")
        self.legend_adjusted.setObjectName("legendAdjusted")
        self.legend_vectors.setObjectName("legendVectors")
        for lab in [self.legend_base, self.legend_original, self.legend_adjusted, self.legend_vectors]:
            lab.setVisible(False)
            legend.addWidget(lab)
        legend.addStretch()
        view_layout.addLayout(legend)

        self.view_stack = QStackedWidget()
        self.table = QTableWidget()
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.handle_table_right_click)

        self.map_page = QFrame()
        map_wrap = self.map_page
        map_wrap.setObjectName("mrfMapWrap")
        map_layout = QVBoxLayout(map_wrap)
        map_layout.setContentsMargins(8, 8, 8, 8)
        map_layout.setSpacing(6)

        self.map_canvas = QgsMapCanvas(self)
        self.map_canvas.setMinimumHeight(500)
        self.map_canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.map_host = MapCanvasContainer(self.map_canvas, self.map_page)
        map_layout.addWidget(self.map_host, 1)

        nav_box = QFrame(self.map_host)
        nav_box.setObjectName("mrfNavBox")
        nav_layout = QVBoxLayout(nav_box)
        nav_layout.setContentsMargins(5, 5, 5, 5)
        nav_layout.setSpacing(5)
        from qgis.PyQt.QtGui import QIcon

        self.btn_map_center = QPushButton()
        self.btn_map_zoom_in = QPushButton()
        self.btn_map_zoom_out = QPushButton()
        self.btn_map_full = QPushButton()

        map_buttons = [
            (self.btn_map_center, "house.svg", "Centralizar visualização"),
            (self.btn_map_zoom_in, "zoom-in.svg", "Aproximar"),
            (self.btn_map_zoom_out, "zoom-out.svg", "Afastar"),
            (self.btn_map_full, "maximize.svg", "Enquadrar todos os pontos"),
        ]

        for button, icon_name, tooltip in map_buttons:
            icon_path = os.path.join(os.path.dirname(__file__), "icons", icon_name)
            button.setIcon(QIcon(icon_path))
            button.setToolTip(tooltip)
            button.setObjectName("mrfMapButton")
            button.setFixedSize(38, 38)
            button.setIconSize(QSize(22, 22))
            nav_layout.addWidget(button)

        nav_box.setFixedWidth(50)
        nav_box.setFixedHeight(186)

        self.btn_map_zoom_in.clicked.connect(lambda: self.map_canvas.zoomScale(self.map_canvas.scale() / 1.5))
        self.btn_map_zoom_out.clicked.connect(lambda: self.map_canvas.zoomScale(self.map_canvas.scale() * 1.5))
        self.btn_map_full.clicked.connect(self.refresh_visualizacao)
        self.btn_map_center.clicked.connect(self.refresh_visualizacao)

        self.hud_e = self._mk_hud_box("E", "-")
        self.hud_n = self._mk_hud_box("N", "-")
        self.hud_scale = self._mk_hud_box("Escala", "-")
        self.scale_graphic = ScaleBarOverlay(self.map_host)

        self.map_host.set_overlays(
            nav_box,
            self.scale_graphic,
            [self.hud_e, self.hud_n, self.hud_scale],
        )

        self.view_stack.addWidget(self.table)
        self.view_stack.addWidget(map_wrap)
        view_layout.addWidget(self.view_stack, 1)
        right_layout.addWidget(view_box, 1)

        self.map_canvas.xyCoordinates.connect(self._update_map_coordinates)
        self.map_canvas.scaleChanged.connect(self._update_map_scale)

        self.load_initial_crs()
        self.update_mode_states()
        self._set_visual_labels_visible(False)

        self.setStyleSheet(self.styleSheet() + """
            QFrame#mrfPanel {
                border: 1px solid #7f858c;
                border-radius: 10px;
                background: rgba(245, 247, 250, 0.03);
            }
            QGroupBox#mrfBlock {
                border: 1px solid #8b9299;
                border-radius: 10px;
                margin-top: 12px;
                padding-top: 7px;
                font-weight: 600;
            }
            QGroupBox#mrfBlock::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
            QGroupBox#mrfInnerBlock {
                border: 1px solid #8b9299;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 7px;
                font-weight: 600;
            }
            QGroupBox#mrfInnerBlock::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
            QFrame#mrfSubPanel, QFrame#mrfMapWrap {
                border: 1px solid #8b9299;
                border-radius: 10px;
                background: rgba(255, 255, 255, 0.02);
            }
            QFrame#mrfNavBox {
                border: 1px solid #6f7780;
                border-radius: 8px;
                background: rgba(20, 24, 27, 0.82);
            }
            QPushButton#mrfMapButton {
                border: 1px solid #4f5963;
                border-radius: 8px;
                background-color: #20262b;
                color: #f5f7fa;
                padding: 4px;
            }
            QPushButton#mrfMapButton:hover {
                background: rgba(54, 62, 70, 0.95);
            }
            QLabel#mrfHudBox {
                border: 1px solid #4f5963;
                border-radius: 8px;
                padding: 4px 10px;
                background-color: #20262b;
                color: #f5f7fa;
                font-size: 11px;
                font-weight: 600;
            }
            QLabel#mrfScaleGraphic {
                border: 1px solid #4f5963;
                border-radius: 8px;
                padding: 6px 10px;
                background-color: #20262b;
                color: #f5f7fa;
                font-size: 12px;
                font-weight: 600;
            }
            QLabel#mrfDeltaLabel {
                color: #1fa463;
                font-size: 16px;
                font-weight: 800;
                padding: 6px;
            }
            QToolButton#mrfInfoButton {
                border: 1px solid #7f8c8d;
                border-radius: 11px;
                font-weight: bold;
            }
            QLabel#legendBase { color: #d62828; font-weight: 700; }
            QLabel#legendOriginal { color: #8e98a3; font-weight: 700; }
            QLabel#legendAdjusted { color: #2e7d32; font-weight: 700; }
            QLabel#legendVectors { color: #3b82c4; font-weight: 700; }
        """)

    def _set_visual_labels_visible(self, visible: bool):
        for widget in [
            self.legend_base, self.legend_original, self.legend_adjusted,
            self.legend_vectors,
        ]:
            widget.setVisible(visible)

    def _update_map_coordinates(self, point):
        try:
            self.hud_e.setText(f"<b>E</b>  {point.x():.3f}")
            self.hud_n.setText(f"<b>N</b>  {point.y():.3f}")
        except Exception:
            pass

    def _nice_distance(self, value: float) -> float:
        if value <= 0:
            return 100.0
        import math
        exponent = math.floor(math.log10(value))
        fraction = value / (10 ** exponent)
        if fraction < 1.5:
            nice = 1
        elif fraction < 3.5:
            nice = 2
        elif fraction < 7.5:
            nice = 5
        else:
            nice = 10
        return nice * (10 ** exponent)

    def _format_distance_label(self, meters: float) -> str:
        if meters >= 1000:
            km = meters / 1000.0
            return f"{km:g} km".replace(".", ",")
        return f"{int(round(meters))} m"

    def _scale_html(self, label_text: str) -> str:
        return (
            "<div style='color:#f5f7fa; font-weight:600; font-size:12px;'>"
            f"{label_text}"
            "</div>"
            "<div style='margin-top:3px;'>"
            "<span style='color:#ffffff;'>┃</span>"
            "<span style='background-color:#000000; color:#000000;'>━━━━━━</span>"
            "<span style='color:#ffffff;'>┃</span>"
            "<span style='background-color:#ffffff; color:#ffffff;'>━━━━━━</span>"
            "<span style='color:#ffffff;'>┃</span>"
            "<span style='background-color:#000000; color:#000000;'>━━━━━━</span>"
            "<span style='color:#ffffff;'>┃</span>"
            "<span style='background-color:#ffffff; color:#ffffff;'>━━━━━━</span>"
            "<span style='color:#ffffff;'>┃</span>"
            "</div>"
        )

    def _update_scale_graphic(self):
        try:
            extent = self.map_canvas.extent()
            target_total = self._nice_distance(extent.width() * 0.22)
            self.scale_graphic.set_total_distance(target_total)
            self.map_host._position_overlays()
        except Exception:
            pass

    def _update_map_scale(self, scale):
        try:
            self.hud_scale.setText(f"<b>Escala</b>  1:{int(scale):,}".replace(",", "."))
            self._update_scale_graphic()
        except Exception:
            self.hud_scale.setText("<b>Escala</b>  -")

    def _selected_satellite_provider(self) -> str:
        if self.radio_sat_google.isChecked():
            return "Google"
        if self.radio_sat_bing.isChecked():
            return "Bing"
        return "Esri"

    def _on_satellite_changed(self, checked: bool):
        if checked and self.chk_visualizacao.isChecked():
            self.refresh_visualizacao()

    def toggle_visualizacao(self, enabled: bool):
        self.satellite_group.setVisible(enabled)
        if not enabled:
            self.view_stack.setCurrentWidget(self.table)
            self._set_visual_labels_visible(False)
            return

        if self.result_df is None or self.df is None or self.ppp_data is None:
            QMessageBox.warning(
                self,
                "MRF Translado GNSS",
                "Calcule o translado antes de abrir a visualização.",
            )
            self.chk_visualizacao.setChecked(False)
            return

        self.view_stack.setCurrentWidget(self.map_page)
        self._set_visual_labels_visible(True)
        self.refresh_visualizacao()

    def refresh_visualizacao(self):
        if self.result_df is None or self.df is None or self.ppp_data is None:
            return

        if not self.is_valid_utm_crs(self.selected_crs):
            QMessageBox.warning(
                self,
                "MRF Translado GNSS",
                "Selecione um CRS projetado UTM em metros antes de visualizar.",
            )
            return

        try:
            self.visual_layers = build_visualization_layers(
                self.df,
                self.result_df,
                self.ppp_data,
                self.selected_crs,
                self._selected_satellite_provider(),
            )
            apply_layers_to_canvas(self.map_canvas, self.visual_layers, self.selected_crs)
            self._update_map_scale(self.map_canvas.scale())
            self.map_host._position_overlays()
        except Exception as exc:
            QMessageBox.critical(self, "MRF Translado GNSS", str(exc))

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
            latest = urlopen(VERSION_URL, timeout=4).read().decode("utf-8").strip()  # nosec
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
            self.delta_label.setText(self._delta_html("-", "-", "-"))
            self.iface.messageBar().pushSuccess("MRF Translado GNSS", "Linha excluída. Nenhum ponto restante.")
            return

        self.populate_table(current_df)
        self.iface.messageBar().pushSuccess("MRF Translado GNSS", "Linha excluída com sucesso.")

    def load_initial_crs(self):
        crs = QgsCoordinateReferenceSystem()

        if CRS_CONFIG_FILE.exists():
            try:
                authid = CRS_CONFIG_FILE.read_text(encoding="utf-8").strip()
                if authid:
                    crs = QgsCoordinateReferenceSystem(authid)
            except Exception:
                crs = QgsCoordinateReferenceSystem()

        if not crs.isValid():
            try:
                project_crs = QgsProject.instance().crs()
                if project_crs and project_crs.isValid():
                    crs = project_crs
            except Exception:
                crs = QgsCoordinateReferenceSystem()

        if crs.isValid():
            self.crs_selector.setCrs(crs)
            self.on_crs_changed(crs)
        else:
            self.update_action_states(False)

    def is_valid_utm_crs(self, crs: QgsCoordinateReferenceSystem) -> bool:
        if crs is None or not crs.isValid():
            return False
        if crs.isGeographic():
            return False

        description = (crs.description() or "").upper()
        authid = (crs.authid() or "").upper()
        proj4 = (crs.toProj4() or "").upper()

        return "UTM" in description or "+PROJ=UTM" in proj4 or "UTM" in authid

    def on_crs_changed(self, crs: QgsCoordinateReferenceSystem):
        self.selected_crs = crs

        if not crs or not crs.isValid():
            self.crs_status.setText("Selecione um CRS projetado UTM em metros para habilitar o plugin.")
            self.update_action_states(False)
            return

        if not self.is_valid_utm_crs(crs):
            self.crs_status.setText(
                "CRS inválido para translado. Selecione um sistema projetado UTM em metros."
            )
            self.update_action_states(False)
            return

        self.coord_line = f"Sistema de Coordenadas: {crs.description()} ({crs.authid()})"
        self.crs_status.setText(f"CRS selecionado: {crs.description()} ({crs.authid()})")
        try:
            CRS_CONFIG_FILE.write_text(crs.authid(), encoding="utf-8")
        except Exception:
            pass
        self.update_action_states(True)

    def update_action_states(self, enabled: bool):
        buttons = [
            self.btn_points,
            self.btn_base,
            self.btn_pdf,
            self.btn_model,
            self.btn_calc,
            self.btn_layers,
            self.btn_export,
            self.btn_report,
        ]
        for button in buttons:
            button.setEnabled(enabled)

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
            self.df, _ = load_points_txt(path)
            self.coord_line = f"Sistema de Coordenadas: {self.selected_crs.description()} ({self.selected_crs.authid()})"
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
        if not self.is_valid_utm_crs(self.selected_crs):
            QMessageBox.warning(
                self,
                "MRF Translado GNSS",
                "Selecione um CRS projetado UTM em metros antes de calcular.",
            )
            return

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
            self.delta_label.setText(self._delta_html(format_pt(dn), format_pt(de), format_pt(dh)))
            self.populate_table(self.result_df)
            if self.chk_visualizacao.isChecked():
                self.refresh_visualizacao()
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
        if self.selected_crs and self.selected_crs.isValid():
            return self.selected_crs.authid()
        return "EPSG:31979"

    def create_layers(self):
        from qgis.core import QgsFeature, QgsField, QgsGeometry, QgsPointXY, QgsProject, QgsVectorLayer

        if self.df is None or self.result_df is None:
            QMessageBox.warning(self, "MRF Translado GNSS", "Calcule o translado antes de criar camadas.")
            return

        if not self.is_valid_utm_crs(self.selected_crs):
            QMessageBox.warning(
                self,
                "MRF Translado GNSS",
                "Selecione um CRS projetado UTM em metros antes de criar camadas.",
            )
            return

        crs = self.selected_crs.authid()
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
        if not self.is_valid_utm_crs(self.selected_crs):
            QMessageBox.warning(
                self,
                "MRF Translado GNSS",
                "Selecione um CRS projetado UTM em metros antes de exportar.",
            )
            return

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
        if not self.is_valid_utm_crs(self.selected_crs):
            QMessageBox.warning(
                self,
                "MRF Translado GNSS",
                "Selecione um CRS projetado UTM em metros antes de gerar o relatório.",
            )
            return

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
        msg = QMessageBox(self)
        msg.setWindowTitle("MRF Translado GNSS")
        msg.setIcon(QMessageBox.Question)
        msg.setText("O que deseja limpar?")
        msg.setInformativeText(
            "Você pode limpar somente os pontos importados do TXT, mantendo a Base levantada "
            "e a PPP/Base conhecida, ou limpar todos os dados do plugin."
        )

        btn_points_only = msg.addButton("Somente pontos TXT", QMessageBox.AcceptRole)
        btn_all = msg.addButton("Limpar tudo", QMessageBox.DestructiveRole)
        btn_cancel = msg.addButton("Cancelar", QMessageBox.RejectRole)
        msg.setDefaultButton(btn_points_only)

        msg.exec_()
        clicked = msg.clickedButton()

        if clicked == btn_cancel:
            return

        clear_all = clicked == btn_all

        # Sempre limpa os pontos importados, o resultado calculado, a tabela e a visualização.
        self.df = None
        self.result_df = None
        self.visual_layers = []

        self.table.clear()
        self.table.setRowCount(0)
        self.table.setColumnCount(0)

        self.map_canvas.setLayers([])
        self.map_canvas.refresh()

        self.chk_delete_rows.setChecked(False)
        self.chk_visualizacao.setChecked(False)
        self.delta_label.setText(self._delta_html("-", "-", "-"))

        if clear_all:
            self.base_data = None
            self.ppp_data = None

            for widget in [
                self.base_name, self.base_e, self.base_n, self.base_h,
                self.ppp_e, self.ppp_n, self.ppp_h,
                self.ppp_sigma_e, self.ppp_sigma_n, self.ppp_sigma_h,
            ]:
                widget.clear()

            self.radio_manual.setChecked(True)
            self.chk_variance.setChecked(True)
            message = "Todos os dados foram limpos."
        else:
            # Mantém Base levantada e PPP/Base conhecida já preenchidas.
            message = "Pontos TXT e resultados calculados foram limpos. Bases mantidas."

        self.update_mode_states()
        self.iface.messageBar().pushSuccess("MRF Translado GNSS", message)
