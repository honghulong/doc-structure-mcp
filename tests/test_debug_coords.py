# -*- coding: utf-8 -*-
"""调试：按行切割竖排框坐标"""
import sys, io, cv2, numpy as np, time, fitz
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from paddleocr import PaddleOCR

doc = fitz.open(r"D:\hongmengProjects\doc-structure-mcp\tests\fixtures\test_scanned_doc.pdf")
mat = fitz.Matrix(200/72, 200/72)
pix = doc[0].get_pixmap(matrix=mat)
img = cv2.imdecode(np.frombuffer(pix.tobytes("png"), np.uint8), cv2.IMREAD_COLOR)
H, W = img.shape[:2]
doc.close()

ocr = PaddleOCR(text_detection_model_name="PP-OCRv5_mobile_det", text_recognition_model_name="PP-OCRv5_mobile_rec",
                use_textline_orientation=True, lang="ch", text_det_thresh=0.01, text_det_box_thresh=0.1)
res = list(ocr.predict(img))

# 找竖排框
for r in res:
    if r is None: continue
    texts = r.get("rec_texts",[]) if isinstance(r,dict) else getattr(r,"rec_texts",[])
    polys = r.get("dt_polys",[]) if isinstance(r,dict) else getattr(r,"dt_polys",[])
    for i in range(len(texts)):
        t = texts[i].strip()
        poly = polys[i] if i < len(polys) else None
        if not t or len(t) < 3: continue
        if poly is not None and len(poly) >= 4:
            pts = [(float(p[0]), float(p[1])) for p in poly if len(p) >= 2]
            if not pts: continue
            xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
            w = max(xs)-min(xs); h = max(ys)-min(ys)
            if h > w * 2 and min(ys) < H * 0.35:
                print(f"竖排框: [{t}] 共{len(t)}字")
                print(f"  坐标: x=[{min(xs):.0f},{max(xs):.0f}] y=[{min(ys):.0f},{max(ys):.0f}]")
                print(f"  尺寸: w={w:.0f} h={h:.0f}")
                print()
                
                # 按字数等分切割
                n = len(t)
                slice_h = h / n
                x1, y1 = int(min(xs)-20), int(min(ys)-20)
                x2, y2 = int(max(xs)+20), int(max(ys)+20)
                
                for idx in range(n):
                    sy = int(y1 + idx * slice_h)
                    ey = int(y1 + (idx+1) * slice_h + 10)
                    crop = img[max(0,sy):min(H,ey), max(0,x1):min(W,x2)]
                    
                    ch = t[idx]
                    print(f"  第{idx+1}字 [{ch}]: y={sy}~{ey}")
                    
                    # 在裁剪图上再检测
                    sub = list(ocr.predict(crop))
                    for sr in sub:
                        if sr is None: continue
                        stexts = sr.get("rec_texts",[]) if isinstance(sr,dict) else getattr(sr,"rec_texts",[])
                        sscores = sr.get("rec_scores",[]) if isinstance(sr,dict) else getattr(sr,"rec_scores",[])
                        spolys = sr.get("dt_polys",[]) if isinstance(sr,dict) else getattr(sr,"dt_polys",[])
                        for j in range(len(stexts)):
                            st = stexts[j].strip()
                            ss = float(sscores[j]) if j < len(sscores) else 0.0
                            spoly = spolys[j] if j < len(spolys) else None
                            if st and ss > 0.5:
                                if spoly:
                                    spts = [(float(p[0]), float(p[1])) for p in spoly if len(p) >= 2]
                                    if spts:
                                        sxs = [p[0] for p in spts]
                                        sys2 = [p[1] for p in spts]
                                        sx = (min(sxs)+max(sxs))/2 + (x1 if x1 > 0 else 0)
                                        sy2 = (min(sys2)+max(sys2))/2 + sy
                                        print(f"    检测: [{st}] x={sx:.0f} y={sy2:.0f}")
                print()
