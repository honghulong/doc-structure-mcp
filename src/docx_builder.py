"""
docx_builder.py — OCR结果 → 结构化DOCX
========================================
功能：XY-Cut排序、标题检测、段落合并、字体控制、签章页留空

排版原则：全部检测框统一按 XY-Cut 排序（Y分组、X排序），
保持从左到右、从上到下的阅读顺序。不特殊处理"竖排文字"，
因为中文本来就是横排书写。
"""
import io, re
from docx import Document
from docx.shared import Pt, Inches, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT


# ─── 页面参考尺寸（默认A4 200dpi） ───
PAGE_W = 1652


def build_honest_docx(pages, grid_cols=24, grid_rows=36):
    """Build a coordinate-faithful DOCX preview from OCR boxes.

    This mode intentionally avoids document-specific interpretation. It does
    not infer invoice fields, table semantics, headings, or paragraphs. Each
    OCR block is placed into a coarse page grid according to its bbox center,
    so the result exposes whether OCR coordinates are good enough for layout
    recovery without collapsing the page into a text stream.
    """
    doc = Document()
    section = doc.sections[0]
    section.top_margin = Cm(0.6)
    section.bottom_margin = Cm(0.6)
    section.left_margin = Cm(0.6)
    section.right_margin = Cm(0.6)

    style = doc.styles["Normal"]
    style.font.name = "SimSun"
    style.font.size = Pt(7)

    for index, pd in enumerate(pages):
        if index > 0:
            doc.add_page_break()

        lines = _normalized_lines(pd.get("ocr_lines", []))
        if not lines:
            doc.add_paragraph("")
            continue

        page_w = max((line["x2"] for line in lines), default=PAGE_W)
        page_h = max((line["y2"] for line in lines), default=PAGE_W * 1.414)
        col_w = page_w / grid_cols
        row_h = page_h / grid_rows

        table = doc.add_table(rows=grid_rows, cols=grid_cols)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = False
        table.allow_autofit = False

        usable_width = section.page_width - section.left_margin - section.right_margin
        for col in table.columns:
            for cell in col.cells:
                cell.width = int(usable_width / grid_cols)
                cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
                for paragraph in cell.paragraphs:
                    paragraph.paragraph_format.space_before = Pt(0)
                    paragraph.paragraph_format.space_after = Pt(0)
                    paragraph.paragraph_format.line_spacing = 1.0

        for line in sorted(lines, key=lambda item: (item["y1"], item["x1"])):
            text = line["text"].strip()
            if not text or line["confidence"] < 0.3:
                continue
            row = min(grid_rows - 1, max(0, int(((line["y1"] + line["y2"]) / 2) / row_h)))
            col = min(grid_cols - 1, max(0, int(((line["x1"] + line["x2"]) / 2) / col_w)))
            cell = table.cell(row, col)
            paragraph = cell.paragraphs[0]
            if paragraph.text:
                paragraph.add_run(" ")
            run = paragraph.add_run(text)
            run.font.name = "SimSun"
            run.font.size = Pt(_font_size_for_box(line, row_h))

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()


def build_docx(pages, skip_blank_pages=True):
    """OCR页列表 → DOCX字节

    红头竖排文字：拆单字 → 按列分组 → 列转行（横排阅读顺序）
    正文区域：XY-Cut 排序（Y分组、X排序）
    """
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "SimSun"
    style.font.size = Pt(11)
    style.paragraph_format.line_spacing = 1.5
    style.paragraph_format.space_after = Pt(2)
    style.paragraph_format.first_line_indent = Cm(0.74)

    for pd in pages:
        page_num = pd["page"]
        lines = pd.get("ocr_lines", [])
        text = pd.get("ocr_text", "")
        page_w = _estimate_page_width(lines) or PAGE_W

        if page_num > 1:
            doc.add_page_break()

        # 签章页
        if skip_blank_pages and _is_seal_page(lines, text):
            _add_seal_page_placeholder(doc, page_num)
            continue

        # 解析：竖排红头（列转行） + 正文块（XY-Cut）
        vertical_rows, body_blocks = _parse_blocks(lines)

        # 红头输出（竖排列转横向行，红色）
        for row_text in vertical_rows:
            p = doc.add_paragraph(row_text)
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            for run in p.runs:
                run.font.name = "SimHei"
                run.font.size = Pt(14)
                run.font.color.rgb = RGBColor(0xCC, 0x00, 0x00)

        # 正文 XY-Cut 排序 + 输出
        body_rows = _xycut_sort(body_blocks)
        body_rows = _merge_body_rows(body_rows)
        _add_rows(doc, body_rows, page_w)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()


# ─── 坐标解析与排序 ───

def _normalized_lines(lines):
    normalized = []
    for line in lines:
        bbox = line.get("bbox", [])
        if len(bbox) < 4:
            continue
        if isinstance(bbox[0], (list, tuple)):
            xs = [float(point[0]) for point in bbox if len(point) >= 2]
            ys = [float(point[1]) for point in bbox if len(point) >= 2]
        else:
            xs = [float(x) for x in bbox[0::2]]
            ys = [float(y) for y in bbox[1::2]]
        if not xs or not ys:
            continue
        normalized.append({
            "text": str(line.get("text", "")),
            "confidence": float(line.get("confidence", 0)),
            "x1": min(xs),
            "y1": min(ys),
            "x2": max(xs),
            "y2": max(ys),
        })
    return normalized


def _font_size_for_box(line, row_h):
    height = max(1.0, line["y2"] - line["y1"])
    ratio = height / max(1.0, row_h)
    if ratio > 1.6:
        return 8
    if ratio < 0.8:
        return 6
    return 7

# 已知部门名（用于拆分合并的行，按长度降序匹配）
DEPT_NAMES = sorted([
    "国家电子文件管理部际联席会议办公室",
    "中国国家铁路集团有限公司",
    "中国人民银行",
    "国家税务总局",
    "国务院国资委",
    "国家标准委",
    "国家档案局",
    "中国民航局",
    "财政部",
    "税务总局",
], key=len, reverse=True)


def _split_departments(text):
    """按已知部门名拆分合并的行"""
    parts = []
    remaining = text
    while remaining:
        matched = False
        for d in DEPT_NAMES:
            if remaining.startswith(d):
                parts.append(d)
                remaining = remaining[len(d):]
                matched = True
                break
        if not matched:
            remaining = remaining[1:]
    return parts


def _split_tall_block_text(text):
    """竖排框合并文字 → 逐字列表，去重相邻重复

    例如 "财税中国国国" → ["财", "税", "中", "国"]
    """
    if not text:
        return []
    chars = list(text)
    deduped = [chars[0]]
    for c in chars[1:]:
        if c != deduped[-1]:
            deduped.append(c)
    return deduped


def _parse_blocks(lines):
    """OCR 行 → (竖向行列表, 正文块列表)

    竖排框（顶部区域 + 高 > 宽×2）：拆单字 → 按 X 列分组 → 列转行
    普通框：保留供 XY-Cut 排序

    过滤策略：只有 Y 在图片前 25% 区域的框才视为红头竖排，
    避免正文区窄字（如"档案""案准"）被误检测。
    """
    vertical_cols = {}   # x_bucket → [char1, char2, ...]
    body_blocks = []

    # 第一遍：收集所有框，找竖排候选
    max_y = 0
    all_entries = []
    tall_candidates = []  # (y1, l, x1, x2, y1, y2, text, conf)
    for l in lines:
        bbox = l.get("bbox", [])
        if len(bbox) < 8:
            continue
        xs = bbox[0::2]; ys = bbox[1::2]
        text = l.get("text", "").strip()
        conf = l.get("confidence", 0)
        if not text or conf < 0.3:
            continue
        x1, x2 = min(xs), max(xs)
        y1, y2 = min(ys), max(ys)
        w = x2 - x1; h = y2 - y1
        max_y = max(max_y, y2)
        entry = (l, x1, x2, y1, y2, text, conf, w, h)
        all_entries.append(entry)
        if h > w * 2 and h > 80:
            tall_candidates.append(entry)

    if not all_entries:
        return [], []

    # 找最顶部竖排框的起始 Y，只保留与其相差 < 60px 的块
    top_y = min((e[3] for e in tall_candidates), default=max_y)
    for l, x1, x2, y1, y2, text, conf, w, h in all_entries:
        cx = (x1 + x2) / 2
        if h > w * 2 and h > 80 and y1 - top_y < 60:
            chars = _split_tall_block_text(text)
            xb = round(cx / 60) * 60
            if xb not in vertical_cols:
                vertical_cols[xb] = []
            vertical_cols[xb].extend(chars)
        else:
            cy = (y1 + y2) / 2
            body_blocks.append({
                "text": text, "conf": conf,
                "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                "cx": cx, "cy": cy, "w": w, "h": h,
            })

    # 竖排列转行
    vertical_rows = _transpose_vertical(vertical_cols)
    return vertical_rows, body_blocks


def _transpose_vertical(cols):
    """竖排列 → 横排行（列转行）

    输入: {x_bucket: [char_list]}
    输出: ["财国人银行部", "税务局国资局", "中行", ...]
    """
    if not cols:
        return []

    # 按 X 排序列
    sorted_x = sorted(cols.keys())
    columns = [cols[xb] for xb in sorted_x]

    # 找最大行数
    max_rows = max(len(c) for c in columns)

    # 列转行
    rows = []
    for ri in range(max_rows):
        row_chars = []
        for col in columns:
            if ri < len(col):
                row_chars.append(col[ri])
        if row_chars:
            # 去除行末空字符引起的多余空格
            rows.append("".join(row_chars))

    return rows


def _xycut_sort(blocks):
    """XY-Cut 排序：全部框按 Y 分组为行，行内按 X 排序"""
    if not blocks:
        return []
    blocks.sort(key=lambda b: b["cy"])
    y_thresh = 30
    rows = [[blocks[0]]]
    for b in blocks[1:]:
        if abs(b["cy"] - rows[-1][-1]["cy"]) < y_thresh:
            rows[-1].append(b)
        else:
            rows.append([b])
    # 每行按 X 排序
    return [sorted(row, key=lambda x: x["cx"]) for row in rows]


# ─── 正文处理 ───

def _merge_body_rows(rows):
    """合并同一段落内连续的行

    规则：
    - 如果某行以标点结尾，且下一行非标题 → 合并
    - 如果某行宽度占满页面，且下一行无缩进 → 合并
    """
    if not rows:
        return rows

    merged = [list(rows[0])]
    for row in rows[1:]:
        prev_text = "".join(b["text"] for b in merged[-1])
        curr_text = "".join(b["text"] for b in row)

        # 当前行是标题 → 不合并
        if _is_heading(curr_text) or _is_dept_line(curr_text):
            merged.append(list(row))
            continue

        # 前一行以标点结尾 → 是段落结束，不合并
        if prev_text and prev_text[-1] in "。；！？":
            merged.append(list(row))
            continue

        # 前一行很短（<15字）且当前行很短 → 可能是独立行（如发文单位）
        if len(prev_text) < 15 and len(curr_text) < 15:
            merged.append(list(row))
            continue

        # 合并
        merged[-1].extend(row)

    return merged


def _is_dept_line(text):
    """判断是否为发文单位行"""
    for dept in DEPT_NAMES:
        if dept in text:
            return True
    return False


def _add_rows(doc, rows, page_w=PAGE_W):
    """输出正文行（含标题检测 + 部门名拆分 + 段落合并）"""
    for row in rows:
        text = "".join(b["text"] for b in row)
        if not text.strip():
            continue
        # 跳过页码
        if re.match(r'^[—\-]\d+[—\-]$', text.strip()):
            continue

        # 尝试按部门名拆分（红头附近的发文单位行）
        dept_parts = _split_departments(text)
        if len(dept_parts) > 1:
            for dept in dept_parts:
                _add_paragraph(doc, dept, style="dept_name")
            continue

        # 判断类别
        is_title = _is_heading(text)
        is_dept = _is_dept_line(text)

        if is_title:
            _add_paragraph(doc, text, style="title")
        elif is_dept:
            _add_paragraph(doc, text, style="dept_name")
        else:
            _add_paragraph(doc, text, style="body")


def _add_paragraph(doc, text, style="body"):
    """添加段落，按样式设置字体/对齐"""
    if style == "title":
        p = doc.add_heading(text, level=2)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in p.runs:
            run.font.name = "SimHei"
            run.font.size = Pt(16)
            run.font.bold = True
            run.font.color.rgb = RGBColor(0, 0, 0)

    elif style == "dept_name":
        p = doc.add_paragraph(text)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in p.runs:
            run.font.name = "SimSun"
            run.font.size = Pt(11)
            run.font.bold = True

    else:  # body
        p = doc.add_paragraph(text)
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        for run in p.runs:
            run.font.name = "SimSun"
            run.font.size = Pt(11)


# ─── 辅助函数 ───

def _is_seal_page(lines, text):
    if len(lines) < 5:
        return False
    short = sum(1 for l in lines if len(l.get("text", "")) <= 3)
    ratio = short / len(lines) if lines else 0
    avg_conf = sum(l.get("confidence", 0) for l in lines) / len(lines) if lines else 0
    return ratio > 0.4 and avg_conf < 0.8


def _add_seal_page_placeholder(doc, page_num):
    p = doc.add_paragraph()
    run = p.add_run(f"—— 第{page_num}页（签章页，已跳过）——")
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(0x99, 0x99, 0x99)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER


def _is_heading(text):
    """标题检测：正则规则 + 发文关键词"""
    patterns = [
        r'^[一二三四五六七八九十]+、',
        r'^（[一二三四五六七八九十]+）',
        r'^\d+\.\s*',
        r'^[（(]\d+[）)]',
        r'关于.+的通知$',
        r'关于.+的.{1,4}$',
        r'^.{0,15}通知$',
        r'^.{0,10}意见$',
        r'^.{0,10}办法$',
        r'^.{0,10}规定$',
    ]
    for pat in patterns:
        if re.match(pat, text):
            return True
    # 短行无句尾标点 → 可能是标题
    if len(text) <= 12 and not text[-1] in "，。；：！？、":
        return True
    return False


def _estimate_page_width(lines):
    """从检测框 x 最大值估算页面宽度"""
    max_x = 0
    for l in lines:
        bbox = l.get("bbox", [])
        if len(bbox) >= 8:
            xs = bbox[0::2]
            max_x = max(max_x, max(xs))
    return int(max_x) if max_x > 0 else PAGE_W
