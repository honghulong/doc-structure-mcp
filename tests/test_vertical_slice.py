# -*- coding: utf-8 -*-
"""竖排框：按字数等分切割 → 逐块识别 → 竖排输出"""
import sys, io, cv2, numpy as np, time, fitz
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from paddleocr import PaddleOCR

doc = fitz.open(r"D:\hongmengProjects\doc-structure-mcp\tests\fixtures\test_scanned_doc.pdf")
mat = fitz.Matrix(300/72, 300/72)
pix = doc[0].get_pixmap(matrix=mat)
img = cv2.imdecode(np.frombuffer(pix.tobytes("png"), np.uint8), cv2.IMREAD_COLOR)
H, W = img.shape[:2]
doc.close()

# 一次OCR检测所有框
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

# 筛选竖排框 + 正常框
vertical = [b for b in boxes if b["h"] > b["w"] * 2 and b["y1"] < H * 0.35]
normal = [b for b in boxes if b not in vertical and b["y1"] < H * 0.35]
normal.sort(key=lambda b: (b["y1"], b["x1"]))

print(f"竖排框: {len(vertical)}个, 横向框: {len(normal)}个\n")

# 对竖排框做切片识别
rec_ocr = PaddleOCR(
    text_detection_model_name="PP-OCRv5_mobile_det",
    text_recognition_model_name="PP-OCRv5_mobile_rec",
    use_textline_orientation=True, lang="ch",
    text_det_thresh=0.3, text_det_box_thresh=0.5,
)

print("="*60)
print("第1页还原结果")
print("="*60)

for vb in sorted(vertical, key=lambda b: b["x1"]):
    x1, y1, x2, y2 = int(vb["x1"]), int(vb["y1"]), int(vb["x2"]), int(vb["y2"])
    text_len = len(vb["text"])  # 字数
    slice_h = (y2 - y1) / text_len  # 每块高度
    
    pad = 10
    chars = []
    for i in range(text_len):
        sy = max(0, int(y1 + i * slice_h - pad))
        ey = min(H, int(y1 + (i+1) * slice_h + pad))
        sx = max(0, x1 - pad)
        ex = min(W, x2 + pad)
        slice_img = img[sy:ey, sx:ex]
        
        # 识别这块
        sr = list(rec_ocr.predict(slice_img))
        for r in sr:
            if r is None: continue
            st = r.get("rec_texts",[]) if isinstance(r,dict) else getattr(r,"rec_texts",[])
            ss = r.get("rec_scores",[]) if isinstance(r,dict) else getattr(r,"rec_scores",[])
            for j in range(len(st)):
                chars.append(st[j].strip())
    
    # 如果没有识别到，用原始文本的字符按序
    if not chars:
        chars = list(vb["text"])
    
    print(f"\n左列 (竖排):")
    for ch in chars:
        print(f"  {ch}")

# 正常的横向框
print(f"\n右列 (横排):")
for b in normal:
    print(f"  {b['text']}")
