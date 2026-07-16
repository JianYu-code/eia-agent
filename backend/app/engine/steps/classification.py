import json
from app.knowledge.retriever import search_knowledge
from app.llm.client import chat
from app.engine.grader import build_issue


async def check_classification(full_text: str) -> list[dict]:
    issues = []

    results = search_knowledge("建设项目环境影响评价分类管理名录 分类等级 报告书 报告表", top_k=5)
    if not results:
        return issues

    ctx = "\n\n".join([f"[{r['title']}] {r['excerpt'][:500]}" for r in results])

    prompt = f"""你是环评审核专家。请审核以下环评报告的行业分类和环评等级判定。

参考标准（分类管理名录）：
{ctx}

报告内容（摘要）：
{full_text[:5000]}

请判断：
1. 项目的行业类别是否准确？
2. 环评等级（报告书/报告表/登记表）判定是否正确？
3. 如果报告中提到了环境敏感区，等级是否需要相应调整？

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
                    "R-CLS-001", item.get("severity", "P1"),
                    "分类管理",
                    item["title"], item.get("finding", ""),
                    law_ref=item.get("law_ref", ""),
                    suggestion=item.get("suggestion", ""),
                ))
    except Exception:
        pass

    return issues
