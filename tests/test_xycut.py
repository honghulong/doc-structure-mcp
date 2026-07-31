# -*- coding: utf-8 -*-
"""XY-Cut 坐标排序算法：按检测框坐标重建阅读顺序"""
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

ocr = PaddleOCR(
    text_detection_model_name="PP-OCRv5_mobile_det",
    text_recognition_model_name="PP-OCRv5_mobile_rec",
    use_textline_orientation=True, lang="ch",
    text_det_thresh=0.3, text_det_box_thresh=0.5,
)
res = list(ocr.predict(img))

# 提取所有带坐标的检测块
blocks = []
for r in res:
    if r is None: continue
    texts = r.get("rec_texts",[]) if isinstance(r,dict) else getattr(r,"rec_texts",[])
    scores = r.get("rec_scores",[]) if isinstance(r,dict) else getattr(r,"rec_scores",[])
    polys = r.get("dt_polys",[]) if isinstance(r,dict) else getattr(r,"dt_polys",[])
    for i in range(len(texts)):
        t = texts[i].strip()
        conf = float(scores[i]) if i < len(scores) else 0.0
        poly = polys[i] if i < len(polys) else None
        if not t or conf < 0.3: continue
        if poly is not None and len(poly) >= 4:
            pts = [(float(p[0]), float(p[1])) for p in poly if len(p) >= 2]
            if pts:
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                blocks.append({
                    "text": t, "conf": conf,
                    "x1": min(xs), "y1": min(ys),
                    "x2": max(xs), "y2": max(ys),
                    "cx": (min(xs)+max(xs))/2,
                    "cy": (min(ys)+max(ys))/2,
                })

# ─── XY-Cut 排序 ───
# Step 1: 按 Y 分组（同一行的文字 y 坐标相近）
blocks.sort(key=lambda b: b["cy"])
y_thresh = 30  # Y 阈值：同一行内 Y 差不超过30px

rows = []
current_row = [blocks[0]] if blocks else []
for b in blocks[1:]:
    if abs(b["cy"] - current_row[-1]["cy"]) < y_thresh:
        current_row.append(b)
    else:
        rows.append(sorted(current_row, key=lambda x: x["cx"]))
        current_row = [b]
if current_row:
    rows.append(sorted(current_row, key=lambda x: x["cx"]))

# Step 2: 输出排序后的文字
print(f"共 {len(rows)} 行，{len(blocks)} 个文本块")
print()
for ri, row in enumerate(rows):
    line = " ".join(b["text"] for b in row)
    y_pos = row[0]["cy"]
    print(f"行{ri+1:2d} (y={y_pos:.0f}): {line}")

# Step 3: 顶部区域的竖排检测（前几行如果x相近则判断为竖排）
print(f"\n{'='*60}")
print("顶部区域分析：(检测竖向排列)")
print("="*60)

top_rows = rows[:5]  # 只看前5行（页面顶部）
for ri, row in enumerate(top_rows):
    xs = [b["cx"] for b in row]
    # 如果这一行的文字x坐标差异大 → 可能包含不同列
    items = [f'{b["text"]}(x={b["cx"]:.0f})' for b in row]
    print(f"  行{ri+1}: {items}")
