# -*- coding: utf-8 -*-
"""
Run one OCR sample on the GPU server and export a Colab-compatible result.

Example:
  python scripts/run_gpu_ocr_sample.py ^
    --input tests/fixtures/test_page2.pdf ^
    --case test_page2_gpu_server_v1
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def render_input(input_path: Path, output_dir: Path, dpi: int) -> list[dict]:
    import cv2
    import fitz

    suffix = input_path.suffix.lower()
    if suffix == ".pdf":
        doc = fitz.open(str(input_path))
        pages = []
        matrix = fitz.Matrix(dpi / 72, dpi / 72)
        for index, page in enumerate(doc, start=1):
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            page_png = output_dir / f"page_{index:03d}.png"
            pix.save(str(page_png))
            image = cv2.imread(str(page_png))
            if image is None:
                raise RuntimeError(f"Cannot read rendered image: {page_png}")
            pages.append({"page_no": index, "image_file": page_png.name, "image": image})
        doc.close()
        return pages

    image = cv2.imread(str(input_path))
    if image is None:
        raise RuntimeError(f"Cannot read image: {input_path}")
    image_out = output_dir / "page_001.png"
    cv2.imwrite(str(image_out), image)
    return [{"page_no": 1, "image_file": image_out.name, "image": image}]


def extract_lines(predict_result) -> list[dict]:
    lines = []
    for result in predict_result:
        if result is None:
            continue
        if isinstance(result, dict):
            texts = result.get("rec_texts", []) or []
            scores = result.get("rec_scores", []) or []
            polys = result.get("dt_polys", []) or []
        else:
            texts = getattr(result, "rec_texts", []) or []
            scores = getattr(result, "rec_scores", []) or []
            polys = getattr(result, "dt_polys", []) or []

        for index, text in enumerate(texts):
            text = str(text or "").strip()
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


def write_markdown(case: str, payload: dict, output_path: Path) -> None:
    lines = [
        f"# GPU OCR Result: {case}",
        "",
        f"- input: `{payload['input_file']}`",
        f"- model: `{payload['model']['det']}` + `{payload['model']['rec']}`",
        f"- dpi: `{payload['dpi']}`",
        f"- pages: `{len(payload['pages'])}`",
        f"- total_duration_ms: `{payload['total_duration_ms']}`",
        f"- paddle_version: `{payload['runtime']['paddle_version']}`",
        f"- cuda_enabled: `{payload['runtime']['cuda_enabled']}`",
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
    parser.add_argument("--input", required=True, help="PDF or image path")
    parser.add_argument("--case", default=None, help="Output case name")
    parser.add_argument("--out-root", default="logs/gpu_ocr", help="Output root directory")
    parser.add_argument("--det", default="PP-OCRv5_server_det")
    parser.add_argument("--rec", default="PP-OCRv5_server_rec")
    parser.add_argument("--dpi", type=int, default=220)
    args = parser.parse_args()

    import paddle
    from paddleocr import PaddleOCR

    input_path = Path(args.input).resolve()
    case = args.case or input_path.stem
    output_dir = Path(args.out_root).resolve() / case
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
    for page in render_input(input_path, output_dir, args.dpi):
        page_start = time.time()
        predict_result = list(ocr.predict(page["image"]))
        lines = extract_lines(predict_result)
        lines.sort(key=lambda item: (item["y1"], item["x1"]))
        image = page["image"]
        pages.append({
            "page_no": page["page_no"],
            "image_file": page["image_file"],
            "width": int(image.shape[1]),
            "height": int(image.shape[0]),
            "duration_ms": int((time.time() - page_start) * 1000),
            "lines": lines,
            "plain_text": "\n".join(line["text"] for line in lines),
        })

    payload = {
        "case": case,
        "input_file": input_path.name,
        "dpi": args.dpi,
        "model": {"det": args.det, "rec": args.rec},
        "runtime": {
            "paddle_version": getattr(paddle, "__version__", ""),
            "cuda_enabled": bool(paddle.is_compiled_with_cuda()),
        },
        "total_duration_ms": int((time.time() - total_start) * 1000),
        "pages": pages,
    }
    (output_dir / "ocr_result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_markdown(case, payload, output_dir / "ocr_result.md")

    print(f"Output directory: {output_dir}")
    print(f"Result JSON: {output_dir / 'ocr_result.json'}")
    print(f"CUDA enabled: {payload['runtime']['cuda_enabled']}")


if __name__ == "__main__":
    main()
