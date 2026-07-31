# -*- coding: utf-8 -*-
"""
快速验证：_parse_blocks 列转行后的红头阅读顺序
"""
import sys, io, cv2, numpy as np, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import sys
sys.path.insert(0, "src")

from paddleocr import PaddleOCR
from docx_builder import _parse_blocks

IMG_PATH = r"D:\hongmengProjects\doc-structure-mcp\tests\fixtures\test_page1.png"

# 1. 加载 + OCR
img = cv2.imdecode(np.fromfile(IMG_PATH, np.uint8), cv2.IMREAD_COLOR)
ocr = PaddleOCR(text_detection_model_name="PP-OCRv5_mobile_det",
                text_recognition_model_name="PP-OCRv5_mobile_rec",
                use_textline_orientation=True, lang="ch",
                text_det_thresh=0.3, text_det_box_thresh=0.5)
res = list(ocr.predict(img))

# 2. 解析为 lines 结构
lines = []
for r in res:
    if r is None: continue
    texts = r.get("rec_texts", []) if isinstance(r, dict) else getattr(r, "rec_texts", [])
    scores = r.get("rec_scores", []) if isinstance(r, dict) else getattr(r, "rec_scores", [])
    polys = r.get("dt_polys", []) if isinstance(r, dict) else getattr(r, "dt_polys", [])
    for i in range(len(texts)):
        t = texts[i].strip()
        conf = float(scores[i]) if i < len(scores) else 0.0
        poly = polys[i] if i < len(polys) else None
        if not t or conf < 0.3: continue
        if poly is not None and len(poly) >= 4:
            bbox = []
            for pt in poly:
                if len(pt) >= 2: bbox.extend([float(pt[0]), float(pt[1])])
            if bbox:
                lines.append({"text": t, "confidence": conf, "bbox": bbox})

# 3. 用 _parse_blocks 解析
print("=" * 60)
print("_parse_blocks 解析结果")
print("=" * 60)
vertical_rows, body_blocks = _parse_blocks(lines)

print(f"\n竖排列转行输出 ({len(vertical_rows)} 行):")
for ri, txt in enumerate(vertical_rows):
    print(f"  行{ri+1}: {txt}")

print(f"\n正文块数: {len(body_blocks)}")
