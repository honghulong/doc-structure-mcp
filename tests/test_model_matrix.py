# -*- coding: utf-8 -*-
"""系统化模型测试：不同检测+识别模型组合"""
import sys, io, cv2, numpy as np, time, fitz, json
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from paddleocr import PaddleOCR

# 测试图
DOC = fitz.open(r"D:\hongmengProjects\doc-structure-mcp\tests\fixtures\test_scanned_doc.pdf")
MAT = fitz.Matrix(200/72, 200/72)
PIX = DOC[0].get_pixmap(matrix=MAT)
IMG = cv2.imdecode(np.frombuffer(PIX.tobytes("png"), np.uint8), cv2.IMREAD_COLOR)
DOC.close()
H = IMG.shape[0]

# 测试组合
tests = [
    # (name, det_model, rec_model, det_thresh)
    ("v1-baseline", "PP-OCRv5_mobile_det", "PP-OCRv5_mobile_rec", 0.3),
    ("v2-SVTRv2",   "PP-OCRv5_mobile_det", "ch_SVTRv2_rec", 0.3),
    ("v3-RepSVTR",  "PP-OCRv5_mobile_det", "ch_RepSVTR_rec", 0.3),
    ("v4-server",   "PP-OCRv5_server_det", "PP-OCRv5_server_rec", 0.3),
]

results = []
for name, det, rec, thresh in tests:
    print(f"\n{'='*60}")
    print(f"测试: {name}")
    print(f"  检测: {det}  识别: {rec}")
    print(f"{'='*60}")
    
    try:
        t0 = time.time()
        ocr = PaddleOCR(
            text_detection_model_name=det,
            text_recognition_model_name=rec,
            use_textline_orientation=True, lang="ch",
            text_det_thresh=thresh, text_det_box_thresh=0.5,
        )
        load_time = time.time() - t0
        
        t0 = time.time()
        res = list(ocr.predict(IMG))
        infer_time = time.time() - t0
        
        # 统计
        all_texts = []
        header_texts = []
        for r in res:
            if r is None: continue
            texts = r.get("rec_texts",[]) if isinstance(r,dict) else getattr(r,"rec_texts",[])
            scores = r.get("rec_scores",[]) if isinstance(r,dict) else getattr(r,"rec_scores",[])
            polys = r.get("dt_polys",[]) if isinstance(r,dict) else getattr(r,"dt_polys",[])
            for i in range(len(texts)):
                t = texts[i].strip()
                s = float(scores[i]) if i < len(scores) else 0.0
                if t and s > 0.5:
                    all_texts.append(t)
                    # 检测竖排框
                    poly = polys[i] if i < len(polys) else None
                    if poly is not None and (isinstance(poly, (list, tuple, np.ndarray))) and len(poly) > 0:
                        try:
                            ys = [float(p[1]) for p in poly if isinstance(p,(list,tuple,np.ndarray)) and len(p) >= 2]
                            if ys and min(ys) < H * 0.35:
                                header_texts.append(t)
                        except Exception:
                            pass
        
        total = len(all_texts)
        header_total = len(header_texts)
        
        print(f"  加载: {load_time:.1f}s  推理: {infer_time:.1f}s")
        print(f"  总行数: {total}  顶部行数: {header_total}")
        print(f"  顶部文本:")
        for t in header_texts[:10]:
            print(f"    {t}")
        
        results.append({
            "name": name, "det": det, "rec": rec,
            "load_time": round(load_time, 1),
            "infer_time": round(infer_time, 1),
            "total_lines": total,
            "header_lines": header_total,
            "header_texts": header_texts[:15],
            "status": "OK"
        })
    except Exception as e:
        print(f"  ❌ 失败: {e}")
        results.append({"name": name, "det": det, "rec": rec, "status": f"ERROR: {str(e)[:60]}"})

# 总结
print(f"\n\n{'='*60}")
print("测试总结")
print("="*60)
for r in results:
    status = r["status"]
    if status == "OK":
        print(f"  {r['name']}: ✅ {r['infer_time']}s | {r['total_lines']}行 | 顶部: {r['header_texts'][:3]}")
    else:
        print(f"  {r['name']}: ❌ {status}")

# 保存日志
log = Path(r"D:\hongmengProjects\doc-structure-mcp\docs\模型测试日志.md")
with open(log, "a", encoding="utf-8") as f:
    f.write(f"\n## 测试批次 {time.strftime('%m-%d %H:%M')}\n\n")
    for r in results:
        if r["status"] == "OK":
            f.write(f"| {r['name']} | {r['infer_time']}s | {r['total_lines']}行 | {r['header_texts'][:5]} |\n")
        else:
            f.write(f"| {r['name']} | ❌ {r['status']} |\n")
print(f"\n日志已更新: {log}")
