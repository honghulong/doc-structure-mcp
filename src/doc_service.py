"""
doc_service.py — PDF→DOCX 转换服务（端口 8768）
================================================
OCR: PaddleOCR PP-OCRv5 mobile
排版: docx_builder XY-Cut 引擎
"""
import os, sys, time, uuid, io, threading
from pathlib import Path

# ─── 环境变量 ───
os.environ["DNNL_DEFAULT_FPMATH_MODE"] = "BF16"
os.environ["FLAGS_use_mkldnn"] = "0"
os.environ["FLAGS_use_mkldnn_fp16"] = "0"

BASE_DIR = Path(__file__).resolve().parent.parent
os.environ.setdefault("PPOCR_HOME", str(BASE_DIR / ".paddleocr_home"))
os.environ.setdefault("PADDLEX_HOME", str(BASE_DIR / ".paddlex_home"))

sys.path.insert(0, str(BASE_DIR / "src"))

from docx_builder import build_docx as build_docx_from_builder

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS

# ─── OCR engine (PaddleOCR mobile, lazy) ───
_ocr_lock = threading.Lock()
_ocr_instance = None

def get_ocr():
    global _ocr_instance
    if _ocr_instance is None:
        with _ocr_lock:
            if _ocr_instance is None:
                print("[Doc] Loading PaddleOCR (PP-OCRv5 mobile)...", flush=True)
                t0 = time.time()
                from paddleocr import PaddleOCR
                _ocr_instance = PaddleOCR(
                    text_detection_model_name='PP-OCRv3_server_det',
                    text_recognition_model_name='PP-OCRv5_mobile_rec',
                    use_textline_orientation=True, lang='ch',
                    text_det_thresh=0.3, text_det_box_thresh=0.5,
                )
                print(f"[Doc] OCR loaded in {time.time()-t0:.1f}s", flush=True)
    return _ocr_instance

# ─── Flask ───
app = Flask(__name__)
CORS(app)

PORT = 8768
TEMP_DIR = BASE_DIR / ".tmp"
TEMP_DIR.mkdir(exist_ok=True)
START_TIME = time.time()
MAX_FILE_SIZE = 20 * 1024 * 1024


# ─── Helpers ───

def pdf_to_images(pdf_path, max_pages=50, dpi=200):
    import fitz
    doc = fitz.open(pdf_path)
    pages = min(len(doc), max_pages)
    results = []
    for i in range(pages):
        mat = fitz.Matrix(dpi/72, dpi/72)
        pix = doc[i].get_pixmap(matrix=mat)
        results.append((i+1, pix.tobytes("png")))
    doc.close()
    return results


def _save_upload(req):
    if "file" not in req.files:
        return None, jsonify({"error": "no file", "status": "error"}), 400
    f = req.files["file"]
    ext = Path(f.filename).suffix.lower()
    tmp = TEMP_DIR / f"doc_{uuid.uuid4().hex}{ext}"
    f.save(str(tmp))
    if tmp.stat().st_size > MAX_FILE_SIZE:
        tmp.unlink()
        return None, jsonify({"error": "file too large", "status": "error"}), 413
    return tmp, None, None


def _cleanup(p):
    if p and p.exists():
        p.unlink(missing_ok=True)


def _save_temp_image(img_bytes):
    path = TEMP_DIR / f"page_{uuid.uuid4().hex}.png"
    path.write_bytes(img_bytes)
    return path


def _parse_ocr_pred(result):
    """PaddleOCR predict() result → line list (handles both dict and object output)"""
    import numpy as np
    lines = []
    for page in result:
        if page is None: continue
        if isinstance(page, dict):
            texts = page.get("rec_texts", []) or []
            scores = page.get("rec_scores", []) or []
            polys = page.get("dt_polys", []) or []
        else:
            texts = getattr(page, "rec_texts", []) or []
            scores = getattr(page, "rec_scores", []) or []
            polys = getattr(page, "dt_polys", []) or []
        for i in range(len(texts)):
            text = str(texts[i] or "").strip()
            if not text: continue
            conf = float(scores[i]) if i < len(scores) else 0.0
            poly = polys[i] if i < len(polys) else None
            bbox = []
            if poly is not None:
                for pt in poly:
                    if isinstance(pt, (list, tuple, np.ndarray)) and len(pt) >= 2:
                        bbox.extend([round(float(pt[0]), 1), round(float(pt[1]), 1)])
            lines.append({"text": text, "confidence": round(conf, 4), "bbox": bbox})
    return lines


def analyze_image(img_path):
    """单图OCR分析"""
    ocr = get_ocr()
    lines = _parse_ocr_pred(list(ocr.predict(img_path)))
    text = "\n".join(l["text"] for l in lines if l["confidence"] > 0.3)
    return {"ocr_lines": lines, "ocr_text": text}


def build_docx(pages_data):
    """分析结果→DOCX（委托给 docx_builder）"""
    return build_docx_from_builder(pages_data)


# ─── API 端点 ───

@app.route("/")
def index():
    return jsonify({
        "service": "Doc Structure Analysis",
        "port": PORT,
        "endpoints": {
            "GET /health": "health check",
            "POST /ocr": "pure OCR",
            "POST /to-docx": "PDF → DOCX",
        }
    })


@app.route("/health")
def health():
    return jsonify({"status": "ok", "port": PORT, "uptime": round(time.time()-START_TIME, 1)})


@app.route("/ocr", methods=["POST"])
def ocr():
    fp, err, code = _save_upload(request)
    if err: return err, code
    try:
        t0 = time.time()
        ocr_eng = get_ocr()
        if fp.suffix.lower() == ".pdf":
            pages = pdf_to_images(str(fp), 1)
            all_lines = []
            for _, ib in pages:
                p = _save_temp_image(ib)
                all_lines.extend(_parse_ocr_pred(list(ocr_eng.predict(str(p)))))
                _cleanup(p)
        else:
            all_lines = _parse_ocr_pred(list(ocr_eng.predict(str(fp))))
        text = "\n".join(l["text"] for l in all_lines if l["confidence"] > 0.3)
        return jsonify({"status": "ok", "ocr_raw": all_lines, "ocr_text": text,
                        "timing": {"s": round(time.time()-t0, 3)}})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        _cleanup(fp)


@app.route("/to-docx", methods=["POST"])
def to_docx():
    fp, err, code = _save_upload(request)
    if err: return err, code
    max_pages = request.form.get("page_limit", 50, type=int)
    try:
        t0 = time.time()
        if fp.suffix.lower() == ".pdf":
            pages_data = pdf_to_images(str(fp), max_pages)
            all_pages = []
            for pn, ib in pages_data:
                p = _save_temp_image(ib)
                data = analyze_image(str(p))
                data["page"] = pn
                all_pages.append(data)
                _cleanup(p)
        else:
            data = analyze_image(str(fp))
            all_pages = [{"page": 1, **data}]

        docx_bytes = build_docx(all_pages)
        print(f"[Doc] to-docx done: {time.time()-t0:.1f}s", flush=True)
        return send_file(io.BytesIO(docx_bytes),
                         mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                         as_attachment=True, download_name=f"doc_{uuid.uuid4().hex[:8]}.docx")
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        _cleanup(fp)


if __name__ == "__main__":
    print(f"[Doc] Starting on :{PORT}", flush=True)
    app.run(host="0.0.0.0", port=PORT, debug=False)
