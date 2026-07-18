import json
from app.llm.client import chat, get_active_profile
from app.engine.grader import build_issue


async def check_text_figure_consistency(full_text: str) -> list[dict]:
    issues = []
    profile = await get_active_profile()
    if not profile:
        return issues

    has_fig = any(kw in full_text[:2000] for kw in ["图", "附图", "插图", "见图", "示意图", "下图", "上表", "下表", "上图"])
    if not has_fig:
        return issues

    prompt = f"""你是环评审核专家。请检查报告中文字描述与图表引用的逻辑一致性。

报告内容（摘要）：
{full_text[:5000]}

请检查（仅检查文字逻辑，不检查实际图片内容）：
1. 正文中引用"见图X"或"如表X"，但该图表编号是否连续且存在？
   - 例如：文中写"见表3-2"，但上下文未出现表3-2
2. 同一数据在不同章节的文字描述是否一致？
   - 例如：前言中说"年产100吨"，工程分析中说"年产120吨"
3. 正文中描述的排放值是否与同一章节表格中的数据一致？
4. 工艺流程文字描述与"图X 工艺流程图"的标题是否匹配？
5. "物料平衡表"或"水平衡表"中数据的加和是否与合计数一致？

注意：本检查仅基于文本内容进行逻辑比对，不涉及实际图片内容的AI视觉识别。
以JSON格式输出：[{{"severity":"P0/P1/P2","title":"...","finding":"...","evidence_location":"...","reasoning":"...","law_ref":"...","suggestion":"..."}}]
如果没有问题输出 []。只输出JSON。"""

    try:
        resp = await chat(prompt, profile=profile)
        resp = resp.strip()
        if resp.startswith("```"): resp = resp.split("```")[1]
        data = json.loads(resp)
        for item in data:
            if isinstance(item, dict) and item.get("title"):
                issues.append(build_issue("R-FIG-001", item.get("severity","P1"), "图文一致性", item["title"], item.get("finding",""),
                    evidence=item.get("evidence_location",""), law_ref=item.get("law_ref",""), suggestion=item.get("suggestion","")))
    except Exception:
        pass

    return issues
