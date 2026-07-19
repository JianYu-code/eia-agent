"""vault → LanceDB 索引重建：扫描 Obsidian 兼容 vault 全部 MD，分块向量化入库"""
import hashlib
import re
from datetime import datetime
from pathlib import Path

import pyarrow as pa

from app.config import LANCE_TABLE, VECTOR_DIM
from app.knowledge.organizer import get_vault_dir, set_setting
from app.knowledge.retriever import embed_texts, get_db, get_table

SCHEMA = pa.schema([
    pa.field("id", pa.string()),
    pa.field("vector", pa.list_(pa.float32(), VECTOR_DIM)),
    pa.field("text", pa.string()),
    pa.field("title", pa.string()),
    pa.field("heading", pa.string()),
    pa.field("category", pa.string()),
    pa.field("standard_id", pa.string()),
    pa.field("source", pa.string()),
    pa.field("file_mtime", pa.float64()),
    pa.field("deprecated", pa.bool_()),
    pa.field("replaced_by", pa.string()),
])

BATCH = 32


def chunk_markdown(text: str, max_chars: int = 800, overlap: int = 100) -> list[dict]:
    sections = re.split(r"\n(?=#{2,3}\s)", text)
    chunks = []
    for section in sections:
        lines = section.strip().split("\n")
        heading = lines[0] if lines and lines[0].startswith("#") else ""
        heading_text = heading.lstrip("#").strip()
        body = "\n".join(lines[1:]) if heading else section
        if len(body) <= max_chars:
            chunks.append({"heading": heading_text, "text": section.strip()})
            continue
        start = 0
        while start < len(body):
            end = min(start + max_chars, len(body))
            chunk_text = (heading + "\n" if heading else "") + body[start:end]
            chunks.append({"heading": heading_text, "text": chunk_text.strip()})
            start += max_chars - overlap
    return chunks


def _guess_standard_id(title: str) -> str:
    m = re.search(r"(?:GB|GB/T|HJ|HJ/T)\s*[\d.\-—]+(?:\s*[—\-]\s*\d{4})?", title)
    return m.group().strip() if m else ""


def _drop_vault_rows():
    """删除表中所有 source 以 vault/ 开头的行（保留 MinerU 直接入库的旧索引）"""
    table = get_table()
    if table is None or table.count_rows() == 0:
        return
    import pyarrow.compute as pc
    at = table.to_lance().to_table()
    mask = pc.invert(pc.starts_with(at.column("source"), "vault/"))
    keep = at.filter(mask)
    db = get_db()
    db.drop_table(LANCE_TABLE, ignore_missing=True)
    db.create_table(LANCE_TABLE, keep, mode="overwrite")
    _reset_table_cache()


def _reset_table_cache():
    import app.knowledge.retriever as retr
    retr._table = None


async def reindex_vault() -> dict:
    """重建 vault 部分索引：清旧 vault 行 → 扫 MD → 分块 → 分批向量化 → 入库"""
    await set_setting("reindex_status", {"phase": "clearing"})
    _drop_vault_rows()

    vault = await get_vault_dir()
    md_files = sorted(vault.rglob("*.md"))
    total_files = len(md_files)
    if total_files == 0:
        await set_setting("reindex_status", {"phase": "done", "files": 0, "chunks": 0,
                                             "finished_at": datetime.now().isoformat()})
        return {"files": 0, "chunks": 0}

    rows = []
    total_chunks = 0
    for fi, md in enumerate(md_files):
        rel = str(md.relative_to(vault))
        await set_setting("reindex_status", {"phase": "indexing", "file_done": fi,
                                             "file_total": total_files, "chunks": total_chunks,
                                             "current": md.name})
        try:
            text = md.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if len(text.strip()) < 20:
            continue
        parts = Path(rel).parts
        category = "/".join(parts[:-1])
        title = md.stem
        std_id = _guess_standard_id(title)
        mtime = md.stat().st_mtime
        source = f"vault/{rel}"

        for ci, chunk in enumerate(chunk_markdown(text)):
            rows.append({
                "id": hashlib.md5((source + str(ci)).encode()).hexdigest(),
                "text": chunk["text"],
                "title": title,
                "heading": chunk.get("heading") or title,
                "category": category,
                "standard_id": std_id,
                "source": source,
                "file_mtime": mtime,
                "deprecated": False,
                "replaced_by": "",
            })

        if len(rows) >= BATCH * 4:
            total_chunks += await _flush_rows(rows)
            rows = []

    if rows:
        total_chunks += await _flush_rows(rows)

    await set_setting("reindex_status", {"phase": "done", "files": total_files,
                                         "chunks": total_chunks,
                                         "finished_at": datetime.now().isoformat()})
    return {"files": total_files, "chunks": total_chunks}


async def _flush_rows(rows: list[dict]) -> int:
    if not rows:
        return 0
    texts = [r["text"] for r in rows]
    vectors = []
    for i in range(0, len(texts), BATCH):
        vectors.extend(embed_texts(texts[i:i + BATCH]))
    for r, v in zip(rows, vectors):
        r["vector"] = v

    db = get_db()
    table = get_table()
    if table is None:
        db.create_table(LANCE_TABLE, rows, schema=SCHEMA, mode="overwrite")
    else:
        table.add(rows)
    _reset_table_cache()
    return len(rows)


async def delete_vault_file(vault_rel_path: str) -> int:
    """删除单个 vault 文件对应的索引行，返回删除数（近似）"""
    table = get_table()
    if table is None or table.count_rows() == 0:
        return 0
    import pyarrow.compute as pc
    at = table.to_lance().to_table()
    target = f"vault/{vault_rel_path}"
    mask = pc.not_equal(at.column("source"), target)
    removed = at.num_rows
    keep = at.filter(mask)
    removed -= keep.num_rows
    db = get_db()
    db.drop_table(LANCE_TABLE, ignore_missing=True)
    db.create_table(LANCE_TABLE, keep, mode="overwrite")
    _reset_table_cache()
    return removed
