"""Utilidades espaciales: leer GeoJSON/Shapefile y muestrear píxeles sobre un raster."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import numpy as np
import pandas as pd

VECTOR_SUFFIXES = {".geojson", ".json", ".shp"}


def georef_from_map_info(map_info: dict[str, float | str]):
    """Devuelve (Affine, CRS|None) desde map_info ENVI, o (None, None)."""
    from rasterio.crs import CRS
    from rasterio.transform import Affine

    from .io_sr import _build_geotransform

    gt = _build_geotransform(map_info)
    if gt is None:
        return None, None
    transform = Affine.from_gdal(*gt)
    crs_wkt = map_info.get("crs_wkt")
    crs = CRS.from_wkt(str(crs_wkt).strip("{}")) if crs_wkt else None
    return transform, crs


def _geojson_crs(data: dict) -> Any:
    crs_block = data.get("crs")
    if not isinstance(crs_block, dict):
        return None
    props = crs_block.get("properties") or {}
    name = props.get("name")
    if not name:
        return None
    from rasterio.crs import CRS

    try:
        return CRS.from_user_input(name)
    except Exception:
        return None


def iter_vector_features(
    path: Path,
) -> Iterator[tuple[dict, dict[str, Any], Any]]:
    """
    Itera features vectoriales.

    Yields
    ------
    geometry : dict GeoJSON-like
    properties : dict
    src_crs : CRS | None  (None → asumir CRS del raster destino)
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in {".geojson", ".json"}:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("type") == "FeatureCollection":
            features = data.get("features", [])
        elif data.get("type") == "Feature":
            features = [data]
        else:
            raise ValueError(f"GeoJSON no reconocido en {path}")
        src_crs = _geojson_crs(data)
        for feat in features:
            geom = feat.get("geometry")
            if not geom:
                continue
            props = feat.get("properties") or {}
            yield geom, dict(props), src_crs
        return

    if suffix == ".shp":
        import shapefile
        from rasterio.crs import CRS

        reader = shapefile.Reader(str(path))
        fields = [f[0] for f in reader.fields[1:]]
        src_crs = None
        prj = path.with_suffix(".prj")
        if prj.is_file():
            try:
                src_crs = CRS.from_wkt(prj.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                src_crs = None
        for sr in reader.iterShapeRecords():
            geom = sr.shape.__geo_interface__
            if geom.get("type") == "Null":
                continue
            props = dict(zip(fields, sr.record))
            yield geom, props, src_crs
        return

    raise ValueError(
        f"Formato vectorial no soportado: {suffix}. Usa {sorted(VECTOR_SUFFIXES)}"
    )


def pixels_under_vector(
    map_info: dict[str, float | str],
    out_shape: tuple[int, int],
    path: Path,
    *,
    valid_mask: np.ndarray | None = None,
    label_field: str | None = None,
    require_label: bool = False,
    max_pixels_per_feature: int | None = 20_000,
    random_state: int = 42,
) -> pd.DataFrame:
    """
    Rasteriza cada geometría (punto/línea/polígono) y lista píxeles cubiertos.

    Columnas: ``line``, ``sample``, ``feature_id``, ``label``.
    """
    from rasterio import features as rio_features
    from rasterio.warp import transform_geom

    transform, cube_crs = georef_from_map_info(map_info)
    if transform is None:
        raise ValueError(
            "El producto no tiene georreferencia usable para cruzar con el vector."
        )

    height, width = out_shape
    if valid_mask is None:
        valid_mask = np.ones((height, width), dtype=bool)
    rng = np.random.default_rng(random_state)
    rows: list[dict] = []

    for feature_id, (geom, props, src_crs) in enumerate(iter_vector_features(path)):
        label = ""
        if label_field:
            raw = props.get(label_field)
            if raw is None or str(raw).strip() == "":
                if require_label:
                    raise ValueError(
                        f"Feature {feature_id} sin campo '{label_field}'. "
                        f"Propiedades: {list(props)}"
                    )
            else:
                label = str(raw)

        if src_crs is not None and cube_crs is not None and src_crs != cube_crs:
            geom = transform_geom(src_crs, cube_crs, geom)

        burned = rio_features.rasterize(
            [(geom, 1)],
            out_shape=(height, width),
            transform=transform,
            fill=0,
            dtype=np.uint8,
            all_touched=True,
        )
        ys, xs = np.where((burned == 1) & valid_mask)
        if ys.size == 0:
            continue
        if max_pixels_per_feature and ys.size > max_pixels_per_feature:
            sel = rng.choice(ys.size, size=max_pixels_per_feature, replace=False)
            ys, xs = ys[sel], xs[sel]
        for r, c in zip(ys.tolist(), xs.tolist()):
            rows.append(
                {
                    "line": r,
                    "sample": c,
                    "feature_id": feature_id,
                    "label": label,
                }
            )

    if not rows:
        raise ValueError(
            "Ningún píxel válido intersecta las geometrías. "
            "Revisa CRS/extensión del vector respecto al cubo."
        )
    return pd.DataFrame(rows)
