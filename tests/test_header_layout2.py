# -*- coding: utf-8 -*-
"""策略3：低阈值检测单字 → 按坐标分组（横/竖）"""
import sys, io, cv2, numpy as np, time, fitz
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from paddleocr import PaddleOCR

doc = fitz.open(r"D:\hongmengProjects\doc-structure-mcp\tests\fixtures\test_scanned_doc.pdf")
mat = fitz.Matrix(200/72, 200/72)
pix = doc[0].get_pixmap(matrix=mat)
nparr = np.frombuffer(pix.tobytes("png"), np.uint8)
img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
H, W = img.shape[:2]
doc.close()

# 低阈值检测单字
ocr = PaddleOCR(
    text_detection_model_name="PP-OCRv5_mobile_det",
    text_recognition_model_name="PP-OCRv5_mobile_rec",
    use_textline_orientation=True, lang="ch",
    text_det_thresh=0.05, text_det_box_thresh=0.2, text_det_unclip_ratio=1.2,
)

t0 = time.time()
res = list(ocr.predict(img))
print(f"OCR: {time.time()-t0:.1f}s")

# 提取顶部字符（y < 页面35%）
chars = []
for r in res:
    if r is None: continue
    texts = r.get("rec_texts",[]) if isinstance(r,dict) else getattr(r,"rec_texts",[])
    scores = r.get("rec_scores",[]) if isinstance(r,dict) else getattr(r,"rec_scores",[])
    polys = r.get("dt_polys",[]) if isinstance(r,dict) else getattr(r,"dt_polys",[])
    for i in range(len(texts)):
        t = texts[i]; conf = float(scores[i]) if i < len(scores) else 0.0
        poly = polys[i] if i < len(polys) else None
        if not t or conf < 0.3: continue
        bbox = []
        if poly is not None:
            for pt in poly:
                if isinstance(pt, (list, tuple, np.ndarray)) and len(pt) >= 2:
                    bbox.extend([float(pt[0]), float(pt[1])])
        if bbox:
            chars.append({"text": t, "conf": conf, 
                         "x": (bbox[0]+bbox[4])/2, "y": (bbox[1]+bbox[5])/2,
                         "w": abs(bbox[4]-bbox[0]), "h": abs(bbox[5]-bbox[1])})

# 只看顶部
header = [c for c in chars if c["y"] < H * 0.30 and len(c["text"]) <= 2]
header.sort(key=lambda c: (c["y"], c["x"]))

print(f"\n顶部单字 ({len(header)}个):")
for c in header:
    print(f"  [{c['text']}] x={c['x']:.0f} y={c['y']:.0f}")

# 按x轴聚类：找竖向列
print(f"\n=== 竖向列检测 ===")
x_bins = {}
for c in header:
    xb = round(c["x"] / 60) * 60
    if xb not in x_bins: x_bins[xb] = []
    x_bins[xb].append(c)

for xb in sorted(x_bins.keys()):
    cols = sorted(x_bins[xb], key=lambda c: c["y"])
    if len(cols) >= 2:
        gap = cols[1]["y"] - cols[0]["y"]
        if 30 < gap < 200:
            text = "".join(c["text"] for c in cols)
            print(f"  x≈{xb}: 竖向 {text}")

# 按y轴聚类：找横向行
print(f"\n=== 横向行检测 ===")
y_bins = {}
for c in header:
    yb = round(c["y"] / 30) * 30
    # 排除已经在竖向列中的字
    in_vertical = False
    for xb2 in sorted(x_bins.keys()):
        cv = sorted(x_bins[xb2], key=lambda cc: cc["y"])
        if len(cv) >= 2 and any(abs(cc["y"]-c["y"])<30 and abs(cc["x"]-c["x"])<40 for cc in cv):
            # 只找竖向列之外的
            pass
    if yb not in y_bins: y_bins[yb] = []
    y_bins[yb].append(c)

for yb in sorted(y_bins.keys()):
    row = sorted(y_bins[yb], key=lambda c: c["x"])
    if len(row) >= 2:
        text = "".join(c["text"] for c in row)
        print(f"  y≈{yb}: 横向 {text}")

# 最终输出建议
print(f"\n=== 建议DOCX输出 ===")
# 左边竖排
left_cols = {xb: cols for xb, cols in x_bins.items() 
             if len(cols) >= 2 and (cols[1]["y"]-cols[0]["y"] > 30)}
for xb in sorted(left_cols.keys()):
    cols = sorted(left_cols[xb], key=lambda c: c["y"])
    text = "".join(c["text"] for c in cols)
    print(f"  竖排: {text}")

# 右边横排（排除已在竖排中的字）
vert_chars = set()
for xb, cols in left_cols.items():
    for c in cols:
        vert_chars.add((round(c["x"]), round(c["y"])))

horiz = [c for c in header if (round(c["x"]), round(c["y"])) not in vert_chars]
horiz.sort(key=lambda c: (c["y"], c["x"]))
y_rows = {}
for c in horiz:
    yb = round(c["y"] / 35) * 35
    if yb not in y_rows: y_rows[yb] = []
    y_rows[yb].append(c)
for yb in sorted(y_rows.keys()):
    row = sorted(y_rows[yb], key=lambda c: c["x"])
    text = "".join(c["text"] for c in row)
    print(f"  横排: {text}")
