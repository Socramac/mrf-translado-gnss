from __future__ import annotations

import json
import os
from configparser import ConfigParser
from datetime import datetime
from urllib.request import urlopen
from pathlib import Path
from typing import Optional

from qgis.PyQt.QtCore import QSize, QTimer, Qt, QVariant
from qgis.PyQt.QtGui import QColor, QPainter, QPen, QBrush, QFont, QIcon, QPixmap
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsMarkerSymbol,
    QgsPointXY,
    QgsProject,
    QgsSingleSymbolRenderer,
    QgsVectorLayer,
)
from qgis.gui import QgsMapCanvas, QgsProjectionSelectionWidget, QgsMapToolPan, QgsMapToolPan, QgsMapToolPan
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QFrame,
    QScrollArea,
    QGraphicsDropShadowEffect,
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
    QHeaderView,
    QAbstractItemView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .core.report_core import create_pdf
from .core.visualization_core import (
    apply_layers_to_canvas,
    build_visualization_layers,
    create_satellite_layer,
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
VERSION_URL = "https://raw.githubusercontent.com/Socramac/mrf-translado-qgis/main/version.txt"
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



class PlanimetricHistogramWidget(QFrame):
    """Histograma simples em QPainter para DP planimétrico."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.values = []
        self.limit_value = None
        self.mean_value = None
        self.setMinimumHeight(135)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setObjectName("mrfHistogramWidget")

    def set_values(self, values, limit_value=None):
        self.values = [float(v) for v in values if v is not None]
        self.limit_value = float(limit_value) if limit_value is not None else None
        self.mean_value = (
            sum(self.values) / len(self.values)
            if self.values else None
        )
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = self.rect().adjusted(12, 10, -12, -18)
        painter.fillRect(self.rect(), QColor("#111820"))

        axis_pen = QPen(QColor("#30363d"))
        axis_pen.setWidth(1)
        painter.setPen(axis_pen)
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())

        if not self.values:
            painter.setPen(QColor("#8b949e"))
            painter.drawText(rect, Qt.AlignCenter, "Sem dados planimétricos")
            painter.end()
            return

        vals = sorted(self.values)
        max_val = max(vals)
        min_val = min(vals)
        if max_val <= 0:
            max_val = 0.001
        if max_val == min_val:
            min_val = 0.0

        n = len(vals)
        bins = min(8, max(4, int(n ** 0.5) + 1))
        span = max_val - min_val
        if span <= 0:
            span = max_val or 0.001

        counts = [0] * bins
        for v in vals:
            idx = int((v - min_val) / span * bins)
            idx = min(max(idx, 0), bins - 1)
            counts[idx] += 1

        max_count = max(counts) or 1
        gap = 4
        bar_w = max(2, (rect.width() - gap * (bins - 1)) / bins)

        bar_color = QColor("#3b82f6")
        for i, count in enumerate(counts):
            h = (count / max_count) * max(1, rect.height() - 18)
            x = rect.left() + i * (bar_w + gap)
            y = rect.bottom() - h
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(bar_color))
            painter.drawRoundedRect(int(x), int(y), int(bar_w), int(h), 3, 3)

        # Linha média
        if self.mean_value is not None:
            x = rect.left() + ((self.mean_value - min_val) / span) * rect.width()
            x = max(rect.left(), min(rect.right(), x))
            pen = QPen(QColor("#60a5fa"))
            pen.setWidth(1)
            pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            painter.drawLine(int(x), rect.top(), int(x), rect.bottom())
            painter.setPen(QColor("#60a5fa"))
            painter.drawText(int(x) + 3, rect.top() + 11, "média")

        # Linha de limite
        if self.limit_value is not None and self.limit_value <= max_val:
            x = rect.left() + ((self.limit_value - min_val) / span) * rect.width()
            x = max(rect.left(), min(rect.right(), x))
            pen = QPen(QColor("#ef4444"))
            pen.setWidth(1)
            painter.setPen(pen)
            painter.drawLine(int(x), rect.top(), int(x), rect.bottom())
            painter.setPen(QColor("#ef4444"))
            painter.drawText(int(x) + 3, rect.bottom() - 3, "limite")

        painter.setPen(QColor("#8b949e"))
        painter.drawText(
            self.rect().adjusted(8, 0, -8, -2),
            Qt.AlignBottom | Qt.AlignLeft,
            f"n={len(vals)} | máx. {max_val:.4f} m | média {self.mean_value:.4f} m",
        )
        painter.end()



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

        self.dashboard_dialog = None
        self.filter_map_dialog = None
        self.base_source_type = "manual"
        self.sigef_denominacao = ""
        self.sigef_credenciado = ""
        self.sigef_vertice_base = ""

        self.filter_df = None
        self.filter_approved_df = None
        self.filter_original_columns = None

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
        icon_path = os.path.join(os.path.dirname(__file__), "icons", icon_name)
        btn.setIcon(QIcon(icon_path))

    def apply_theme_styles(self):
        self.delta_label.setStyleSheet("font-size:16px; font-weight:800; padding:6px;")

        for button in [
            self.btn_points,
            self.btn_base,
            self.btn_pdf,
            self.btn_emitente,
            self.btn_model,
            self.btn_layers,
            self.btn_export,
        ]:
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
        self.grp_crs = grp_crs
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
        self.grp_base = grp_base
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
        self.grp_ppp = grp_ppp
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
        self.btn_variance_help.setIcon(QIcon(str(PLUGIN_DIR / "icons" / "info.svg")))
        self.btn_variance_help.setToolTip(self._variance_help_text())
        self.btn_variance_help.setAutoRaise(False)
        self.btn_variance_help.setFixedSize(24, 24)
        self.btn_variance_help.clicked.connect(self._show_variance_help)
        self.btn_variance_help.setObjectName("mrfInfoButton")
        self.btn_variance_help.setStyleSheet(
            "QToolButton#mrfInfoButton {background-color:#2563eb;border:1px solid #60a5fa;"
            "border-radius:12px;padding:3px;}"
            "QToolButton#mrfInfoButton:hover {background-color:#1d4ed8;}"
        )

        self.chk_precision_filter = QCheckBox("Aplicar filtro de precisão posicional")
        self.chk_precision_filter.setChecked(False)
        self.chk_precision_filter.toggled.connect(self.update_precision_filter_mode)

        self.btn_precision_filter_help = QToolButton()
        self.btn_precision_filter_help.setIcon(QIcon(str(PLUGIN_DIR / "icons" / "info.svg")))
        self.btn_precision_filter_help.setToolTip("Explicação do filtro de precisão posicional")
        self.btn_precision_filter_help.setAutoRaise(False)
        self.btn_precision_filter_help.setFixedSize(24, 24)
        self.btn_precision_filter_help.clicked.connect(self._show_precision_filter_help)
        self.btn_precision_filter_help.setObjectName("mrfInfoButton")
        self.btn_precision_filter_help.setStyleSheet(self.btn_variance_help.styleSheet())

        self.filter_info_card = QFrame()
        self.filter_info_card.setObjectName("mrfFilterInfoCard")
        self.filter_info_card.setStyleSheet(
            "QFrame#mrfFilterInfoCard {background-color:#0f1722;border:1px solid #2f81f7;"
            "border-radius:10px;} QLabel {background:transparent;}"
        )
        self.filter_info_card.setMinimumHeight(105)
        card_layout = QHBoxLayout(self.filter_info_card)
        card_layout.setContentsMargins(12, 6, 12, 6)
        card_layout.setSpacing(14)
        left = QVBoxLayout(); left.setSpacing(3)
        title_row = QHBoxLayout(); title_row.setSpacing(8)
        icon = QLabel(); icon.setFixedSize(30, 30); icon.setAlignment(Qt.AlignCenter)
        icon.setPixmap(QIcon(str(PLUGIN_DIR / "icons" / "shield-check.svg")).pixmap(22, 22))
        icon.setStyleSheet("background-color:#2563eb;border-radius:7px;padding:4px;")
        title = QLabel("FILTRO DE PRECISÃO POSICIONAL")
        title.setStyleSheet("color:#f0f6fc;font-size:13px;font-weight:900;")
        title_row.addWidget(icon); title_row.addWidget(title); title_row.addStretch(1)
        left.addLayout(title_row)
        desc = QLabel(
            "Conforme o MTGIR 1.4.3, a precisão posicional absoluta "
            "é a resultante planimétrica dos desvios-padrão."
        )
        desc.setWordWrap(True); desc.setStyleSheet("color:#c9d1d9;font-size:11px;")
        left.addWidget(desc)
        formula = QLabel("σP = √(DP E² + DP N²)")
        formula.setAlignment(Qt.AlignCenter); formula.setFixedHeight(30)
        formula.setStyleSheet(
            "color:#fff;"
            "font-size:16px;"
            "font-weight:900;"
            "border:1px solid #2f81f7;"
            "border-radius:7px;"
            "background-color:#101826;"
            "padding:3px 14px;"
        )
        left.addWidget(formula, 0, Qt.AlignCenter)
        right = QVBoxLayout(); right.setSpacing(3)
        lim_title = QLabel("Limites de precisão posicional (item 1.4.4):")
        lim_title.setStyleSheet("color:#f0f6fc;font-size:12px;font-weight:900;")
        right.addWidget(lim_title)
        for color, val in [
            ("#22c55e", "Limite artificial: melhor ou igual a 0,50 m"),
            ("#facc15", "Limite natural: melhor ou igual a 3,00 m"),
            ("#ef4444", "Limite inacessível: melhor ou igual a 7,50 m"),
        ]:
            lab = QLabel(f"<span style='color:{color};font-size:22px;'>●</span> {val}")
            lab.setTextFormat(Qt.RichText); lab.setStyleSheet("color:#c9d1d9;font-size:11px;")
            right.addWidget(lab)
        tol = QLabel("A tolerância admitida será de no máximo três vezes o valor da precisão para o tipo de limite.")
        tol.setWordWrap(True); tol.setStyleSheet("color:#c9d1d9;font-size:11px;")
        right.addWidget(tol)
        card_layout.addLayout(left, 1); card_layout.addLayout(right, 1)

        self.filter_controls = QFrame()
        self.filter_controls.setObjectName("mrfFilterControls")
        self.filter_controls.setStyleSheet(
            "QFrame#mrfFilterControls {"
            "background-color:#111820;"
            "border:1px solid #30363d;"
            "border-radius:8px;"
            "}"
            "QGroupBox#mrfFilterSubBox {"
            "background-color:#111820;"
            "border:1px solid #30363d;"
            "border-radius:8px;"
            "margin-top:10px;"
            "font-weight:900;"
            "color:#f0f6fc;"
            "}"
            "QGroupBox#mrfFilterSubBox::title {"
            "subcontrol-origin:margin;"
            "left:10px;"
            "padding:0 5px;"
            "}"
        )
        controls = QHBoxLayout(self.filter_controls)
        controls.setContentsMargins(10, 7, 10, 7)
        controls.setSpacing(10)

        # Bloco: Tipo de limite
        limit_box = QGroupBox("Tipo de limite (conforme item 1.4.4 do MTGIR)")
        limit_box.setObjectName("mrfFilterSubBox")
        limit_box_layout = QHBoxLayout(limit_box)
        limit_box_layout.setContentsMargins(10, 17, 10, 8)
        limit_box_layout.setSpacing(8)

        self.filter_limit_type = "artificial"
        self.btn_limit_artificial = QPushButton("Limite artificial\n≤ 0,50 m")
        self.btn_limit_natural = QPushButton("Limite natural\n≤ 3,00 m")
        self.btn_limit_inaccessible = QPushButton("Limite inacessível\n≤ 7,50 m")

        self.btn_limit_artificial.setIcon(QIcon(str(PLUGIN_DIR / "icons" / "limit_artificial.png")))
        self.btn_limit_natural.setIcon(QIcon(str(PLUGIN_DIR / "icons" / "limit_natural.png")))
        self.btn_limit_inaccessible.setIcon(QIcon(str(PLUGIN_DIR / "icons" / "limit_inaccessible.png")))

        for button in [
            self.btn_limit_artificial,
            self.btn_limit_natural,
            self.btn_limit_inaccessible,
        ]:
            button.setCheckable(True)
            button.setMinimumHeight(56)
            button.setIconSize(QSize(30, 30))
            button.setStyleSheet(
                "QPushButton {"
                "background-color:#161b22;"
                "border:1px solid #30363d;"
                "border-radius:9px;"
                "color:#f0f6fc;"
                "font-weight:900;"
                "font-size:11px;"
                "padding:6px 9px;"
                "text-align:left;"
                "}"
                "QPushButton:hover {"
                "border:1px solid #58a6ff;"
                "background-color:#1f2937;"
                "}"
                "QPushButton:checked {"
                "background-color:#0f2416;"
                "border:2px solid #22c55e;"
                "color:#ffffff;"
                "}"
            )
            limit_box_layout.addWidget(button, 1)

        self.btn_limit_artificial.setChecked(True)
        self.btn_limit_artificial.clicked.connect(
            lambda: self._select_filter_limit("artificial")
        )
        self.btn_limit_natural.clicked.connect(
            lambda: self._select_filter_limit("natural")
        )
        self.btn_limit_inaccessible.clicked.connect(
            lambda: self._select_filter_limit("inaccessible")
        )

        # Bloco: Tolerância
        tolerance_box = QGroupBox("Tolerância admitida (até 3× o limite)")
        tolerance_box.setObjectName("mrfFilterSubBox")
        tolerance_layout = QVBoxLayout(tolerance_box)
        tolerance_layout.setContentsMargins(10, 17, 10, 8)
        tolerance_layout.setSpacing(6)

        self.filter_limit_value = QDoubleSpinBox()
        self.filter_limit_value.setDecimals(3)
        self.filter_limit_value.setMinimum(0.001)
        self.filter_limit_value.setMaximum(999.000)
        self.filter_limit_value.setValue(0.500)
        self.filter_limit_value.setSuffix(" m")
        self.filter_limit_value.setEnabled(False)

        self.filter_tolerance_label = QLabel("(3× o limite selecionado)")
        self.filter_tolerance_label.setStyleSheet(
            "color:#c9d1d9;"
            "font-size:11px;"
            "font-weight:700;"
        )

        tolerance_layout.addWidget(self.filter_limit_value)
        tolerance_layout.addWidget(self.filter_tolerance_label)

        # Bloco: Ações
        action_box = QFrame()
        action_box.setObjectName("mrfFilterActions")
        action_box.setStyleSheet(
            "QFrame#mrfFilterActions {background:transparent;border:none;}"
            "QPushButton#mrfApplyFilter {"
            "background-color:#1f6feb;"
            "color:#f0f6fc;"
            "border:1px solid #2f81f7;"
            "border-radius:8px;"
            "font-weight:900;"
            "padding:9px 18px;"
            "}"
            "QPushButton#mrfApplyFilter:hover {background-color:#2563eb;}"
            "QPushButton#mrfClearFilter {"
            "background-color:#161b22;"
            "color:#f0f6fc;"
            "border:1px solid #30363d;"
            "border-radius:8px;"
            "font-weight:900;"
            "padding:9px 18px;"
            "}"
            "QPushButton#mrfClearFilter:hover {border-color:#8b949e;}"
            "QPushButton#mrfExportFilter {"
            "background-color:#166534;"
            "color:#f0f6fc;"
            "border:1px solid #22c55e;"
            "border-radius:8px;"
            "font-weight:900;"
            "padding:9px 18px;"
            "}"
            "QPushButton#mrfExportFilter:hover {background-color:#15803d;}"
            "QPushButton:disabled {color:#8b949e;background-color:#1f2937;border-color:#30363d;}"
        )
        action_layout = QVBoxLayout(action_box)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(8)

        top_action_row = QHBoxLayout()
        top_action_row.setSpacing(8)

        self.btn_apply_precision_filter = QPushButton("Aplicar filtro")
        self.btn_apply_precision_filter.setIcon(QIcon(str(PLUGIN_DIR / "icons" / "funnel.svg")))
        self.btn_apply_precision_filter.setObjectName("mrfApplyFilter")
        self.btn_apply_precision_filter.setIconSize(QSize(22, 22))
        self.btn_apply_precision_filter.setMinimumHeight(40)
        self.btn_apply_precision_filter.clicked.connect(self.apply_precision_filter)

        self.btn_clear_precision_filter = QPushButton("Limpar filtro")
        self.btn_clear_precision_filter.setIcon(QIcon(str(PLUGIN_DIR / "icons" / "trash-2.svg")))
        self.btn_clear_precision_filter.setObjectName("mrfClearFilter")
        self.btn_clear_precision_filter.setIconSize(QSize(22, 22))
        self.btn_clear_precision_filter.setMinimumHeight(40)
        self.btn_clear_precision_filter.clicked.connect(self.clear_precision_filter)

        self.btn_export_precision_filter = QPushButton("Exportar TXT aprovado")
        self.btn_export_precision_filter.setIcon(QIcon(str(PLUGIN_DIR / "icons" / "download.svg")))
        self.btn_export_precision_filter.setObjectName("mrfExportFilter")
        self.btn_export_precision_filter.setIconSize(QSize(22, 22))
        self.btn_export_precision_filter.setMinimumHeight(42)
        self.btn_export_precision_filter.setEnabled(False)
        self.btn_export_precision_filter.clicked.connect(self.export_precision_filtered_txt)

        top_action_row.addWidget(self.btn_apply_precision_filter, 1)
        top_action_row.addWidget(self.btn_clear_precision_filter, 1)
        action_layout.addLayout(top_action_row)
        action_layout.addWidget(self.btn_export_precision_filter)

        self.radio_export_fixed = QRadioButton("Fixo")
        self.radio_export_fixed_float = QRadioButton("Fixo/Float")
        self.radio_export_all = QRadioButton("Todos aprovados")

        export_radio_layout = QHBoxLayout()
        export_radio_layout.setContentsMargins(2, 0, 2, 0)
        export_radio_layout.setSpacing(14)

        for radio in [
            self.radio_export_fixed,
            self.radio_export_fixed_float,
            self.radio_export_all,
        ]:
            radio.setStyleSheet(
                "color:#c9d1d9;"
                "font-size:11px;"
                "font-weight:700;"
            )
            radio.toggled.connect(self._update_export_button_state)
            export_radio_layout.addWidget(radio)

        export_radio_layout.addStretch(1)
        action_layout.addLayout(export_radio_layout)

        self.export_gnss_warning = QLabel("")
        self.export_gnss_warning.setStyleSheet(
            "color:#f59e0b;"
            "font-size:10px;"
            "font-weight:700;"
        )
        self.export_gnss_warning.setVisible(False)
        action_layout.addWidget(self.export_gnss_warning)


        controls.addWidget(limit_box, 2)
        controls.addWidget(tolerance_box, 1)
        controls.addWidget(action_box, 1)

        self.filter_info_card.setVisible(False)
        self.filter_controls.setVisible(False)

        options_layout.addWidget(self.chk_delete_rows, 0, 0)
        options_layout.addWidget(self.chk_visualizacao, 1, 0)
        options_layout.addWidget(self.chk_variance, 2, 0)
        options_layout.addWidget(self.btn_variance_help, 2, 1)
        options_layout.addWidget(self.chk_precision_filter, 3, 0)
        options_layout.addWidget(self.btn_precision_filter_help, 3, 1)
        options_layout.addWidget(self.filter_info_card, 0, 2, 4, 1)
        options_layout.addWidget(self.filter_controls, 4, 0, 1, 3)
        options_layout.setColumnStretch(0, 0)
        options_layout.setColumnStretch(1, 0)
        options_layout.setColumnStretch(2, 1)
        right_layout.addWidget(options_box)

        # Bloco da tabela/mapa
        view_box = self._mk_group("Importação do TXT / Visualização do mapa")
        view_layout = QVBoxLayout(view_box)
        view_layout.setContentsMargins(10, 18, 10, 10)
        view_layout.setSpacing(8)

        view_tabs = QHBoxLayout()
        view_tabs.setSpacing(6)
        self.btn_view_table = QPushButton("Tabela de pontos")
        self.btn_view_map = QPushButton("Visualização do mapa")
        self.btn_view_dashboard = QPushButton("Dashboard QC")
        for button in [
            self.btn_view_table,
            self.btn_view_map,
            self.btn_view_dashboard,
        ]:
            button.setMinimumHeight(30)
            button.setMinimumWidth(120)
            button.setObjectName("mrfTabButton")
            self._style_button(button, "light")
            view_tabs.addWidget(button)
        view_tabs.addStretch(1)
        view_layout.addLayout(view_tabs)

        self.btn_view_table.clicked.connect(self.show_table_view)
        self.btn_view_map.clicked.connect(self.show_map_view)
        self.btn_view_dashboard.clicked.connect(self.show_dashboard_view)

        visual_header = QGroupBox("Visualização")
        self.visual_header = visual_header
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
        self.legend_base = QLabel(
            "<span style='color:#ef4444;'>▲</span> "
            "<span style='color:#c9d1d9;'>Base PPP/Conhecida</span>"
        )
        self.legend_original = QLabel(
            "<span style='color:#9ca3af;'>●</span> "
            "<span style='color:#c9d1d9;'>Pontos originais</span>"
        )
        self.legend_adjusted = QLabel(
            "<span style='color:#22c55e;'>●</span> "
            "<span style='color:#c9d1d9;'>Pontos ajustados</span>"
        )
        self.legend_vectors = QLabel(
            "<span style='color:#3b82f6;'>━</span> "
            "<span style='color:#c9d1d9;'>Vetores base → ajustados</span>"
        )
        self.legend_base.setTextFormat(Qt.RichText)
        self.legend_original.setTextFormat(Qt.RichText)
        self.legend_adjusted.setTextFormat(Qt.RichText)
        self.legend_vectors.setTextFormat(Qt.RichText)
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
        self.map_canvas.setMinimumHeight(360)
        self.map_canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._map_pan_tool = QgsMapToolPan(self.map_canvas)
        self.map_canvas.setMapTool(self._map_pan_tool)

        self.map_host = MapCanvasContainer(self.map_canvas, self.map_page)
        map_layout.addWidget(self.map_host, 1)

        nav_box = QFrame(self.map_host)
        nav_box.setObjectName("mrfNavBox")
        nav_layout = QVBoxLayout(nav_box)
        nav_layout.setContentsMargins(5, 5, 5, 5)
        nav_layout.setSpacing(5)

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

        self.dashboard_page = self._build_dashboard_page()
        self.view_stack.addWidget(self.table)
        self.view_stack.addWidget(map_wrap)
        view_layout.addWidget(self.view_stack, 1)
        right_layout.addWidget(view_box, 1)

        self.map_canvas.xyCoordinates.connect(self._update_map_coordinates)
        self.map_canvas.scaleChanged.connect(self._update_map_scale)






        self.setStyleSheet(self.styleSheet() + """
            QDialog#mrfDashboardDialog {
                background-color: #0d1117;
                color: #c9d1d9;
            }

            QDialog {
                background-color: #0d1117;
                color: #c9d1d9;
            }

            QFrame#mrfPanel,
            QFrame#mrfMapWrap {
                border: 1px solid #30363d;
                border-radius: 10px;
                background-color: rgba(22, 27, 34, 0.82);
            }

            QGroupBox#mrfBlock,
            QGroupBox#mrfInnerBlock,
            QGroupBox {
                border: 1px solid #30363d;
                border-radius: 10px;
                margin-top: 12px;
                padding-top: 8px;
                font-weight: 600;
                background-color: rgba(22, 27, 34, 0.45);
            }

            QGroupBox#mrfBlock::title,
            QGroupBox#mrfInnerBlock::title,
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: #c9d1d9;
            }


            QFrame#mrfMetricGrid {
                background-color: transparent;
                border: 1px solid #30363d;
                border-radius: 6px;
            }

            QFrame#mrfMetricCell {
                background: transparent;
                border: none;
            }


            QFrame#mrfDashHeader {
                background: transparent;
                border: none;
                margin: 0px;
                padding: 0px;
            }

            QLabel#mrfDashTitle {
                color: #f0f6fc;
                font-size: 13px;
                font-weight: 700;
                border: none;
                margin: 0px;
                padding: 0px;
            }


            QFrame#mrfHistogramWidget {
                background-color: transparent;
                border: 1px solid #30363d;
                border-radius: 6px;
            }

            QFrame#mrfDashCard {

                background-color: #161b22;
                border: 1px solid #30363d;
                border-radius: 10px;
            }

            QLabel#mrfDashTitle {
                color: #f0f6fc;
                font-size: 12px;
                font-weight: 900;
                padding: 0px;
                margin: 0px;
                min-height: 30px;
            }

            QLabel#mrfDashCaption {
                color: #8b949e;
                font-size: 10px;
                font-weight: 600;
            }

            QLabel#mrfDashTextValue,
            QLabel#mrfDashValue {
                color: #f0f6fc;
                font-weight: 800;
            }

            QFrame#mrfDashSep {
                background-color: #30363d;
                min-width: 1px;
                max-width: 1px;
                min-height: 1px;
                max-height: 1px;
            }


            QTableWidget#mrfMetricTable {
                background-color: transparent;
                gridline-color: #30363d;
                border: none;
                color: #e6edf3;
                font-size: 11px;
            }

            QTableWidget#mrfMetricTable::item {
                border-bottom: 1px solid #30363d;
                border-right: 1px solid #30363d;
                padding: 5px;
            }

            QLabel[class="titulo_card"] {
                color: #8b949e;
                font-weight: bold;
                font-size: 11px;
                text-transform: uppercase;
                min-height: 35px;
                max-height: 35px;
                padding-left: 5px;
                border: none;
                background: transparent;
            }

            QTableWidget,
            QTableWidget#mrfDashTable {
                background-color: transparent;
                gridline-color: #30363d;
                color: #e6edf3;
                border: none;
                selection-background-color: #21262d;
            }

            QTableWidget#mrfDashTable::item {
                padding: 2px;
            }

            QHeaderView::section {
                background-color: #21262d;
                color: #8b949e;
                border: 1px solid #30363d;
                padding: 4px;
                font-weight: bold;
            }

            QScrollBar:horizontal {
                border: none;
                background: #161b22;
                height: 8px;
                margin: 0px 18px 0px 18px;
                border-radius: 4px;
            }

            QScrollBar::handle:horizontal {
                background: #30363d;
                min-width: 25px;
                border-radius: 4px;
            }

            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal {
                background: transparent;
                width: 0px;
            }

            QPushButton#mrfDashButton,
            QPushButton#mrfTabButton {
                background-color: #21262d;
                color: #f0f6fc;
                border: 1px solid #30363d;
                border-radius: 6px;
                padding: 6px 10px;
                font-weight: 700;
            }


            /* Tabelas métricas flat: remove relevo nativo e mantém linhas finas */
            QTableWidget#mrfMetricTable {
                background-color: transparent;
                gridline-color: #30363d;
                border: none;
                outline: none;
                color: #e6edf3;
                font-size: 11px;
            }

            QTableWidget#mrfMetricTable::item {
                border-bottom: 1px solid #30363d;
                border-right: 1px solid #30363d;
                padding: 5px;
                background-color: transparent;
            }

            QTableWidget#mrfDashTable {
                background-color: transparent;
                gridline-color: #30363d;
                border: none;
                outline: none;
                color: #e6edf3;
            }

            QTableWidget#mrfDashTable::item {
                border-bottom: 1px solid #30363d;
                padding: 3px;
                background-color: transparent;
            }

            QLabel[class="titulo_card"] {
                color: #c9d1d9;
                font-weight: bold;
                font-size: 11px;
                min-height: 35px;
                max-height: 35px;
                padding-left: 5px;
                border: none;
                background: transparent;
            }

            QPushButton#mrfDashButton:hover,
            QPushButton#mrfTabButton:hover {
                background-color: #30363d;
            }
        """)

        self.configurar_layout_cards()
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

            QFrame#mrfDashboardPage {
                background: transparent;
            }
            QFrame#mrfDashCard {
                border: 1px solid #3d4852;
                border-radius: 10px;
                background-color: #20262b;
            }
            QLabel#mrfDashTitle {
                color: #f8fafc;
                font-size: 12px;
                font-weight: 900;
            }
            QLabel#mrfDashCaption,
            QLabel#mrfDashAlertText {
                color: #cbd5e1;
                font-size: 10px;
                font-weight: 650;
            }
            QLabel#mrfDashTextValue {
                color: #f8fafc;
                font-size: 11px;
                font-weight: 900;
            }
            QLabel#mrfDashValue {
                color: #f8fafc;
                font-weight: 900;
            }
            QFrame#mrfDashSep {
                background-color: #4f5963;
            }
            QFrame#mrfDashFooter {
                border: 1px solid #3d4852;
                border-radius: 8px;
                background-color: #20262b;
            }
            QPushButton#mrfDashButton {
                border: 1px solid #4f5963;
                border-radius: 8px;
                padding: 6px 12px;
                background-color: #20262b;
                color: #f8fafc;
                font-weight: 800;
            }
            QFrame#mrfLimitsPanel {
                border: 1px solid #4f5963;
                border-radius: 8px;
                background-color: #1a1f24;
            }
            QPushButton#mrfTabButton {
                padding: 7px 12px;
                border-radius: 8px;
                font-weight: 700;
                background: #3b4a57;
                color: white;
                border: 1px solid #556575;
            }
            QTableWidget#mrfDashTable {
                border: 1px solid #34404a;
                border-radius: 6px;
                background-color: #171b1f;
                alternate-background-color: #20262b;
                gridline-color: #34404a;
                color: #e5e7eb;
                font-size: 10px;
                selection-background-color: #1f4e79;
            }
            QTableWidget#mrfDashTable::item {
                padding: 2px;
            }
            QHeaderView::section {
                background-color: #20262b;
                color: #f8fafc;
                border: 1px solid #34404a;
                padding: 3px;
                font-size: 10px;
                font-weight: 700;
            }

            QLabel#legendBase { color: #d62828; font-weight: 700; }
            QLabel#legendOriginal { color: #8e98a3; font-weight: 700; }
            QLabel#legendAdjusted { color: #2e7d32; font-weight: 700; }
            QLabel#legendVectors { color: #3b82c4; font-weight: 700; }
        """)


    def _dash_icon_badge(self, icon_name: str, fallback: str, color: str) -> QLabel:
        badge = QLabel()
        badge.setFixedSize(30, 30)
        badge.setAlignment(Qt.AlignCenter)
        badge.setObjectName("mrfDashIconBadge")
        icon_path = os.path.join(os.path.dirname(__file__), "icons", icon_name)
        if icon_name and os.path.exists(icon_path):
            badge.setPixmap(QIcon(icon_path).pixmap(18, 18))
        else:
            badge.setText(fallback)
        badge.setStyleSheet(
            "QLabel#mrfDashIconBadge {"
            f"background-color:{color};"
            "border-radius:15px;"
            "color:white;"
            "font-size:14px;"
            "font-weight:900;"
            "}"
        )
        return badge


    def _dash_header(
        self,
        icon_name: str,
        fallback: str,
        title: str,
        color: str,
    ) -> QFrame:
        header_frame = QFrame()
        header_frame.setObjectName("mrfDashHeader")
        header_frame.setMinimumHeight(32)
        header_frame.setMaximumHeight(32)

        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        badge = self._dash_icon_badge(icon_name, fallback, color)
        badge.setFixedSize(28, 28)

        title_label = QLabel(title.upper())
        title_label.setObjectName("mrfDashTitle")
        title_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        title_label.setMinimumHeight(28)
        title_label.setMaximumHeight(28)
        title_label.setStyleSheet(
            "color:#f0f6fc;"
            "font-size:12px;"
            "font-weight:900;"
            "background:transparent;"
            "border:none;"
        )

        header_layout.addWidget(badge, 0, Qt.AlignVCenter)
        header_layout.addWidget(title_label, 1, Qt.AlignVCenter)

        return header_frame


    def _fmt_signed_m(self, value, dec: int = 3) -> str:
        if value is None:
            return "Não informado"
        sign = "+" if float(value) >= 0 else ""
        return f"{sign}{self._fmt_m(float(value), dec)}"

    def _fmt_plain_m(self, value, dec: int = 2) -> str:
        if value is None:
            return "Não informado"
        return self._fmt_m(float(value), dec)

    def _base_info_row(self, layout: QGridLayout, row: int, label: str, value: str, color: str = "#f0f6fc"):
        lab = self._dash_caption(label)
        val = self._dash_text_value()
        val.setText(value or "Não informado")
        val.setStyleSheet(f"color:{color}; font-weight:900;")
        layout.addWidget(lab, row, 0)
        layout.addWidget(val, row, 1)
        return val

    def _add_big_card_icon(self, parent_layout: QHBoxLayout, icon_name: str):
        icon_label = QLabel()
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setFixedSize(118, 118)
        icon_path = os.path.join(os.path.dirname(__file__), "icons", icon_name)
        if os.path.exists(icon_path):
            pixmap = QPixmap(icon_path)
            if not pixmap.isNull():
                icon_label.setPixmap(
                    pixmap.scaled(
                        104,
                        104,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    )
                )
            else:
                icon_label.setPixmap(QIcon(icon_path).pixmap(104, 104))
        icon_label.setStyleSheet("QLabel { background: transparent; border: none; }")
        parent_layout.addWidget(icon_label, 0, Qt.AlignCenter)
        return icon_label



    def _set_big_icon(self, icon_label: QLabel, icon_name: str):
        icon_path = os.path.join(os.path.dirname(__file__), "icons", icon_name)
        if os.path.exists(icon_path):
            pixmap = QPixmap(icon_path)
            if not pixmap.isNull():
                icon_label.setPixmap(
                    pixmap.scaled(
                        104,
                        104,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    )
                )
            else:
                icon_label.setPixmap(QIcon(icon_path).pixmap(104, 104))

    def _set_base_rows(self, title_labels: list, value_labels: list, rows: list[tuple[str, str, str]]):
        for idx, (title_label, value_label) in enumerate(zip(title_labels, value_labels)):
            if idx < len(rows):
                title, value, color = rows[idx]
                title_label.setText(title)
                value_label.setText(value or "Não informado")
                value_label.setStyleSheet(f"color:{color}; font-weight:900;")
                title_label.setVisible(True)
                value_label.setVisible(True)
            else:
                title_label.setVisible(False)
                value_label.setVisible(False)

    def _orbit_color(self, orbit_text: str) -> str:
        orbit = (orbit_text or "").upper().replace("Á", "A").replace("À", "A")
        if "ULTRA" in orbit:
            return "#ef4444"
        if "FINAL" in orbit:
            return "#22c55e"
        if "RAPIDA" in orbit:
            return "#f59e0b"
        return "#f0f6fc"


    def _dash_card(self, icon: str, title: str, color: str) -> tuple[QFrame, QVBoxLayout]:
        icon_map = {
            "☰": "list-check.svg",
            "⊙": "arrow-right-left.svg",
            "★": "star.svg",
            "◇": "crosshair.svg",
            "!": "shield.svg",
            "✓": "crosshair_2.svg",
        }
        icon_name = icon if str(icon).lower().endswith(".svg") else icon_map.get(icon, "")
        fallback = "" if str(icon).lower().endswith(".svg") else icon

        card = QFrame()
        card.setObjectName("mrfDashCard")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(3)
        header = self._dash_header(
            icon_name,
            fallback,
            title,
            color,
        )
        header.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(header)
        return card, layout

    def _dash_caption(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("mrfDashCaption")
        label.setWordWrap(False)
        return label

    def _dash_value(self, color: str = "", size: int = 12) -> QLabel:
        label = QLabel("-")
        label.setAlignment(Qt.AlignCenter)
        label.setWordWrap(False)
        label.setObjectName("mrfDashValue")
        if color:
            label.setStyleSheet(f"color:{color}; font-size:{size}px; font-weight:900;")
        else:
            label.setStyleSheet(f"font-size:{size}px; font-weight:900;")
        return label

    def _dash_text_value(self, color: str = "") -> QLabel:
        label = QLabel("-")
        label.setObjectName("mrfDashTextValue")
        label.setWordWrap(False)
        if color:
            label.setStyleSheet(f"color:{color}; font-weight:900;")
        return label

    def _dash_vline(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.VLine)
        line.setObjectName("mrfDashSep")
        line.setMinimumWidth(1)
        line.setMaximumWidth(1)
        line.setStyleSheet("background-color:#30363d; min-width:1px; max-width:1px;")
        return line

    def _dash_hline(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setObjectName("mrfDashSep")
        line.setMinimumHeight(1)
        line.setMaximumHeight(1)
        line.setStyleSheet("background-color:#30363d; min-height:1px; max-height:1px;")
        return line

    def _dash_add_metric(
        self,
        grid: QGridLayout,
        row: int,
        col: int,
        title: str,
        value_label: QLabel,
    ) -> None:
        title_label = self._dash_caption(title)
        title_label.setAlignment(Qt.AlignCenter)
        grid.addWidget(title_label, row, col)
        grid.addWidget(value_label, row + 1, col)

    def _dash_alert_row(self, symbol: str, color: str, text: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        badge = QLabel(symbol)
        badge.setFixedSize(18, 18)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(
            "QLabel {"
            f"background-color:{color};"
            "border-radius:9px;"
            "color:white;"
            "font-size:11px;"
            "font-weight:900;"
            "}"
        )

        label = QLabel(text)
        label.setObjectName("mrfDashAlertText")
        label.setWordWrap(False)
        label.setStyleSheet(f"color:{color};")

        layout.addWidget(badge)
        layout.addWidget(label, 1)
        return row

    def _dash_table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget()
        table.setObjectName("mrfDashTable")
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setRowCount(0)

        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setShowGrid(True)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setWordWrap(False)
        table.setFrameStyle(QFrame.NoFrame)

        header = table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignCenter)
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)

        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)

        return table

    def _dash_set_rows(self, table: QTableWidget, rows: list[list[str]]) -> None:
        table.setRowCount(len(rows))
        for row_index, values in enumerate(rows):
            for col_index, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                if col_index == 0:
                    item.setTextAlignment(Qt.AlignCenter)
                table.setItem(row_index, col_index, item)

        if table.columnCount() == 4:
            widths = [34, 280, 120, 120]
        elif table.columnCount() == 5:
            widths = [34, 280, 120, 110, 110]
        elif table.columnCount() == 3:
            widths = [34, 280, 120]
        else:
            widths = [130] * table.columnCount()

        for idx, width in enumerate(widths[:table.columnCount()]):
            table.setColumnWidth(idx, width)

        for row in range(table.rowCount()):
            table.setRowHeight(row, 24)

        table.viewport().update()
        table.updateGeometry()


    def _metric_table(self, rows: int, cols: int) -> QTableWidget:
        table = QTableWidget(rows, cols)
        table.setObjectName("mrfMetricTable")
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setVisible(False)
        table.setShowGrid(True)
        table.setWordWrap(False)
        table.setFocusPolicy(Qt.NoFocus)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setFrameStyle(QFrame.NoFrame)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.verticalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        return table

    def _set_metric_cell(
        self,
        table: QTableWidget,
        row: int,
        col: int,
        title: str,
        label: QLabel,
    ) -> None:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 3, 4, 3)
        layout.setSpacing(1)

        title_label = self._dash_caption(title)
        title_label.setAlignment(Qt.AlignCenter)
        label.setAlignment(Qt.AlignCenter)

        layout.addWidget(title_label)
        layout.addWidget(label)
        table.setCellWidget(row, col, widget)



    def _metric_cell(
        self,
        title: str,
        value_label: QLabel,
        right_border: bool = True,
        bottom_border: bool = False,
    ) -> QFrame:
        cell = QFrame()
        cell.setObjectName("mrfMetricCell")
        borders = []
        if right_border:
            borders.append("border-right: 1px solid #30363d;")
        if bottom_border:
            borders.append("border-bottom: 1px solid #30363d;")
        cell.setStyleSheet(
            "QFrame#mrfMetricCell {"
            "background: transparent;"
            + "".join(borders)
            + "}"
        )

        layout = QVBoxLayout(cell)
        layout.setContentsMargins(6, 5, 6, 5)
        layout.setSpacing(2)

        title_label = self._dash_caption(title)
        title_label.setAlignment(Qt.AlignCenter)
        value_label.setAlignment(Qt.AlignCenter)

        layout.addStretch(1)
        if title:
            layout.addWidget(title_label)
        else:
            layout.addSpacing(10)
        layout.addWidget(value_label)
        layout.addStretch(1)
        return cell

    def _metric_grid(self, rows: int, cols: int) -> tuple[QFrame, QGridLayout]:
        grid_frame = QFrame()
        grid_frame.setObjectName("mrfMetricGrid")
        grid_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        grid = QGridLayout(grid_frame)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(0)
        grid.setVerticalSpacing(0)

        for col in range(cols):
            grid.setColumnStretch(col, 1)
        for row in range(rows):
            grid.setRowStretch(row, 1)

        return grid_frame, grid


    def _build_dashboard_page(self) -> QWidget:
        page = QFrame()
        page.setObjectName("mrfDashboardPage")
        page.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout = QGridLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(8)

        summary, summary_layout = self._dash_card("☰", "Resumo geral", "#3b82f6")
        summary.setMinimumHeight(205)
        summary.setMaximumHeight(205)
        summary.setMinimumHeight(215)
        summary.setMaximumHeight(215)
        self.dash_total_imported = self._dash_text_value()
        self.dash_total_adjusted = self._dash_text_value()
        self.dash_crs = self._dash_text_value()
        self.dash_reference = self._dash_text_value()
        self.dash_variance = self._dash_text_value()

        summary_grid = QGridLayout()
        summary_grid.setContentsMargins(0, 2, 0, 0)
        summary_grid.setHorizontalSpacing(6)
        summary_grid.setVerticalSpacing(3)
        for row, (title, value) in enumerate([
            ("Total importado", self.dash_total_imported),
            ("Total ajustado", self.dash_total_adjusted),
            ("CRS", self.dash_crs),
            ("Referência", self.dash_reference),
            ("Variância", self.dash_variance),
        ]):
            summary_grid.addWidget(self._dash_caption(title), row, 0)
            summary_grid.addWidget(value, row, 1)
        summary_grid.setColumnStretch(0, 1)
        summary_grid.setColumnStretch(1, 1)
        summary_layout.addLayout(summary_grid)
        layout.addWidget(summary, 0, 0)

        vectors, vectors_layout = self._dash_card("⊙", "Vetores do translado aplicado", "#22c55e")
        vectors.setMinimumHeight(205)
        vectors.setMaximumHeight(205)
        vectors.setMinimumHeight(215)
        vectors.setMaximumHeight(215)
        self.dash_de = self._dash_value("#22c55e", 14)
        self.dash_dn = self._dash_value("#22c55e", 14)
        self.dash_dh = self._dash_value("#ef4444", 14)
        self.dash_mag2d = self._dash_value(size=13)
        self.dash_mag3d = self._dash_value(size=13)


        vectors_metric = QFrame()
        vectors_metric.setObjectName("mrfMetricGrid")

        vectors_outer = QVBoxLayout(vectors_metric)
        vectors_outer.setContentsMargins(0, 0, 0, 0)
        vectors_outer.setSpacing(0)

        vectors_top, vectors_top_grid = self._metric_grid(1, 3)

        vectors_top_grid.addWidget(
            self._metric_cell("ΔE", self.dash_de, right_border=True),
            0, 0
        )
        vectors_top_grid.addWidget(
            self._metric_cell("ΔN", self.dash_dn, right_border=True),
            0, 1
        )
        vectors_top_grid.addWidget(
            self._metric_cell("ΔH", self.dash_dh, right_border=False),
            0, 2
        )

        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet("background-color:#30363d;border:none;")

        vectors_bottom, vectors_bottom_grid = self._metric_grid(1, 2)

        vectors_bottom_grid.addWidget(
            self._metric_cell("Mag. 2D", self.dash_mag2d, right_border=True),
            0, 0
        )
        vectors_bottom_grid.addWidget(
            self._metric_cell("Mag. 3D", self.dash_mag3d, right_border=False),
            0, 1
        )

        vectors_outer.addWidget(vectors_top)
        vectors_outer.addWidget(divider)
        vectors_outer.addWidget(vectors_bottom)

        vectors_layout.addWidget(vectors_metric, 1)

        layout.addWidget(vectors, 0, 1)

        quality, quality_layout = self._dash_card("★", "Controle de qualidade", "#8b5cf6")
        quality.setMinimumHeight(205)
        quality.setMaximumHeight(205)
        quality.setMinimumHeight(215)
        quality.setMaximumHeight(215)
        self.dash_max_2d = self._dash_text_value()
        self.dash_max_3d = self._dash_text_value()
        self.dash_max_plan = self._dash_text_value()
        self.dash_max_h = self._dash_text_value()
        quality_grid = QGridLayout()
        quality_grid.setHorizontalSpacing(6)
        quality_grid.setVerticalSpacing(4)
        for row, (title, value) in enumerate([
            ("Maior desloc. 2D", self.dash_max_2d),
            ("Maior desloc. 3D", self.dash_max_3d),
            ("Maior DP planim.", self.dash_max_plan),
            ("Maior DP H", self.dash_max_h),
        ]):
            quality_grid.addWidget(self._dash_caption(title), row, 0)
            quality_grid.addWidget(value, row, 1)
        quality_layout.addLayout(quality_grid)
        layout.addWidget(quality, 0, 2)

        precision, precision_layout = self._dash_card("◇", "Precisão dos pontos (DP)", "#3b82f6")
        self.dash_dp_e_mean = self._dash_value(size=12)
        self.dash_dp_e_max = self._dash_value(size=12)
        self.dash_dp_n_mean = self._dash_value(size=12)
        self.dash_dp_n_max = self._dash_value(size=12)
        self.dash_dp_h_mean = self._dash_value(size=12)
        self.dash_dp_h_max = self._dash_value(size=12)
        self.dash_plan_mean = self._dash_value("#60a5fa", 12)
        self.dash_plan_max = self._dash_value("#60a5fa", 12)

        precision_metric, precision_grid = self._metric_grid(2, 4)
        precision_cells = [
            ("DP E média", self.dash_dp_e_mean, 0, 0),
            ("DP E máx.", self.dash_dp_e_max, 0, 1),
            ("DP N média", self.dash_dp_n_mean, 0, 2),
            ("DP N máx.", self.dash_dp_n_max, 0, 3),
            ("DP H média", self.dash_dp_h_mean, 1, 0),
            ("DP H máx.", self.dash_dp_h_max, 1, 1),
            ("Planim. média", self.dash_plan_mean, 1, 2),
            ("Planim. máx.", self.dash_plan_max, 1, 3),
        ]
        for title, value, row, col in precision_cells:
            precision_grid.addWidget(
                self._metric_cell(
                    title,
                    value,
                    right_border=(col < 3),
                    bottom_border=(row == 0),
                ),
                row,
                col,
            )
        precision_layout.addWidget(precision_metric, 1)
        layout.addWidget(precision, 1, 0)

        precision_status, precision_status_layout = self._dash_card(
            "✓",
            "Resumo da precisão",
            "#22c55e",
        )
        self.dash_precision_class = self._dash_value("#22c55e", 13)
        self.dash_precision_class.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.dash_precision_plan_max = self._dash_text_value()
        self.dash_precision_plan_mean = self._dash_value("#60a5fa", 12)
        self.dash_precision_h_max = self._dash_text_value()
        self.dash_precision_h_mean = self._dash_value("#60a5fa", 12)

        precision_status_body = QVBoxLayout()
        precision_status_body.setContentsMargins(0, 6, 0, 0)
        precision_status_body.setSpacing(6)

        precision_status_body.addWidget(
            self.dash_precision_class,
            0,
            Qt.AlignLeft | Qt.AlignVCenter,
        )

        precision_status_divider = QFrame()
        precision_status_divider.setFixedHeight(1)
        precision_status_divider.setStyleSheet("background-color:#30363d;border:none;")
        precision_status_body.addWidget(precision_status_divider)

        precision_metric, precision_grid = self._metric_grid(1, 2)

        plan_cell = QFrame()
        plan_cell.setObjectName("mrfMetricCell")
        plan_cell.setStyleSheet(
            "QFrame#mrfMetricCell {"
            "background: transparent;"
            "border-right: 1px solid #30363d;"
            "}"
        )
        plan_layout = QVBoxLayout(plan_cell)
        plan_layout.setContentsMargins(8, 6, 8, 6)
        plan_layout.setSpacing(3)

        plan_title = self._dash_caption("PLANIMETRIA")
        plan_title.setAlignment(Qt.AlignCenter)
        plan_layout.addWidget(plan_title)

        plan_max_row = QHBoxLayout()
        plan_max_row.setContentsMargins(0, 0, 0, 0)
        plan_max_row.addWidget(self._dash_caption("Máx."), 0, Qt.AlignLeft)
        plan_max_row.addWidget(self.dash_precision_plan_max, 1, Qt.AlignLeft)
        plan_layout.addLayout(plan_max_row)

        plan_mean_row = QHBoxLayout()
        plan_mean_row.setContentsMargins(0, 0, 0, 0)
        plan_mean_row.addWidget(self._dash_caption("Média"), 0, Qt.AlignLeft)
        plan_mean_row.addWidget(self.dash_precision_plan_mean, 1, Qt.AlignLeft)
        plan_layout.addLayout(plan_mean_row)

        h_cell = QFrame()
        h_cell.setObjectName("mrfMetricCell")
        h_cell.setStyleSheet(
            "QFrame#mrfMetricCell {"
            "background: transparent;"
            "border: none;"
            "}"
        )
        h_layout = QVBoxLayout(h_cell)
        h_layout.setContentsMargins(8, 6, 8, 6)
        h_layout.setSpacing(3)

        h_title = self._dash_caption("ALTIMETRIA")
        h_title.setAlignment(Qt.AlignCenter)
        h_layout.addWidget(h_title)

        h_max_row = QHBoxLayout()
        h_max_row.setContentsMargins(0, 0, 0, 0)
        h_max_row.addWidget(self._dash_caption("Máx."), 0, Qt.AlignLeft)
        h_max_row.addWidget(self.dash_precision_h_max, 1, Qt.AlignLeft)
        h_layout.addLayout(h_max_row)

        h_mean_row = QHBoxLayout()
        h_mean_row.setContentsMargins(0, 0, 0, 0)
        h_mean_row.addWidget(self._dash_caption("Média"), 0, Qt.AlignLeft)
        h_mean_row.addWidget(self.dash_precision_h_mean, 1, Qt.AlignLeft)
        h_layout.addLayout(h_mean_row)

        precision_grid.addWidget(plan_cell, 0, 0)
        precision_grid.addWidget(h_cell, 0, 1)

        precision_status_body.addWidget(precision_metric, 1)
        precision_status_layout.addLayout(precision_status_body)
        layout.addWidget(precision_status, 1, 1)

        alerts, alerts_layout = self._dash_card("!", "Alertas de qualidade", "#f59e0b")
        self.dash_alerts_area = QVBoxLayout()
        self.dash_alerts_area.setSpacing(2)
        alerts_layout.addLayout(self.dash_alerts_area)

        limit_row = QHBoxLayout()
        self.btn_config_limits = QPushButton("Configurar limites")
        self.btn_config_limits.setObjectName("mrfDashButton")
        self.btn_config_limits.setCheckable(True)
        self.btn_config_limits.toggled.connect(self.toggle_limits_panel)
        limit_row.addStretch(1)
        limit_row.addWidget(self.btn_config_limits)
        alerts_layout.addLayout(limit_row)

        self.limits_panel = QFrame()
        self.limits_panel.setObjectName("mrfLimitsPanel")
        limits_grid = QGridLayout(self.limits_panel)
        limits_grid.setContentsMargins(6, 5, 6, 5)
        self.limit_plan = QDoubleSpinBox()
        self.limit_h = QDoubleSpinBox()
        self.limit_2d = QDoubleSpinBox()
        for spin, value in [
            (self.limit_plan, 0.050),
            (self.limit_h, 0.100),
            (self.limit_2d, 0.500),
        ]:
            spin.setDecimals(3)
            spin.setRange(0.001, 999.000)
            spin.setSingleStep(0.010)
            spin.setValue(value)
            spin.setSuffix(" m")
            spin.valueChanged.connect(self.update_dashboard)
        limits_grid.addWidget(QLabel("DP planim."), 0, 0)
        limits_grid.addWidget(self.limit_plan, 0, 1)
        limits_grid.addWidget(QLabel("DP H"), 1, 0)
        limits_grid.addWidget(self.limit_h, 1, 1)
        limits_grid.addWidget(QLabel("Desloc. 2D"), 2, 0)
        limits_grid.addWidget(self.limit_2d, 2, 1)
        self.limits_panel.setVisible(False)
        alerts_layout.addWidget(self.limits_panel)
        layout.addWidget(alerts, 1, 2)

        # =========================
        # CARDS INFERIORES - BASE
        # =========================

        self.base_info_card, self.base_info_layout = self._dash_card(
            "landmark.svg",
            "Informações da Base",
            "#f59e0b",
        )
        self.base_detail_card, self.base_detail_layout = self._dash_card(
            "shield-check.svg",
            "Detalhes da Base",
            "#3b82f6",
        )
        for card in [self.base_info_card, self.base_detail_card]:
            card.setStyleSheet(
                "QFrame#mrfDashCard {"
                "background-color:#111820;"
                "border: 1px solid #d99a00;"
                "border-radius: 8px;"
                "}"
                "QFrame#mrfDashHeader {"
                "background:transparent;"
                "border:none;"
                "}"
                "QLabel#mrfDashTitle {"
                "color:#f0f6fc;"
                "font-size:12px;"
                "font-weight:900;"
                "background:transparent;"
                "border:none;"
                "}"
                "QLabel#mrfDashCaption {"
                "color:#8b949e;"
                "font-weight:700;"
                "background:transparent;"
                "border:none;"
                "}"
                "QLabel#mrfDashTextValue {"
                "color:#f0f6fc;"
                "font-weight:900;"
                "background:transparent;"
                "border:none;"
                "}"
            )

        # ---- Informações da Base
        info_body = QHBoxLayout()
        info_body.setContentsMargins(0, 4, 0, 0)
        info_body.setSpacing(12)
        info_grid = QGridLayout()
        info_grid.setContentsMargins(4, 0, 4, 0)
        info_grid.setHorizontalSpacing(12)
        info_grid.setVerticalSpacing(4)
        info_body.addLayout(info_grid, 1)

        self.base_info_title_labels = []
        self.base_info_value_labels = []
        for row in range(8):
            title_label = self._dash_caption("-")
            value_label = self._dash_text_value()
            value_label.setWordWrap(False)
            info_grid.addWidget(title_label, row, 0)
            info_grid.addWidget(value_label, row, 1)
            self.base_info_title_labels.append(title_label)
            self.base_info_value_labels.append(value_label)

        self.base_info_icon = self._add_big_card_icon(info_body, "satellite_gold_ready.png")
        self.base_info_layout.addLayout(info_body, 1)

        # ---- Detalhes da Base
        detail_body = QHBoxLayout()
        detail_body.setContentsMargins(0, 4, 0, 0)
        detail_body.setSpacing(12)
        detail_grid = QGridLayout()
        detail_grid.setContentsMargins(4, 0, 4, 0)
        detail_grid.setHorizontalSpacing(12)
        detail_grid.setVerticalSpacing(4)
        detail_body.addLayout(detail_grid, 1)

        self.base_detail_title_labels = []
        self.base_detail_value_labels = []
        for row in range(7):
            title_label = self._dash_caption("-")
            value_label = self._dash_text_value()
            value_label.setWordWrap(True)
            value_label.setMinimumHeight(22 if row < 3 else 26)
            value_label.setStyleSheet("color:#f0f6fc; font-weight:900; font-size:10px;")
            detail_grid.addWidget(title_label, row, 0)
            detail_grid.addWidget(value_label, row, 1)
            self.base_detail_title_labels.append(title_label)
            self.base_detail_value_labels.append(value_label)

        self.base_detail_icon = self._add_big_card_icon(detail_body, "shield_check_gold.svg")
        self.base_detail_layout.addLayout(detail_body, 1)

        base_cards_row = QWidget()
        base_cards_row.setObjectName("mrfBaseCardsRow")
        base_cards_layout = QHBoxLayout(base_cards_row)
        base_cards_layout.setContentsMargins(0, 0, 0, 0)
        base_cards_layout.setSpacing(8)
        base_cards_layout.addWidget(self.base_info_card, 1)
        base_cards_layout.addWidget(self.base_detail_card, 1)
        layout.addWidget(base_cards_row, 2, 0, 1, 3)

        footer = QFrame()
        footer.setObjectName("mrfDashFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(10, 5, 10, 5)
        self.dash_generated = QLabel("Dashboard ainda não gerado.")
        self.dash_generated.setObjectName("mrfDashCaption")
        self.btn_update_dashboard = QPushButton("Atualizar Dashboard")
        self.btn_export_dashboard_pdf = QPushButton("Exportar Resumo (PDF)")
        for button in [self.btn_update_dashboard, self.btn_export_dashboard_pdf]:
            button.setObjectName("mrfDashButton")
        self.btn_update_dashboard.clicked.connect(self.update_dashboard)
        self.btn_export_dashboard_pdf.clicked.connect(self.generate_report)
        footer_layout.addWidget(self.dash_generated)
        footer_layout.addStretch(1)
        footer_layout.addWidget(self.btn_update_dashboard)
        footer_layout.addWidget(self.btn_export_dashboard_pdf)
        layout.addWidget(footer, 3, 0, 1, 3)

        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(2, 1)
        layout.setRowStretch(0, 0)
        layout.setRowStretch(1, 0)
        layout.setRowStretch(2, 1)
        layout.setRowStretch(3, 0)
        return page

    def toggle_limits_panel(self, enabled: bool):
        self.limits_panel.setVisible(enabled)
        self.dashboard_page.updateGeometry()

    def _clear_alert_rows(self):
        while self.dash_alerts_area.count():
            item = self.dash_alerts_area.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _add_dashboard_alert(self, symbol: str, color: str, text: str) -> None:
        self.dash_alerts_area.addWidget(self._dash_alert_row(symbol, color, text))

    def _fmt_m(self, value: float, dec: int = 3) -> str:
        return f"{float(value):.{dec}f} m".replace(".", ",")

    def _reference_label(self) -> str:
        if not self.ppp_data:
            return "-"
        if self.ppp_data.source_kind == "PPP_IBGE":
            return "PPP-IBGE"
        if self.ppp_data.source_kind:
            return str(self.ppp_data.source_kind)
        return "Manual"

    def show_table_view(self):
        self.view_stack.setCurrentWidget(self.table)
        self.satellite_group.setVisible(False)
        self.visual_header.setVisible(False)
        self._set_visual_labels_visible(False)

    def _current_visualization_df(self):
        if (
            hasattr(self, "chk_precision_filter")
            and self.chk_precision_filter.isChecked()
            and self.filter_approved_df is not None
        ):
            return self.filter_approved_df
        if self.result_df is not None:
            return self.result_df
        return self.df

    def _style_filter_point_layer(self, layer, color: str):
        symbol = QgsMarkerSymbol.createSimple(
            {
                "name": "circle",
                "color": color,
                "outline_color": "white",
                "outline_width": "0.35",
                "size": "2.0",
            }
        )
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))

    def _build_filter_status_layer(self, df, layer_name: str, status_label: str, color: str):
        layer = QgsVectorLayer(
            f"Point?crs={self.selected_crs.authid()}",
            layer_name,
            "memory",
        )
        provider = layer.dataProvider()
        provider.addAttributes(
            [
                QgsField("Nome", QVariant.String),
                QgsField("Status", QVariant.String),
                QgsField("SigmaP", QVariant.Double),
                QgsField("Limite", QVariant.Double),
                QgsField("Motivo", QVariant.String),
            ]
        )
        layer.updateFields()

        features = []
        for _, row in df.iterrows():
            feature = QgsFeature(layer.fields())
            feature["Nome"] = str(row.get("Nome", ""))
            feature["Status"] = status_label
            feature["SigmaP"] = float(row.get("Precisão Planimétrica", 0.0))
            feature["Limite"] = float(row.get("Limite Aplicado", 0.0))
            feature["Motivo"] = str(row.get("Motivo", ""))
            feature.setGeometry(
                QgsGeometry.fromPointXY(
                    QgsPointXY(float(row["Este"]), float(row["Norte"]))
                )
            )
            features.append(feature)

        provider.addFeatures(features)
        layer.updateExtents()
        self._style_filter_point_layer(layer, color)
        return layer

    def _build_filter_map_layers(self, add_to_project: bool = False):
        if self.filter_df is None:
            self.apply_precision_filter(show_message=False)

        if self.filter_df is None or self.filter_df.empty:
            raise ValueError("Não há pontos filtrados para criar camadas.")

        approved_df = self.filter_df[
            self.filter_df["Status Filtro"] == "Aprovado"
        ].copy()
        rejected_df = self.filter_df[
            self.filter_df["Status Filtro"] == "Reprovado"
        ].copy()

        qgis_layers = []

        if not approved_df.empty:
            qgis_layers.append(
                self._build_filter_status_layer(
                    approved_df,
                    "MRF - Pontos aprovados Filtro MTGIR",
                    "Aprovado",
                    "#22c55e",
                )
            )

        if not rejected_df.empty:
            qgis_layers.append(
                self._build_filter_status_layer(
                    rejected_df,
                    "MRF - Pontos reprovados Filtro MTGIR",
                    "Reprovado",
                    "#ef4444",
                )
            )

        if add_to_project and qgis_layers:
            project = QgsProject.instance()
            for layer in list(project.mapLayers().values()):
                if layer.name() in [
                    "MRF - Pontos aprovados Filtro MTGIR",
                    "MRF - Pontos reprovados Filtro MTGIR",
                ]:
                    project.removeMapLayer(layer.id())
            project.addMapLayers(qgis_layers)

        return qgis_layers


    def open_filter_map_dialog(self):
        if self.filter_df is None:
            self.apply_precision_filter(show_message=False)

        if self.filter_df is None or self.filter_df.empty:
            QMessageBox.warning(
                self,
                "MRF Translado GNSS",
                "Aplique o filtro antes de abrir a visualização.",
            )
            return

        if not self.is_valid_utm_crs(self.selected_crs):
            QMessageBox.warning(
                self,
                "MRF Translado GNSS",
                "Selecione um CRS projetado UTM em metros antes de visualizar.",
            )
            return

        try:
            self.visual_layers = self._build_filter_map_layers(
                add_to_project=False
            )
            apply_layers_to_canvas(
                self.map_canvas,
                self.visual_layers,
                self.selected_crs,
            )
            self._update_map_scale(self.map_canvas.scale())
            self.map_host._position_overlays()

            try:
                pan_tool = QgsMapToolPan(self.map_canvas)
                self.map_canvas.setMapTool(pan_tool)
                self.map_canvas._mrf_pan_tool = pan_tool
            except Exception:
                pass
        except Exception as exc:
            QMessageBox.critical(self, "MRF Translado GNSS", str(exc))
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(
            "Visualização do mapa - Filtro de precisão posicional"
        )
        dialog.resize(1450, 860)
        dialog.setMinimumSize(1200, 720)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setStyleSheet(
            "background-color:#111820;"
            "border-bottom:1px solid #30363d;"
        )
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 8, 12, 8)

        title = QLabel(
            "VISUALIZAÇÃO DO MAPA - FILTRO MTGIR"
        )
        title.setStyleSheet(
            "color:#f0f6fc;"
            "font-size:13px;"
            "font-weight:900;"
        )

        approved = int(
            (
                self.filter_df["Status Filtro"] == "Aprovado"
            ).sum()
        )
        rejected = int(
            (
                self.filter_df["Status Filtro"] == "Reprovado"
            ).sum()
        )

        info = QLabel(
            f"Aprovados: {approved}  |  "
            f"Reprovados: {rejected}"
        )
        info.setStyleSheet(
            "color:#c9d1d9;"
            "font-size:11px;"
            "font-weight:700;"
        )

        header_layout.addWidget(title)
        header_layout.addStretch(1)
        header_layout.addWidget(info)

        layout.addWidget(header)

        # REUTILIZA EXATAMENTE O MESMO MAP HOST DO TRANSLADO
        self.map_page.setParent(dialog)
        layout.addWidget(self.map_page, 1)

        def restore_map():
            self.map_page.setParent(self)
            self.view_stack.addWidget(self.map_page)
            self.view_stack.setCurrentWidget(self.table)

        dialog.finished.connect(lambda _: restore_map())

        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

        self.filter_map_dialog = dialog


    def _activate_map_pan(self):
        try:
            self.map_canvas.setMapTool(self._map_pan_tool)
        except Exception:
            pass

    def show_map_view(self):
        if (
            hasattr(self, "chk_precision_filter")
            and self.chk_precision_filter.isChecked()
        ):
            return

        if not self.chk_visualizacao.isChecked():
            self.chk_visualizacao.setChecked(True)
            return

        self.view_stack.setCurrentWidget(self.map_page)
        self.satellite_group.setVisible(True)
        self.visual_header.setVisible(True)
        self._set_visual_labels_visible(True)
        self.refresh_visualizacao()
        self._activate_map_pan()


    def show_dashboard_view(self):
        if self.result_df is None:
            QMessageBox.warning(
                self,
                "MRF Translado GNSS",
                "Calcule o translado antes de abrir o Dashboard QC.",
            )
            return

        self.satellite_group.setVisible(False)
        self.visual_header.setVisible(False)
        self._set_visual_labels_visible(False)

        if self.dashboard_dialog is None:
            self.dashboard_dialog = QDialog(self)
            self.dashboard_dialog.setWindowTitle("MRF Translado GNSS - Dashboard QC")
            self.dashboard_dialog.setObjectName("mrfDashboardDialog")
            self.dashboard_dialog.resize(1260, 720)
            self.dashboard_dialog.setMinimumSize(1050, 620)

            dialog_layout = QVBoxLayout(self.dashboard_dialog)
            dialog_layout.setContentsMargins(10, 10, 10, 10)
            dialog_layout.setSpacing(0)

            self.dashboard_page.setParent(self.dashboard_dialog)
            dialog_layout.addWidget(self.dashboard_page)

        self.dashboard_dialog.show()
        self.dashboard_dialog.raise_()
        self.dashboard_dialog.activateWindow()
        QTimer.singleShot(0, self.update_dashboard)

    def update_dashboard(self):
        if self.df is None or self.result_df is None or self.base_data is None or self.ppp_data is None:
            return

        self.dash_total_imported.setText(str(len(self.df)))
        self.dash_total_adjusted.setText(str(len(self.result_df)))
        crs_text = self.selected_crs.description()
        self.dash_crs.setText(crs_text.replace("SIRGAS 2000 / UTM zone", "SIRGAS 2000 / UTM"))
        self.dash_crs.setToolTip(crs_text)
        self.dash_reference.setText(self._reference_label())

        variance_state = "ATIVA" if self.chk_variance.isChecked() else "DESATIVADA"
        variance_color = "#22c55e" if self.chk_variance.isChecked() else "#ef4444"
        self.dash_variance.setText(variance_state)
        self.dash_variance.setStyleSheet(f"color:{variance_color}; font-weight:900;")

        de = float(self.ppp_data.east - self.base_data.east)
        dn = float(self.ppp_data.north - self.base_data.north)
        dh = float(self.ppp_data.h - self.base_data.h)
        mag2d = (de ** 2 + dn ** 2) ** 0.5
        mag3d = (de ** 2 + dn ** 2 + dh ** 2) ** 0.5
        self.dash_de.setText(self._fmt_m(de))
        self.dash_dn.setText(self._fmt_m(dn))
        self.dash_dh.setText(self._fmt_m(dh))
        self.dash_mag2d.setText(self._fmt_m(mag2d))
        self.dash_mag3d.setText(self._fmt_m(mag3d))

        dash = self.result_df.copy()
        dash["Desloc2D"] = (
            (dash["Este Ajustado"] - dash["Este"]) ** 2
            + (dash["Norte Ajustado"] - dash["Norte"]) ** 2
        ) ** 0.5
        dash["Desloc3D"] = (
            dash["Desloc2D"] ** 2
            + (dash["Altitude Ajustada"] - dash["Altitude Elipsoidal"]) ** 2
        ) ** 0.5
        # Robustez para Memorial SIGEF/base manual:
        # se algum DP ajustado vier vazio/NaN, usa o DP original do TXT.
        for adjusted_col, original_col in [
            ("DP E Ajustado", "DP E"),
            ("DP N Ajustado", "DP N"),
            ("DP U Ajustado", "DP U"),
        ]:
            if adjusted_col not in dash.columns and original_col in dash.columns:
                dash[adjusted_col] = dash[original_col]
            if adjusted_col in dash.columns:
                dash[adjusted_col] = dash[adjusted_col].replace("", None)
                dash[adjusted_col] = dash[adjusted_col].astype(float)
                if original_col in dash.columns:
                    dash[original_col] = dash[original_col].replace("", None).astype(float)
                    dash[adjusted_col] = dash[adjusted_col].fillna(dash[original_col])

        dash["DPPlan"] = (
            dash["DP E Ajustado"].astype(float) ** 2
            + dash["DP N Ajustado"].astype(float) ** 2
        ) ** 0.5

        dp_e = dash["DP E Ajustado"].astype(float)
        dp_n = dash["DP N Ajustado"].astype(float)
        dp_h = dash["DP U Ajustado"].astype(float)
        dp_plan = dash["DPPlan"].astype(float)

        self.dash_dp_e_mean.setText(self._fmt_m(dp_e.mean(), 4))
        self.dash_dp_e_max.setText(self._fmt_m(dp_e.max(), 4))
        self.dash_dp_n_mean.setText(self._fmt_m(dp_n.mean(), 4))
        self.dash_dp_n_max.setText(self._fmt_m(dp_n.max(), 4))
        self.dash_dp_h_mean.setText(self._fmt_m(dp_h.mean(), 4))
        self.dash_dp_h_max.setText(self._fmt_m(dp_h.max(), 4))
        self.dash_plan_mean.setText(self._fmt_m(dp_plan.mean(), 4))
        self.dash_plan_max.setText(self._fmt_m(dp_plan.max(), 4))

        self.dash_max_2d.setText(self._fmt_m(dash["Desloc2D"].max()))
        self.dash_max_3d.setText(self._fmt_m(dash["Desloc3D"].max()))
        self.dash_max_plan.setText(self._fmt_m(dp_plan.max(), 4))
        self.dash_max_h.setText(self._fmt_m(dp_h.max(), 4))

        plan_limit = self.limit_plan.value()
        h_limit = self.limit_h.value()
        plan_ok = dp_plan.max() <= plan_limit
        h_ok = dp_h.max() <= h_limit
        general_ok = plan_ok and h_ok

        self.dash_precision_class.setText("APROVADA" if general_ok else "ATENÇÃO")
        self.dash_precision_class.setStyleSheet(
            "color:#22c55e; font-size:13px; font-weight:900;"
            if general_ok else
            "color:#f59e0b; font-size:13px; font-weight:900;"
        )
        self.dash_precision_plan_max.setText(
            self._fmt_m(dp_plan.max(), 4)
        )
        self.dash_precision_plan_mean.setText(
            self._fmt_m(dp_plan.mean(), 4)
        )
        self.dash_precision_h_max.setText(
            self._fmt_m(dp_h.max(), 4)
        )
        self.dash_precision_h_mean.setText(
            self._fmt_m(dp_h.mean(), 4)
        )

        self._clear_alert_rows()
        d2_limit = self.limit_2d.value()

        if dp_plan.max() > plan_limit:
            self._add_dashboard_alert("!", "#f59e0b", f"DP planimétrica acima de {format_pt(plan_limit, 3)} m.")
        else:
            self._add_dashboard_alert(
                "✓",
                "#22c55e",
                f"DP planimétrica dentro do limite de {format_pt(plan_limit, 3)} m.",
            )

        if dp_h.max() > h_limit:
            self._add_dashboard_alert("!", "#f59e0b", f"DP H acima de {format_pt(h_limit, 3)} m.")
        else:
            self._add_dashboard_alert("✓", "#22c55e", f"DP H dentro do limite de {format_pt(h_limit, 3)} m.")

        if dash["Desloc2D"].max() > d2_limit:
            self._add_dashboard_alert("!", "#f59e0b", f"Deslocamento 2D acima de {format_pt(d2_limit, 3)} m.")
        else:
            self._add_dashboard_alert(
                "✓",
                "#22c55e",
                f"Deslocamento 2D dentro do limite de {format_pt(d2_limit, 3)} m.",
            )

        self._add_dashboard_alert("i", variance_color, f"Propagação de variância: {variance_state}.")
        self._add_dashboard_alert("i", "#3b82f6", f"CRS: {self.selected_crs.description()}")

        source_kind = self.ppp_data.source_kind if self.ppp_data else ""
        ppp_meta = self.ppp_data if self.ppp_data and source_kind == "PPP_IBGE" else None
        memorial_meta = self.ppp_data if self.ppp_data and source_kind == "BASE_CONHECIDA" else None

        if ppp_meta:
            self._set_big_icon(self.base_info_icon, "satellite_gold_ready.png")
            self._set_big_icon(self.base_detail_icon, "shield_check_gold.svg")

            orbit_text = getattr(ppp_meta, "orbit_type", None) or "Não informado"
            orbit_color = self._orbit_color(orbit_text)

            if ppp_meta.sigma_n is not None and ppp_meta.sigma_e is not None and ppp_meta.sigma_h is not None:
                sigma_txt = (
                    f"Lat.: {self._fmt_m(ppp_meta.sigma_n, 3)}     "
                    f"Lon.: {self._fmt_m(ppp_meta.sigma_e, 3)}     "
                    f"Alt.: {self._fmt_m(ppp_meta.sigma_h, 3)}"
                )
            else:
                sigma_txt = "Não informado"

            if getattr(ppp_meta, "geoid_model", None):
                geoid_text = ppp_meta.geoid_model
                if getattr(ppp_meta, "geoid_factor", None) is not None:
                    geoid_text += f" (Fator: {self._fmt_m(ppp_meta.geoid_factor, 2)}"
                    if getattr(ppp_meta, "geoid_uncertainty", None) is not None:
                        geoid_text += f" | Incerteza: {self._fmt_m(ppp_meta.geoid_uncertainty, 2)}"
                    geoid_text += ")"
            else:
                geoid_text = "Não informado"

            self._set_base_rows(
                self.base_info_title_labels,
                self.base_info_value_labels,
                [
                    ("Origem da Base", "PPP-IBGE", "#f59e0b"),
                    ("Data da Sessão", (getattr(ppp_meta, "session_start", None) or "Não informado"), "#f59e0b"),
                    ("Época de Referência", getattr(ppp_meta, "reference_epoch", None) or "2000.4", "#f59e0b"),
                    ("Órbitas dos Satélites", orbit_text, orbit_color),
                    (
                        "Frequência Processada",
                        getattr(ppp_meta, "processed_frequency", None) or "Não informado",
                        "#f59e0b",
                    ),
                    ("Sigma (95%)", sigma_txt, "#f0f6fc"),
                    ("Altitude Geométrica (m)", self._fmt_plain_m(getattr(ppp_meta, "alt_20004", None), 2), "#f0f6fc"),
                    ("Modelo Geoidal", geoid_text, "#f0f6fc"),
                ],
            )

            def _coord_text(lat, lon, alt):
                if not lat and not lon:
                    return "Não informado"
                return (
                    f"Lat.: {lat}    Alt.: {self._fmt_plain_m(alt, 2)}\n"
                    f"Lon.: {lon}"
                )

            self._set_base_rows(
                self.base_detail_title_labels,
                self.base_detail_value_labels,
                [
                    ("Tipo de Coordenada", "Latitude, longitude e altitude geodésicas", "#f0f6fc"),
                    (
                        "Coordenadas (Época 2000.4)",
                        _coord_text(
                            ppp_meta.lat_20004,
                            ppp_meta.lon_20004,
                            ppp_meta.alt_20004,
                        ),
                        "#f0f6fc",
                    ),
                    (
                        "Coordenadas (Data do Levant.)",
                        _coord_text(
                            ppp_meta.lat_survey,
                            ppp_meta.lon_survey,
                            ppp_meta.alt_survey,
                        ),
                        "#f0f6fc",
                    ),
                    (
                        "Diferenças (Data - Época 2000.4)",
                        f"ΔN: {self._fmt_signed_m(getattr(ppp_meta, 'dn_epoch', None), 3)}     "
                        f"ΔE: {self._fmt_signed_m(getattr(ppp_meta, 'de_epoch', None), 3)}\n"
                        f"ΔH: {self._fmt_signed_m(getattr(ppp_meta, 'dh_epoch', None), 2)}",
                        "#f0f6fc",
                    ),
                ],
            )

        elif memorial_meta:
            self._set_big_icon(self.base_info_icon, "memorial.png")
            self._set_big_icon(self.base_detail_icon, "shield_check_gold.svg")

            sistema = getattr(self, "sigef_sistema_geodesico", "SIRGAS 2000") or "SIRGAS 2000"
            denominacao = getattr(self, "sigef_denominacao", "Não informado") or "Não informado"
            credenciado = (
                getattr(self, "sigef_credenciado", "")
                or getattr(self.emitente, "codigo_credenciado", "")
                or "Não informado"
            )
            vertice = (
                getattr(self, "sigef_vertice_base", "")
                or getattr(memorial_meta, "source_code", "")
                or "Não informado"
            )
            doc_rt = getattr(self, "sigef_documento_rt", "") or "Não informado"
            data_cert = getattr(self, "sigef_data_certificacao", "") or "Não informado"
            status = getattr(self, "sigef_status_certificacao", "") or "Certificação SIGEF"

            self._set_base_rows(
                self.base_info_title_labels,
                self.base_info_value_labels,
                [
                    ("Origem da Base", "MEMORIAL SIGEF", "#f59e0b"),
                    ("Sistema Geodésico", sistema, "#f0f6fc"),
                    ("Denominação", denominacao, "#f0f6fc"),
                    ("Código do Credenciado", credenciado, "#f0f6fc"),
                    ("Vértice Utilizado", vertice, "#f59e0b"),
                ],
            )

            self._set_base_rows(
                self.base_detail_title_labels,
                self.base_detail_value_labels,
                [
                    ("Tipo de Coordenada", "Latitude, longitude e altitude geodésicas", "#f0f6fc"),
                    ("Documento de RT", doc_rt, "#f0f6fc"),
                    ("Data da Certificação", data_cert, "#f0f6fc"),
                    ("Status da Certificação", status, "#22c55e"),
                    ("Observação", "Memorial SIGEF não possui sigma PPP.", "#f59e0b"),
                ],
            )

        else:
            self._set_big_icon(self.base_info_icon, "info.svg")
            self._set_big_icon(self.base_detail_icon, "circle-slash.svg")
            self._set_base_rows(
                self.base_info_title_labels,
                self.base_info_value_labels,
                [
                    ("Origem da Base", "BASE MANUAL", "#f59e0b"),
                    ("Sistema Geodésico", "Não informado", "#f0f6fc"),
                    ("Denominação", "Não informado", "#f0f6fc"),
                    (
                        "Código do Credenciado",
                        getattr(self.emitente, "codigo_credenciado", "")
                        or "Não informado",
                        "#f0f6fc",
                    ),
                    ("Vértice Utilizado", "Não informado", "#f0f6fc"),
                ],
            )
            self._set_base_rows(
                self.base_detail_title_labels,
                self.base_detail_value_labels,
                [
                    ("Tipo de Coordenada", "Sem metadados", "#f0f6fc"),
                    ("Status", "BASE MANUAL", "#f59e0b"),
                    ("Observação", "Sem validação externa.", "#f59e0b"),
                ],
            )

        self.dash_generated.setText(
            f"Dashboard gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
        )
        self.dashboard_page.update()
        self.dashboard_page.updateGeometry()
        self.dashboard_page.layout().activate()
        if self.dashboard_dialog is not None:
            self.dashboard_dialog.updateGeometry()
        self.update()
        self.dashboard_page.updateGeometry()
        if self.dashboard_dialog is not None:
            self.dashboard_dialog.updateGeometry()

    def configurar_layout_cards(self):
        main_layout = self.layout()
        if main_layout:
            try:
                main_layout.setStretch(0, 0)
                main_layout.setStretch(1, 10)
                main_layout.setStretch(2, 1)
            except Exception:
                pass

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

    def toggle_visualizacao(self, checked):
        if not checked:
            self.view_stack.setCurrentWidget(self.table)
            self.satellite_group.setVisible(False)
            self.visual_header.setVisible(False)
            self._set_visual_labels_visible(False)
            return

        filter_active = (
            hasattr(self, "chk_precision_filter")
            and self.chk_precision_filter.isChecked()
        )

        if filter_active:
            if self.df is None:
                QMessageBox.warning(
                    self,
                    "MRF Translado GNSS",
                    "Importe um TXT de pontos antes de abrir a visualização.",
                )
                self.chk_visualizacao.setChecked(False)
                return
            if self.filter_approved_df is None:
                self.apply_precision_filter(show_message=False)
            if self.filter_approved_df is None or self.filter_approved_df.empty:
                QMessageBox.warning(
                    self,
                    "MRF Translado GNSS",
                    "Não há pontos aprovados para visualizar no mapa.",
                )
                self.chk_visualizacao.setChecked(False)
                return
        elif self.result_df is None or self.df is None or self.ppp_data is None:
            QMessageBox.warning(
                self,
                "MRF Translado GNSS",
                "Calcule o translado antes de abrir a visualização.",
            )
            self.chk_visualizacao.setChecked(False)
            return

        self.view_stack.setCurrentWidget(self.map_page)
        self.satellite_group.setVisible(True)
        self.visual_header.setVisible(True)
        self._set_visual_labels_visible(True)
        self.refresh_visualizacao()


    def _build_filter_approved_layers(self, approved_df):
        layer = QgsVectorLayer(
            f"Point?crs={self.selected_crs.authid()}",
            "Pontos aprovados - Filtro MTGIR",
            "memory",
        )
        provider = layer.dataProvider()
        provider.addAttributes([
            QgsField("Nome", QVariant.String),
            QgsField("Status", QVariant.String),
            QgsField("SigmaP", QVariant.Double),
            QgsField("Limite", QVariant.Double),
        ])
        layer.updateFields()

        features = []
        for _, row in approved_df.iterrows():
            feature = QgsFeature(layer.fields())
            feature["Nome"] = str(row.get("Nome", ""))
            feature["Status"] = str(row.get("Status Filtro", "Aprovado"))
            feature["SigmaP"] = float(row.get("Precisão Planimétrica", 0.0))
            feature["Limite"] = float(row.get("Limite Aplicado", 0.0))
            feature.setGeometry(
                QgsGeometry.fromPointXY(
                    QgsPointXY(float(row["Este"]), float(row["Norte"]))
                )
            )
            features.append(feature)

        provider.addFeatures(features)
        layer.updateExtents()
        return [layer]


    def refresh_visualizacao(self):
        filter_active = (
            hasattr(self, "chk_precision_filter")
            and self.chk_precision_filter.isChecked()
        )

        if filter_active:
            if self.filter_df is None:
                self.apply_precision_filter(show_message=False)

            if self.filter_df is None or self.filter_df.empty:
                QMessageBox.warning(
                    self,
                    "MRF Translado GNSS",
                    "Aplique o filtro antes de abrir a visualização.",
                )
                return

            if not self.is_valid_utm_crs(self.selected_crs):
                QMessageBox.warning(
                    self,
                    "MRF Translado GNSS",
                    "Selecione um CRS projetado UTM em metros antes de visualizar.",
                )
                return

            try:
                self.visual_layers = self._build_filter_map_layers(
                    add_to_project=False
                )
                apply_layers_to_canvas(
                    self.map_canvas,
                    self.visual_layers,
                    self.selected_crs,
                )
                self._update_map_scale(self.map_canvas.scale())
                self.map_host._position_overlays()
                self._activate_map_pan()
            except Exception as exc:
                QMessageBox.critical(self, "MRF Translado GNSS", str(exc))
            return

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
            apply_layers_to_canvas(
                self.map_canvas,
                self.visual_layers,
                self.selected_crs,
            )
            self._update_map_scale(self.map_canvas.scale())
            self.map_host._position_overlays()
            self._activate_map_pan()
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

        current_df = self._current_visualization_df()

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


    def _precision_filter_help_text(self) -> str:
        return (
            "Filtro de precisão posicional conforme MTGIR 2ª Edição, itens 1.4.3 e 1.4.4.\n\n"
            "Item 1.4.3: σP = √(σφ² + σλ²). No padrão TXT: σP = √(DP E² + DP N²).\n\n"
            "Item 1.4.4: limite artificial ≤ 0,50 m; limite natural ≤ 3,00 m; "
            "limite inacessível ≤ 7,50 m. A tolerância admitida será de no máximo três vezes "
            "o valor da precisão para o tipo de limite."
        )

    def _show_precision_filter_help(self):
        QMessageBox.information(self, "Filtro de precisão posicional", self._precision_filter_help_text())

    def _select_filter_limit(self, limit_type: str):
        self.filter_limit_type = limit_type
        self.btn_limit_artificial.setChecked(limit_type == "artificial")
        self.btn_limit_natural.setChecked(limit_type == "natural")
        self.btn_limit_inaccessible.setChecked(limit_type == "inaccessible")
        self._on_filter_limit_changed()

    def _on_filter_limit_changed(self):
        values = {
            "artificial": 0.500,
            "natural": 3.000,
            "inaccessible": 7.500,
        }
        limit_value = values.get(self.filter_limit_type, 0.500)
        self.filter_limit_value.setValue(limit_value)
        self.filter_tolerance_label.setText(
            f"(3× o limite selecionado: {format_pt(limit_value * 3, 3)} m)"
        )


    def _filter_limit_label(self) -> str:
        labels = {
            "artificial": "Limite artificial",
            "natural": "Limite natural",
            "inaccessible": "Limite inacessível",
        }
        return labels.get(self.filter_limit_type, "Limite artificial")


    def update_precision_filter_mode(self):
        active = self.chk_precision_filter.isChecked()
        self.filter_info_card.setVisible(active)
        self.filter_controls.setVisible(active)

        self.btn_view_dashboard.setVisible(not active)
        self.btn_view_dashboard.setEnabled(not active)
        self.btn_view_map.setVisible(not active)
        self.btn_view_map.setEnabled(not active)

        for widget in [
            self.grp_crs,
            self.grp_base,
            self.grp_ppp,
            self.chk_variance,
            self.btn_variance_help,
            self.btn_calc,
            self.btn_report,
            self.btn_base,
            self.btn_pdf,
            self.btn_export,
        ]:
            widget.setEnabled(not active)

        self.btn_layers.setEnabled(True)

        if active:
            self.chk_variance.setChecked(False)
            self.chk_visualizacao.setChecked(False)
            self.delta_label.setText(self._delta_html("-", "-", "-"))
            self.show_table_view()
            self._on_filter_limit_changed()
            if self.df is not None:
                self.apply_precision_filter(show_message=False)
        else:
            self.btn_view_map.setVisible(True)
            self.btn_view_map.setEnabled(True)
            self.btn_view_dashboard.setVisible(True)
            self.btn_view_dashboard.setEnabled(True)
            self.clear_precision_filter(show_message=False)
            self.update_mode_states()


    def apply_precision_filter(self, show_message: bool = True):
        if self.df is None or self.df.empty:
            if show_message:
                QMessageBox.warning(self, "MRF Translado GNSS", "Importe um TXT de pontos antes de aplicar o filtro.")
            return
        missing = [col for col in ["DP E", "DP N"] if col not in self.df.columns]
        if missing:
            QMessageBox.warning(self, "MRF Translado GNSS", "O TXT precisa conter as colunas DP E e DP N.")
            return
        df = self.df.copy()
        for col in ["DP E", "DP N", "DP U"]:
            if col in df.columns:
                df[col] = df[col].astype(str).str.replace(",", ".", regex=False).astype(float)
        limit_value = float(self.filter_limit_value.value())
        df["Precisão Planimétrica"] = (df["DP E"].astype(float) ** 2 + df["DP N"].astype(float) ** 2) ** 0.5
        df["Limite Aplicado"] = limit_value
        df["Tipo de Limite"] = self._filter_limit_label()
        df["Status Filtro"] = df["Precisão Planimétrica"].apply(
            lambda value: "Aprovado"
            if float(value) <= limit_value
            else "Reprovado"
        )
        df["Motivo"] = df.apply(
            lambda row: "-"
            if row["Status Filtro"] == "Aprovado"
            else (
                f"σP {format_pt(float(row['Precisão Planimétrica']), 3)} m "
                f"> {format_pt(limit_value, 3)} m"
            ),
            axis=1,
        )
        self.filter_df = df
        self.filter_approved_df = df[df["Status Filtro"] == "Aprovado"].copy()
        self.populate_table(df)
        self._update_export_radio_state()
        if show_message:
            approved = len(self.filter_approved_df)
            total = len(df)
            self.iface.messageBar().pushSuccess(
                "MRF Translado GNSS",
                f"Filtro aplicado: {approved} aprovados e "
                f"{total - approved} reprovados.",
            )

    def clear_precision_filter(self, show_message: bool = True):
        self.filter_df = None
        self.filter_approved_df = None
        if self.df is not None:
            self.populate_table(self.df)
        if show_message:
            self.iface.messageBar().pushSuccess("MRF Translado GNSS", "Filtro removido.")


    def _has_solution_classification(self) -> bool:
        if self.filter_df is None:
            return False

        possible_columns = [
            "Status",
            "Solução",
            "Solution",
            "Qualidade",
            "Fix",
            "Tipo",
        ]

        for column in possible_columns:
            if column in self.filter_df.columns:
                values = (
                    self.filter_df[column]
                    .astype(str)
                    .str.upper()
                    .tolist()
                )
                for value in values:
                    if any(
                        token in value
                        for token in ["FIX", "FLOAT", "RTK"]
                    ):
                        return True
        return False

    def _update_export_button_state(self):
        checked = any(
            [
                self.radio_export_fixed.isChecked(),
                self.radio_export_fixed_float.isChecked(),
                self.radio_export_all.isChecked(),
            ]
        )
        self.btn_export_precision_filter.setEnabled(checked)

    def _update_export_radio_state(self):
        has_classification = self._has_solution_classification()

        self.radio_export_fixed.setEnabled(has_classification)
        self.radio_export_fixed_float.setEnabled(has_classification)

        if has_classification:
            self.export_gnss_warning.setVisible(False)
        else:
            self.radio_export_fixed.setChecked(False)
            self.radio_export_fixed_float.setChecked(False)
            self.radio_export_fixed.setEnabled(False)
            self.radio_export_fixed_float.setEnabled(False)
            self.radio_export_all.setChecked(True)
            self.export_gnss_warning.setText(
                "TXT não possui classificação de solução GNSS"
            )
            self.export_gnss_warning.setVisible(True)

        self._update_export_button_state()

    def export_precision_filtered_txt(self):
        if self.filter_df is None or self.filter_df.empty:
            QMessageBox.warning(
                self,
                "MRF Translado GNSS",
                "Não há pontos aprovados para exportar.",
            )
            return

        approved_df = self.filter_df[
            self.filter_df["Status Filtro"] == "Aprovado"
        ].copy()

        if approved_df.empty:
            QMessageBox.warning(
                self,
                "MRF Translado GNSS",
                "Não há pontos aprovados para exportar.",
            )
            return

        export_df = approved_df.copy()

        possible_columns = [
            "Status",
            "Solução",
            "Solution",
            "Qualidade",
            "Fix",
            "Tipo",
        ]

        status_column = None
        for column in possible_columns:
            if column in export_df.columns:
                status_column = column
                break

        if status_column:
            status_series = export_df[status_column].astype(str).str.upper()

            if self.radio_export_fixed.isChecked():
                export_df = export_df[
                    status_series.str.contains("FIX", na=False)
                ]

            elif self.radio_export_fixed_float.isChecked():
                export_df = export_df[
                    (
                        status_series.str.contains("FIX", na=False)
                    )
                    | (
                        status_series.str.contains("FIXO", na=False)
                    )
                    | (
                        status_series.str.contains("FLOAT", na=False)
                    )
                    | (
                        status_series.str.contains(
                            "FLUTUANTE",
                            na=False,
                        )
                    )
                ]

        if export_df.empty:
            QMessageBox.warning(
                self,
                "MRF Translado GNSS",
                "Nenhum ponto corresponde ao filtro selecionado.",
            )
            return

        added_columns = [
            "Precisão Planimétrica",
            "Limite Aplicado",
            "Tipo de Limite",
            "Status Filtro",
            "Motivo",
        ]

        if self.filter_original_columns:
            export_columns = [
                col for col in self.filter_original_columns
                if col in export_df.columns
            ]
        else:
            export_columns = [
                col for col in export_df.columns
                if col not in added_columns
            ]

        export_df = export_df[export_columns].copy()

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Exportar TXT aprovado",
            "pontos_aprovados.txt",
            "Arquivo TXT (*.txt)",
        )

        if not path:
            return

        export_df.to_csv(
            path,
            sep="\t",
            index=False,
            encoding="utf-8-sig",
        )

        self.iface.messageBar().pushSuccess(
            "MRF Translado GNSS",
            "TXT exportado com sucesso.",
        )


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

        if hasattr(self, "chk_precision_filter") and self.chk_precision_filter.isChecked():
            self.update_precision_filter_mode()

    def update_mode_states(self):
        if hasattr(self, "chk_precision_filter") and self.chk_precision_filter.isChecked():
            return

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
            self.filter_original_columns = list(self.df.columns)
            self.coord_line = (
                "Sistema de Coordenadas: "
                f"{self.selected_crs.description()} ({self.selected_crs.authid()})"
            )
            if hasattr(self, "chk_precision_filter") and self.chk_precision_filter.isChecked():
                self.apply_precision_filter(show_message=False)
                self.iface.messageBar().pushSuccess(
                    "MRF Translado GNSS",
                    f"{len(self.df)} pontos importados e filtrados.",
                )
            else:
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
                self.base_source_type = "ppp"
                self.sigef_denominacao = ""
                self.sigef_credenciado = ""
                self.sigef_vertice_base = ""
            else:
                vertices = parse_memorial_sigef_pdf(path)
                dlg = VertexSelectorDialog(vertices, self)
                if not dlg.exec_():
                    return
                selected_vertex = dlg.selected_vertex
                ppp = memorial_vertex_to_pppdata(selected_vertex, source_pdf=path)
                self.base_source_type = "memorial"
                self.sigef_denominacao = selected_vertex.get("denominacao", "Não informado")
                self.sigef_credenciado = selected_vertex.get("credenciado", "Não informado")
                self.sigef_vertice_base = selected_vertex.get("codigo", "Não informado")
                self.sigef_sistema_geodesico = selected_vertex.get("sistema_geodesico", "SIRGAS 2000")
                self.sigef_documento_rt = selected_vertex.get("documento_rt", "")
                self.sigef_data_certificacao = selected_vertex.get("data_certificacao", "")
                self.sigef_status_certificacao = selected_vertex.get("status_certificacao", "")

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
        if self.radio_manual.isChecked():
            self.base_source_type = "manual"
        elif self.ppp_data and self.ppp_data.source_kind == "PPP_IBGE":
            self.base_source_type = "ppp"
        elif self.ppp_data and self.ppp_data.source_kind == "BASE_CONHECIDA":
            self.base_source_type = "memorial"

        use_variance = self.chk_variance.isChecked()
        sigma_e = None
        if use_variance and self.ppp_sigma_e.text().strip():
            sigma_e = float(
                self.ppp_sigma_e.text().replace(",", ".")
            )
        sigma_n = None
        if use_variance and self.ppp_sigma_n.text().strip():
            sigma_n = float(
                self.ppp_sigma_n.text().replace(",", ".")
            )
        sigma_h = None
        if use_variance and self.ppp_sigma_h.text().strip():
            sigma_h = float(
                self.ppp_sigma_h.text().replace(",", ".")
            )

        imported = self.ppp_data

        return PPPData(
            float(self.ppp_e.text().replace(",", ".")),
            float(self.ppp_n.text().replace(",", ".")),
            float(self.ppp_h.text().replace(",", ".")),
            sigma_e,
            sigma_n,
            sigma_h,
            imported.source_pdf if imported else None,
            imported.source_kind if imported else None,
            imported.source_code if imported else None,
            getattr(imported, "session_start", None) if imported else None,
            getattr(imported, "session_end", None) if imported else None,
            getattr(imported, "reference_epoch", None) if imported else None,
            getattr(imported, "orbit_type", None) if imported else None,
            getattr(imported, "processed_frequency", None) if imported else None,
            getattr(imported, "normal_height", None) if imported else None,
            getattr(imported, "geoid_model", None) if imported else None,
            getattr(imported, "geoid_factor", None) if imported else None,
            getattr(imported, "geoid_uncertainty", None) if imported else None,
            getattr(imported, "lat_20004", None) if imported else None,
            getattr(imported, "lon_20004", None) if imported else None,
            getattr(imported, "alt_20004", None) if imported else None,
            getattr(imported, "utm_n_20004", None) if imported else None,
            getattr(imported, "utm_e_20004", None) if imported else None,
            getattr(imported, "lat_survey", None) if imported else None,
            getattr(imported, "lon_survey", None) if imported else None,
            getattr(imported, "alt_survey", None) if imported else None,
            getattr(imported, "utm_n_survey", None) if imported else None,
            getattr(imported, "utm_e_survey", None) if imported else None,
            getattr(imported, "dn_epoch", None) if imported else None,
            getattr(imported, "de_epoch", None) if imported else None,
            getattr(imported, "dh_epoch", None) if imported else None,
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
            if hasattr(self, "dashboard_page") and self.view_stack.currentWidget() == self.dashboard_page:
                self.update_dashboard()
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
                item = QTableWidgetItem(str(value))
                if "Status Filtro" in cols:
                    try:
                        status_value = str(df.iloc[row_index][cols.index("Status Filtro")]).upper()
                    except Exception:
                        status_value = ""
                    if "APROVADO" in status_value:
                        item.setBackground(QBrush(QColor(22, 101, 52)))
                        item.setForeground(QBrush(QColor("#f0f6fc")))
                    elif "REPROVADO" in status_value:
                        item.setBackground(QBrush(QColor(127, 29, 29)))
                        item.setForeground(QBrush(QColor("#f0f6fc")))
                self.table.setItem(row_index, col_index, item)
        self.table.resizeColumnsToContents()

    def infer_epsg(self):
        if self.selected_crs and self.selected_crs.isValid():
            return self.selected_crs.authid()
        return "EPSG:31979"

    def create_layers(self):
        if (
            hasattr(self, "chk_precision_filter")
            and self.chk_precision_filter.isChecked()
        ):
            if self.filter_df is None:
                self.apply_precision_filter(show_message=False)
            if self.filter_df is None or self.filter_df.empty:
                QMessageBox.warning(
                    self,
                    "MRF Translado GNSS",
                    "Aplique o filtro antes de criar as camadas.",
                )
                return
            if not self.is_valid_utm_crs(self.selected_crs):
                QMessageBox.warning(
                    self,
                    "MRF Translado GNSS",
                    "Selecione um CRS projetado UTM em metros antes de criar camadas.",
                )
                return
            try:
                self._build_filter_map_layers(add_to_project=True)
                self.iface.messageBar().pushSuccess(
                    "MRF Translado GNSS",
                    "Camadas do filtro criadas no QGIS.",
                )
            except Exception as exc:
                QMessageBox.critical(self, "MRF Translado GNSS", str(exc))
            return

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
            feature.setGeometry(
                QgsGeometry.fromPointXY(
                    QgsPointXY(
                        float(row["Este Ajustado"]),
                        float(row["Norte Ajustado"]),
                    )
                )
            )
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
        if hasattr(self, "dash_generated"):
            self.dash_generated.setText("Dashboard ainda não gerado.")

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
