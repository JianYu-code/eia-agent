import httpx
import lancedb

from app.config import LANCE_DB_DIR, LANCE_TABLE, OLLAMA_EMBED_URL, OLLAMA_EMBED_MODEL

_db = None
_table = None


def get_db() -> lancedb.DBConnection:
    global _db
    if _db is None:
        _db = lancedb.connect(LANCE_DB_DIR)
    return _db


def get_table():
    global _table
    if _table is None:
        db = get_db()
        try:
            _table = db.open_table(LANCE_TABLE)
        except Exception:
            _table = None
    return _table


def embed_texts(texts: list[str]) -> list[list[float]]:
    resp = httpx.post(
        OLLAMA_EMBED_URL,
        json={"model": OLLAMA_EMBED_MODEL, "input": texts},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["embeddings"]


def embed_query(query: str) -> list[float]:
    return embed_texts([query])[0]


def search_knowledge(query: str, top_k: int = 5) -> list[dict]:
    table = get_table()
    if table is None or table.count_rows() == 0:
        return []

    query_vec = embed_query(query)

    results = table.search(query_vec).limit(top_k).to_list()

    items = []
    for r in results:
        items.append({
            "id": r.get("id", ""),
            "title": r.get("title", "未命名"),
            "relative_path": r.get("heading", ""),
            "category": r.get("category", ""),
            "excerpt": (r.get("text", "") or "")[:500],
            "score": r.get("_distance", 0),
            "deprecated": bool(r.get("deprecated", False)),
            "replaced_by": r.get("replaced_by", ""),
            "standard_id": r.get("standard_id", ""),
        })
    return items


def table_count() -> int:
    table = get_table()
    if table is None:
        return 0
    return table.count_rows()


def get_source_index() -> dict[str, dict]:
    table = get_table()
    if table is None or table.count_rows() == 0:
        return {}

    at = table.to_lance().to_table(columns=["source", "file_mtime"])
    sources = at.column("source").to_pylist()
    mtimes = at.column("file_mtime").to_pylist() if "file_mtime" in at.schema.names else [0.0] * len(sources)

    index = {}
    for src, mt in zip(sources, mtimes):
        if src not in index:
            index[src] = {"file_mtime": mt, "chunk_count": 0}
        index[src]["chunk_count"] += 1
    return index


def get_outdated_documents() -> list[dict]:
    table = get_table()
    if table is None or table.count_rows() == 0:
        return []

    import pyarrow.compute as pc
    at = table.to_lance().to_table()
    mask = pc.equal(at.column("deprecated"), True)
    deprecated_at = at.filter(mask)

    if deprecated_at.num_rows == 0:
        return []

    seen = set()
    docs = []
    sources = deprecated_at.column("source").to_pylist()
    titles = deprecated_at.column("title").to_pylist()
    replaced = deprecated_at.column("replaced_by").to_pylist()
    categories = deprecated_at.column("category").to_pylist()
    mt_column = deprecated_at.column("file_mtime").to_pylist() if "file_mtime" in deprecated_at.schema.names else [0.0] * deprecated_at.num_rows

    for i in range(deprecated_at.num_rows):
        src = sources[i]
        if src in seen:
            continue
        seen.add(src)
        from datetime import datetime
        mt = mt_column[i] if isinstance(mt_column[i], (int, float)) else 0.0
        docs.append({
            "source": src,
            "title": titles[i],
            "category": categories[i],
            "replaced_by": replaced[i] or "",
            "file_mtime": datetime.fromtimestamp(mt).isoformat() if mt > 0 else "",
        })

    docs.sort(key=lambda d: d["file_mtime"], reverse=True)
    return docs


def delete_chunks_by_source(source: str):
    table = get_table()
    if table is None:
        return
    import pyarrow.compute as pc
    at = table.to_lance().to_table()
    mask = pc.not_equal(at.column("source"), source)
    keep = at.filter(mask)
    get_db().drop_table(LANCE_TABLE, ignore_missing=True)
    get_db().create_table(LANCE_TABLE, keep, mode="overwrite")
    global _table
    _table = None


def mark_deprecated(source: str, replaced_by: str = ""):
    table = get_table()
    if table is None or table.count_rows() == 0:
        return
    import pyarrow.compute as pc
    at = table.to_lance().to_table()
    dep_mask = pc.equal(at.column("source"), source)
    updated_deprecated = pc.if_else(dep_mask, pc.scalar(True), at.column("deprecated"))
    updated_replaced = pc.if_else(dep_mask, pc.scalar(replaced_by), at.column("replaced_by"))

    new_at = at.set_column(at.schema.get_field_index("deprecated"), "deprecated", updated_deprecated)
    new_at = new_at.set_column(new_at.schema.get_field_index("replaced_by"), "replaced_by", updated_replaced)

    get_db().drop_table(LANCE_TABLE, ignore_missing=True)
    get_db().create_table(LANCE_TABLE, new_at, mode="overwrite")
    global _table
    _table = None
