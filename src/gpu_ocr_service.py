# -*- coding: utf-8 -*-
"""
Generic PaddleOCR GPU service.

Boundary:
- Provides only GET /health and POST /ocr.
- Returns generic OCR JSON.
- Does not rebuild DOCX, extract invoice fields, or apply business rules.
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
os.environ.setdefault("PPOCR_HOME", str(BASE_DIR / ".paddleocr_home"))
os.environ.setdefault("PADDLEX_HOME", str(BASE_DIR / ".paddlex_home"))

from flask import Flask, jsonify, request
from flask_cors import CORS


app = Flask(__name__)
CORS(app)

START_TIME = time.time()
HOST = os.environ.get("GPU_OCR_HOST", "0.0.0.0")
PORT = int(os.environ.get("GPU_OCR_PORT", "8769"))
MAX_FILE_SIZE = int(os.environ.get("GPU_OCR_MAX_FILE_SIZE_MB", "50")) * 1024 * 1024
DEFAULT_DPI = int(os.environ.get("GPU_OCR_DPI", "220"))
DEFAULT_MAX_PAGES = int(os.environ.get("GPU_OCR_MAX_PAGES", "50"))
DEFAULT_DET_MODEL = os.environ.get("GPU_OCR_DET_MODEL", "PP-OCRv5_server_det")
DEFAULT_REC_MODEL = os.environ.get("GPU_OCR_REC_MODEL", "PP-OCRv5_server_rec")

TEMP_DIR = BASE_DIR / ".tmp" / "gpu_ocr"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

_ocr_lock = threading.Lock()
_ocr_instance = None
_runtime_cache = None


def runtime_info() -> dict:
    global _runtime_cache
    if _runtime_cache is None:
        import paddle

        _runtime_cache = {
            "paddle_version": getattr(paddle, "__version__", ""),
            "cuda_enabled": bool(paddle.is_compiled_with_cuda()),
        }
    return dict(_runtime_cache)


def get_ocr():
    global _ocr_instance
    if _ocr_instance is None:
        with _ocr_lock:
            if _ocr_instance is None:
                print(
                    f"[GPU OCR] Loading PaddleOCR det={DEFAULT_DET_MODEL} rec={DEFAULT_REC_MODEL}",
                    flush=True,
                )
                started = time.time()
                from paddleocr import PaddleOCR

                _ocr_instance = PaddleOCR(
                    text_detection_model_name=DEFAULT_DET_MODEL,
                    text_recognition_model_name=DEFAULT_REC_MODEL,
                    use_textline_orientation=True,
                    lang="ch",
                    text_det_thresh=0.3,
                    text_det_box_thresh=0.5,
                )
                print(f"[GPU OCR] OCR loaded in {time.time() - started:.1f}s", flush=True)
    return _ocr_instance


def save_upload() -> tuple[Path | None, tuple | None]:
    if "file" not in request.files:
        return None, (jsonify({"status": "error", "error": "missing file field"}), 400)

    upload = request.files["file"]
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in {".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}:
        return None, (jsonify({"status": "error", "error": f"unsupported file type: {suffix}"}), 400)

    path = TEMP_DIR / f"upload_{uuid.uuid4().hex}{suffix}"
    upload.save(str(path))
    if path.stat().st_size > MAX_FILE_SIZE:
        path.unlink(missing_ok=True)
        return None, (jsonify({"status": "error", "error": "file too large"}), 413)
    return path, None


def render_input(input_path: Path, dpi: int, max_pages: int) -> list[dict]:
    import cv2
    import fitz

    if input_path.suffix.lower() == ".pdf":
        doc = fitz.open(str(input_path))
        pages = []
        matrix = fitz.Matrix(dpi / 72, dpi / 72)
        try:
            for index, page in enumerate(doc, start=1):
                if index > max_pages:
                    break
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                image_path = TEMP_DIR / f"page_{uuid.uuid4().hex}.png"
                pix.save(str(image_path))
                image = cv2.imread(str(image_path))
                image_path.unlink(missing_ok=True)
                if image is None:
                    raise RuntimeError(f"cannot read rendered page {index}")
                pages.append({"page_no": index, "image": image})
        finally:
            doc.close()
        return pages

    image = cv2.imread(str(input_path))
    if image is None:
        raise RuntimeError(f"cannot read image: {input_path.name}")
    return [{"page_no": 1, "image": image}]


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
                    points.append([round(float(point[0]), 1), round(float(point[1]), 1)])
            if len(points) < 4:
                continue

            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            lines.append({
                "text": text,
                "confidence": round(score, 6),
                "bbox": points,
                "x1": min(xs),
                "y1": min(ys),
                "x2": max(xs),
                "y2": max(ys),
            })
    return lines


@app.get("/")
def index():
    return jsonify({
        "status": "ok",
        "service": "PaddleOCR GPU Service",
        "endpoints": {
            "GET /health": "health check",
            "POST /ocr": "generic OCR JSON",
        },
    })


@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "paddleocr-gpu",
        "port": PORT,
        "model": {"det": DEFAULT_DET_MODEL, "rec": DEFAULT_REC_MODEL},
        "runtime": runtime_info(),
        "uptime_seconds": round(time.time() - START_TIME, 1),
    })


@app.post("/ocr")
def ocr():
    input_path, error_response = save_upload()
    if error_response:
        return error_response

    assert input_path is not None
    started = time.time()
    dpi = request.form.get("dpi", DEFAULT_DPI, type=int)
    max_pages = request.form.get("max_pages", DEFAULT_MAX_PAGES, type=int)

    try:
        ocr_engine = get_ocr()
        pages = []
        for rendered in render_input(input_path, dpi=dpi, max_pages=max_pages):
            page_started = time.time()
            image = rendered["image"]
            lines = extract_lines(list(ocr_engine.predict(image)))
            lines.sort(key=lambda item: (item["y1"], item["x1"]))
            pages.append({
                "page_no": rendered["page_no"],
                "width": int(image.shape[1]),
                "height": int(image.shape[0]),
                "duration_ms": int((time.time() - page_started) * 1000),
                "lines": lines,
                "plain_text": "\n".join(line["text"] for line in lines),
            })

        return jsonify({
            "status": "ok",
            "engine": "paddleocr",
            "model": {"det": DEFAULT_DET_MODEL, "rec": DEFAULT_REC_MODEL},
            "runtime": runtime_info(),
            "input": {
                "filename": Path(request.files["file"].filename or input_path.name).name,
                "type": input_path.suffix.lower().lstrip("."),
                "dpi": dpi,
                "max_pages": max_pages,
            },
            "pages": pages,
            "timing": {"total_ms": int((time.time() - started) * 1000)},
        })
    except Exception as exc:
        import traceback

        traceback.print_exc()
        return jsonify({"status": "error", "error": str(exc)}), 500
    finally:
        input_path.unlink(missing_ok=True)


if __name__ == "__main__":
    print(f"[GPU OCR] Starting on {HOST}:{PORT}", flush=True)
    app.run(host=HOST, port=PORT, debug=False)
