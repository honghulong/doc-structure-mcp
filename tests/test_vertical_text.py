# -*- coding: utf-8 -*-
"""竖排文字检测：超高文本框→切割单字→竖排输出"""
import sys, io, cv2, numpy as np, time, fitz, math
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from paddleocr import PaddleOCR

doc = fitz.open(r"D:\hongmengProjects\doc-structure-mcp\tests\fixtures\test_scanned_doc.pdf")
mat = fitz.Matrix(300/72, 300/72)  # 更高分辨率
pix = doc[0].get_pixmap(matrix=mat)
nparr = np.frombuffer(pix.tobytes("png"), np.uint8)
img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
doc.close()

H, W = img.shape[:2]
print(f"图片: {W}x{H}")

ocr = PaddleOCR(
    text_detection_model_name="PP-OCRv5_mobile_det",
    text_recognition_model_name="PP-OCRv5_mobile_rec",
    use_textline_orientation=True, lang="ch",
    text_det_thresh=0.3, text_det_box_thresh=0.5,
)

t0 = time.time()
res = list(ocr.predict(img))
print(f"OCR: {time.time()-t0:.1f}s")

# 提取所有检测框
boxes = []
for r in res:
    if r is None: continue
    texts = r.get("rec_texts",[]) if isinstance(r,dict) else getattr(r,"rec_texts",[])
    scores = r.get("rec_scores",[]) if isinstance(r,dict) else getattr(r,"rec_scores",[])
    polys = r.get("dt_polys",[]) if isinstance(r,dict) else getattr(r,"dt_polys",[])
    for i in range(len(texts)):
        t = texts[i]
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

# 判断竖排框：h > w * 1.5
vertical_boxes = [b for b in boxes if b["h"] > b["w"] * 1.5 and b["y1"] < H * 0.25]
normal_boxes = [b for b in boxes if b not in vertical_boxes]

print(f"\n竖排框: {len(vertical_boxes)}个")
for vb in vertical_boxes:
    print(f"  {vb['text']}  x={vb['x1']:.0f} y={vb['y1']:.0f} w={vb['w']:.0f} h={vb['h']:.0f}")

# 对竖排框：在更高分辨率图上切割单字区域，逐个识别
print(f"\n=== 竖排文字重识别 ===")
ocr2 = PaddleOCR(
    text_detection_model_name="PP-OCRv5_mobile_det",
    text_recognition_model_name="PP-OCRv5_mobile_rec",
    use_textline_orientation=True, lang="ch",
    text_det_thresh=0.1,  # 更低的阈值
    text_det_box_thresh=0.3,
)

for vb in vertical_boxes:
    x1, y1, x2, y2 = int(vb["x1"]), int(vb["y1"]), int(vb["x2"]), int(vb["y2"])
    # 扩大裁剪区域
    pad = 20
    crop = img[max(0,y1-pad):min(H,y2+pad), max(0,x1-pad):min(W,x2+pad)]
    
    # 在裁剪图上再OCR（高分辨率下容易检测单字）
    sub_res = list(ocr2.predict(crop))
    
    chars = []
    for r in sub_res:
        if r is None: continue
        texts = r.get("rec_texts",[]) if isinstance(r,dict) else getattr(r,"rec_texts",[])
        scores = r.get("rec_scores",[]) if isinstance(r,dict) else getattr(r,"rec_scores",[])
        polys = r.get("dt_polys",[]) if isinstance(r,dict) else getattr(r,"dt_polys",[])
        for i in range(len(texts)):
            t = texts[i]
            s = float(scores[i]) if i < len(scores) else 0.0
            if t and s > 0.3:
                chars.append((t, s))
    
    # 按Y排序（竖排从上到下）
    # 由于text没有坐标，直接按输出顺序（通常从上到下）
    vertical_text = "".join(c[0] for c in chars)
    print(f"  {vb['text']} → 竖排重识别: {vertical_text}")

# 横向框正常输出
print(f"\n=== 横向文本（正常） ===")
normal_top = [b for b in normal_boxes if b["y1"] < H * 0.25]
normal_top.sort(key=lambda b: (b["y1"], b["x1"]))
for b in normal_top:
    print(f"  y={b['y1']:.0f} {b['text']}")
