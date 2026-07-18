import json
from app.knowledge.retriever import search_knowledge
from app.llm.client import chat, get_active_profile
from app.engine.grader import build_issue


async def check_emission_factors(full_text: str) -> list[dict]:
    issues = []
    profile = await get_active_profile()
    if not profile:
        return issues

    results = search_knowledge("产排污核算系数 产污系数 排污系数 排放源统计调查 产排污核算方法和系数手册 行业系数", top_k=6)
    kb_ctx = "\n".join([f"[{r['title']}] {r['excerpt'][:300]}" for r in results])

    prompt = f"""你是环评审核专家。请核对报告中使用的产污系数是否准确。

报告内容（摘要）：
{full_text[:4000]}

参考系数手册：
{kb_ctx}

请检查：
1. 报告中是否使用了产污系数/排污系数来核算污染物排放量？
2. 如果使用了系数法，系数来源是否注明（如《排污许可证申请与核发技术规范 锅炉》中的系数）？
3. 系数数值是否在行业合理范围内？是否存在明显的数量级错误？
4. 是否分别考虑了有组织排放和无组织排放的系数差异？
5. 对于VOCs排放，是否区分了物料衡算法和系数法？

以JSON格式输出：[{{"severity":"P0/P1/P2","title":"...","finding":"...","evidence_location":"...","reasoning":"...","law_ref":"...","suggestion":"..."}}]
如果没有问题输出 []。只输出JSON。"""

    try:
        resp = await chat(prompt, profile=profile)
        resp = resp.strip()
        if resp.startswith("```"): resp = resp.split("```")[1]
        data = json.loads(resp)
        for item in data:
            if isinstance(item, dict) and item.get("title"):
                issues.append(build_issue("R-COEFF-001", item.get("severity","P1"), "产污系数", item["title"], item.get("finding",""),
                    evidence=item.get("evidence_location",""), law_ref=item.get("law_ref",""), suggestion=item.get("suggestion","")))
    except Exception:
        pass

    return issues
