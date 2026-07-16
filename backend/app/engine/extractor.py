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
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
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
    chapters = []
    lines = text.split("\n")
    current_chapter = None
    current_content = []

    for line in lines:
        match = re.match(r"^(#{1,4})\s+(.+)", line)
        if match:
            if current_chapter:
                current_chapter["content"] = "\n".join(current_content).strip()
                chapters.append(current_chapter)
            level = len(match.group(1))
            title = match.group(2).strip()
            current_chapter = {"title": title, "level": level, "content": ""}
            current_content = []
        elif current_chapter:
            current_content.append(line)

    if current_chapter:
        current_chapter["content"] = "\n".join(current_content).strip()
        chapters.append(current_chapter)

    if not chapters and text.strip():
        chapters = [{"title": "全文", "level": 1, "content": text.strip()}]

    return chapters
