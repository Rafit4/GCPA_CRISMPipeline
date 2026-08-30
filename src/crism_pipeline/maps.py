"""Generación de mapas minerales a partir de cubos SR."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import rasterio

from .config import viviano_config
from .io_sr import BAND_ALIASES, SRCube, load_cube
from .stretch import StretchLimits, stretch_band, stretch_rgb


def _geotransform(cube: SRCube, width: int, height: int):
    from .io_sr import _build_geotransform

    mi = cube.map_info
    if not mi:
        return None, None
    try:
        gt = _build_geotransform(mi)
        if gt is None:
            return None, None
        transform = rasterio.Affine.from_gdal(*gt)
        crs = mi.get("crs_wkt")
        if crs:
            crs = str(crs).strip("{}")
        return transform, crs
    except (TypeError, ValueError):
        return None, None


def save_geotiff(array: np.ndarray, out_path: Path, cube: SRCube, count: int = 1):
    height, width = array.shape[:2]
    transform, crs = _geotransform(cube, width, height)
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": count,
        "dtype": array.dtype,
        "compress": "lzw",
    }
    if transform and crs:
        profile["crs"] = crs
        profile["transform"] = transform
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, "w", **profile) as dst:
        if count == 1:
            dst.write(array, 1)
        else:
            for i in range(count):
                dst.write(array[:, :, i], i + 1)


def _resolve_hdr_name(cube: SRCube, name: str) -> str:
    """Nombre de banda tal como aparece en el cubo SR / HDR."""
    try:
        return cube._resolve_band_name(name)
    except KeyError:
        return BAND_ALIASES.get(name, name)


def _channel_record(
    cube: SRCube,
    channel: str,
    limits: StretchLimits,
) -> dict:
    hdr_name = _resolve_hdr_name(cube, limits.band_name)
    return {
        "channel": channel,
        "index": limits.band_name,
        "hdr_name": hdr_name,
        "vmin": limits.vmin,
        "vmax": limits.vmax,
        "mode": limits.mode,
    }


def write_stretch_sidecar(
    out_path: Path,
    *,
    product_id: str,
    map_kind: str,
    map_name: str,
    channels: list[dict],
    rgb_names: list[str] | None = None,
) -> Path:
    """Escribe JSON con vmin/vmax absolutos para reproducir el stretch en QGIS/GIS."""
    payload = {
        "product_id": product_id,
        "map_kind": map_kind,
        "map_name": map_name,
        "reference": "Viviano-Beck et al. (2014) §5.3",
        "note": (
            "vmin/vmax son valores absolutos del índice (no percentiles). "
            "En QGIS: Simbología → Multibanda a color → Min/Max → Valores definidos "
            "por el usuario, e introduce estos límites por canal. "
            "No uses 0 y 99 como máximos: 99 sería un percentil, no el valor de la banda."
        ),
        "channels": channels,
        "qgis": {
            "renderer": "multiband_color" if rgb_names else "singleband_gray",
            "contrast_enhancement": "UserDefined",
            "rgb_order_hdr": rgb_names,
            "steps": [
                "Abre el cubo SR (.IMG o GeoTIFF de 60 bandas), no el browse ya estirado.",
                "Propiedades de capa → Simbología → Multibanda a color (o Banda gris simple).",
                "Asigna las bandas hdr_name a R/G/B (o la banda del índice).",
                "Mejora de contraste: Sin mejora, o Min/Max con valores de usuario.",
                "Pon vmin y vmax de cada canal según este archivo.",
            ],
        },
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out_path


def render_index_map(
    cube: SRCube,
    index_name: str,
    out_dir: Path,
    *,
    save_geotiff_flag: bool = True,
) -> Path:
    """Mapa en escala de grises de un índice individual."""
    band = cube.band(index_name)
    stretched, limits = stretch_band(band, index_name, return_limits=True)

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{cube.product_id}_{index_name}"
    png_path = out_dir / f"{stem}.png"
    tif_path = out_dir / f"{stem}.tif"
    stretch_path = out_dir / f"{stem}.stretch.json"

    fig, ax = plt.subplots(figsize=(10, 8))
    im = ax.imshow(stretched, cmap="gray", interpolation="nearest")
    ax.set_title(f"{index_name} — {cube.product_id}")
    ax.axis("off")
    plt.colorbar(im, ax=ax, fraction=0.03)
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    if save_geotiff_flag:
        save_geotiff(stretched, tif_path, cube)

    write_stretch_sidecar(
        stretch_path,
        product_id=cube.product_id,
        map_kind="index",
        map_name=index_name,
        channels=[_channel_record(cube, "gray", limits)],
    )
    return png_path


def render_browse_product(
    cube: SRCube,
    browse_code: str,
    out_dir: Path,
    *,
    save_geotiff_flag: bool = True,
) -> Path:
    """Genera mapa RGB tipo browse product (Viviano 2014 Tabla 3)."""
    cfg = viviano_config()["browse_products"]
    if browse_code not in cfg:
        raise KeyError(f"Browse '{browse_code}' no definido. Opciones: {list(cfg)}")

    spec = cfg[browse_code]
    r_name, g_name, b_name = spec["rgb"]
    rgb, limits = stretch_rgb(
        cube.band(r_name),
        cube.band(g_name),
        cube.band(b_name),
        (r_name, g_name, b_name),
        return_limits=True,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{cube.product_id}_{browse_code}"
    png_path = out_dir / f"{stem}.png"
    tif_path = out_dir / f"{stem}.tif"
    stretch_path = out_dir / f"{stem}.stretch.json"

    fig, ax = plt.subplots(figsize=(10, 8))
    ax.imshow(rgb, interpolation="nearest")
    ax.set_title(f"{browse_code} — {spec['name']} — {cube.product_id}")
    ax.axis("off")
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    if save_geotiff_flag:
        save_geotiff(rgb, tif_path, cube, count=3)

    channel_labels = ("R", "G", "B")
    channels = [
        _channel_record(cube, ch, lim) for ch, lim in zip(channel_labels, limits, strict=True)
    ]
    hdr_rgb = [c["hdr_name"] for c in channels]
    write_stretch_sidecar(
        stretch_path,
        product_id=cube.product_id,
        map_kind="browse",
        map_name=browse_code,
        channels=channels,
        rgb_names=hdr_rgb,
    )
    return png_path


def browse_component_indices(browse_codes: list[str]) -> list[str]:
    """Índices únicos (canales RGB) usados por los browse products indicados."""
    cfg = viviano_config()["browse_products"]
    names: list[str] = []
    seen: set[str] = set()
    for code in browse_codes:
        if code not in cfg:
            raise KeyError(f"Browse '{code}' no definido. Opciones: {list(cfg)}")
        for name in cfg[code]["rgb"]:
            key = str(name).upper()
            if key in seen:
                continue
            seen.add(key)
            names.append(str(name))
    return names


def render_browse_component_maps(
    cube: SRCube,
    out_dir: Path,
    browse_codes: list[str],
) -> list[Path]:
    """Mapas en escala de grises de los canales RGB de los browse seleccionados."""
    outputs: list[Path] = []
    for index_name in browse_component_indices(browse_codes):
        outputs.append(render_index_map(cube, index_name, out_dir / "indices"))
    return outputs


def render_mineral_group_maps(
    cube: SRCube,
    out_dir: Path,
    groups: list[str] | None = None,
) -> list[Path]:
    """Mapas del índice principal de cada grupo mineral."""
    mineral_cfg = viviano_config()["mineral_groups"]
    targets = groups or list(mineral_cfg.keys())
    outputs: list[Path] = []

    for key in targets:
        if key not in mineral_cfg:
            continue
        primary = mineral_cfg[key]["primary"]
        outputs.append(render_index_map(cube, primary, out_dir / "indices"))
    return outputs


def generate_all_maps(
    source: Path,
    out_dir: Path,
    *,
    browse_codes: list[str] | None = None,
    mineral_groups: list[str] | None = None,
    include_indices: bool = False,
) -> list[Path]:
    """Genera browse products + índices de sus canales RGB en ``indices/``.

    Si ``include_indices`` es True, añade también los índices principales de
    ``mineral_groups`` (todos, o solo los listados en ``mineral_groups``).
    """
    cube = load_cube(source)
    outputs: list[Path] = []

    codes = browse_codes or ["MAF", "PHY", "HYD", "CAR", "FEM"]
    for code in codes:
        outputs.append(render_browse_product(cube, code, out_dir / "browse"))

    outputs.extend(render_browse_component_maps(cube, out_dir, codes))

    if include_indices:
        outputs.extend(render_mineral_group_maps(cube, out_dir, mineral_groups))
    return outputs
