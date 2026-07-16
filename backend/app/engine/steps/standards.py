import json
from app.knowledge.retriever import search_knowledge
from app.llm.client import chat
from app.engine.grader import build_issue


async def check_standards(full_text: str, standards_found: list[str]) -> list[dict]:
    issues = []
    if not standards_found:
        return issues

    for std in list(set(standards_found))[:12]:
        results = search_knowledge(f"{std} 替代 废止 更新 现行", top_k=3)
        if results and results[0].get("score", 1.0) < 0.6:
            issues.append(build_issue(
                "R-STD-001", "P1", "标准引用",
                f"标准 {std} 可能已废止或被替代",
                f"报告中引用了 {std}，知识库检索发现可能存在更新版本。",
                evidence=results[0].get("excerpt", "")[:200],
                suggestion=f"请确认 {std} 是否为现行有效版本，建议在知识库中查询最新替代标准。",
            ))

    return issues
