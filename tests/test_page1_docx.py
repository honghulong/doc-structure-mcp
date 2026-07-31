# -*- coding: utf-8 -*-
"""
端到端测试：test_page1.png → DOCX
调用 doc_service 或直调 docx_builder
"""
import sys, io, cv2, numpy as np, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import sys
sys.path.insert(0, "src")

from paddleocr import PaddleOCR
from docx_builder import build_docx

IMG_PATH = r"D:\hongmengProjects\doc-structure-mcp\tests\fixtures\test_page1.png"
DOCX_OUT = r"D:\hongmengProjects\doc-structure-mcp\tests\output\test_page1_v3.docx"

print("=" * 60)
print("test_page1.png → DOCX 端到端测试")
print("=" * 60)

# 1. 加载图片
img = cv2.imdecode(np.fromfile(IMG_PATH, np.uint8), cv2.IMREAD_COLOR)
if img is None:
    print(f"[ERROR] 无法读取图片: {IMG_PATH}")
    sys.exit(1)
H, W = img.shape[:2]
print(f"[图片] {W}x{H}")

# 2. OCR
ocr = PaddleOCR(
    text_detection_model_name="PP-OCRv5_mobile_det",
    text_recognition_model_name="PP-OCRv5_mobile_rec",
    use_textline_orientation=True, lang="ch",
    text_det_thresh=0.3, text_det_box_thresh=0.5,
)
t0 = time.time()
res = list(ocr.predict(img))
print(f"[OCR] {len(res)} 检测框, 耗时 {time.time()-t0:.1f}s")

# 3. 解析为 pages 结构
lines = []
for r in res:
    if r is None:
        continue
    texts = r.get("rec_texts", []) if isinstance(r, dict) else getattr(r, "rec_texts", [])
    scores = r.get("rec_scores", []) if isinstance(r, dict) else getattr(r, "rec_scores", [])
    polys = r.get("dt_polys", []) if isinstance(r, dict) else getattr(r, "dt_polys", [])
    for i in range(len(texts)):
        t = texts[i].strip()
        conf = float(scores[i]) if i < len(scores) else 0.0
        poly = polys[i] if i < len(polys) else None
        if not t or conf < 0.3:
            continue
        if poly is not None and len(poly) >= 4:
            bbox = []
            for pt in poly:
                if len(pt) >= 2:
                    bbox.extend([float(pt[0]), float(pt[1])])
            if bbox:
                lines.append({
                    "text": t,
                    "confidence": conf,
                    "bbox": bbox,
                })

pages = [{"page": 1, "ocr_lines": lines, "ocr_text": "\n".join(l["text"] for l in lines)}]
print(f"[解析] {len(lines)} 有效行")

# 4. 构建 DOCX
t0 = time.time()
docx_bytes = build_docx(pages)
with open(DOCX_OUT, "wb") as f:
    f.write(docx_bytes)
print(f"[DOCX] 生成成功: {len(docx_bytes)} 字节, 耗时 {time.time()-t0:.1f}s")
print(f"  输出: {DOCX_OUT}")
print("=" * 60)
