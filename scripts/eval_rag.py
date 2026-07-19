"""RAG 检索效果评测：12 个典型问题，对比 新检索(过滤+重排+扩展) vs 旧行为(纯向量top5) 的 top-5 命中率
用法: python scripts/eval_rag.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

EVAL_QUESTIONS = [
    ("燃煤锅炉二氧化硫排放限值是多少", "GB13271-2014"),
    ("环境空气质量标准 PM2.5 年均浓度限值", "GB3095"),
    ("危险废物贮存污染控制标准现行版本", "GB18597"),
    ("声环境质量标准2类区夜间限值", "GB3096-2008"),
    ("建设项目环评分类管理名录报告书报告表判定", "分类管理名录"),
    ("地下水环境影响评价等级如何判定", "HJ610-2016"),
    ("大气评价等级 Pmax 判定方法", "HJ2.2-2018"),
    ("一般工业固体废物贮存标准现行版本", "GB18599"),
    ("废活性炭属于危险废物吗代码是多少", "危险废物名录"),
    ("污水综合排放标准COD一级限值", "GB8978-1996"),
    ("环境风险评价Q值如何计算", "HJ169-2018"),
    ("地表水环境质量标准氨氮限值", "GB3838-2002"),
]


def hit(results: list[dict], expect: str) -> bool:
    norm = expect.replace(" ", "").upper()
    for r in results[:5]:
        sid = (r.get("standard_id", "") or "").replace(" ", "").upper()
        title = (r.get("title", "") or "").replace(" ", "").upper()
        if norm in sid or norm in title:
            return True
    return False


def legacy_search(query: str, top_k: int = 5) -> list[dict]:
    """模拟旧行为：纯向量 top_k，无过滤无重排"""
    from app.knowledge.retriever import get_table, embed_query
    table = get_table()
    if table is None:
        return []
    results = table.search(embed_query(query)).limit(top_k).to_list()
    return [{"standard_id": r.get("standard_id", ""), "title": r.get("title", ""),
             "deprecated": bool(r.get("deprecated", False))} for r in results]


def main():
    from app.knowledge.retriever import search_knowledge, table_count
    print(f"知识库分块数: {table_count()}")
    print(f"{'问题':<28} {'期望':<16} {'旧':<4} {'新':<4} {'新结果含废止'}")
    new_hits = old_hits = 0
    for q, expect in EVAL_QUESTIONS:
        old = legacy_search(q, 5)
        new = search_knowledge(q, top_k=5)
        o, n = hit(old, expect), hit(new, expect)
        old_hits += o
        new_hits += n
        dep = "是" if any(r.get("deprecated") for r in new) else "否"
        print(f"{q[:26]:<28} {expect:<16} {'O' if o else 'X':<4} {'O' if n else 'X':<4} {dep}")
    print(f"\n旧行为 top-5 命中: {old_hits}/{len(EVAL_QUESTIONS)}")
    print(f"新检索 top-5 命中: {new_hits}/{len(EVAL_QUESTIONS)}")


if __name__ == "__main__":
    main()
