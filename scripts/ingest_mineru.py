import sys
import os
import re
from pathlib import Path

import lancedb
import pyarrow as pa

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.config import MINERU_OUTPUT_DIR, LANCE_DB_DIR, LANCE_TABLE, VECTOR_DIM
from app.knowledge.retriever import embed_texts

VECTOR_DIM = 1024

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


def scan_md_files(root_dir: str) -> list[dict]:
    files = []
    root = Path(root_dir)
    for md_path in root.rglob("*.md"):
        rel = md_path.relative_to(root)
        parts = rel.parts
        category = "/".join(parts[:-2]) if len(parts) >= 2 else ""
        files.append({
            "path": str(md_path),
            "filename": md_path.name,
            "category": category,
            "title": md_path.stem,
            "mtime": os.path.getmtime(md_path),
        })
    return files


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
    patterns = [
        r"(?:GB|GB/T|HJ|HJ/T)\s*[\d.\-—]+(?:\s*[—\-]\s*\d{4})?",
        r"第\s*\d+\s*号",
    ]
    for p in patterns:
        match = re.search(p, title)
        if match:
            return match.group().strip()
    return ""


OBSOLETE_FILENAME_KEYWORDS = ["废止", "作废", "失效", "已被替代", "已被代替", "（废止）", "（作废）"]

REPLACEMENT_PATTERNS = [
    re.compile(r"(?:本标准|本文件|本导则|本规范)(?:代替|替代|代替了|替代了)\s*(GB(?:/T)?|HJ(?:/T)?)\s*[\d.\-—]+(?:\s*[—\-]\s*\d{4})?"),
    re.compile(r"(?:代替|替代|取代|代替了|替代了)(?:了?\s*)(GB(?:/T)?|HJ(?:/T)?)\s*[\d.\-—]+(?:\s*[—\-]\s*\d{4})?"),
    re.compile(r"自\s*(?:本(?:标准|文件|导则|规范))?\s*(?:实施|执行|施行)之日\s*(?:起)?\s*[,，]?\s*(GB(?:/T)?|HJ(?:/T)?)\s*[\d.\-—]+(?:\s*[—\-]\s*\d{4})?\s*(?:同时)?\s*废止"),
]

STD_ID_PATTERN = re.compile(r"(?:GB|GB/T|HJ|HJ/T)\s*[\d.\-—]+(?:\s*[—\-]\s*\d{4})?")


def _find_replacement_relationships(all_files: list[dict]) -> list[dict]:
    """返回 [{source, action: 'mark_self_obsolete'|'mark_others_obsolete', targets: [std_id]}]"""
    actions = []

    for f in all_files:
        filename_lower = f["filename"].lower()
        title = f["title"]

        is_self_obsolete = any(kw in title for kw in OBSOLETE_FILENAME_KEYWORDS)
        if is_self_obsolete:
            actions.append({"source": f["path"], "action": "mark_self_obsolete", "targets": []})
            continue

        try:
            text = Path(f["path"]).read_text(encoding="utf-8", errors="ignore")
            head = text[:3000]
        except Exception:
            continue

        headline_obsolete = any(kw in head[:500] for kw in ["废止", "已废止", "作废"])
        if headline_obsolete and is_self_obsolete is not False:
            actions.append({"source": f["path"], "action": "mark_self_obsolete", "targets": []})
            continue

        replaced_ids = set()
        for pattern in REPLACEMENT_PATTERNS:
            for match in pattern.finditer(head):
                raw = match.group(0)
                std_match = STD_ID_PATTERN.search(raw)
                if std_match:
                    std_id = std_match.group().strip()
                    if std_id != _guess_standard_id(title):
                        replaced_ids.add(std_id)

        if replaced_ids:
            self_id = _guess_standard_id(title)
            targets = list(replaced_ids)
            if self_id and self_id not in targets:
                actions.append({
                    "source": f["path"],
                    "action": "mark_others_obsolete",
                    "targets": targets,
                    "replacement_reason": f"{self_id} 代替了 {'、'.join(targets)}",
                })

    return actions


def detect_and_apply_obsolete(all_files: list[dict] = None):
    """扫描已入库内容，自动检测废止/替代关系并打标"""
    from app.knowledge.retriever import get_table, mark_deprecated
    print("=== 检测废止/替代关系 ===")

    if all_files is None:
        all_files = scan_md_files(MINERU_OUTPUT_DIR)
        print(f"扫描 {len(all_files)} 个文件")

    actions = _find_replacement_relationships(all_files)
    print(f"发现 {len(actions)} 个废止/替代关系")

    self_obsolete = 0
    replaced = 0
    for action in actions:
        if action["action"] == "mark_self_obsolete":
            mark_deprecated(action["source"])
            self_obsolete += 1
            print(f"  [自身废止] {Path(action['source']).name}")
        elif action["action"] == "mark_others_obsolete":
            for target_id in action["targets"]:
                table = get_table()
                if table is None or table.count_rows() == 0:
                    continue
                at = table.to_lance().to_table()
                import pyarrow.compute as pc
                mask = pc.equal(at.column("standard_id"), target_id)
                matched = at.filter(mask)
                if matched.num_rows > 0:
                    sources = set(matched.column("source").to_pylist())
                    for src in sources:
                        reason = action.get("replacement_reason", "")
                        mark_deprecated(src, reason)
                        replaced += 1
                        print(f"  [标记废止] {Path(src).name} → 被 {action['replacement_reason']}")

    print(f"\n自身废止: {self_obsolete} 个文件")
    print(f"标记替代: {replaced} 个文件")def _make_row(md_file: dict, chunk_idx: int, chunk: dict) -> dict:
    import hashlib
    chunk_id = hashlib.md5((md_file["path"] + str(chunk_idx)).encode()).hexdigest()
    standard_id = _guess_standard_id(md_file["title"])
    heading = chunk.get("heading", "") if chunk.get("heading") else md_file["title"]
    return {
        "id": chunk_id,
        "text": chunk["text"],
        "title": md_file["title"],
        "heading": heading,
        "category": md_file["category"],
        "standard_id": standard_id,
        "source": md_file["path"],
        "file_mtime": md_file["mtime"],
        "deprecated": False,
        "replaced_by": "",
    }


def _dedup_ids(table) -> set:
    """读取已有 id 集合"""
    if table is None or table.count_rows() == 0:
        return set()
    return set(table.to_lance().to_table(columns=["id"]).column("id").to_pylist())


def _flush(db, table, rows):
    texts = [r["text"] for r in rows]
    vecs = embed_texts(texts)
    for i, r in enumerate(rows):
        r["vector"] = vecs[i]

    if table is None:
        db.create_table(LANCE_TABLE, rows, schema=SCHEMA, mode="overwrite")
    else:
        table.add(rows)


def full_ingest():
    """全量入库（首次或重建）"""
    print("=== 全量入库 ===")
    db = lancedb.connect(LANCE_DB_DIR)

    try:
        db.drop_table(LANCE_TABLE)
        print("已删除旧知识库")
    except Exception:
        pass

    md_files = scan_md_files(MINERU_OUTPUT_DIR)
    print(f"找到 {len(md_files)} 个 MD 文件")

    table = None
    total_chunks = 0
    batch_rows = []

    for idx, md_file in enumerate(md_files):
        try:
            text = Path(md_file["path"]).read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            print(f"  [跳过] {md_file['path']}: {e}")
            continue

        chunks = chunk_markdown(text)
        for ci, chunk in enumerate(chunks):
            row = _make_row(md_file, ci, chunk)
            batch_rows.append(row)
            total_chunks += 1

            if len(batch_rows) >= 100:
                _flush(db, table, batch_rows)
                table = db.open_table(LANCE_TABLE)
                print(f"  进度: {idx+1}/{len(md_files)}, 已入库 {total_chunks} 条")
                batch_rows = []

        if (idx + 1) % 200 == 0:
            print(f"  进度: {idx+1}/{len(md_files)}")

    if batch_rows:
        _flush(db, table, batch_rows)
        table = db.open_table(LANCE_TABLE)

    print(f"\n全量入库完成: {total_chunks} 条记录")
    print()
    detect_and_apply_obsolete(md_files)


def sync_incremental():
    """增量同步：只处理新增/更新的文件，废止文件保留并标记"""
    print("=== 增量同步 ===")
    from app.knowledge.retriever import get_source_index, delete_chunks_by_source, mark_deprecated, get_table

    db = lancedb.connect(LANCE_DB_DIR)
    table = get_table()

    if table is None or table.count_rows() == 0:
        print("知识库为空，执行全量入库...")
        return full_ingest()

    source_index = get_source_index()
    print(f"已索引文件: {len(source_index)}")

    md_files = scan_md_files(MINERU_OUTPUT_DIR)
    on_disk = {f["path"]: f for f in md_files}
    print(f"磁盘文件: {len(on_disk)}")

    new_count = 0
    update_count = 0
    deprecate_count = 0
    batch_rows = []
    table = None

    # NEW / UPDATED
    for path, f in on_disk.items():
        if path not in source_index:
            # 新文件
            try:
                text = Path(path).read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                print(f"  [跳过] {path}: {e}")
                continue
            chunks = chunk_markdown(text)
            for ci, chunk in enumerate(chunks):
                batch_rows.append(_make_row(f, ci, chunk))
                new_count += 1
                if len(batch_rows) >= 100:
                    _flush(db, table, batch_rows)
                    table = db.open_table(LANCE_TABLE)
                    batch_rows = []
        elif abs(f["mtime"] - source_index[path]["file_mtime"]) > 1.0:
            # 文件已更新，删旧入新，旧版本标记为废止
            standard_id = _guess_standard_id(f["title"])
            mark_deprecated(path, standard_id)
            delete_chunks_by_source(path)
            deprecate_count += 1

            try:
                text = Path(path).read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                print(f"  [跳过] {path}: {e}")
                continue
            chunks = chunk_markdown(text)
            for ci, chunk in enumerate(chunks):
                batch_rows.append(_make_row(f, ci, chunk))
                update_count += new_count
                if len(batch_rows) >= 100:
                    _flush(db, table, batch_rows)
                    table = db.open_table(LANCE_TABLE)
                    batch_rows = []

    # DELETED from disk → mark as deprecated
    for path in source_index:
        if path not in on_disk:
            mark_deprecated(path)
            deprecate_count += 1
            print(f"  [废止] {path} → 已标记为废止")

    if batch_rows:
        _flush(db, table, batch_rows)
        table = db.open_table(LANCE_TABLE)

    table = db.open_table(LANCE_TABLE)
    print(f"\n增量同步完成:")
    print(f"  新增: {new_count} 条")
    print(f"  废止: {deprecate_count} 个文件")
    print(f"  知识库总数: {table.count_rows()} 条")


def remove_files(sources: list[str]):
    """按 source 路径批量删除"""
    from app.knowledge.retriever import delete_chunks_by_source
    removed = 0
    for src in sources:
        try:
            delete_chunks_by_source(src)
            removed += 1
            print(f"  [删除] {src}")
        except Exception as e:
            print(f"  [失败] {src}: {e}")
    print(f"\n共删除 {removed} 个文件的所有块")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("command", nargs="?", default="full", choices=["full", "sync", "remove", "detect-obsolete"])
    parser.add_argument("--sources", nargs="*", default=[], help="要删除的文件路径（remove 命令用）")
    args = parser.parse_args()

    if args.command == "sync":
        sync_incremental()
    elif args.command == "remove":
        remove_files(args.sources)
    elif args.command == "detect-obsolete":
        detect_and_apply_obsolete()
    else:
        full_ingest()
