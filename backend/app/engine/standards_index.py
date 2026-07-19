import json
import os
import re
import sqlite3
from pathlib import Path

INDEX_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "standards_index.json"


def _norm_sid(sid: str) -> str:
    s = (sid or "").strip().upper()
    s = re.sub(r"\s+", "", s)
    return s.replace("—", "-").replace("–", "-").replace("－", "-")


def _make_entry(title: str, dep, rep: str, category: str, self_sid: str = "") -> dict:
    replaced = []
    if rep:
        for part in str(rep).replace("代替了", ",").split(","):
            part = part.strip()
            if part and _norm_sid(part) != self_sid:
                replaced.append(part)
    return {
        "title": title or "",
        "status": "废止" if dep else "现行",
        "category": category or "",
        "replaced_by": replaced,
    }


def _lance_entries() -> dict:
    """源 1：LanceDB 向量库中的标准索引"""
    entries = {}
    try:
        from app.knowledge.retriever import get_table
        table = get_table()
        if table is None or table.count_rows() == 0:
            return entries
        at = table.to_lance().to_table()
        sids = at.column("standard_id").to_pylist()
        titles = at.column("title").to_pylist()
        deps = at.column("deprecated").to_pylist()
        reps = at.column("replaced_by").to_pylist()
        cats = at.column("category").to_pylist()
        for sid, title, dep, rep, cat in zip(sids, titles, deps, reps, cats):
            norm = _norm_sid(sid)
            if len(norm) < 5 or norm in entries:
                continue
            entries[norm] = _make_entry(title, dep, rep, cat, norm)
    except Exception:
        pass
    return entries


def _file_index_entries() -> dict:
    """源 2：SQLite file_index 表（文件级索引，含废止标记）"""
    entries = {}
    try:
        from app.config import DATABASE_URL
        m = re.search(r"sqlite\+aiosqlite:///(.+)", DATABASE_URL)
        if not m or not os.path.exists(m.group(1)):
            return entries
        conn = sqlite3.connect(f"file:{m.group(1)}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT standard_id, title, deprecated, replaced_by, category "
                "FROM file_index WHERE standard_id != ''").fetchall()
        finally:
            conn.close()
        for sid, title, dep, rep, cat in rows:
            norm = _norm_sid(sid)
            if len(norm) < 5 or norm in entries:
                continue
            entries[norm] = _make_entry(title, dep, rep, cat, norm)
    except Exception:
        pass
    return entries


REPLACEMENT_MAP_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "replacement_map.json"


def _replacement_map() -> dict:
    """scan_xiaozhi detect 产出的替代关系映射 {被替代: 替代者}，用于回填 LanceDB 侧缺失的 replaced_by"""
    if REPLACEMENT_MAP_PATH.exists():
        try:
            return json.loads(REPLACEMENT_MAP_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def build_standards_index() -> dict:
    """双源合并重建精确标准索引：LanceDB ∪ file_index（file_index 废止标记优先），
    再用替代关系映射回填 LanceDB 侧缺失的 replaced_by"""
    index = _lance_entries()
    for sid, entry in _file_index_entries().items():
        if sid not in index or entry["status"] == "废止":
            index[sid] = entry

    rel = _replacement_map()
    for sid, entry in index.items():
        if entry["status"] == "废止" and not entry["replaced_by"]:
            src = rel.get(sid)
            if src:
                entry["replaced_by"] = [src]

    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    return index


def load_standards_index() -> dict:
    if INDEX_PATH.exists():
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def ensure_standards_index(min_expected: int = 100) -> dict:
    """启动自检：索引缺失或明显过期（条目数 < 数据源应有量的 1/10）→ 自动重建"""
    index = load_standards_index()
    expected = max(len(_file_index_entries()), min_expected)
    if len(index) >= max(expected // 10, 1) and len(index) >= min_expected:
        return index
    rebuilt = build_standards_index()
    if rebuilt:
        print(f"[standards] 标准索引已自动重建：{len(index)} → {len(rebuilt)} 条")
        return rebuilt
    return index


def exact_standard_lookup(std_id: str) -> dict | None:
    """精确查找标准的状态和替代关系"""
    index = load_standards_index()
    if not index:
        index = build_standards_index()
    return index.get(std_id)
