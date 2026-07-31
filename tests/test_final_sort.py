# -*- coding: utf-8 -*-
"""实用方案：XY-Cut + 竖排框拆字"""
import sys, io, cv2, numpy as np, time, fitz
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from paddleocr import PaddleOCR

doc = fitz.open(r"D:\hongmengProjects\doc-structure-mcp\tests\fixtures\test_scanned_doc.pdf")
mat = fitz.Matrix(200/72, 200/72)
pix = doc[0].get_pixmap(matrix=mat)
img = cv2.imdecode(np.frombuffer(pix.tobytes("png"), np.uint8), cv2.IMREAD_COLOR)
H, W = img.shape[:2]
doc.close()

ocr = PaddleOCR(text_detection_model_name="PP-OCRv5_mobile_det", text_recognition_model_name="PP-OCRv5_mobile_rec",
                use_textline_orientation=True, lang="ch", text_det_thresh=0.3, text_det_box_thresh=0.5)
res = list(ocr.predict(img))

blocks = []
for r in res:
    if r is None: continue
    texts = r.get("rec_texts",[]) if isinstance(r,dict) else getattr(r,"rec_texts",[])
    scores = r.get("rec_scores",[]) if isinstance(r,dict) else getattr(r,"rec_scores",[])
    polys = r.get("dt_polys",[]) if isinstance(r,dict) else getattr(r,"dt_polys",[])
    for i in range(len(texts)):
        t = texts[i].strip(); conf = float(scores[i]) if i < len(scores) else 0.0
        poly = polys[i] if i < len(polys) else None
        if not t or conf < 0.3: continue
        if poly is not None and len(poly) >= 4:
            pts = [(float(p[0]), float(p[1])) for p in poly if len(p) >= 2]
            if pts:
                xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
                blocks.append({"text": t, "conf": conf, "x1": min(xs), "y1": min(ys), "x2": max(xs), "y2": max(ys),
                              "cx": (min(xs)+max(xs))/2, "cy": (min(ys)+max(ys))/2, "w": max(xs)-min(xs), "h": max(ys)-min(ys)})

# XY-Cut: 按Y分组
blocks.sort(key=lambda b: b["cy"])
rows = []
current_row = [blocks[0]]
for b in blocks[1:]:
    if abs(b["cy"] - current_row[-1]["cy"]) < 30:
        current_row.append(b)
    else:
        rows.append(sorted(current_row, key=lambda x: x["cx"]))
        current_row = [b]
if current_row:
    rows.append(sorted(current_row, key=lambda x: x["cx"]))

# 对每行中的竖排框拆字
print("=== 拆字后最终输出 ===")
for ri, row in enumerate(rows):
    texts = []
    for b in row:
        if b["h"] > b["w"] * 2 and b["h"] > 300:
            # 竖排框：每个字单独一行（但同一行内应该只出现一个字）
            n = len(b["text"])
            slice_h = b["h"] / n
            # 这个框在这一行应该取哪个字？
            row_y = b["cy"]  # 行中心Y
            char_idx = int((row_y - b["y1"]) / slice_h)
            char_idx = max(0, min(char_idx, n-1))
            texts.append(b["text"][char_idx])
        else:
            texts.append(b["text"])
    line = "".join(texts)
    if line.strip():
        y_pos = row[0]["cy"]
        print(f"  y={y_pos:.0f} {line}")
