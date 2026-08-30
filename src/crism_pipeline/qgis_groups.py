"""Genera un proyecto QGIS con GeoTIFF agrupados por browse/índice/mineral."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .config import ROOT, resolve_path
from .layout import discover_maps
from .qgis_layout import find_qgis_python

_LOADER = Path(__file__).with_name("qgis_load_groups.py")

_LEGACY_SHARED = {"browse", "indices", "detection", "classification", "layouts", "shp"}


def _short_id(product_id: str) -> str:
    return (product_id or "").split("_")[0] or product_id


def _skip_legacy_shared(path: Path, maps_root: Path) -> bool:
    try:
        rel = path.resolve().relative_to(maps_root.resolve())
    except ValueError:
        return False
    parts = rel.parts
    if any(part.startswith("_") for part in parts):
        return True
    return len(parts) == 2 and parts[0].lower() in _LEGACY_SHARED


def build_groups_manifest(
    maps_root: Path,
    *,
    kinds: list[str] | None = None,
    project_path: Path | None = None,
) -> dict:
    maps_root = Path(maps_root)
    specs = discover_maps(maps_root, kinds=kinds)
    specs = [s for s in specs if not _skip_legacy_shared(s.path, maps_root)]
    items = []
    for spec in specs:
        items.append(
            {
                "tif": str(spec.path.resolve()),
                "kind": spec.kind,
                "map_name": spec.map_name,
                "product_id": spec.product_id,
                "layer_name": _short_id(spec.product_id),
                "class_names": spec.class_names,
            }
        )
    out = Path(project_path) if project_path else maps_root / "caves_grupos.qgz"
    used_kinds = sorted({i["kind"] for i in items})
    return {
        "project_path": str(out.resolve()),
        "nest_kinds": len(used_kinds) > 1,
        "visible_group": "TRU",
        "items": items,
    }


def write_groups_manifest(manifest: dict, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return dest


def export_qgis_project(
    maps_root: Path | None = None,
    out: Path | None = None,
    *,
    kinds: list[str] | None = None,
    run: bool = True,
) -> Path:
    """Escribe manifest + .qgz (si QGIS Desktop está instalado)."""
    maps_root = Path(maps_root) if maps_root else resolve_path("maps")
    kinds = kinds or ["browse"]
    out = Path(out) if out else maps_root / "caves_grupos.qgz"
    manifest = build_groups_manifest(maps_root, kinds=kinds, project_path=out)
    if not manifest["items"]:
        raise FileNotFoundError(f"No hay GeoTIFF de tipo {kinds} en {maps_root}")

    manifest_path = out.with_name(out.stem + "_manifest.json")
    write_groups_manifest(manifest, manifest_path)

    qgis_py = find_qgis_python()
    hint = out.with_name("LEEME_GRUPOS_QGIS.txt")
    qgis_bat = str(qgis_py) if qgis_py else r"C:\Program Files\QGIS 3.34.7\bin\python-qgis-ltr.bat"
    hint.write_text(
        "Proyecto QGIS con grupos (TRU, CAR, CR2, …)\n"
        "============================================\n\n"
        "1. Abre el .qgz en QGIS Desktop, o\n"
        "2. En la consola Python de QGIS:\n\n"
        f'   exec(open(r"{_LOADER}", encoding="utf-8").read())\n'
        f'   load_from_manifest(r"{manifest_path}", into_current=True)\n\n'
        "Regenerar el proyecto:\n"
        f'   & "{qgis_bat}" "{_LOADER}" --manifest "{manifest_path}"\n',
        encoding="utf-8",
    )

    if not run:
        return manifest_path
    if qgis_py is None:
        print("QGIS Desktop no encontrado. Manifest listo para cargar a mano:")
        print(f"  {manifest_path}")
        return manifest_path

    cmd = [str(qgis_py), str(_LOADER), "--manifest", str(manifest_path), "--out", str(out)]
    log_path = out.with_name(out.stem + "_qgis.log")
    result = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    log_path.write_text(
        (result.stdout or "") + "\n" + (result.stderr or ""),
        encoding="utf-8",
    )
    if result.returncode != 0 or not out.is_file():
        raise RuntimeError(
            "QGIS no pudo crear el proyecto. "
            f"Revisa {log_path} o abre el manifest desde la consola de QGIS."
        )
    return out
