import json
from app.knowledge.retriever import search_knowledge
from app.llm.client import chat, get_active_profile
from app.engine.grader import build_issue


async def check_hazardous_waste(full_text: str) -> list[dict]:
    issues = []
    profile = await get_active_profile()
    if not profile:
        return issues

    has_hw = any(kw in full_text for kw in ["危废","危险废物","HW","离子交换树脂","废机油","废活性炭","废催化剂","废溶剂","废酸","废碱","含油","含铅"])
    if not has_hw:
        return issues

    results = search_knowledge("国家危险废物名录 HW 废物代码 危险废物类别 有机树脂 废矿物油 900", top_k=6)
    kb_ctx = "\n".join([f"[{r['title']}] {r['excerpt'][:350]}" for r in results])

    prompt = f"""你是危废管理专家。请核查报告中的危险废物识别和代码归类是否准确。

报告内容（摘要）：
{full_text[:4000]}

参考危废名录：
{kb_ctx}

请逐项检查：
1. 报告中提到的固体废物中，是否遗漏了应识别为危险废物的种类？
   - 如废机油（HW08）、废活性炭（可能HW06/HW49）、废离子交换树脂（HW13）、废催化剂等
2. 已识别的危废代码是否正确？
   - 例如：废离子交换树脂的正确代码是 HW13 900-015-13（有机树脂类废物）
   - 例如：废机油的正确代码是 HW08 900-214-08
3. 危废代码中的废物类别和废物代码是否匹配？
4. 是否给出了危废的产生量（或估算方法）、暂存方式和处置去向？
5. 对于未识别的危废，应提示需要补充识别

注意：由于危废名录复杂，判别结果仅作参考。请标注"仅供参考，建议查阅最新版《国家危险废物名录》确认"。
以JSON格式输出：[{{"severity":"P0/P1/P2","title":"...","finding":"...","evidence_location":"...","reasoning":"...","law_ref":"...","suggestion":"..."}}]
如果没有问题输出 []。只输出JSON。"""

    try:
        resp = await chat(prompt, profile=profile)
        resp = resp.strip()
        if resp.startswith("```"): resp = resp.split("```")[1]
        data = json.loads(resp)
        for item in data:
            if isinstance(item, dict) and item.get("title"):
                issues.append(build_issue("R-HW-001", item.get("severity","P1"), "危废核查", item["title"], item.get("finding",""),
                    evidence=item.get("evidence_location",""), law_ref=item.get("law_ref",""), suggestion=item.get("suggestion","")))
    except Exception:
        pass

    return issues
