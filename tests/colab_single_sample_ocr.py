# -*- coding: utf-8 -*-
"""
Colab single-sample OCR export.

Usage in Colab:
  1. Upload one sample, for example test_page1.png or test_page2.pdf.
  2. Upload this script.
  3. Run:
       !python colab_single_sample_ocr.py --input test_page1.png --case red_header_v1
     or:
       !python colab_single_sample_ocr.py --input test_page2.pdf --case invoice_v1
  4. Download the generated zip from /content/ocr_lab_outputs/<case>.zip.
"""
from __future__ import annotations

import argparse
import json
import time
import zipfile
from pathlib import Path


def _install_hint() -> str:
    return (
        "If imports fail in Colab, run:\n"
        "!pip install -q paddlepaddle-gpu paddleocr==3.6.0 pymupdf opencv-python-headless pillow"
    )


def _render_input(input_path: Path, dpi: int) -> list[dict]:
    import cv2
    import fitz

    suffix = input_path.suffix.lower()
    if suffix == ".pdf":
        doc = fitz.open(str(input_path))
        pages = []
        matrix = fitz.Matrix(dpi / 72, dpi / 72)
        for index, page in enumerate(doc, start=1):
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            page_png = input_path.with_name(f"{input_path.stem}_page_{index:03d}.png")
            pix.save(str(page_png))
            image = cv2.imread(str(page_png))
            pages.append({"page_no": index, "image_path": page_png, "image": image})
        doc.close()
        return pages

    image = cv2.imread(str(input_path))
    if image is None:
        raise RuntimeError(f"Cannot read image: {input_path}")
    return [{"page_no": 1, "image_path": input_path, "image": image}]


def _extract_lines(predict_result) -> list[dict]:
    lines = []
    for result in predict_result:
        if result is None:
            continue
        if isinstance(result, dict):
            texts = result.get("rec_texts", [])
            scores = result.get("rec_scores", [])
            polys = result.get("dt_polys", [])
        else:
            texts = getattr(result, "rec_texts", [])
            scores = getattr(result, "rec_scores", [])
            polys = getattr(result, "dt_polys", [])

        for index, text in enumerate(texts):
            text = str(text).strip()
            if not text:
                continue
            score = float(scores[index]) if index < len(scores) else 0.0
            poly = polys[index] if index < len(polys) else None
            if poly is None:
                continue
            points = []
            for point in poly:
                if len(point) >= 2:
                    points.append([float(point[0]), float(point[1])])
            if len(points) < 4:
                continue
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            lines.append({
                "text": text,
                "confidence": score,
                "bbox": points,
                "x1": min(xs),
                "y1": min(ys),
                "x2": max(xs),
                "y2": max(ys),
            })
    return lines


def _write_markdown(case: str, payload: dict, output_path: Path) -> None:
    lines = [
        f"# OCR Lab Result: {case}",
        "",
        f"- input: `{payload['input_file']}`",
        f"- model: `{payload['model']['det']}` + `{payload['model']['rec']}`",
        f"- dpi: `{payload['dpi']}`",
        f"- pages: `{len(payload['pages'])}`",
        f"- total_duration_ms: `{payload['total_duration_ms']}`",
        "",
        "## Pages",
        "",
    ]
    for page in payload["pages"]:
        lines.extend([
            f"### Page {page['page_no']}",
            "",
            f"- image: `{page['image_file']}`",
            f"- size: `{page['width']}x{page['height']}`",
            f"- duration_ms: `{page['duration_ms']}`",
            f"- line_count: `{len(page['lines'])}`",
            "",
            "```text",
            page["plain_text"],
            "```",
            "",
        ])
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="PDF or image file uploaded to Colab")
    parser.add_argument("--case", default=None, help="Case name for output directory")
    parser.add_argument("--det", default="PP-OCRv5_server_det")
    parser.add_argument("--rec", default="PP-OCRv5_server_rec")
    parser.add_argument("--dpi", type=int, default=220)
    args = parser.parse_args()

    try:
        import cv2  # noqa: F401
        from paddleocr import PaddleOCR
    except Exception as exc:
        raise SystemExit(f"{exc}\n\n{_install_hint()}") from exc

    input_path = Path(args.input).resolve()
    case = args.case or input_path.stem
    output_dir = Path("/content/ocr_lab_outputs") / case
    output_dir.mkdir(parents=True, exist_ok=True)

    ocr = PaddleOCR(
        text_detection_model_name=args.det,
        text_recognition_model_name=args.rec,
        use_textline_orientation=True,
        lang="ch",
        text_det_thresh=0.3,
        text_det_box_thresh=0.5,
    )

    total_start = time.time()
    pages = []
    for page in _render_input(input_path, args.dpi):
        page_start = time.time()
        predict_result = list(ocr.predict(page["image"]))
        lines = _extract_lines(predict_result)
        lines.sort(key=lambda item: (item["y1"], item["x1"]))

        image_name = f"page_{page['page_no']:03d}.png"
        image_out = output_dir / image_name
        import cv2
        cv2.imwrite(str(image_out), page["image"])

        pages.append({
            "page_no": page["page_no"],
            "image_file": image_name,
            "width": int(page["image"].shape[1]),
            "height": int(page["image"].shape[0]),
            "duration_ms": int((time.time() - page_start) * 1000),
            "lines": lines,
            "plain_text": "\n".join(line["text"] for line in lines),
        })

    payload = {
        "case": case,
        "input_file": input_path.name,
        "dpi": args.dpi,
        "model": {"det": args.det, "rec": args.rec},
        "total_duration_ms": int((time.time() - total_start) * 1000),
        "pages": pages,
    }
    json_path = output_dir / "ocr_result.json"
    md_path = output_dir / "ocr_result.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(case, payload, md_path)

    zip_path = output_dir.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in output_dir.rglob("*"):
            archive.write(path, path.relative_to(output_dir.parent))

    print(f"Output directory: {output_dir}")
    print(f"Download zip: {zip_path}")


if __name__ == "__main__":
    main()
