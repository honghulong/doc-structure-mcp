# -*- coding: utf-8 -*-
"""竖排框旋转90°再识别"""
import sys, io, cv2, numpy as np, time, fitz
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from paddleocr import PaddleOCR

doc = fitz.open(r"D:\hongmengProjects\doc-structure-mcp\tests\fixtures\test_scanned_doc.pdf")
mat = fitz.Matrix(300/72, 300/72)
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
            boxes.append({"text": t, "conf": conf, "x1": bbox[0], "y1": bbox[1],
                         "x2": bbox[4], "y2": bbox[5],
                         "w": abs(bbox[4]-bbox[0]), "h": abs(bbox[5]-bbox[1])})

# 识别竖排框
vertical = [b for b in boxes if b["h"] > b["w"] * 2 and b["y1"] < H * 0.35]
normal = [b for b in boxes if b not in vertical and b["y1"] < H * 0.35]
normal.sort(key=lambda b: (b["y1"], b["x1"]))

print(f"竖排框: {len(vertical)}个\n")

for vb in sorted(vertical, key=lambda b: b["x1"]):
    x1, y1, x2, y2 = int(vb["x1"]), int(vb["y1"]), int(vb["x2"]), int(vb["y2"])
    pad = 30
    crop = img[max(0,y1-pad):min(H,y2+pad), max(0,x1-pad):min(W,x2+pad)]
    
    # 旋转90°顺时针（竖变横）
    rotated = cv2.rotate(crop, cv2.ROTATE_90_CLOCKWISE)
    
    print(f"原始: {vb['text']}")
    print(f"裁剪: {crop.shape[1]}x{crop.shape[0]} → 旋转: {rotated.shape[1]}x{rotated.shape[0]}")
    
    # 在旋转后的图上OCR
    res2 = list(ocr.predict(rotated))
    
    texts = []
    for r in res2:
        if r is None: continue
        tlist = r.get("rec_texts",[]) if isinstance(r,dict) else getattr(r,"rec_texts",[])
        slist = r.get("rec_scores",[]) if isinstance(r,dict) else getattr(r,"rec_scores",[])
        for j in range(len(tlist)):
            texts.append(tlist[j].strip())
    
    result = "".join(texts)
    print(f"旋转后: {result}")
    print()

print("横向框:")
for b in normal:
    print(f"  {b['text']}")
