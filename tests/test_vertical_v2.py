# -*- coding: utf-8 -*-
"""竖排框检测：切出单字区域，按Y排序后竖排输出"""
import sys, io, cv2, numpy as np, time, fitz
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from paddleocr import PaddleOCR

doc = fitz.open(r"D:\hongmengProjects\doc-structure-mcp\tests\fixtures\test_scanned_doc.pdf")
mat = fitz.Matrix(300/72, 300/72)
pix = doc[0].get_pixmap(matrix=mat)
nparr = np.frombuffer(pix.tobytes("png"), np.uint8)
img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
H, W = img.shape[:2]
doc.close()

ocr = PaddleOCR(
    text_detection_model_name="PP-OCRv5_mobile_det",
    text_recognition_model_name="PP-OCRv5_mobile_rec",
    use_textline_orientation=True, lang="ch",
    text_det_thresh=0.3, text_det_box_thresh=0.5,
)
res = list(ocr.predict(img))

# 提取所有检测框
boxes = []
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
            x1, y1 = bbox[0], bbox[1]
            x2, y2 = bbox[4], bbox[5]
            w, h = abs(x2-x1), abs(y2-y1)
            boxes.append({"text": t, "conf": conf, "x1": x1, "y1": y1, "x2": x2, "y2": y2, "w": w, "h": h})

# 筛选竖排框：h > w * 2 且位于页面顶部
vertical_boxes = [b for b in boxes if b["h"] > b["w"] * 2 and b["y1"] < H * 0.35]

print(f"检测到 {len(vertical_boxes)} 个竖排框:")
for vb in vertical_boxes:
    print(f"  [{vb['text']}] x={vb['x1']:.0f} y={vb['y1']:.0f} w={vb['w']:.0f} h={vb['h']:.0f}")

print(f"\n{'='*60}")
print("竖排框内单字检测：")
print("="*60)

# 对每个竖排框：用低阈值在裁剪图上检测单字
ocr2 = PaddleOCR(
    text_detection_model_name="PP-OCRv5_mobile_det",
    text_recognition_model_name="PP-OCRv5_mobile_rec",
    use_textline_orientation=True, lang="ch",
    text_det_thresh=0.05, text_det_box_thresh=0.2,
)

for vb in vertical_boxes:
    x1, y1, x2, y2 = int(vb["x1"]), int(vb["y1"]), int(vb["x2"]), int(vb["y2"])
    pad = 30
    crop = img[max(0,y1-pad):min(H,y2+pad), max(0,x1-pad):min(W,x2+pad)]
    
    sub_res = list(ocr2.predict(crop))
    
    sub_chars = []
    for r in sub_res:
        if r is None: continue
        texts = r.get("rec_texts",[]) if isinstance(r,dict) else getattr(r,"rec_texts",[])
        scores = r.get("rec_scores",[]) if isinstance(r,dict) else getattr(r,"rec_scores",[])
        polys = r.get("dt_polys",[]) if isinstance(r,dict) else getattr(r,"dt_polys",[])
        for i in range(len(texts)):
            t = texts[i].strip()
            s = float(scores[i]) if i < len(scores) else 0.0
            poly = polys[i] if i < len(polys) else None
            if t and s > 0.5:
                # 计算在原图中的Y坐标
                if poly:
                    cy = (poly[0][1] + poly[2][1]) / 2 + (y1 - pad)
                else:
                    cy = len(sub_chars) * 50  # fallback
                sub_chars.append({"text": t, "conf": s, "y": cy})
    
    # 按Y排序（竖排从上到下）
    sub_chars.sort(key=lambda c: c["y"])
    vertical_text = "".join(c["text"] for c in sub_chars)
    y_positions = ", ".join(f"{c['text']}(y={c['y']:.0f})" for c in sub_chars)
    
    print(f"\n框 [{vb['text']}]:")
    print(f"  原始: {vb['text']}")
    print(f"  竖排: {vertical_text}")
    print(f"  详情: {y_positions}")

# 非竖排框正常处理
print(f"\n{'='*60}")
print("第1页完整还原结果：")
print("="*60)

# 收集竖排输出
vertical_results = {}
for vb in vertical_boxes:
    x1 = int(vb["x1"])
    sub_chars = []
    # (这里复用上面的检测逻辑，简化起见先直接用原始检测)
    sub_chars.append({"text": "财", "conf": 0.99, "y": 0})  # placeholder
    vertical_results[x1] = sub_chars

# 输出
normal = [b for b in boxes if b not in vertical_boxes and b["y1"] < H * 0.35]
normal.sort(key=lambda b: (b["y1"], b["x1"]))
for b in normal:
    print(f"  {b['text']}")
