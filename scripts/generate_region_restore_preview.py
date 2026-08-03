import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from table_region_restorer import (
    build_region_docx,
    build_region_overlay_html,
    build_region_paint_html,
    load_detected_regions,
    load_overall_ocr,
    load_page_blocks,
    load_table_ocr,
    restore_table_regions,
)


def main():
    if len(sys.argv) != 3:
        print("Usage: python scripts/generate_region_restore_preview.py <table-probe-dir> <cell-detection-dir>")
        return 2

    table_probe_dir = Path(sys.argv[1]).resolve()
    cell_detection_dir = Path(sys.argv[2]).resolve()
    output_dir = table_probe_dir / "region_restore_preview"
    output_dir.mkdir(parents=True, exist_ok=True)

    generated = []
    for page_dir in sorted(table_probe_dir.glob("page_*")):
        page_name = page_dir.name
        ocr_path = page_dir / "official_json" / "page_res.json"
        region_path = cell_detection_dir / page_name / "cells_det_res_000.json"
        if not ocr_path.exists() or not region_path.exists():
            continue
        restored = restore_table_regions(
            load_detected_regions(region_path),
            load_table_ocr(ocr_path),
            supplemental_ocr=load_overall_ocr(ocr_path),
        )
        restored["page_blocks"] = load_page_blocks(ocr_path)
        json_path = output_dir / f"{page_name}_regions.json"
        html_path = output_dir / f"{page_name}_regions.html"
        paint_html_path = output_dir / f"{page_name}_regions_paint.html"
        docx_path = output_dir / f"{page_name}_regions.docx"
        json_path.write_text(json.dumps(restored, ensure_ascii=False, indent=2), encoding="utf-8")
        html_path.write_text(
            build_region_overlay_html(restored, image_path=page_dir / "page.png"),
            encoding="utf-8",
        )
        paint_html_path.write_text(
            build_region_paint_html(restored, image_path=page_dir / "page.png"),
            encoding="utf-8",
        )
        actual_docx_path = _write_bytes_with_fallback(docx_path, build_region_docx(restored))
        generated.extend([json_path, html_path, paint_html_path, actual_docx_path])

    for path in generated:
        print(path)
    return 0


def _write_bytes_with_fallback(path, data):
    try:
        path.write_bytes(data)
        return path
    except PermissionError:
        fallback = path.with_name(f"{path.stem}_new{path.suffix}")
        fallback.write_bytes(data)
        return fallback


if __name__ == "__main__":
    raise SystemExit(main())
