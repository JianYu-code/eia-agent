import json
import re
from app.knowledge.retriever import search_knowledge
from app.llm.client import chat, get_active_profile
from app.engine.grader import build_issue


async def check_calculations(full_text: str) -> list[dict]:
    issues = []
    profile = await get_active_profile()
    if not profile:
        return issues

    results = search_knowledge("锅炉 废气 排放量 计算 物料衡算 SO₂ 颗粒物 NOx", top_k=5)
    kb_ctx = "\n".join([f"[{r['title']}] {r['excerpt'][:300]}" for r in results])

    prompt = f"""你是环评审核专家。请检查报告中数值计算的合理性和准确性。

报告内容（摘要）：
{full_text[:5000]}

请检查：
1. 报告中出现的数值（排放量、浓度、去除效率等）是否存在明显的计算错误？
   - 例如：SO₂=2×B×S×(1-η)，如果给出了B(耗煤量)、S(硫分)、η(脱硫效率)，可以反算验证
   - 例如：颗粒物=B×A×d_fh×(1-η)/(1-C_fh)，可以验算
2. 是否存在数值单位错误（如mg/m³写成了kg/m³）？
3. 是否存在明显的数量级错误（如排放量10t/a写成10000t/a）？
4. 附表中的数值是否与正文一致？是否存在表内数据加总与合计不符？
5. 排放速率(kg/h)与排放总量(t/a)之间的换算是否正确（注意年运行时间）？

以JSON格式输出：[{{"severity":"P0/P1/P2","title":"...","finding":"...","evidence_location":"...","reasoning":"...","law_ref":"...","suggestion":"..."}}]
如果没有发现问题输出 []。只输出JSON。"""

    try:
        resp = await chat(prompt, profile=profile)
        resp = resp.strip()
        if resp.startswith("```"): resp = resp.split("```")[1]
        data = json.loads(resp)
        for item in data:
            if isinstance(item, dict) and item.get("title"):
                issues.append(build_issue("R-CALC-001", item.get("severity","P1"), "计算校验", item["title"], item.get("finding",""),
                    evidence=item.get("evidence_location",""), law_ref=item.get("law_ref",""), suggestion=item.get("suggestion","")))
    except Exception:
        pass

    return issues
