import json
from app.knowledge.retriever import search_knowledge
from app.llm.client import chat, get_active_profile
from app.engine.grader import build_issue


async def check_emission_limits(full_text: str) -> list[dict]:
    issues = []
    profile = await get_active_profile()
    if not profile:
        return issues

    results = search_knowledge("锅炉大气污染物排放标准 水污染物排放标准 噪声排放标准 限值 mg/m³", top_k=8)
    kb_ctx = "\n".join([f"[{r['title']}] {r['excerpt'][:400]}" for r in results])

    prompt = f"""你是环评审核专家。请验证报告中引用的排放标准限值是否准确。

报告内容（摘要）：
{full_text[:5000]}

参考标准原文：
{kb_ctx}

请逐条检查：
1. 报告中提到的排放限值（如"颗粒物50mg/m³、SO₂300mg/m³"）是否与标准原文一致？
2. 如果报告引用的是地方标准，地方标准限值是否严于国家标准？
3. 是否混淆了"新建锅炉"和"在用锅炉"的限值？
4. "特别排放限值"的适用条件是否正确（重点地区/执行特别限值的区域）？
5. 有无标准限值引用的单位错误（如mg/m³写成g/m³）？

以JSON格式输出：[{{"severity":"P0/P1/P2","title":"...","finding":"...","evidence_location":"...","reasoning":"...","law_ref":"...","suggestion":"..."}}]
如果标准限值引用全部正确，输出 []。只输出JSON。"""

    try:
        resp = await chat(prompt, profile=profile)
        resp = resp.strip()
        if resp.startswith("```"): resp = resp.split("```")[1]
        data = json.loads(resp)
        for item in data:
            if isinstance(item, dict) and item.get("title"):
                issues.append(build_issue("R-LIMIT-001", item.get("severity","P1"), "排放标准限值", item["title"], item.get("finding",""),
                    evidence=item.get("evidence_location",""), law_ref=item.get("law_ref",""), suggestion=item.get("suggestion","")))
    except Exception:
        pass

    return issues
