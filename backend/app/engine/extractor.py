import os
import re
from pathlib import Path


def extract_text(file_path: str) -> dict:
    ext = Path(file_path).suffix.lower()

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


def _extract_md(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    return _parse_content(content)


def _extract_txt(path: str) -> dict:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    return _parse_content(content)


def _extract_pdf(path: str) -> dict:
    try:
        import fitz
        doc = fitz.open(path)
        content = ""
        for page in doc:
            content += page.get_text()
        doc.close()
        return _parse_content(content)
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

            style = (p.style.name if p.style and p.style.name else "").lower()
            heading_level = 0

            # Layer 1: style name
            if "heading 1" in style or "标题 1" in style or "1 heading" in style:
                heading_level = 1
            elif "heading 2" in style or "标题 2" in style or "2 heading" in style:
                heading_level = 2
            elif "heading 3" in style or "标题 3" in style or "3 heading" in style:
                heading_level = 3

            # Layer 2: font size detection (big bold text = heading)
            if not heading_level:
                for run in p.runs:
                    try:
                        size = run.font.size
                        if size:
                            pt = size / 12700  # EMU to pt
                            if pt >= 14 and run.bold and len(text) < 80:
                                heading_level = 1
                                break
                    except Exception:
                        continue

            # Layer 3: content pattern match
            if not heading_level and len(text) < 80:
                import re
                cn_patterns = [
                    re.compile(r"^第[0-9一二三四五六七八九十百]+章\s*(.*)"),
                    re.compile(r"^[（(][一二三四五六七八九十]+[）)]\s*(.*)"),
                    re.compile(r"^[一二三四五六七八九十]+[、．，,]\s*(.*)"),
                    re.compile(r"^\d+(?:\.\d+)*\s+(.+?)(?:\s*\.{3,})?$"),
                ]
                for pat in cn_patterns:
                    if pat.match(text):
                        heading_level = 2
                        break

            if heading_level == 1:
                paragraphs.append("# " + text)
            elif heading_level == 2:
                paragraphs.append("## " + text)
            elif heading_level == 3:
                paragraphs.append("### " + text)
            else:
                paragraphs.append(text)

        content = "\n".join(paragraphs)
        tables = []
        for table in doc.tables:
            rows = [[cell.text for cell in row.cells] for row in table.rows]
            tables.append(rows)
        return {"full_text": content, "chapters": _split_chapters(content), "tables": tables}
    except ImportError:
        return _parse_content(f"[DOCX 解析需要 python-docx 库] {path}")


def _parse_content(content: str) -> dict:
    return {
        "full_text": content,
        "chapters": _split_chapters(content),
        "tables": [],
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
