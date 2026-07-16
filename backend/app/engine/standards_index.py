import json
from pathlib import Path

INDEX_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "standards_index.json"


def build_standards_index():
    """从 LanceDB 知识库导出精确的标准版本索引"""
    from app.knowledge.retriever import get_table

    table = get_table()
    if table is None or table.count_rows() == 0:
        return {}

    at = table.to_lance().to_table()
    sids = at.column("standard_id").to_pylist()
    titles = at.column("title").to_pylist()
    deps = at.column("deprecated").to_pylist()
    reps = at.column("replaced_by").to_pylist()
    categories = at.column("category").to_pylist()

    index = {}
    seen = set()
    for sid, title, dep, rep, cat in zip(sids, titles, deps, reps, categories):
        if not sid or sid in seen:
            continue
        seen.add(sid)
        status = "废止" if dep else "现行"
        replaced = []
        if rep:
            for part in rep.replace("代替了", ",").split(","):
                part = part.strip()
                if part and part != sid:
                    replaced.append(part)
        index[sid] = {
            "title": title,
            "status": status,
            "category": cat,
            "replaced_by": replaced,
        }

    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    return index


def load_standards_index() -> dict:
    if INDEX_PATH.exists():
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def exact_standard_lookup(std_id: str) -> dict | None:
    """精确查找标准的状态和替代关系"""
    index = load_standards_index()
    if not index:
        index = build_standards_index()
    return index.get(std_id)
