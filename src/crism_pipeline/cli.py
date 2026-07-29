"""Interfaz de línea de comandos del pipeline CRISM MTRDR SR."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import resolve_path


def _add_download(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("download", help="Descargar cubos SR y/o IF desde ODE")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--pdsid", help="Product ID o patrón con *")
    g.add_argument("--bbox", nargs=4, type=float, metavar=("W", "E", "S", "N"))
    g.add_argument(
        "--ids-file",
        type=Path,
        help="SearchResults.txt de ODE (columna PRODUCT ID) o lista un ID por línea",
    )
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--max-products", type=int, default=None)
    p.add_argument(
        "--data",
        choices=["sr", "if", "both"],
        default="sr",
        help="Qué descargar: sr (índices), if (cubo I/F) o both (default: sr)",
    )


def _add_process(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "export",
        help="[Opcional] Exportar copia GeoTIFF desde cubo ENVI",
    )
    p.add_argument("--input", type=Path, required=True, help="Directorio o .hdr/.img del producto")
    p.add_argument("--out", type=Path, default=None)


def _add_maps(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("maps", help="Generar mapas minerales y browse products")
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--out", type=Path, default=None)
    p.add_argument(
        "--browse",
        nargs="+",
        default=["MAF", "PHY", "HYD", "CAR", "FEM"],
        help="Códigos browse (TRU, MAF, PHY, ...)",
    )


def _add_detect(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("detect", help="Detección binaria de minerales")
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--out", type=Path, default=None)
    p.add_argument(
        "--mineral",
        nargs="+",
        default=None,
        help="Claves de mineral (olivine, hcp, mg_carbonate, ...)",
    )


def _add_classify(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("classify", help="Clasificación de unidades geológicas")
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--out", type=Path, default=None)
    p.add_argument(
        "--method",
        choices=["kmeans", "signature", "supervised"],
        default="kmeans",
    )
    p.add_argument("--n-clusters", type=int, default=None)
    p.add_argument(
        "--training",
        "--training-csv",
        dest="training",
        type=Path,
        default=None,
        help="CSV (row,col,unit), GeoJSON o Shapefile con atributo de clase",
    )
    p.add_argument(
        "--unit-field",
        default="unit",
        help="Nombre del atributo de clase en GeoJSON/Shapefile (default: unit)",
    )


def _add_run(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("run", help="Pipeline completo: maps, detect, classify sobre cubo ENVI")
    p.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Directorio ENVI en data/raw o ruta al .hdr/.img",
    )
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--method", choices=["kmeans", "signature"], default="kmeans")
    p.add_argument("--n-clusters", type=int, default=5)


def cmd_download(args: argparse.Namespace) -> int:
    from .download import download_batch, download_scene

    out = args.out or resolve_path("raw")
    data = args.data
    if args.ids_file:
        from .download import parse_ids_file

        ids = parse_ids_file(args.ids_file)
        if not ids:
            raise SystemExit(f"No se encontraron Product IDs en {args.ids_file}")
        download_batch(ids, out, max_products=args.max_products, data=data)
    elif args.pdsid:
        download_scene(
            pdsid=args.pdsid, out_dir=out, max_products=args.max_products, data=data
        )
    else:
        download_scene(
            bbox=tuple(args.bbox), out_dir=out, max_products=args.max_products, data=data
        )
    print(f"Descarga completada en {out} (datos: {data})")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from .io_sr import export_geotiff, load_sr_cube

    out_root = args.out or resolve_path("processed")
    cube = load_sr_cube(args.input)
    tif_path = out_root / f"{cube.product_id}.tif"
    export_geotiff(cube, tif_path)
    print(f"GeoTIFF exportado: {tif_path}")
    print(f"Bandas: {len(cube.band_names)} | Forma: {cube.data.shape}")
    return 0


def cmd_maps(args: argparse.Namespace) -> int:
    from .maps import generate_all_maps

    out = args.out or resolve_path("maps")
    paths = generate_all_maps(args.input, out, browse_codes=args.browse)
    print(f"Mapas generados: {len(paths)} en {out}")
    return 0


def cmd_detect(args: argparse.Namespace) -> int:
    from .detection import run_detection_pipeline

    out = args.out or (resolve_path("maps") / "detection")
    results = run_detection_pipeline(args.input, out, args.mineral)
    for key, res in results.items():
        print(f"  {key}: {res.coverage_pct:.2f}% cobertura")
    return 0


def cmd_classify(args: argparse.Namespace) -> int:
    from .classification import run_classification_pipeline

    out = args.out or (resolve_path("maps") / "classification")
    run_classification_pipeline(
        args.input,
        out,
        method=args.method,
        n_clusters=args.n_clusters,
        training_path=args.training,
        unit_field=args.unit_field,
    )
    print(f"Clasificación guardada en {out}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from .classification import run_classification_pipeline
    from .detection import run_detection_pipeline
    from .io_sr import load_sr_cube
    from .maps import generate_all_maps

    cube = load_sr_cube(args.input)
    maps_dir = resolve_path("maps") / cube.product_id
    generate_all_maps(args.input, maps_dir)
    run_detection_pipeline(args.input, maps_dir / "detection")
    run_classification_pipeline(
        args.input,
        maps_dir / "classification",
        method=args.method,
        n_clusters=args.n_clusters,
    )
    print(f"Pipeline completo para {cube.product_id}")
    print(f"Salidas en {maps_dir}")
    return 0


def _add_extract_if(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "extract-if",
        help="Extraer firmas I/F de píxeles bajo un GeoJSON/Shapefile a CSV",
    )
    p.add_argument("--input", type=Path, required=True, help="Directorio o .hdr del cubo IF")
    p.add_argument(
        "--vector",
        type=Path,
        required=True,
        help="GeoJSON o Shapefile (puntos, líneas o polígonos)",
    )
    p.add_argument("--out", type=Path, required=True, help="CSV de salida")
    p.add_argument(
        "--label-field",
        default=None,
        help="Atributo opcional a copiar como etiqueta (p. ej. unit)",
    )
    p.add_argument(
        "--max-pixels-per-feature",
        type=int,
        default=20_000,
        help="Tope de píxeles por geometría (default 20000)",
    )


def cmd_extract_if(args: argparse.Namespace) -> int:
    from .io_if import export_spectra_from_vector, load_if_cube

    cube = load_if_cube(args.input)
    out, n = export_spectra_from_vector(
        cube,
        args.vector,
        args.out,
        label_field=args.label_field,
        max_pixels_per_feature=args.max_pixels_per_feature,
    )
    print(f"Exportadas {n} firmas I/F → {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crism-pipeline",
        description="Pipeline CRISM MTRDR SR — Viviano-Beck et al. (2014)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    _add_download(sub)
    _add_process(sub)  # comando "export"
    _add_maps(sub)
    _add_detect(sub)
    _add_classify(sub)
    _add_extract_if(sub)
    _add_run(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "download": cmd_download,
        "export": cmd_export,
        "process": cmd_export,  # alias legado
        "maps": cmd_maps,
        "detect": cmd_detect,
        "classify": cmd_classify,
        "extract-if": cmd_extract_if,
        "run": cmd_run,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
