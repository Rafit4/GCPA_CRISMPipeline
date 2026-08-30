"""Exportación opcional de láminas con el compositor de impresión de QGIS (PyQGIS)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .layout import MapSpec, discover_maps

_QGIS_SCRIPT_NAME = "qgis_export_layouts.py"


def find_qgis_python() -> Path | None:
    """Localiza python-qgis.bat / python-qgis (instalación de QGIS Desktop)."""
    for env_key in ("QGIS_PYTHON", "PYTHON_QGIS"):
        env = os.environ.get(env_key)
        if env and Path(env).is_file():
            return Path(env)

    names = ("python-qgis.bat", "python-qgis-ltr.bat", "python-qgis")
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found)

    roots: list[Path] = []
    for key in ("ProgramFiles", "ProgramFiles(x86)", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        val = os.environ.get(key)
        if val:
            roots.append(Path(val))
    roots.extend(
        [
            Path(r"C:\Program Files"),
            Path(r"C:\Program Files (x86)"),
            Path(r"C:\OSGeo4W64"),
            Path(r"C:\OSGeo4W"),
        ]
    )
    found_bats: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        resolved = root.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        candidates = []
        if "OSGeo" in root.name or root.name.upper().startswith("QGIS"):
            candidates.append(root)
        candidates.extend(sorted(root.glob("QGIS*")))
        for install in candidates:
            for name in names:
                bat = install / "bin" / name
                if bat.is_file():
                    found_bats.append(bat)
    if not found_bats:
        return None
    return sorted(found_bats, key=lambda p: p.as_posix())[-1]


def _manifest_from_specs(specs: list[MapSpec], out_dir: Path, paper: str, dpi: int, formats: list[str]) -> dict:
    return {
        "out_dir": str(out_dir.resolve()),
        "paper": paper.upper(),
        "dpi": int(dpi),
        "formats": formats,
        "project_path": str((out_dir / "crism_layouts.qgz").resolve()),
        "items": [
            {
                "tif": str(spec.path.resolve()),
                "kind": spec.kind,
                "title": spec.title,
                "subtitle": spec.subtitle,
                "product_id": spec.product_id,
                "map_name": spec.map_name,
                "class_names": spec.class_names,
            }
            for spec in specs
        ],
    }


def write_qgis_exporter(out_dir: Path) -> Path:
    """Escribe el script PyQGIS autónomo junto a las láminas."""
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / _QGIS_SCRIPT_NAME
    dest.write_text(_PYQGIS_SCRIPT, encoding="utf-8")
    return dest


def export_qgis_layouts(
    input_path: Path,
    out_dir: Path | None = None,
    *,
    kinds: list[str] | None = None,
    paper: str = "A4",
    dpi: int = 300,
    formats: list[str] | None = None,
    run: bool = True,
) -> list[Path]:
    """Genera un proyecto/script QGIS y, si hay QGIS, exporta PDF/PNG del compositor."""
    specs = discover_maps(input_path, kinds=kinds)
    if not specs:
        raise FileNotFoundError(
            f"No se encontraron GeoTIFF de mapas/detección/clasificación en {input_path}"
        )
    out_dir = Path(out_dir) if out_dir else Path(input_path) / "layouts" / "qgis"
    out_dir.mkdir(parents=True, exist_ok=True)
    formats = [f.lower().lstrip(".") for f in (formats or ["pdf", "png"])]

    script = write_qgis_exporter(out_dir)
    manifest_path = out_dir / "qgis_layout_manifest.json"
    manifest = _manifest_from_specs(specs, out_dir, paper, dpi, formats)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    written = [script, manifest_path]
    qgis_py = find_qgis_python()
    qgis_bat = str(qgis_py) if qgis_py else r"C:\Program Files\QGIS 3.34.0\bin\python-qgis.bat"
    hint = out_dir / "LEEME_QGIS.txt"
    hint.write_text(
        "Láminas QGIS (compositor de impresión)\n"
        "=====================================\n\n"
        "1. Instala QGIS 3.34 LTR o 3.x (https://qgis.org/download/).\n"
        "2. Ejecuta, en PowerShell:\n\n"
        f'   & "{qgis_bat}" "{script}" --manifest "{manifest_path}"\n\n'
        "O define la variable QGIS_PYTHON con la ruta a python-qgis.bat.\n"
        "El script crea crism_layouts.qgz (capas + layouts) y exporta PDF/PNG.\n",
        encoding="utf-8",
    )
    written.append(hint)

    if not run:
        return written
    if qgis_py is None:
        return written

    cmd = [str(qgis_py), str(script), "--manifest", str(manifest_path)]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    log_path = out_dir / "qgis_export.log"
    log_path.write_text(
        f"$ {' '.join(cmd)}\n\nstdout:\n{result.stdout}\n\nstderr:\n{result.stderr}\n"
        f"exit={result.returncode}\n",
        encoding="utf-8",
    )
    written.append(log_path)
    if result.returncode != 0:
        raise RuntimeError(
            "QGIS no pudo exportar las láminas. "
            f"Revisa {log_path}. Código {result.returncode}."
        )
    written.extend(sorted(out_dir.glob("*_qgis.pdf")))
    written.extend(sorted(out_dir.glob("*_qgis.png")))
    qgz = out_dir / "crism_layouts.qgz"
    if qgz.is_file():
        written.append(qgz)
    return written


# Script autónomo: se ejecuta con el Python de QGIS, no con el venv del pipeline.
_PYQGIS_SCRIPT = r'''# -*- coding: utf-8 -*-
"""Exporta layouts de impresión CRISM. Uso: python-qgis.bat este_script.py --manifest manifest.json"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _init_qgis():
    from qgis.core import QgsApplication

    prefix = os.environ.get("QGIS_PREFIX_PATH")
    if not prefix:
        exe = Path(sys.executable)
        # .../apps/qgis/python o .../bin/python-qgis
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


def _paper_mm(name: str) -> tuple[float, float]:
    papers = {"A4": (297.0, 210.0), "A3": (420.0, 297.0), "LETTER": (279.4, 215.9)}
    return papers.get(name.upper(), papers["A4"])


def _style_layer(layer, kind: str, class_names: list):
    from qgis.core import (
        QgsColorRampShader,
        QgsMultiBandColorRenderer,
        QgsPalettedRasterRenderer,
        QgsRasterShader,
        QgsSingleBandGrayRenderer,
        QgsSingleBandPseudoColorRenderer,
        QgsContrastEnhancement,
    )
    from qgis.PyQt.QtGui import QColor

    if kind == "browse" and layer.bandCount() >= 3:
        renderer = QgsMultiBandColorRenderer(layer.dataProvider(), 1, 2, 3)
        layer.setRenderer(renderer)
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
            classes.append(
                QgsPalettedRasterRenderer.Class(i, QColor(tab[i % len(tab)]), str(name))
            )
        layer.setRenderer(QgsPalettedRasterRenderer(layer.dataProvider(), 1, classes))
        layer.triggerRepaint()
        return

    renderer = QgsSingleBandGrayRenderer(layer.dataProvider(), 1)
    enhancement = QgsContrastEnhancement(layer.dataProvider().dataType(1))
    enhancement.setContrastEnhancementAlgorithm(
        QgsContrastEnhancement.StretchToMinimumMaximum
    )
    stats = layer.dataProvider().bandStatistics(1)
    enhancement.setMinimumValue(stats.minimum)
    enhancement.setMaximumValue(stats.maximum)
    renderer.setContrastEnhancement(enhancement)
    layer.setRenderer(renderer)
    layer.triggerRepaint()


def _north_svg() -> str:
    from qgis.core import QgsApplication

    for folder in QgsApplication.svgPaths():
        folder = Path(folder)
        for name in ("north_arrow/north_arrow.svg", "arrows/NorthArrow_02.svg", "arrows/NorthArrow_01.svg"):
            cand = folder / name
            if cand.is_file():
                return str(cand)
        matches = list(folder.rglob("*orth*.svg"))
        if matches:
            return str(matches[0])
    return ""


def _build_layout(project, layer, item: dict, paper: str):
    from qgis.core import (
        QgsLayoutItemLabel,
        QgsLayoutItemLegend,
        QgsLayoutItemMap,
        QgsLayoutItemPage,
        QgsLayoutItemPicture,
        QgsLayoutItemScaleBar,
        QgsLayoutPoint,
        QgsLayoutSize,
        QgsPrintLayout,
        QgsUnitTypes,
    )
    from qgis.PyQt.QtGui import QColor, QFont

    w, h = _paper_mm(paper)
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName(f"{item['product_id']}_{item['map_name']}")
    page = layout.pageCollection().page(0)
    page.setPageSize(QgsLayoutSize(w, h, QgsUnitTypes.LayoutMillimeters))

    title = QgsLayoutItemLabel(layout)
    title.setText(item["title"])
    title.setFont(QFont("DejaVu Sans", 14, QFont.Bold))
    title.setFontColor(QColor("#1a2744"))
    title.attemptMove(QgsLayoutPoint(12, 8, QgsUnitTypes.LayoutMillimeters))
    title.attemptResize(QgsLayoutSize(w - 30, 10, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(title)

    sub = QgsLayoutItemLabel(layout)
    sub.setText(item["subtitle"])
    sub.setFont(QFont("DejaVu Sans", 8))
    sub.setFontColor(QColor("#4a5a6a"))
    sub.attemptMove(QgsLayoutPoint(12, 18, QgsUnitTypes.LayoutMillimeters))
    sub.attemptResize(QgsLayoutSize(w - 30, 7, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(sub)

    brand = QgsLayoutItemLabel(layout)
    brand.setText("GCPA")
    brand.setFont(QFont("DejaVu Sans", 11, QFont.Bold))
    brand.setFontColor(QColor("#2a7a8c"))
    brand.setHAlign(2)
    brand.attemptMove(QgsLayoutPoint(w - 40, 8, QgsUnitTypes.LayoutMillimeters))
    brand.attemptResize(QgsLayoutSize(28, 8, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(brand)

    map_item = QgsLayoutItemMap(layout)
    map_w, map_h = w * 0.62, h - 50
    map_item.attemptMove(QgsLayoutPoint(12, 28, QgsUnitTypes.LayoutMillimeters))
    map_item.attemptResize(QgsLayoutSize(map_w, map_h, QgsUnitTypes.LayoutMillimeters))
    map_item.setExtent(layer.extent())
    map_item.setCrs(layer.crs())
    map_item.setFrameEnabled(True)
    map_item.setFrameStrokeColor(QColor("#1a2744"))
    map_item.setFrameStrokeWidth(QgsLayoutSize(0.4, QgsUnitTypes.LayoutMillimeters))
    map_item.setLayers([layer])
    layout.addLayoutItem(map_item)

    legend = QgsLayoutItemLegend(layout)
    legend.setLinkedMap(map_item)
    legend.setTitle("Leyenda")
    legend.setAutoUpdateModel(True)
    legend.attemptMove(QgsLayoutPoint(map_w + 16, 28, QgsUnitTypes.LayoutMillimeters))
    legend.attemptResize(QgsLayoutSize(w - map_w - 28, map_h * 0.62, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(legend)

    scale = QgsLayoutItemScaleBar(layout)
    scale.setLinkedMap(map_item)
    scale.setStyle("Single Box")
    scale.setNumberOfSegments(2)
    scale.setNumberOfSegmentsLeft(0)
    scale.setUnits(QgsUnitTypes.DistanceKilometers)
    scale.setUnitLabel("km")
    try:
        scale.applyDefaultSize()
    except Exception:
        pass
    scale.attemptMove(QgsLayoutPoint(14, h - 18, QgsUnitTypes.LayoutMillimeters))
    scale.attemptResize(QgsLayoutSize(55, 10, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(scale)

    svg = _north_svg()
    if svg:
        north = QgsLayoutItemPicture(layout)
        north.setPicturePath(svg)
        north.setLinkedMap(map_item)
        try:
            north.setNorthMode(QgsLayoutItemPicture.GridNorth)
        except Exception:
            pass
        north.attemptMove(QgsLayoutPoint(map_w - 6, 32, QgsUnitTypes.LayoutMillimeters))
        north.attemptResize(QgsLayoutSize(14, 18, QgsUnitTypes.LayoutMillimeters))
        layout.addLayoutItem(north)

    foot = QgsLayoutItemLabel(layout)
    crs = layer.crs().authid() or layer.crs().description() or "CRS desconocido"
    foot.setText(
        f"Viviano-Beck et al. (2014)  ·  CRISM MTRDR SR  ·  GCPA  ·  {crs}  ·  {item['product_id']}"
    )
    foot.setFont(QFont("DejaVu Sans", 7))
    foot.setFontColor(QColor("#5a6a7a"))
    foot.attemptMove(QgsLayoutPoint(12, h - 8, QgsUnitTypes.LayoutMillimeters))
    foot.attemptResize(QgsLayoutSize(w - 24, 6, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(foot)
    return layout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    out_dir = Path(manifest["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    paper = manifest.get("paper", "A4")
    dpi = int(manifest.get("dpi", 300))
    formats = [f.lower() for f in manifest.get("formats", ["pdf", "png"])]

    qgs = _init_qgis()
    from qgis.core import (
        QgsLayoutExporter,
        QgsProject,
        QgsRasterLayer,
    )

    project = QgsProject.instance()
    project.clear()
    exported = []
    try:
        for item in manifest["items"]:
            tif = item["tif"]
            name = f"{item['product_id']}_{item['map_name']}"
            layer = QgsRasterLayer(tif, name)
            if not layer.isValid():
                print(f"Capa inválida: {tif}", file=sys.stderr)
                continue
            _style_layer(layer, item["kind"], item.get("class_names") or [])
            project.addMapLayer(layer)
            layout = _build_layout(project, layer, item, paper)
            project.layoutManager().addLayout(layout)
            exporter = QgsLayoutExporter(layout)
            stem = f"{item['product_id']}_{item['map_name']}_{item['kind']}_qgis"
            if "pdf" in formats:
                pdf = out_dir / f"{stem}.pdf"
                settings = QgsLayoutExporter.PdfExportSettings()
                settings.dpi = dpi
                res = exporter.exportToPdf(str(pdf), settings)
                print(f"PDF {pdf} -> {res}")
                exported.append(str(pdf))
            if "png" in formats:
                png = out_dir / f"{stem}.png"
                img = QgsLayoutExporter.ImageExportSettings()
                img.dpi = dpi
                res = exporter.exportToImage(str(png), img)
                print(f"PNG {png} -> {res}")
                exported.append(str(png))
        qgz = Path(manifest.get("project_path") or (out_dir / "crism_layouts.qgz"))
        project.write(str(qgz))
        print(f"Proyecto {qgz}")
    finally:
        qgs.exitQgis()
    print(f"Exportados {len(exported)} archivos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
