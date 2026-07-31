# -*- coding: utf-8 -*-
"""分析红头文字坐标：判断横竖排列"""
import sys, io, cv2, numpy as np, time, fitz
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from paddleocr import PaddleOCR

doc = fitz.open(r"D:\hongmengProjects\doc-structure-mcp\tests\fixtures\test_scanned_doc.pdf")
mat = fitz.Matrix(200/72, 200/72)
pix = doc[0].get_pixmap(matrix=mat)
nparr = np.frombuffer(pix.tobytes("png"), np.uint8)
img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
doc.close()

ocr = PaddleOCR(
    text_detection_model_name="PP-OCRv5_mobile_det",
    text_recognition_model_name="PP-OCRv5_mobile_rec",
    use_textline_orientation=True, lang="ch",
    text_det_thresh=0.3, text_det_box_thresh=0.5,
)
res = list(ocr.predict(img))

# 提取顶部（y < 页面30%）的所有单字坐标
top_chars = []
for r in res:
    if r is None: continue
    texts = r.get("rec_texts",[]) if isinstance(r,dict) else getattr(r,"rec_texts",[])
    scores = r.get("rec_scores",[]) if isinstance(r,dict) else getattr(r,"rec_scores",[])
    polys = r.get("dt_polys",[]) if isinstance(r,dict) else getattr(r,"dt_polys",[])
    for i in range(len(texts)):
        t = texts[i]
        conf = float(scores[i]) if i < len(scores) else 0.0
        poly = polys[i] if i < len(polys) else None
        if not t or conf < 0.5: continue
        bbox = []
        if poly is not None:
            for pt in poly:
                if isinstance(pt, (list, tuple, np.ndarray)) and len(pt) >= 2:
                    bbox.extend([float(pt[0]), float(pt[1])])
        if bbox:
            x1, y1 = bbox[0], bbox[1]
            x2, y2 = bbox[4], bbox[5]
            cx, cy = (x1+x2)/2, (y1+y2)/2
            w, h = abs(x2-x1), abs(y2-y1)
            top_chars.append({"text": t, "conf": conf, "x": cx, "y": cy, "w": w, "h": h})

# 只看顶部 y < 700
header = [c for c in top_chars if c["y"] < 700]
header.sort(key=lambda c: (c["y"], c["x"]))

print(f"红头区域字符 ({len(header)}个):")
print(f"{'文本':>8} {'x':>6} {'y':>6} {'w':>5} {'h':>5}")
for c in header:
    print(f"  {c['text']}  {c['x']:6.0f} {c['y']:6.0f} {c['w']:5.0f} {c['h']:5.0f}")

# 分析：检测竖向排列
# 竖向文字：x相近，y递增
print(f"\n=== 竖向排列检测 (x相近, y间隔均匀) ===")
cols = {}
for c in header:
    x_bin = round(c["x"] / 50) * 50  # 按50px分组
    if x_bin not in cols:
        cols[x_bin] = []
    cols[x_bin].append(c)

for x_bin in sorted(cols.keys()):
    chars = sorted(cols[x_bin], key=lambda c: c["y"])
    if len(chars) >= 2:
        texts = "".join(c["text"] for c in chars)
        y_gaps = [chars[i+1]["y"] - chars[i]["y"] for i in range(len(chars)-1)]
        avg_gap = sum(y_gaps) / len(y_gaps)
        is_vertical = avg_gap > 30  # 竖向字间距大
        print(f"  x≈{x_bin}: {'竖向' if is_vertical else '横向'} {texts} (字间距avg={avg_gap:.0f})")
        for c in chars:
            print(f"    {c['text']} y={c['y']:.0f}")

# 竖向文字应该独立成行，横向文字合并
print(f"\n=== 正确排列 === ")
vertical_chars = [c for c in header if c["x"] < 500]  # 左侧竖向
horizontal_chars = [c for c in header if c["x"] >= 500]  # 右侧横向

vertical_chars.sort(key=lambda c: c["y"])
horizontal_rows = {}
for c in horizontal_chars:
    y_bin = round(c["y"] / 40) * 40
    if y_bin not in horizontal_rows:
        horizontal_rows[y_bin] = []
    horizontal_rows[y_bin].append(c)

# 输出正确分组
for vc in vertical_chars:
    print(f"  {vc['text']}")  # 竖向：每个字一行

for y_bin in sorted(horizontal_rows.keys()):
    chars = sorted(horizontal_rows[y_bin], key=lambda c: c["x"])
    text = "".join(c["text"] for c in chars)
    print(f"  {text}")  # 横向：合并为行
