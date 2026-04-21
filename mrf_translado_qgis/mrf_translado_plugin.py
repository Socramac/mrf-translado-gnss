from __future__ import annotations
import os
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from .mrf_translado_dialog import MRFTransladoDialog

class MRFTransladoPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.dialog = None

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, "icons", "plugin_icon.svg")
        self.action = QAction(QIcon(icon_path), "MRF Translado GNSS", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("MRF Translado GNSS", self.action)

    def unload(self):
        if self.action:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginMenu("MRF Translado GNSS", self.action)
        self.dialog = None

    def _dialog_closed(self, *args):
        self.dialog = None

    def run(self):
        # Sempre cria uma nova janela para evitar estado residual visual
        self.dialog = MRFTransladoDialog(self.iface, self.iface.mainWindow())
        self.dialog.destroyed.connect(self._dialog_closed)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
