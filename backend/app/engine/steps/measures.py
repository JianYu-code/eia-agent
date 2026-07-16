import json
from app.knowledge.retriever import search_knowledge
from app.llm.client import chat
from app.engine.grader import build_issue


async def check_measures(full_text: str) -> list[dict]:
    issues = []

    results = search_knowledge("污染防治 可行技术 最佳可行技术指南 处理效率", top_k=5)
    if not results:
        return issues

    ctx = "\n\n".join([f"[{r['title']}] {r['excerpt'][:500]}" for r in results])

    prompt = f"""你是环评审核专家。请审核以下环评报告中的污染防治措施部分。

参考标准：
{ctx}

报告内容（摘要）：
{full_text[:6000]}

请检查：
1. 措施是否具体（有工艺参数、设计指标）还是形式化描述？
2. 处理效率是否有依据（类比数据、工程设计、技术规范）？
3. 是否考虑了最不利工况下的处理效果？
4. 治理后的排放浓度能否满足排放标准限值？
5. 对废水/废气/固废/噪声各要素的措施是否完整？

以JSON数组格式输出问题列表。如果没有发现问题输出 []。只输出JSON。"""

    try:
        resp = await chat(prompt)
        resp = resp.strip()
        if resp.startswith("```"):
            resp = resp.split("```")[1]
            if resp.startswith("json"):
                resp = resp[4:]
        data = json.loads(resp)
        for item in data:
            if isinstance(item, dict) and item.get("title"):
                issues.append(build_issue(
                    "R-MSR-001", item.get("severity", "P1"),
                    "污染防治措施",
                    item["title"], item.get("finding", ""),
                    law_ref=item.get("law_ref", ""),
                    suggestion=item.get("suggestion", ""),
                ))
    except Exception:
        pass

    return issues
