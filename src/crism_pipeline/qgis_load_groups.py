# -*- coding: utf-8 -*-
"""Carga GeoTIFF CRISM en grupos QGIS (TRU, CAR, CR2, índices, …).

Uso (Python de QGIS, no el venv del pipeline):
  python-qgis-ltr.bat qgis_load_groups.py --manifest groups_manifest.json

Desde la consola de QGIS (proyecto ya abierto):
  exec(open(r"RUTA\\qgis_load_groups.py", encoding="utf-8").read())
  load_from_manifest(r"RUTA\\groups_manifest.json", into_current=True)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path


KIND_FOLDERS = {
    "browse": "Browse",
    "index": "Índices",
    "detection": "Detección",
    "classification": "Clasificación",
}

BROWSE_ORDER = (
    "TRU", "VNA", "FEM", "FM2", "TAN", "IRA", "FAL", "MAF", "PHY",
    "HYS", "HYD", "CHL", "CAR", "CR2", "ICE",
)


def _init_qgis():
    from qgis.core import QgsApplication

    prefix = os.environ.get("QGIS_PREFIX_PATH")
    if not prefix:
        exe = Path(sys.executable)
        for parent in exe.parents:
            for name in ("qgis", "qgis-ltr"):
                cand = parent / "apps" / name
                if cand.is_dir():
                    prefix = str(cand)
                    break
            if prefix:
                break
    if prefix:
        QgsApplication.setPrefixPath(prefix, True)
    qgs = QgsApplication([], False)
    qgs.initQgis()
    return qgs


def _gui_iface():
    try:
        from qgis.utils import iface

        return iface
    except Exception:
        return None


def _style_layer(layer, kind: str, class_names: list):
    from qgis.core import (
        QgsContrastEnhancement,
        QgsMultiBandColorRenderer,
        QgsPalettedRasterRenderer,
        QgsSingleBandGrayRenderer,
    )
    from qgis.PyQt.QtGui import QColor

    if kind == "browse" and layer.bandCount() >= 3:
        layer.setRenderer(QgsMultiBandColorRenderer(layer.dataProvider(), 1, 2, 3))
        layer.triggerRepaint()
        return

    if kind == "detection":
        classes = [
            QgsPalettedRasterRenderer.Class(0, QColor("#f4f1ea"), "Ausente"),
            QgsPalettedRasterRenderer.Class(1, QColor("#c0392b"), "Presente"),
            QgsPalettedRasterRenderer.Class(255, QColor("#c0392b"), "Presente"),
        ]
        layer.setRenderer(QgsPalettedRasterRenderer(layer.dataProvider(), 1, classes))
        layer.triggerRepaint()
        return

    if kind == "classification":
        tab = [
            "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
            "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
        ]
        stats = layer.dataProvider().bandStatistics(1)
        n = max(int(stats.maximum) + 1 if stats.maximum == stats.maximum else 1, len(class_names), 1)
        classes = []
        for i in range(n):
            name = class_names[i] if i < len(class_names) else f"Cluster {i}"
            classes.append(QgsPalettedRasterRenderer.Class(i, QColor(tab[i % len(tab)]), str(name)))
        layer.setRenderer(QgsPalettedRasterRenderer(layer.dataProvider(), 1, classes))
        layer.triggerRepaint()
        return

    renderer = QgsSingleBandGrayRenderer(layer.dataProvider(), 1)
    enhancement = QgsContrastEnhancement(layer.dataProvider().dataType(1))
    enhancement.setContrastEnhancementAlgorithm(QgsContrastEnhancement.StretchToMinimumMaximum)
    stats = layer.dataProvider().bandStatistics(1)
    enhancement.setMinimumValue(stats.minimum)
    enhancement.setMaximumValue(stats.maximum)
    renderer.setContrastEnhancement(enhancement)
    layer.setRenderer(renderer)
    layer.triggerRepaint()


def _group_sort_key(name: str) -> tuple:
    upper = name.upper()
    if upper in BROWSE_ORDER:
        return (0, BROWSE_ORDER.index(upper))
    return (1, name.lower())


def _ensure_group(parent, name: str):
    for child in parent.children():
        if child.nodeType() == child.NodeGroup and child.name() == name:
            return child
    return parent.addGroup(name)


def load_from_manifest(manifest_path, into_current: bool = False, project_path=None):
    """Añade capas agrupadas al proyecto QGIS actual o a uno nuevo."""
    from qgis.core import QgsProject, QgsRasterLayer

    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    items = manifest.get("items") or []
    if not items:
        print("Manifest vacío: no hay GeoTIFF para cargar.")
        return 0

    kinds = {item.get("kind", "browse") for item in items}
    nest = bool(manifest.get("nest_kinds", len(kinds) > 1))
    visible = (manifest.get("visible_group") or "TRU").upper()

    project = QgsProject.instance()
    if not into_current:
        project.clear()

    root = project.layerTreeRoot()
    first_layer = None
    loaded = 0

    buckets: dict[tuple[str, str], list] = defaultdict(list)
    for item in items:
        kind = item.get("kind") or "browse"
        group_name = str(item.get("map_name") or "otros")
        buckets[(kind, group_name)].append(item)

    kind_order = [k for k in KIND_FOLDERS if k in kinds]
    for extra in kinds:
        if extra not in kind_order:
            kind_order.append(extra)

    for kind in kind_order:
        names = sorted({g for (k, g) in buckets if k == kind}, key=_group_sort_key)
        parent = root
        if nest:
            parent = _ensure_group(root, KIND_FOLDERS.get(kind, kind))
            parent.setExpanded(kind == "browse")
            parent.setItemVisibilityChecked(True)

        for gname in names:
            grp = _ensure_group(parent, gname)
            grp.setExpanded(False)
            show = gname.upper() == visible and kind == "browse"
            grp.setItemVisibilityChecked(show)

            for item in sorted(buckets[(kind, gname)], key=lambda x: x.get("layer_name") or x.get("product_id") or ""):
                tif = item["tif"]
                name = item.get("layer_name") or Path(tif).stem
                layer = QgsRasterLayer(tif, name)
                if not layer.isValid():
                    print(f"Capa inválida: {tif}", file=sys.stderr)
                    continue
                _style_layer(layer, kind, item.get("class_names") or [])
                project.addMapLayer(layer, False)
                grp.addLayer(layer)
                if first_layer is None:
                    first_layer = layer
                loaded += 1

    if first_layer is not None:
        project.setCrs(first_layer.crs())
        iface = _gui_iface()
        if iface is not None:
            iface.mapCanvas().setExtent(first_layer.extent())
            iface.mapCanvas().refresh()

    dest = project_path or manifest.get("project_path")
    if dest and not into_current:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        project.write(str(dest))
        print(f"Proyecto QGIS: {dest}")

    print(f"Capas cargadas: {loaded} en {len(buckets)} grupos")
    return loaded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cargar GeoTIFF CRISM agrupados en QGIS")
    parser.add_argument("--manifest", required=True, help="JSON con la lista de GeoTIFF")
    parser.add_argument("--out", default=None, help="Ruta .qgz (default: project_path del manifest)")
    parser.add_argument(
        "--into-current",
        action="store_true",
        help="Añadir al proyecto abierto (consola QGIS) en lugar de crear uno nuevo",
    )
    args = parser.parse_args(argv)

    iface = _gui_iface()
    into_current = bool(args.into_current or (iface is not None and args.out is None))
    qgs = None
    if iface is None:
        qgs = _init_qgis()
    try:
        load_from_manifest(args.manifest, into_current=into_current, project_path=args.out)
    finally:
        if qgs is not None:
            qgs.exitQgis()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
