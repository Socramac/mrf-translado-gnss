import os
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .mrf_translado_dialog import MRFTransladoDialog


class MRFTransladoPlugin:

    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dialog = None

    def initGui(self):
        icon_path = os.path.join(
            os.path.dirname(__file__),
            "icons",
            "plugin_icon.svg",
        )

        self.action = QAction(
            QIcon(icon_path),
            "MRF Translado GNSS",
            self.iface.mainWindow(),
        )
        self.action.triggered.connect(self.run)

        self.iface.addPluginToMenu("&MRF Translado", self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.action is not None:
            self.iface.removePluginMenu("&MRF Translado", self.action)
            self.iface.removeToolBarIcon(self.action)
        if self.dialog is not None:
            self.dialog.close()
            self.dialog.deleteLater()
            self.dialog = None

    def run(self):
        if self.dialog is not None:
            try:
                self.dialog.close()
                self.dialog.deleteLater()
            except Exception:
                pass
            self.dialog = None

        self.dialog = MRFTransladoDialog(self.iface, self.iface.mainWindow())
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
