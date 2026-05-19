from __future__ import annotations

from typing import Dict, List, Optional

from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsPointXY,
    QgsRasterLayer,
    QgsRectangle,
    QgsSingleSymbolRenderer,
    QgsVectorLayer,
)
from qgis.gui import QgsMapCanvas

SATELLITE_PROVIDERS: Dict[str, str] = {
    "Esri": (
        "type=xyz&url=https://server.arcgisonline.com/ArcGIS/rest/services/"
        "World_Imagery/MapServer/tile/{z}/{y}/{x}"
    ),
    "Google": "type=xyz&url=https://mt1.google.com/vt/lyrs%3Ds%26x%3D{x}%26y%3D{y}%26z%3D{z}",
    # Bing usa quadkey. Alguns builds do QGIS não expandem {q}; fica como opção experimental.
    "Bing": "type=xyz&url=https://ecn.t3.tiles.virtualearth.net/tiles/a{q}.jpeg%3Fg%3D1",
}


def _make_memory_layer(geometry: str, name: str, crs_authid: str) -> QgsVectorLayer:
    layer = QgsVectorLayer(f"{geometry}?crs={crs_authid}", name, "memory")
    provider = layer.dataProvider()
    provider.addAttributes(
        [
            QgsField("Nome", QVariant.String),
            QgsField("Tipo", QVariant.String),
            QgsField("Status", QVariant.String),
        ]
    )
    layer.updateFields()
    return layer


def _style_point_layer(layer: QgsVectorLayer, color: str, size: float, shape: str = "circle") -> None:
    symbol = QgsMarkerSymbol.createSimple(
        {
            "name": shape,
            "color": color,
            "outline_color": "white",
            "outline_width": "0.35",
            "size": str(size),
        }
    )
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))


def _style_line_layer(layer: QgsVectorLayer, color: str = "#1f4e79", width: float = 0.65) -> None:
    symbol = QgsLineSymbol.createSimple(
        {
            "color": color,
            "width": str(width),
            "line_style": "solid",
        }
    )
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))


def _add_point(layer: QgsVectorLayer, name: str, tipo: str, status: str, east: float, north: float) -> None:
    feature = QgsFeature(layer.fields())
    feature["Nome"] = str(name)
    feature["Tipo"] = str(tipo)
    feature["Status"] = str(status)
    feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(float(east), float(north))))
    layer.dataProvider().addFeature(feature)


def _add_line(
    layer: QgsVectorLayer,
    name: str,
    tipo: str,
    status: str,
    east_start: float,
    north_start: float,
    east_end: float,
    north_end: float,
) -> None:
    feature = QgsFeature(layer.fields())
    feature["Nome"] = str(name)
    feature["Tipo"] = str(tipo)
    feature["Status"] = str(status)
    feature.setGeometry(
        QgsGeometry.fromPolylineXY(
            [
                QgsPointXY(float(east_start), float(north_start)),
                QgsPointXY(float(east_end), float(north_end)),
            ]
        )
    )
    layer.dataProvider().addFeature(feature)


def create_satellite_layer(provider_name: str) -> Optional[QgsRasterLayer]:
    uri = SATELLITE_PROVIDERS.get(provider_name)
    if not uri:
        return None

    layer = QgsRasterLayer(uri, f"Satélite - {provider_name}", "wms")
    if not layer.isValid():
        return None
    return layer


def build_visualization_layers(
    original_df,
    adjusted_df,
    known_base,
    crs: QgsCoordinateReferenceSystem,
    provider_name: str,
) -> List:
    if crs is None or not crs.isValid():
        raise ValueError("CRS inválido para visualização.")

    crs_authid = crs.authid()
    satellite = create_satellite_layer(provider_name)

    base_layer = _make_memory_layer("Point", "MRF - Base PPP/Conhecida", crs_authid)
    original_layer = _make_memory_layer("Point", "MRF - Pontos Originais", crs_authid)
    adjusted_layer = _make_memory_layer("Point", "MRF - Pontos Ajustados", crs_authid)
    vector_layer = _make_memory_layer("LineString", "MRF - Vetores Base para Ajustados", crs_authid)

    base_name = "BASE PPP/CONHECIDA"
    if getattr(known_base, "source_code", None):
        base_name = str(known_base.source_code)
    elif getattr(known_base, "source_kind", None) == "PPP_IBGE":
        base_name = "BASE PPP-IBGE"

    _add_point(base_layer, base_name, "Base", "Conhecida", known_base.east, known_base.north)

    for _, row in original_df.iterrows():
        _add_point(
            original_layer,
            row.get("Nome", ""),
            "Original",
            row.get("Status", ""),
            row["Este"],
            row["Norte"],
        )

    for _, row in adjusted_df.iterrows():
        _add_point(
            adjusted_layer,
            row.get("Nome", ""),
            "Ajustado",
            row.get("Status", ""),
            row["Este Ajustado"],
            row["Norte Ajustado"],
        )
        _add_line(
            vector_layer,
            row.get("Nome", ""),
            "Vetor Base-Ajustado",
            row.get("Status", ""),
            known_base.east,
            known_base.north,
            row["Este Ajustado"],
            row["Norte Ajustado"],
        )

    for layer in [base_layer, original_layer, adjusted_layer, vector_layer]:
        layer.updateExtents()

    # Símbolos menores para não poluir a conferência visual.
    _style_point_layer(base_layer, "#d62828", 2.0, "triangle")
    _style_point_layer(original_layer, "#9aa3ad", 1.2, "circle")
    _style_point_layer(adjusted_layer, "#2fb344", 1.35, "circle")
    _style_line_layer(vector_layer, "#1f4e79", 0.55)

    # QgsMapCanvas desenha respeitando a ordem da lista. Mantemos o raster no fim
    # para evitar que a imagem de satélite cubra pontos e vetores em alguns builds.
    layers = [base_layer, adjusted_layer, original_layer, vector_layer]
    if satellite is not None:
        layers.append(satellite)
    return layers


def _combined_extent(layers: List) -> Optional[QgsRectangle]:
    extent: Optional[QgsRectangle] = None
    for layer in layers:
        if not hasattr(layer, "extent"):
            continue
        layer_extent = layer.extent()
        if layer_extent is None or layer_extent.isNull():
            continue
        if extent is None:
            extent = QgsRectangle(layer_extent)
        else:
            extent.combineExtentWith(layer_extent)
    return extent


def apply_layers_to_canvas(canvas: QgsMapCanvas, layers: List, crs: QgsCoordinateReferenceSystem) -> None:
    canvas.setDestinationCrs(crs)
    canvas.setCanvasColor(QColor("white"))
    canvas.setLayers(layers)

    extent = _combined_extent([layer for layer in layers if isinstance(layer, QgsVectorLayer)])
    if extent is not None and not extent.isNull():
        extent.scale(1.25)
        canvas.setExtent(extent)
    canvas.refresh()
