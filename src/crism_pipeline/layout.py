"""Láminas cartográficas (título, leyenda, escala, norte, márgenes) desde GeoTIFF."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.patches import FancyBboxPatch, Polygon, Rectangle
from matplotlib.ticker import FuncFormatter, MaxNLocator

from .config import viviano_config

# Radio medio de Marte (IAU 2000 / Mars 2000), metros.
MARS_RADIUS_M = 3_396_190.0
CITATION = "Viviano-Beck et al. (2014) · CRISM MTRDR SR · GCPA"
KINDS = ("browse", "index", "detection", "classification")
PAPER_MM = {
    "A4": (297.0, 210.0),
    "A3": (420.0, 297.0),
    "letter": (279.4, 215.9),
}
_TAB10 = [
    "#1f77b4",
    "#ff7f0e",
    "#2ca02c",
    "#d62728",
    "#9467bd",
    "#8c564b",
    "#e377c2",
    "#7f7f7f",
    "#bcbd22",
    "#17becf",
]


@dataclass
class MapSpec:
    path: Path
    kind: str
    product_id: str
    map_name: str
    title: str
    subtitle: str
    stretch: dict | None = None
    class_names: list[str] = field(default_factory=list)


def _load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _first(data: dict | None, *keys, default=None):
    if not data:
        return default
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return default


def _product_id_from_stem(stem: str) -> str:
    match = re.match(
        r"((?:FRT|HRL|HRS)[0-9A-Fa-f]+_\d+_(?:SR|IF|sr|if)\d+[A-Za-z]_MTR3)",
        stem,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1)
    parts = stem.split("_")
    if len(parts) >= 4:
        return "_".join(parts[:4])
    return stem


def _stretch_sidecar(tif: Path) -> dict | None:
    return _load_json(tif.with_name(f"{tif.stem}.stretch.json"))


def discover_maps(root: Path, kinds: list[str] | None = None) -> list[MapSpec]:
    """Encuentra GeoTIFF de browse, índices, detección y clasificación."""
    root = Path(root)
    if root.is_file() and root.suffix.lower() in {".tif", ".tiff"}:
        files = [root]
    elif root.is_dir():
        files = sorted(
            p for p in root.rglob("*") if p.suffix.lower() in {".tif", ".tiff"}
        )
    else:
        raise FileNotFoundError(root)

    allowed = set(kinds or KINDS)
    browse_codes = {k.upper() for k in viviano_config().get("browse_products", {})}
    out: list[MapSpec] = []
    seen: set[Path] = set()
    for path in files:
        resolved = path.resolve()
        if resolved in seen:
            continue
        if any(part.lower() == "layouts" for part in path.parts):
            continue
        spec = _spec_from_tif(path, browse_codes)
        if spec is None or spec.kind not in allowed:
            continue
        seen.add(resolved)
        out.append(spec)
    return out


def _spec_from_tif(path: Path, browse_codes: set[str]) -> MapSpec | None:
    stem = path.stem
    parent = path.parent.name.lower()
    stretch = _stretch_sidecar(path)
    product_id = str(_first(stretch, "product_id") or _product_id_from_stem(stem))
    map_kind = _first(stretch, "map_kind", "map_kind")
    map_name = str(
        _first(stretch, "map_name", "map_name") or stem.replace(f"{product_id}_", "", 1)
    )

    if parent == "detection" or stem.endswith("_detection"):
        mineral = re.sub(r"_detection$", "", map_name)
        groups = viviano_config().get("mineral_groups", {})
        label = groups.get(mineral, {}).get("label", mineral.replace("_", " "))
        return MapSpec(
            path=path,
            kind="detection",
            product_id=product_id,
            map_name=mineral,
            title=f"Detección: {label}",
            subtitle=f"{product_id}  ·  máscara binaria (presente / ausente)",
        )

    if parent == "classification" or stem.endswith("_units"):
        method = re.sub(r"_units$", "", map_name)
        meta = _load_json(path.parent / f"{product_id}_{method}_meta.json")
        classes = [str(c) for c in (meta or {}).get("classes") or []]
        method_label = (meta or {}).get("method", method)
        return MapSpec(
            path=path,
            kind="classification",
            product_id=product_id,
            map_name=method,
            title=f"Unidades geológicas ({method_label})",
            subtitle=f"{product_id}  ·  clasificación de índices SR",
            class_names=classes,
        )

    token = map_name.upper()
    is_browse = (
        map_kind == "browse"
        or parent == "browse"
        or (token in browse_codes and parent != "indices")
    )
    if is_browse and not stem.endswith("_detection") and not stem.endswith("_units"):
        cfg = viviano_config().get("browse_products", {})
        spec = cfg.get(token, {})
        channels = list((stretch or {}).get("channels") or [])
        rgb = spec.get("rgb") or [c.get("index") for c in channels]
        rgb_txt = ", ".join(str(x) for x in rgb if x)
        name = spec.get("name", token)
        return MapSpec(
            path=path,
            kind="browse",
            product_id=product_id,
            map_name=token,
            title=f"{token} — {name}",
            subtitle=f"{product_id}  ·  R/G/B: {rgb_txt}" if rgb_txt else product_id,
            stretch=stretch,
        )

    if map_kind == "index" or parent == "indices":
        ch = ((stretch or {}).get("channels") or [{}])[0]
        vmin, vmax = _first(ch, "vmin", "vmin"), _first(ch, "vmax", "vmax")
        extra = ""
        if vmin is not None and vmax is not None:
            extra = f"  ·  stretch {vmin:.4g} – {vmax:.4g}"
        return MapSpec(
            path=path,
            kind="index",
            product_id=product_id,
            map_name=str(map_name),
            title=f"Índice {map_name}",
            subtitle=f"{product_id}{extra}",
            stretch=stretch,
        )
    return None


def _read_raster(path: Path):
    import rasterio

    with rasterio.open(path) as src:
        data = src.read()
        transform = src.transform
        crs = src.crs
        bounds = src.bounds
        nodata = src.nodata
        tags = dict(src.tags())
    return data, transform, crs, bounds, nodata, tags


def _is_geographic(crs) -> bool:
    if crs is None:
        return False
    try:
        return bool(crs.is_geographic)
    except Exception:
        return False


def _crs_label(crs) -> str:
    if crs is None:
        return "sin CRS (solo píxeles)"
    try:
        name = getattr(crs, "name", None) or str(crs)
        kind = "geográfico" if _is_geographic(crs) else "proyectado"
        return f"{kind} · {str(name)[:72]}"
    except Exception:
        return str(crs)[:80]


def _meters_per_map_unit(crs, bounds) -> float | None:
    if crs is None:
        return None
    try:
        units = (crs.linear_units or "").lower()
    except Exception:
        units = ""
    if units in {"metre", "meter", "metres", "meters", "m"}:
        return 1.0
    if _is_geographic(crs):
        lat = (float(bounds.bottom) + float(bounds.top)) / 2.0
        return (np.pi / 180.0) * MARS_RADIUS_M * max(np.cos(np.deg2rad(lat)), 0.05)
    return None


def _nice_length_m(target_m: float) -> float:
    if target_m <= 0:
        return 1000.0
    exp = int(np.floor(np.log10(target_m)))
    base = 10.0 ** exp
    for mult in (1, 2, 5, 10):
        if mult * base >= target_m * 0.55:
            return float(mult * base)
    return float(10 * base)


def _scale_label(meters: float) -> str:
    if meters >= 1000:
        km = meters / 1000.0
        if abs(km - round(km)) < 1e-6:
            return f"{int(round(km))} km"
        return f"{km:g} km"
    if abs(meters - round(meters)) < 1e-6:
        return f"{int(round(meters))} m"
    return f"{meters:g} m"


def _prepare_image(spec: MapSpec, data: np.ndarray, nodata) -> tuple[np.ndarray, dict]:
    info: dict = {"cmap": None, "vmin": None, "vmax": None, "norm": None}

    if spec.kind == "browse" or (data.shape[0] >= 3 and spec.kind not in {"detection", "classification", "index"}):
        rgb = np.transpose(data[:3], (1, 2, 0))
        if np.issubdtype(rgb.dtype, np.integer):
            rgb = rgb.astype(float) / 255.0
        else:
            finite = np.isfinite(rgb)
            if finite.any():
                lo, hi = np.nanpercentile(rgb[finite], (1, 99))
                if hi <= lo:
                    hi = lo + 1.0
                rgb = np.clip((rgb - lo) / (hi - lo), 0, 1)
            else:
                rgb = np.zeros((*rgb.shape[:2], 3), dtype=float)
        return np.clip(rgb, 0, 1), info

    band = np.asarray(data[0], dtype=float)
    if nodata is not None:
        band = np.where(band == nodata, np.nan, band)

    if spec.kind == "detection":
        present = np.where(np.isnan(band), np.nan, (band > 0).astype(float))
        cmap = ListedColormap(["#f4f1ea", "#c0392b"])
        info.update(cmap=cmap, vmin=0, vmax=1)
        return present, info

    if spec.kind == "classification":
        masked = np.where((band < 0) | np.isnan(band), np.nan, band)
        valid = masked[np.isfinite(masked)]
        if valid.size == 0:
            n = max(len(spec.class_names), 1)
        else:
            n = int(np.nanmax(valid)) + 1
            n = max(n, len(spec.class_names), 1)
        colors = (_TAB10 * ((n // 10) + 1))[:n]
        cmap = ListedColormap(colors)
        bounds = np.arange(-0.5, n + 0.5, 1.0)
        info.update(cmap=cmap, norm=BoundaryNorm(bounds, cmap.N), n_classes=n)
        return masked, info

    finite = np.isfinite(band)
    if finite.any():
        vmin = float(np.nanpercentile(band[finite], 1))
        vmax = float(np.nanpercentile(band[finite], 99))
        if vmax <= vmin:
            vmax = vmin + 1e-6
    else:
        vmin, vmax = 0.0, 1.0
    info.update(cmap="gray", vmin=vmin, vmax=vmax)
    return band, info


def _north_arrow(ax) -> None:
    x, y = 0.93, 0.80
    ax.add_patch(
        Rectangle(
            (x - 0.010, y - 0.07),
            0.020,
            0.08,
            transform=ax.transAxes,
            facecolor="#1a2744",
            zorder=20,
        )
    )
    ax.add_patch(
        Polygon(
            [(x - 0.032, y + 0.01), (x + 0.032, y + 0.01), (x, y + 0.12)],
            transform=ax.transAxes,
            closed=True,
            facecolor="#1a2744",
            zorder=21,
        )
    )
    ax.text(
        x,
        y + 0.145,
        "N",
        transform=ax.transAxes,
        ha="center",
        va="bottom",
        fontsize=9,
        fontweight="bold",
        color="#1a2744",
        zorder=22,
    )


def _draw_scale_bar(ax, bounds, meters_per_unit: float | None) -> str:
    if meters_per_unit is None or meters_per_unit <= 0:
        ax.text(
            0.04,
            0.05,
            "Escala no disponible (sin georreferencia)",
            transform=ax.transAxes,
            fontsize=7,
            color="#333333",
            va="bottom",
        )
        return "no disponible"

    width_u = abs(float(bounds.right) - float(bounds.left))
    height_u = abs(float(bounds.top) - float(bounds.bottom))
    length_m = _nice_length_m(width_u * meters_per_unit * 0.22)
    length_u = length_m / meters_per_unit
    x0 = float(bounds.left) + 0.06 * width_u
    y0 = float(bounds.bottom) + 0.055 * height_u
    h = 0.012 * height_u
    ax.add_patch(Rectangle((x0, y0), length_u, h, facecolor="white", edgecolor="#1a2744", lw=0.8, zorder=15))
    ax.add_patch(Rectangle((x0, y0), length_u / 2.0, h, facecolor="#1a2744", edgecolor="#1a2744", lw=0.8, zorder=16))
    label = _scale_label(length_m)
    ax.text(
        x0 + length_u / 2.0,
        y0 + h * 2.1,
        label,
        ha="center",
        va="bottom",
        fontsize=8,
        color="#1a2744",
        fontweight="bold",
        zorder=17,
    )
    return label


def _format_axis(ax, crs, bounds) -> None:
    if crs is None:
        ax.set_xlabel("muestra (píxel)", fontsize=8)
        ax.set_ylabel("línea (píxel)", fontsize=8)
        return
    if _is_geographic(crs):
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.4f}°"))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.4f}°"))
        ax.set_xlabel("Longitud", fontsize=8)
        ax.set_ylabel("Latitud", fontsize=8)
    else:
        span = abs(float(bounds.right) - float(bounds.left))
        if span > 5000:
            ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v / 1000:.1f}"))
            ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v / 1000:.1f}"))
            ax.set_xlabel("Easting (km)", fontsize=8)
            ax.set_ylabel("Northing (km)", fontsize=8)
        else:
            ax.set_xlabel("Easting", fontsize=8)
            ax.set_ylabel("Northing", fontsize=8)
    ax.xaxis.set_major_locator(MaxNLocator(5))
    ax.yaxis.set_major_locator(MaxNLocator(5))


def _draw_legend(ax, spec: MapSpec, info: dict) -> None:
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.set_title("Leyenda", loc="left", fontsize=11, fontweight="bold", color="#1a2744", pad=8)
    y = 0.88

    if spec.kind == "browse":
        channels = list((spec.stretch or {}).get("channels") or [])
        colors = {"R": "#c0392b", "G": "#1e8449", "B": "#2471a3"}
        if not channels:
            channels = [{"channel": c, "index": "?"} for c in ("R", "G", "B")]
        for ch in channels:
            key = str(ch.get("channel", "")).upper()[:1] or "R"
            color = colors.get(key, "#555555")
            ax.add_patch(Rectangle((0.04, y - 0.02), 0.10, 0.055, facecolor=color, edgecolor="#1a2744", lw=0.6))
            idx = ch.get("index") or ch.get("hdr_name") or ch.get("hdr_name") or ""
            vmin, vmax = _first(ch, "vmin", "vmin"), _first(ch, "vmax", "vmax")
            extra = f"\n{vmin:.4g} – {vmax:.4g}" if vmin is not None and vmax is not None else ""
            ax.text(0.18, y + 0.01, f"{key}  {idx}{extra}", va="center", fontsize=8, color="#1a2744")
            y -= 0.13
        ax.text(
            0.04,
            0.16,
            "Composición RGB tipo browse\nCRISM. Color intenso = índice\nmás alto (stretch absoluto).",
            fontsize=7.5,
            color="#445566",
            va="bottom",
        )
        return

    if spec.kind == "detection":
        ax.add_patch(Rectangle((0.04, y - 0.02), 0.10, 0.055, facecolor="#c0392b", edgecolor="#1a2744", lw=0.6))
        ax.text(0.18, y + 0.01, "Presente (detectado)", va="center", fontsize=8.5, color="#1a2744")
        y -= 0.12
        ax.add_patch(Rectangle((0.04, y - 0.02), 0.10, 0.055, facecolor="#f4f1ea", edgecolor="#1a2744", lw=0.6))
        ax.text(0.18, y + 0.01, "Ausente / no válido", va="center", fontsize=8.5, color="#1a2744")
        ax.text(
            0.04,
            0.22,
            "Reglas AND de umbral\nen índices SR\n(config/viviano2014.yaml).",
            fontsize=7.5,
            color="#445566",
            va="bottom",
        )
        return

    if spec.kind == "classification":
        n = int(info.get("n_classes") or max(len(spec.class_names), 1))
        names = spec.class_names or [f"Cluster {i}" for i in range(n)]
        colors = (_TAB10 * ((n // 10) + 1))[:n]
        for i, (name, color) in enumerate(zip(names, colors)):
            if y < 0.12:
                ax.text(0.04, 0.06, f"… {n - i} clases más", fontsize=7, color="#667788")
                break
            ax.add_patch(Rectangle((0.04, y - 0.02), 0.10, 0.05, facecolor=color, edgecolor="#1a2744", lw=0.5))
            ax.text(0.18, y + 0.005, str(name), va="center", fontsize=8, color="#1a2744")
            y -= 0.085
        return

    sm = plt.cm.ScalarMappable(cmap=info.get("cmap") or "gray")
    sm.set_clim(info.get("vmin") or 0, info.get("vmax") or 1)
    cax = ax.inset_axes([0.10, 0.18, 0.16, 0.62])
    cb = plt.colorbar(sm, cax=cax)
    cb.ax.tick_params(labelsize=7)
    cb.set_label("valor estirado", fontsize=7)
    ch = ((spec.stretch or {}).get("channels") or [{}])[0]
    vmin, vmax = _first(ch, "vmin", "vmin"), _first(ch, "vmax", "vmax")
    if vmin is not None:
        ax.text(
            0.42,
            0.55,
            f"vmin  {vmin:.4g}\nvmax  {vmax:.4g}\nmodo  {_first(ch, 'mode', 'mode', default='—')}",
            fontsize=8,
            color="#1a2744",
            va="center",
            family="monospace",
        )


def _draw_sheet(spec: MapSpec, paper: str, dpi: int):
    data, _transform, crs, bounds, nodata, _tags = _read_raster(spec.path)
    image, info = _prepare_image(spec, data, nodata)

    w_mm, h_mm = PAPER_MM.get(paper.upper(), PAPER_MM["A4"])
    fig = plt.figure(figsize=(w_mm / 25.4, h_mm / 25.4), facecolor="#f7f4ee", dpi=dpi)
    fig.patches.append(
        FancyBboxPatch(
            (0.012, 0.018),
            0.976,
            0.964,
            transform=fig.transFigure,
            boxstyle="square,pad=0",
            facecolor="none",
            edgecolor="#1a2744",
            linewidth=1.1,
        )
    )

    ax_map = fig.add_axes([0.07, 0.14, 0.60, 0.70])
    ax_leg = fig.add_axes([0.70, 0.14, 0.26, 0.70])
    ax_leg.set_facecolor("#f7f4ee")
    ax_leg.add_patch(
        FancyBboxPatch(
            (0.0, 0.0),
            1.0,
            1.0,
            transform=ax_leg.transAxes,
            boxstyle="round,pad=0.02,rounding_size=0.02",
            facecolor="#efeae0",
            edgecolor="#c5bba8",
            linewidth=0.8,
        )
    )

    extent = [float(bounds.left), float(bounds.right), float(bounds.bottom), float(bounds.top)]
    im_kw = dict(extent=extent, origin="upper", interpolation="nearest", aspect="equal")
    if info.get("norm") is not None:
        ax_map.imshow(image, cmap=info.get("cmap"), norm=info["norm"], **im_kw)
    elif info.get("cmap") is not None:
        ax_map.imshow(
            image, cmap=info["cmap"], vmin=info.get("vmin"), vmax=info.get("vmax"), **im_kw
        )
    else:
        ax_map.imshow(image, **im_kw)

    ax_map.set_xlim(extent[0], extent[1])
    ax_map.set_ylim(extent[2], extent[3])
    for spine in ax_map.spines.values():
        spine.set_color("#1a2744")
        spine.set_linewidth(1.15)
    ax_map.tick_params(colors="#1a2744", labelsize=7, length=3)
    ax_map.grid(True, linestyle=":", linewidth=0.45, color="#6a7a8a", alpha=0.55)
    _format_axis(ax_map, crs, bounds)

    mpu = _meters_per_map_unit(crs, bounds)
    scale_txt = _draw_scale_bar(ax_map, bounds, mpu)
    _north_arrow(ax_map)
    _draw_legend(ax_leg, spec, info)

    fig.text(0.07, 0.935, spec.title, fontsize=15, fontweight="bold", color="#1a2744", ha="left", va="top")
    fig.text(0.07, 0.905, spec.subtitle, fontsize=9, color="#4a5a6a", ha="left", va="top")
    fig.text(0.97, 0.935, "GCPA", fontsize=11, fontweight="bold", color="#2a7a8c", ha="right", va="top")

    footer = (
        f"{CITATION}  ·  {date.today().isoformat()}  ·  "
        f"CRS: {_crs_label(crs)}  ·  escala: {scale_txt}"
    )
    fig.text(0.07, 0.045, footer, fontsize=7, color="#5a6a7a", ha="left", va="center")
    fig.text(0.97, 0.045, spec.path.name, fontsize=7, color="#8a7a6a", ha="right", va="center")
    return fig


def render_map_sheet(
    spec: MapSpec,
    out_dir: Path,
    *,
    paper: str = "A4",
    dpi: int = 300,
    formats: list[str] | None = None,
) -> list[Path]:
    """Renderiza una lámina cartográfica (PDF y/o PNG)."""
    formats = [f.lower().lstrip(".") for f in (formats or ["pdf", "png"])]
    fig = _draw_sheet(spec, paper, dpi)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{spec.product_id}_{spec.map_name}_{spec.kind}_sheet"
    written: list[Path] = []
    for fmt in formats:
        dest = out_dir / f"{stem}.{fmt}"
        fig.savefig(dest, dpi=dpi, facecolor=fig.get_facecolor(), edgecolor="none")
        written.append(dest)
    plt.close(fig)
    return written


def render_layouts(
    input_path: Path,
    out_dir: Path | None = None,
    *,
    kinds: list[str] | None = None,
    paper: str = "A4",
    dpi: int = 300,
    formats: list[str] | None = None,
    atlas: bool = True,
) -> list[Path]:
    """Genera láminas para todos los GeoTIFF descubiertos bajo ``input_path``."""
    specs = discover_maps(input_path, kinds=kinds)
    if not specs:
        raise FileNotFoundError(
            f"No se encontraron GeoTIFF de mapas/detección/clasificación en {input_path}"
        )
    out_dir = Path(out_dir) if out_dir else Path(input_path) / "layouts"
    if out_dir.resolve() == Path(input_path).resolve() and Path(input_path).is_dir():
        out_dir = Path(input_path) / "layouts"
    out_dir.mkdir(parents=True, exist_ok=True)
    formats = [f.lower().lstrip(".") for f in (formats or ["pdf", "png"])]

    written: list[Path] = []
    atlas_path: Path | None = None
    atlas_pdf: PdfPages | None = None
    if atlas and "pdf" in formats:
        ids = sorted({s.product_id for s in specs})
        name = f"atlas_{ids[0]}.pdf" if len(ids) == 1 else "atlas_layouts.pdf"
        atlas_path = out_dir / name
        atlas_pdf = PdfPages(atlas_path)

    try:
        for spec in specs:
            fig = _draw_sheet(spec, paper, dpi)
            stem = f"{spec.product_id}_{spec.map_name}_{spec.kind}_sheet"
            for fmt in formats:
                dest = out_dir / f"{stem}.{fmt}"
                fig.savefig(dest, dpi=dpi, facecolor=fig.get_facecolor(), edgecolor="none")
                written.append(dest)
            if atlas_pdf is not None:
                atlas_pdf.savefig(fig, dpi=dpi, facecolor=fig.get_facecolor())
            plt.close(fig)
    finally:
        if atlas_pdf is not None:
            atlas_pdf.close()
            if atlas_path is not None:
                written.append(atlas_path)
    return written


def export_cartography(
    input_path: Path,
    out_dir: Path | None = None,
    *,
    kinds: list[str] | None = None,
    paper: str = "A4",
    dpi: int = 300,
    formats: list[str] | None = None,
    atlas: bool = True,
    engine: str = "matplotlib",
) -> list[Path]:
    """Genera láminas (matplotlib y/o compositor QGIS) desde GeoTIFF del pipeline."""
    engine = engine.lower()
    if engine not in {"matplotlib", "qgis", "both"}:
        raise ValueError(f"Motor desconocido: {engine}. Usa matplotlib, qgis o both.")
    input_path = Path(input_path)
    out_dir = Path(out_dir) if out_dir else input_path / "layouts"
    written: list[Path] = []
    if engine in {"matplotlib", "both"}:
        written.extend(
            render_layouts(
                input_path,
                out_dir,
                kinds=kinds,
                paper=paper,
                dpi=dpi,
                formats=formats,
                atlas=atlas,
            )
        )
    if engine in {"qgis", "both"}:
        from .qgis_layout import export_qgis_layouts, find_qgis_python

        qgis_files = export_qgis_layouts(
            input_path,
            out_dir / "qgis",
            kinds=kinds,
            paper=paper,
            dpi=dpi,
            formats=formats,
            run=True,
        )
        written.extend(qgis_files)
        if find_qgis_python() is None:
            print(
                "QGIS Desktop no se encontró: se escribió un script PyQGIS en "
                f"{out_dir / 'qgis'} (ver LEEME_QGIS.txt). "
                "Las láminas matplotlib sí se generan con --engine matplotlib|both."
            )
    return written
