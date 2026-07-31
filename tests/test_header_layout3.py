# -*- coding: utf-8 -*-
"""极低阈值检测单字 → 按坐标分列（竖排）vs 分行（横排）"""
import sys, io, cv2, numpy as np, time, fitz
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from paddleocr import PaddleOCR

doc = fitz.open(r"D:\hongmengProjects\doc-structure-mcp\tests\fixtures\test_scanned_doc.pdf")
mat = fitz.Matrix(200/72, 200/72)
pix = doc[0].get_pixmap(matrix=mat)
nparr = np.frombuffer(pix.tobytes("png"), np.uint8)
img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
H, W = img.shape[:2]
doc.close()

ocr = PaddleOCR(
    text_detection_model_name="PP-OCRv5_mobile_det",
    text_recognition_model_name="PP-OCRv5_mobile_rec",
    use_textline_orientation=True, lang="ch",
    text_det_thresh=0.01, text_det_box_thresh=0.1, text_det_unclip_ratio=1.0,
)
res = list(ocr.predict(img))

chars = []
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
        bbox = []
        if poly is not None:
            for pt in poly:
                if isinstance(pt, (list, tuple, np.ndarray)) and len(pt) >= 2:
                    bbox.extend([float(pt[0]), float(pt[1])])
        if bbox:
            cx = (bbox[0]+bbox[4])/2
            cy = (bbox[1]+bbox[5])/2
            chars.append({"text": t, "conf": conf, "x": cx, "y": cy,
                         "w": abs(bbox[4]-bbox[0]), "h": abs(bbox[5]-bbox[1])})

header = [c for c in chars if c["y"] < H * 0.20 and len(c["text"]) <= 3]
header.sort(key=lambda c: (c["y"], c["x"]))

print("顶部单字/小词:")
for c in header:
    print(f"  [{c['text']:4s}] x={c['x']:.0f} y={c['y']:.0f}")

# 按 x 坐标分组（竖向列检测）
cols = defaultdict(list)
for c in header:
    xb = round(c["x"] / 60) * 60
    cols[xb].append(c)

print(f"\n=== 竖向列 (x相近, y递增) ===")
col_output = []
for xb in sorted(cols.keys()):
    cv = sorted(cols[xb], key=lambda cc: cc["y"])
    if len(cv) >= 2:
        gaps = [cv[i+1]["y"] - cv[i]["y"] for i in range(len(cv)-1)]
        avg_gap = sum(gaps)/len(gaps)
        text = "".join(cc["text"] for cc in cv)
        if avg_gap > 30:
            print(f"  列 x≈{xb:3.0f}: 竖向 |{text}| (间距{avg_gap:.0f}px)")
            col_output.append(("v", xb, text))
        else:
            print(f"  列 x≈{xb:3.0f}: 横向? |{text}|")

# 按 y 坐标分组（横向行检测）
rows = defaultdict(list)
for c in header:
    yb = round(c["y"] / 25) * 25
    rows[yb].append(c)

print(f"\n=== 横向行 (y相近, x递增) ===")
row_output = []
for yb in sorted(rows.keys()):
    rv = sorted(rows[yb], key=lambda cc: cc["x"])
    if len(rv) >= 2:
        text = "".join(cc["text"] for cc in rv)
        print(f"  行 y≈{yb:3.0f}: {text}")
        row_output.append(("h", yb, text))

# 综合输出
print(f"\n{'='*60}")
print("第1页顶部还原 (按坐标布局)")
print("="*60)
for item in sorted(col_output + row_output, key=lambda x: (x[1] if x[0]=="v" else 0, x[1])):
    typ, pos, text = item
    if typ == "v":
        for ch in text:
            print(f"  {ch}")
    else:
        print(f"  {text}")
