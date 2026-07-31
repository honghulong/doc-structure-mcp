# -*- coding: utf-8 -*-
"""混合排序：顶部列分组(竖排) + 正文XY-Cut(横排)"""
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
                              "cx": (min(xs)+max(xs))/2, "cy": (min(ys)+max(ys))/2, "h": max(ys)-min(ys)})

# 区分顶部(竖排区)和正文(横排区)
header_end = 700  # y < 700 = 顶部红头区
header = [b for b in blocks if b["cy"] < header_end]
body = [b for b in blocks if b["cy"] >= header_end]

print(f"顶部: {len(header)}块, 正文: {len(body)}块\n")

# ─── 顶部：列分组(竖排) ───
# 按x聚类
cols = defaultdict(list)
for b in header:
    xb = round(b["cx"] / 80) * 80
    cols[xb].append(b)

print("=== 顶部还原 (列分组) ===")
for xb in sorted(cols.keys()):
    cv = sorted(cols[xb], key=lambda c: c["cy"])
    if len(cv) >= 2:
        # 竖排列：每字一行
        for c in cv:
            print(f"  {c['text']}")
        print()  # 列间空行

# ─── 正文：XY-Cut(横排) ───
body.sort(key=lambda b: b["cy"])
y_thresh = 30
rows = []
if body:
    current_row = [body[0]]
    for b in body[1:]:
        if abs(b["cy"] - current_row[-1]["cy"]) < y_thresh:
            current_row.append(b)
        else:
            rows.append(sorted(current_row, key=lambda x: x["cx"]))
            current_row = [b]
    if current_row:
        rows.append(sorted(current_row, key=lambda x: x["cx"]))

print("=== 正文还原 (XY-Cut横排) ===")
for ri, row in enumerate(rows):
    text = "".join(b["text"] for b in row)
    print(f"  {text}")
