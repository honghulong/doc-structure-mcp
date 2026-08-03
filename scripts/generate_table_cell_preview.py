import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from table_cell_mapper import build_cell_docx, build_cell_overlay_html, load_structure_result, map_table_cells


def main():
    if len(sys.argv) != 2:
        print("Usage: python scripts/generate_table_cell_preview.py <table-probe-result-dir>")
        return 2

    result_dir = Path(sys.argv[1]).resolve()
    output_dir = result_dir / "cell_mapping_preview"
    output_dir.mkdir(parents=True, exist_ok=True)

    generated = []
    for page_dir in sorted(result_dir.glob("page_*")):
        source = page_dir / "structure_res_000.json"
        if not source.exists():
            continue
        result = load_structure_result(source)
        mapping = map_table_cells(result)
        page_name = page_dir.name
        json_path = output_dir / f"{page_name}_cells.json"
        html_path = output_dir / f"{page_name}_cells.html"
        docx_path = output_dir / f"{page_name}_cells.docx"
        json_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
        html_path.write_text(
            build_cell_overlay_html(mapping, image_path=page_dir / "page.png"),
            encoding="utf-8",
        )
        actual_docx_path = _write_bytes_with_fallback(docx_path, build_cell_docx(mapping))
        generated.extend([json_path, html_path, actual_docx_path])

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
