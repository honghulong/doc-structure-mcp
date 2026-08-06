# -*- coding: utf-8 -*-
"""
Generic PaddleOCR GPU service.

Boundary:
- Provides GET /health, POST /ocr, and POST /structure.
- Returns OCR JSON plus optional document/table structure JSON.
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
DEFAULT_STRUCTURE = os.environ.get("GPU_OCR_DEFAULT_STRUCTURE", "0").lower() in {"1", "true", "yes"}
TABLE_CELL_MODEL = os.environ.get("GPU_TABLE_CELL_MODEL", "RT-DETR-L_wired_table_cell_det")

TEMP_DIR = BASE_DIR / ".tmp" / "gpu_ocr"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

_ocr_lock = threading.Lock()
_ocr_instance = None
_structure_lock = threading.Lock()
_structure_instance = None
_cells_lock = threading.Lock()
_cells_instance = None
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


def get_structure_pipeline():
    global _structure_instance
    if _structure_instance is None:
        with _structure_lock:
            if _structure_instance is None:
                print("[GPU OCR] Loading PPStructureV3", flush=True)
                started = time.time()
                from paddleocr import PPStructureV3

                attempts = [
                    {
                        "use_doc_orientation_classify": False,
                        "use_doc_unwarping": False,
                        "use_textline_orientation": False,
                        "use_formula_recognition": False,
                        "use_seal_recognition": False,
                        "use_table_recognition": True,
                        "use_wired_table_cells_trans_to_html": True,
                        "use_ocr_results_with_table_cells": True,
                        "device": "gpu",
                    },
                    {
                        "use_doc_orientation_classify": False,
                        "use_doc_unwarping": False,
                        "use_textline_orientation": False,
                        "use_formula_recognition": False,
                        "use_seal_recognition": False,
                        "use_table_recognition": True,
                        "device": "gpu",
                    },
                    {"device": "gpu"},
                    {},
                ]
                last_error = None
                for kwargs in attempts:
                    try:
                        _structure_instance = {"pipeline": PPStructureV3(**kwargs), "kwargs": kwargs}
                        print(f"[GPU OCR] PPStructureV3 loaded in {time.time() - started:.1f}s kwargs={kwargs}", flush=True)
                        break
                    except (TypeError, ValueError) as exc:
                        last_error = exc
                        print(f"[GPU OCR] PPStructureV3 kwargs rejected: {exc}", flush=True)
                if _structure_instance is None:
                    raise last_error
    return _structure_instance


def get_cells_detector():
    global _cells_instance
    if _cells_instance is None:
        with _cells_lock:
            if _cells_instance is None:
                print(f"[GPU OCR] Loading TableCellsDetection model={TABLE_CELL_MODEL}", flush=True)
                started = time.time()
                from paddleocr import TableCellsDetection

                attempts = [
                    {"model_name": TABLE_CELL_MODEL, "device": "gpu"},
                    {"model_name": TABLE_CELL_MODEL},
                    {},
                ]
                last_error = None
                for kwargs in attempts:
                    try:
                        _cells_instance = {"pipeline": TableCellsDetection(**kwargs), "kwargs": kwargs}
                        print(f"[GPU OCR] TableCellsDetection loaded in {time.time() - started:.1f}s kwargs={kwargs}", flush=True)
                        break
                    except (TypeError, ValueError) as exc:
                        last_error = exc
                        print(f"[GPU OCR] TableCellsDetection kwargs rejected: {exc}", flush=True)
                if _cells_instance is None:
                    raise last_error
    return _cells_instance


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
                if image is None:
                    image_path.unlink(missing_ok=True)
                    raise RuntimeError(f"cannot read rendered page {index}")
                pages.append({"page_no": index, "image": image, "image_path": image_path})
        finally:
            doc.close()
        return pages

    image = cv2.imread(str(input_path))
    if image is None:
        raise RuntimeError(f"cannot read image: {input_path.name}")
    return [{"page_no": 1, "image": image, "image_path": input_path}]


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


def jsonable(obj):
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(key): jsonable(value) for key, value in obj.items() if key != "img"}
    if isinstance(obj, (list, tuple)):
        return [jsonable(value) for value in obj]
    if hasattr(obj, "tolist"):
        return jsonable(obj.tolist())
    if hasattr(obj, "json"):
        return jsonable(obj.json)
    return str(obj)


def normalize_pipeline_results(output) -> list[dict]:
    results = []
    for result in output:
        results.append(jsonable(result.json if hasattr(result, "json") else result))
    return results


def unwrap_result(payload):
    if isinstance(payload, dict) and isinstance(payload.get("res"), dict):
        return payload["res"]
    return payload


def box_to_rect(box):
    if not box:
        return None
    if isinstance(box, dict):
        for key in ["coordinate", "bbox", "box", "cell_box", "table_bbox"]:
            if key in box:
                return box_to_rect(box[key])
    if isinstance(box, (list, tuple)):
        if len(box) == 4 and all(isinstance(value, (int, float)) for value in box):
            x1, y1, x2, y2 = [float(value) for value in box]
            return [x1, y1, x2, y2]
        points = []
        for item in box:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                points.append([float(item[0]), float(item[1])])
        if points:
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            return [min(xs), min(ys), max(xs), max(ys)]
    return None


def find_table_items(page_json) -> list[dict]:
    result = unwrap_result(page_json)
    tables = []
    if isinstance(result, dict):
        for key in ["table_res_list", "tables", "table_results"]:
            value = result.get(key)
            if isinstance(value, list):
                tables.extend(item for item in value if isinstance(item, dict))
    return tables


def collect_cell_boxes(table_item) -> list[list[float]]:
    boxes = []
    if not isinstance(table_item, dict):
        return boxes
    for key in ["cell_box_list", "cell_bbox_list", "cells"]:
        value = table_item.get(key)
        if isinstance(value, list):
            for item in value:
                rect = box_to_rect(item)
                if rect:
                    boxes.append(rect)
    return boxes


def extract_detected_regions(cells_json) -> list[dict]:
    result = unwrap_result(cells_json)
    boxes = result.get("boxes") if isinstance(result, dict) else []
    regions = []
    if not isinstance(boxes, list):
        return regions
    for index, box in enumerate(boxes, start=1):
        rect = box_to_rect(box)
        if not rect:
            continue
        label = box.get("label", "cell") if isinstance(box, dict) else "cell"
        score = box.get("score", 0.0) if isinstance(box, dict) else 0.0
        regions.append({"index": index, "label": label, "score": score, "coordinate": rect})
    return regions


def build_structure_summary(structure_json, cells_json=None) -> dict:
    tables = find_table_items(structure_json)
    normalized_tables = []
    for index, table in enumerate(tables, start=1):
        cells = collect_cell_boxes(table)
        table_rect = box_to_rect(table.get("bbox") or table.get("box") or table.get("table_bbox"))
        normalized_tables.append({
            "index": index,
            "bbox": table_rect,
            "cell_box_count": len(cells),
            "cell_boxes": cells,
            "has_pred_html": bool(table.get("pred_html") or table.get("html")),
            "pred_html": table.get("pred_html") or table.get("html") or "",
            "has_table_ocr": bool(table.get("table_ocr_pred")),
            "table_ocr_pred": table.get("table_ocr_pred") or {},
        })
    regions = extract_detected_regions(cells_json or {})
    return {
        "table_count": len(normalized_tables),
        "cell_box_count": sum(table["cell_box_count"] for table in normalized_tables),
        "region_count": len(regions),
        "tables": normalized_tables,
        "regions": regions,
    }


def build_restore_payload(structure_json, cells_json=None) -> dict:
    from table_cell_mapper import map_table_cells
    from table_region_restorer import restore_table_regions

    result = unwrap_result(structure_json)
    payload = {"cell_mapping": map_table_cells(result)}
    regions = extract_detected_regions(cells_json or {})
    tables = find_table_items(result)
    table_ocr = tables[0].get("table_ocr_pred", {}) if tables else {}
    supplemental_ocr = result.get("overall_ocr_res", {}) if isinstance(result, dict) else {}
    if regions:
        restored = restore_table_regions(regions, table_ocr, supplemental_ocr=supplemental_ocr)
        restored["page_blocks"] = _page_blocks_from_structure(result)
        payload["region_restore"] = restored
    return payload


def _page_blocks_from_structure(result) -> list[dict]:
    blocks = []
    parsing = result.get("parsing_res_list") if isinstance(result, dict) else None
    if not isinstance(parsing, list):
        return blocks
    for block in parsing:
        label = block.get("block_label", "")
        if label in {"table", "seal", "image"}:
            continue
        text = str(block.get("block_content", "")).strip()
        rect = box_to_rect(block.get("block_bbox"))
        if text and rect:
            blocks.append({"label": label, "text": text, "rect": rect})
    return blocks


@app.get("/")
def index():
    return jsonify({
        "status": "ok",
        "service": "PaddleOCR GPU Service",
        "endpoints": {
            "GET /health": "health check",
            "POST /ocr": "generic OCR JSON; pass structure=1 for full structure",
            "POST /structure": "OCR + PPStructureV3 + TableCellsDetection JSON",
        },
    })


@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "paddleocr-gpu",
        "port": PORT,
        "model": {
            "det": DEFAULT_DET_MODEL,
            "rec": DEFAULT_REC_MODEL,
            "table_cell": TABLE_CELL_MODEL,
        },
        "defaults": {
            "dpi": DEFAULT_DPI,
            "max_pages": DEFAULT_MAX_PAGES,
            "structure": DEFAULT_STRUCTURE,
        },
        "runtime": runtime_info(),
        "uptime_seconds": round(time.time() - START_TIME, 1),
    })


def process_request(force_structure: bool = False):
    input_path, error_response = save_upload()
    if error_response:
        return error_response

    assert input_path is not None
    started = time.time()
    dpi = request.form.get("dpi", DEFAULT_DPI, type=int)
    max_pages = request.form.get("max_pages", DEFAULT_MAX_PAGES, type=int)
    include_structure = force_structure or request.form.get("structure", str(int(DEFAULT_STRUCTURE))).lower() in {"1", "true", "yes", "full"}
    include_cells = request.form.get("cells", "1").lower() in {"1", "true", "yes"}
    include_restore = request.form.get("restore", "1").lower() in {"1", "true", "yes"}
    rendered_pages = []

    try:
        ocr_engine = get_ocr()
        structure_ref = get_structure_pipeline() if include_structure else None
        cells_ref = get_cells_detector() if include_structure and include_cells else None
        pages = []
        rendered_pages = render_input(input_path, dpi=dpi, max_pages=max_pages)
        for rendered in rendered_pages:
            page_started = time.time()
            image = rendered["image"]
            lines = extract_lines(list(ocr_engine.predict(image)))
            lines.sort(key=lambda item: (item["y1"], item["x1"]))
            page_payload = {
                "page_no": rendered["page_no"],
                "width": int(image.shape[1]),
                "height": int(image.shape[0]),
                "lines": lines,
                "plain_text": "\n".join(line["text"] for line in lines),
            }
            if include_structure and structure_ref:
                structure_started = time.time()
                image_path = str(rendered["image_path"])
                structure_results = normalize_pipeline_results(structure_ref["pipeline"].predict(image_path))
                structure_json = structure_results[0] if structure_results else {}
                cells_results = []
                cells_json = {}
                if cells_ref:
                    cells_results = normalize_pipeline_results(cells_ref["pipeline"].predict(image_path, threshold=0.3, batch_size=1))
                    cells_json = cells_results[0] if cells_results else {}
                page_payload["structure"] = {
                    "pipeline": "PPStructureV3",
                    "pipeline_kwargs": structure_ref["kwargs"],
                    "raw_results": structure_results,
                    "summary": build_structure_summary(structure_json, cells_json),
                    "duration_ms": int((time.time() - structure_started) * 1000),
                }
                if cells_ref:
                    page_payload["table_cells_detection"] = {
                        "pipeline": "TableCellsDetection",
                        "pipeline_kwargs": cells_ref["kwargs"],
                        "raw_results": cells_results,
                    }
                if include_restore:
                    page_payload["restore"] = build_restore_payload(structure_json, cells_json, global_lines=lines)
            page_payload["duration_ms"] = int((time.time() - page_started) * 1000)
            pages.append(page_payload)

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
                "structure": include_structure,
                "cells": include_cells,
                "restore": include_restore,
            },
            "pages": pages,
            "timing": {"total_ms": int((time.time() - started) * 1000)},
        })
    except Exception as exc:
        import traceback

        traceback.print_exc()
        return jsonify({"status": "error", "error": str(exc)}), 500
    finally:
        for rendered in rendered_pages:
            page_path = rendered.get("image_path")
            if page_path and Path(page_path) != input_path:
                Path(page_path).unlink(missing_ok=True)
        input_path.unlink(missing_ok=True)


@app.post("/ocr")
def ocr():
    return process_request(force_structure=False)


@app.post("/structure")
def structure():
    return process_request(force_structure=True)


if __name__ == "__main__":
    print(f"[GPU OCR] Starting on {HOST}:{PORT}", flush=True)
    app.run(host=HOST, port=PORT, debug=False)
