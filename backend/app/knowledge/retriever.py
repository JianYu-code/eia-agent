import re

import httpx

try:
    import lancedb
except ImportError:
    lancedb = None

from app.config import LANCE_DB_DIR, LANCE_TABLE, OLLAMA_EMBED_URL, OLLAMA_EMBED_MODEL

STD_ID_RE = re.compile(r"(?:GB|GB/T|HJ|HJ/T|DB)\s*\d+[\d.\-—]*\d+")

_db = None
_table = None
_embed_warned = False


def _norm_sid(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").upper()).replace("—", "-").replace("–", "-")


def _bigrams(s: str) -> set:
    s = re.sub(r"[^一-龥]", "", s or "")
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else set()


def _title_boost_sids(query: str) -> dict:
    """标准索引标题倒排：{standard_id: bigram 重合度}（重合度 ≥0.2），按重合度降序。
    限值类无编号查询的主题→标准映射（如"燃煤锅炉二氧化硫"→GB13271）。"""
    try:
        from app.engine.standards_index import load_standards_index
        index = load_standards_index()
    except Exception:
        return {}
    q_bg = _bigrams(query)
    if not q_bg:
        return {}
    hits = {}
    for sid, entry in index.items():
        t_bg = _bigrams(entry.get("title", ""))
        if t_bg:
            ratio = len(q_bg & t_bg) / len(t_bg)
            if ratio >= 0.2:
                hits[sid] = round(ratio, 3)
    return dict(sorted(hits.items(), key=lambda x: -x[1]))


def _rerank(results: list[dict], query: str, top_k: int) -> list[dict]:
    """真重排：向量距离 + 标准号精确命中（大权重）+ 索引标题倒排 + 标题命中
    + 标题完整命中 + 文本 bigram 重合 - 废止重罚"""
    q_sids = {_norm_sid(m.group(0)) for m in STD_ID_RE.finditer(query)}
    q_kws = [w for w in set(re.findall(r"[一-龥]{2,}", query)) if len(w) >= 2][:6]
    boost_sids = _title_boost_sids(query) if not q_sids else set()
    q_bg = _bigrams(query)
    scored = []
    for r in results:
        dist = r.get("_distance", 1.0)
        sid = _norm_sid(r.get("standard_id", ""))
        title = (r.get("title") or "").upper().replace(" ", "")
        if sid and any(q == sid or q in sid or sid in q for q in q_sids):
            dist -= 1.0
        if q_sids and any(_norm_sid(q) in title for q in q_sids):
            dist -= 0.3
        if boost_sids and sid in boost_sids:
            dist -= 0.6 * boost_sids[sid]
        raw_title = r.get("title") or ""
        if raw_title and len(raw_title) >= 4 and raw_title in query:
            dist -= 0.15
        for kw in q_kws:
            if kw in raw_title:
                dist -= 0.05
        t_bg = _bigrams(r.get("text", "") or "")
        if t_bg:
            dist -= min(len(q_bg & t_bg) / max(len(q_bg), 1) * 0.3, 0.3)
        if r.get("deprecated"):
            dist += 0.5
        scored.append((dist, r))
    scored.sort(key=lambda x: x[0])
    return [r for _, r in scored[:top_k]]


def _merge_neighbors(items: list[dict], max_chars: int = 800) -> list[dict]:
    """同 source+heading 的相邻分块拼接（保住被截断的限值表），excerpt 上限 max_chars"""
    merged: dict[tuple, dict] = {}
    order = []
    for it in items:
        key = (it.get("source", ""), it.get("relative_path", ""))
        if key in merged:
            base = merged[key]
            room = max_chars - len(base["excerpt"]) - 1
            if room > 50:
                base["excerpt"] += "\n" + it["excerpt"][:room]
            continue
        merged[key] = dict(it)
        order.append(key)
    return [merged[k] for k in order]


def get_db():
    if lancedb is None:
        raise RuntimeError("lancedb 未安装，知识库检索不可用")
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


def check_embed_health() -> dict:
    """Ollama 向量化服务健康检查"""
    try:
        resp = httpx.post(
            OLLAMA_EMBED_URL,
            json={"model": OLLAMA_EMBED_MODEL, "input": ["健康检查"]},
            timeout=10,
        )
        resp.raise_for_status()
        ok = bool(resp.json().get("embeddings"))
        return {"available": ok, "url": OLLAMA_EMBED_URL, "model": OLLAMA_EMBED_MODEL,
                "error": "" if ok else "响应无 embeddings"}
    except Exception as e:
        return {"available": False, "url": OLLAMA_EMBED_URL, "model": OLLAMA_EMBED_MODEL,
                "error": str(e)[:200]}


def search_knowledge(query: str, top_k: int = 5, include_deprecated: bool = False) -> list[dict]:
    """知识库检索：候选池×5 → 废止过滤（默认）→ 真重排 → 邻块拼接。
    Ollama/LanceDB 不可用时返回空列表（降级不致命）。
    include_deprecated=True 时放行废止分块（替代沿革类查询专用，重排仍降权）。"""
    global _embed_warned
    try:
        table = get_table()
        if table is None or table.count_rows() == 0:
            return []
    except Exception as e:
        if not _embed_warned:
            _embed_warned = True
            print(f"[knowledge] 向量库不可用（{str(e)[:120]}），检索降级为空。")
        return []

    try:
        query_vec = embed_query(query)
    except Exception as e:
        if not _embed_warned:
            _embed_warned = True
            print(f"[knowledge] 向量化服务不可用（{str(e)[:120]}），检索降级为空。请检查 Ollama 是否启动。")
        return []

    pool_size = max(top_k * 5, 25)
    search = table.search(query_vec)
    if not include_deprecated:
        try:
            search = search.where("deprecated = false", prefilter=True)
        except Exception:
            pass
    results = search.limit(pool_size).to_list()

    q_sids_explicit = {_norm_sid(m.group(0)) for m in STD_ID_RE.finditer(query)}
    boosts = _title_boost_sids(query) if not q_sids_explicit else {}
    direct_sids = list(q_sids_explicit) + [s for s, ratio in boosts.items() if ratio >= 0.5][:2]
    if direct_sids:
        have = {_norm_sid(r.get("standard_id", "")) for r in results}
        for sid in direct_sids[:3]:
            if sid in have:
                continue
            m = re.match(r"^([A-Z/]+)(\d+)[-—]?(\d+)$", sid)
            if not m:
                continue
            try:
                where = f"standard_id LIKE '%{m.group(2)}%{m.group(3)}%'"
                if not include_deprecated:
                    where += " AND deprecated = false"
                results.extend(table.search(query_vec).where(where, prefilter=True).limit(2).to_list())
            except Exception:
                continue

    results = _rerank(results, query, top_k)

    items = []
    for r in results:
        full_text = r.get("text", "") or ""
        excerpt = full_text[:800] if len(full_text) > 800 else full_text
        items.append({
            "id": r.get("id", ""),
            "title": r.get("title", "未命名"),
            "relative_path": f"{r.get('heading', '')}",
            "category": r.get("category", ""),
            "excerpt": excerpt,
            "score": r.get("_distance", 0),
            "deprecated": bool(r.get("deprecated", False)),
            "replaced_by": r.get("replaced_by", ""),
            "standard_id": r.get("standard_id", ""),
            "source": r.get("source", ""),
        })
    return _merge_neighbors(items)


def table_count() -> int:
    try:
        table = get_table()
        if table is None:
            return 0
        return table.count_rows()
    except Exception:
        return 0


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
    import pyarrow as pa
    at = table.to_lance().to_table()

    sources = at.column("source").to_pylist()
    dep_list = list(at.column("deprecated").to_pylist())
    rep_list = list(at.column("replaced_by").to_pylist())

    changed = False
    for i, src in enumerate(sources):
        if src == source:
            dep_list[i] = True
            rep_list[i] = replaced_by
            changed = True

    if not changed:
        return

    idx_dep = at.schema.get_field_index("deprecated")
    idx_rep = at.schema.get_field_index("replaced_by")
    new_at = at.set_column(idx_dep, "deprecated", pa.array(dep_list, type=pa.bool_()))
    new_at = new_at.set_column(idx_rep, "replaced_by", pa.array(rep_list, type=pa.string()))

    get_db().drop_table(LANCE_TABLE, ignore_missing=True)
    get_db().create_table(LANCE_TABLE, new_at, mode="overwrite")
    global _table
    _table = None


def mark_deprecated_batch(updates: list[tuple[str, str]]):
    """批量标记废止: [(source_path, replaced_by), ...] — 一次重建完成"""
    if not updates:
        return
    table = get_table()
    if table is None or table.count_rows() == 0:
        return
    import pyarrow as pa
    at = table.to_lance().to_table()

    update_map = {src: reason for src, reason in updates}

    sources = at.column("source").to_pylist()
    dep_list = list(at.column("deprecated").to_pylist())
    rep_list = list(at.column("replaced_by").to_pylist())

    changed = 0
    for i, src in enumerate(sources):
        if src in update_map:
            dep_list[i] = True
            rep_list[i] = update_map[src]
            changed += 1

    if not changed:
        return

    idx_dep = at.schema.get_field_index("deprecated")
    idx_rep = at.schema.get_field_index("replaced_by")
    new_at = at.set_column(idx_dep, "deprecated", pa.array(dep_list, type=pa.bool_()))
    new_at = new_at.set_column(idx_rep, "replaced_by", pa.array(rep_list, type=pa.string()))

    get_db().drop_table(LANCE_TABLE, ignore_missing=True)
    get_db().create_table(LANCE_TABLE, new_at, mode="overwrite")
    global _table
    _table = None
    print(f"  批量标记 {changed} 条记录为废止")
