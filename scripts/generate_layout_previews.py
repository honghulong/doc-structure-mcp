import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from layout_preview import build_overlay_html, build_plain_docx


def main():
    if len(sys.argv) != 2:
        print("Usage: python scripts/generate_layout_previews.py <three-model-result-dir>")
        return 2

    result_dir = Path(sys.argv[1]).resolve()
    output_dir = result_dir / "layout_preview_v2"
    output_dir.mkdir(parents=True, exist_ok=True)

    generated = []
    for model_dir in ["mobile", "mid", "server"]:
        source = result_dir / model_dir / "ocr_result.json"
        if not source.exists():
            continue
        result = json.loads(source.read_text(encoding="utf-8"))
        html_path = output_dir / f"{model_dir}_overlay.html"
        docx_path = output_dir / f"{model_dir}_plain.docx"
        html_path.write_text(
            build_overlay_html(result, image_root=source.parent),
            encoding="utf-8",
        )
        actual_docx_path = _write_bytes_with_fallback(docx_path, build_plain_docx(result))
        generated.extend([html_path, actual_docx_path])

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
