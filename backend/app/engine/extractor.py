import os
import re
from pathlib import Path

SUPPORTED_EXTS = {".md", ".txt", ".pdf", ".docx"}

TABLE_CAPTION_RE = re.compile(r"^表\s*(\d+(?:\s*[.\-—]\s*\d+)+)\s*([^\n]{0,60})")

_CN_HEADING_PATTERNS = [
    re.compile(r"^第[0-9一二三四五六七八九十百]+章\s*(.*)"),
    re.compile(r"^[（(][一二三四五六七八九十]+[）)]\s*(.*)"),
    re.compile(r"^[一二三四五六七八九十]+[、．，,]\s*(.*)"),
    re.compile(r"^\d+(?:\.\d+)*\s+(.+?)(?:\s*\.{3,})?$"),
]

PACKAGE_FILE_CLASSES = {
    "环评批复": ["批复", "审批", "核准", "决定"],
    "验收监测报告": ["验收监测", "验收调查", "监测报告", "检测报告"],
    "验收意见": ["验收意见", "验收结论", "专家组意见"],
    "其他说明事项": ["其他需要说明", "其他说明"],
    "信息公开证据": ["公示", "公开", "网站截图", "报纸"],
    "应急预案": ["应急预案", "应急预 案"],
    "风险评估": ["风险评估", "风险评价"],
    "资源调查": ["资源调查", "应急资源"],
    "编制说明": ["编制说明"],
    "发布令": ["发布令", "批准页", "颁布令"],
    "评审意见": ["评审意见", "评估意见", "审查意见"],
    "监测附件": ["监测附件", "原始数据", "检测附件", "质控"],
}


def extract_text(file_path: str) -> dict:
    path = Path(file_path)
    if path.is_dir():
        return _extract_folder(path)
    ext = path.suffix.lower()

    if ext == ".md":
        return _extract_md(file_path)
    elif ext == ".txt":
        return _extract_txt(file_path)
    elif ext == ".pdf":
        return _extract_pdf(file_path)
    elif ext == ".docx":
        return _extract_docx(file_path)
    else:
        return _extract_txt(file_path)


def _extract_folder(folder: Path) -> dict:
    """资料包提取：遍历目录内支持的文件，逐个提取后合并，并做文件名分类"""
    files = []
    for fp in sorted(folder.rglob("*")):
        if fp.is_file() and fp.suffix.lower() in SUPPORTED_EXTS:
            try:
                data = extract_text(str(fp))
                if data.get("full_text", "").strip():
                    files.append({
                        "name": fp.name,
                        "rel_path": str(fp.relative_to(folder)),
                        "text": data["full_text"],
                        "chapters": data.get("chapters", []),
                        "tables": data.get("tables", []),
                        "category": classify_file(fp.name),
                    })
            except Exception:
                files.append({
                    "name": fp.name,
                    "rel_path": str(fp.relative_to(folder)),
                    "text": "",
                    "chapters": [],
                    "category": "解析失败",
                })
    if not files:
        raise ValueError(f"资料包中未找到可解析的文件（支持 {'/'.join(SUPPORTED_EXTS)}）")

    merged_parts = []
    for f in files:
        merged_parts.append(f"\n\n===== 文件：{f['name']} =====\n\n{f['text']}")
    full_text = "".join(merged_parts).strip()

    chapters = []
    tables = []
    for f in files:
        for ch in f["chapters"]:
            chapters.append({**ch, "source_file": f["name"]})
        for t in f.get("tables", []):
            tables.append({**t, "source_file": f["name"]})

    return {
        "full_text": full_text,
        "chapters": chapters,
        "tables": tables,
        "files": files,
        "is_package": True,
    }


def classify_file(filename: str) -> str:
    """按文件名关键词分类资料包文件"""
    stem = Path(filename).stem
    for category, keywords in PACKAGE_FILE_CLASSES.items():
        if any(kw in stem for kw in keywords):
            return category
    return "其他"


def package_completeness(files: list[dict], required: list[str]) -> list[str]:
    """返回缺失的资料类别"""
    present = {f.get("category", "其他") for f in files if f.get("text", "").strip()}
    return [r for r in required if r not in present]


def _extract_md(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    return _parse_content(content)


def _extract_txt(path: str) -> dict:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    return _parse_content(content)


def _docx_heading_level(p, text: str) -> int:
    """DOCX 段落标题层级判定：样式名 → 字号加粗 → 中文编号模式"""
    style = (p.style.name if p.style and p.style.name else "").lower()
    if "heading 1" in style or "标题 1" in style or "1 heading" in style:
        return 1
    if "heading 2" in style or "标题 2" in style or "2 heading" in style:
        return 2
    if "heading 3" in style or "标题 3" in style or "3 heading" in style:
        return 3
    for run in p.runs:
        try:
            size = run.font.size
            if size:
                pt = size / 12700  # EMU to pt
                if pt >= 14 and run.bold and len(text) < 80:
                    return 1
        except Exception:
            continue
    if len(text) < 80:
        for pat in _CN_HEADING_PATTERNS:
            if pat.match(text):
                return 2
    return 0


def _norm_table_no(num: str) -> str:
    return re.sub(r"\s+", "", num or "").replace("—", "-").replace("–", "-")


def _extract_docx_tables(doc) -> list[dict]:
    """按文档顺序遍历段落与表格：表格关联表号（表X.Y-Z 题注）与所属章节。
    题注在表上方 2 段内或紧邻表下方均识别。"""
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx.oxml.ns import qn

    tables = []
    current_chapter = ""
    pending_caption = None
    gap = 99
    prev_table_idx = None

    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            p = Paragraph(child, doc)
            text = p.text.strip()
            if not text:
                continue
            if _docx_heading_level(p, text):
                current_chapter = text
                pending_caption = None
                gap = 99
                prev_table_idx = None
                continue
            m = TABLE_CAPTION_RE.match(text)
            if m:
                num = _norm_table_no(m.group(1))
                if prev_table_idx is not None and not tables[prev_table_idx]["caption"]:
                    tables[prev_table_idx]["caption"] = text
                    tables[prev_table_idx]["number"] = num
                else:
                    pending_caption = (text, num)
                    gap = 0
                prev_table_idx = None
                continue
            gap += 1
            if gap > 2:
                pending_caption = None
            prev_table_idx = None
        elif child.tag == qn("w:tbl"):
            t = Table(child, doc)
            rows = [[re.sub(r"\s+", " ", c.text).strip() for c in r.cells] for r in t.rows]
            if not rows:
                continue
            cap, num = pending_caption or ("", "")
            tables.append({"caption": cap, "number": num, "headers": rows[0],
                           "rows": rows[1:], "chapter": current_chapter, "source": "docx"})
            pending_caption = None
            gap = 99
            prev_table_idx = len(tables) - 1
    return tables


def _extract_pdf_tables(path: str, chapters: list[dict]) -> list[dict]:
    """PDF 表格提取（PyMuPDF find_tables）；矢量页有效，扫描页自动无结果"""
    tables = []
    try:
        import fitz
    except ImportError:
        return tables
    try:
        doc = fitz.open(path)
    except Exception:
        return tables
    try:
        for page in doc:
            if len(tables) >= 300:
                break
            try:
                finder = page.find_tables()
            except Exception:
                continue
            for t in getattr(finder, "tables", []):
                try:
                    data = t.extract()
                except Exception:
                    continue
                rows = [[re.sub(r"\s+", " ", str(c or "")).strip() for c in row] for row in data if row]
                if len(rows) < 2:
                    continue
                cap, num = "", ""
                try:
                    above = page.get_text(clip=fitz.Rect(0, max(0, t.bbox[1] - 70), page.rect.width, t.bbox[1]))
                    for ln in reversed([l.strip() for l in above.splitlines() if l.strip()]):
                        m = TABLE_CAPTION_RE.match(ln)
                        if m:
                            cap, num = ln, _norm_table_no(m.group(1))
                            break
                except Exception:
                    pass
                tables.append({"caption": cap, "number": num, "headers": rows[0],
                               "rows": rows[1:], "chapter": "", "source": "pdf"})
    finally:
        doc.close()

    for t in tables:
        key = t["caption"] or (t["headers"][0] if t["headers"] else "")
        if len(key) < 4:
            continue
        for ch in chapters:
            if key[:30] in ch.get("content", ""):
                t["chapter"] = ch.get("title", "")
                break
    return tables


def _parse_md_tables(text: str) -> list[dict]:
    """Markdown/TXT 管道表格解析（MinerU 输出等）"""
    lines = text.split("\n")
    tables = []
    current_chapter = ""
    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        hm = re.match(r"^#{1,4}\s+(.+)", ln)
        if hm:
            current_chapter = hm.group(1).strip()
        if not ln.startswith("|"):
            i += 1
            continue
        start = i
        block = []
        while i < len(lines) and lines[i].strip().startswith("|"):
            block.append(lines[i])
            i += 1
        rows = [[c.strip() for c in b.strip().strip("|").split("|")] for b in block]
        if len(rows) < 2:
            continue
        if not all(re.match(r"^:?-{2,}:?$", c.replace(" ", "")) for c in rows[1] if c):
            continue
        cap, num = "", ""
        for back in range(start - 1, max(-1, start - 4), -1):
            bl = lines[back].strip()
            if not bl:
                continue
            m = TABLE_CAPTION_RE.match(bl)
            if m:
                cap, num = bl, _norm_table_no(m.group(1))
            break
        tables.append({"caption": cap, "number": num, "headers": rows[0],
                       "rows": rows[2:], "chapter": current_chapter, "source": "md"})
    return tables


def _extract_pdf(path: str) -> dict:
    try:
        import fitz
        doc = fitz.open(path)
        content = ""
        for page in doc:
            content += page.get_text()
        doc.close()
        result = _parse_content(content)
        result["tables"] = _extract_pdf_tables(path, result["chapters"])
        return result
    except ImportError:
        return _parse_content(f"[PDF 解析需要 PyMuPDF 库] {path}")


def _extract_docx(path: str) -> dict:
    try:
        from docx import Document
        doc = Document(path)
        paragraphs = []
        for p in doc.paragraphs:
            text = p.text.strip()
            if not text:
                paragraphs.append("")
                continue

            heading_level = _docx_heading_level(p, text)
            if heading_level == 1:
                paragraphs.append("# " + text)
            elif heading_level == 2:
                paragraphs.append("## " + text)
            elif heading_level == 3:
                paragraphs.append("### " + text)
            else:
                paragraphs.append(text)

        content = "\n".join(paragraphs)
        tables = _extract_docx_tables(doc)

        chapters = _split_chapters(content)
        if len(content) < 500 or len(chapters) <= 1 or len(content) < 2000:
            try:
                import fitz
                pymu_doc = fitz.open(path)
                pymu_text = "\n".join(page.get_text() for page in pymu_doc)
                pymu_doc.close()
                if len(pymu_text) > len(content):
                    content = pymu_text
                    chapters = _split_chapters(content)
            except Exception:
                pass

        return {"full_text": content, "chapters": chapters, "tables": tables}
    except ImportError:
        return _parse_content(f"[DOCX 解析需要 python-docx 库] {path}")


def _parse_content(content: str) -> dict:
    return {
        "full_text": content,
        "chapters": _split_chapters(content),
        "tables": _parse_md_tables(content),
    }


def _split_chapters(text: str) -> list[dict]:
    patterns = [
        re.compile(r"^(#{1,4})\s+(.+)"),
        re.compile(r"^第[0-9一二三四五六七八九十百]+章\s*(.*)"),
        re.compile(r"^[（(][一二三四五六七八九十]+[）)]\s*(.*)"),
        re.compile(r"^[一二三四五六七八九十]+[、．，,]\s*(.*)"),
        re.compile(r"^\d+(?:\.\d+)*\s+(.+?)(?:\s*\.{3,})?$"),
    ]

    chapters = []
    lines = text.split("\n")
    current_chapter = None
    current_content = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current_chapter:
                current_content.append(line)
            continue

        matched = False
        for idx, pat in enumerate(patterns):
            m = pat.match(stripped)
            if m:
                if current_chapter:
                    current_chapter["content"] = "\n".join(current_content).strip()
                    chapters.append(current_chapter)

                if idx == 0:
                    title = m.group(2).strip()
                    level = len(m.group(1))
                else:
                    title = m.group(1).strip() if m.lastindex and m.lastindex >= 1 else stripped
                    level = 2

                if len(title) > 80:
                    continue

                current_chapter = {"title": title, "level": level, "content": ""}
                current_content = []
                matched = True
                break

        if not matched and current_chapter:
            current_content.append(line)

    if current_chapter:
        current_chapter["content"] = "\n".join(current_content).strip()
        chapters.append(current_chapter)

    if not chapters and text.strip():
        chapters = [{"title": "全文", "level": 1, "content": text.strip()}]

    return chapters
