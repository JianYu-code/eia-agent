import json
import re
from app.knowledge.retriever import search_knowledge
from app.llm.client import chat, get_active_profile
from app.engine.grader import build_issue


async def check_sensitive_targets(full_text: str) -> list[dict]:
    issues = []
    profile = await get_active_profile()
    if not profile:
        return issues

    coords = re.findall(r"(\d{2,3})[°度](\d{1,2})[′分](\d{1,2}[\d.]*)[″秒]", full_text)
    has_coords = len(coords) > 0

    results = search_knowledge("环境敏感目标 环境保护目标 自然保护区 水源保护区 学校 医院 居民区", top_k=5)
    kb_ctx = "\n".join([f"[{r['title']}] {r['excerpt'][:300]}" for r in results])

    prompt = f"""你是环评审核专家。请检查报告中的环境敏感目标识别是否完善。

{"注意：报告中包含地理坐标，请检查这些坐标附近可能存在的敏感目标。" if has_coords else "注意：报告中未发现明确的地理坐标，这本身就是一个需要注意的问题。"}

报告内容（摘要）：
{full_text[:4000]}

请检查：
1. 是否识别了厂界/项目周边500m范围内的敏感目标（居民区、学校、医院、水源地等）？
2. 是否列出了敏感目标的名称、方位、距离？
3. 是否绘制了敏感目标分布图或说明了敏感目标的位置关系？
{"4. 如有地理坐标，是否说明了坐标系的类型（WGS84/CGCS2000等）？" if has_coords else ""}
5. 项目是否涉及自然保护区、风景名胜区、饮用水源保护区等特殊敏感区？如果涉及，是否进行了专题分析？

以JSON格式输出问题列表：[{{"severity":"P0/P1/P2","title":"...","finding":"...","evidence_location":"...","reasoning":"...","law_ref":"...","suggestion":"..."}}]
如果没有问题输出 []。只输出JSON。"""

    try:
        resp = await chat(prompt, profile=profile)
        resp = resp.strip()
        if resp.startswith("```"): resp = resp.split("```")[1]
        data = json.loads(resp)
        for item in data:
            if isinstance(item, dict) and item.get("title"):
                issues.append(build_issue("R-SENS-001", item.get("severity","P1"), "敏感目标", item["title"], item.get("finding",""),
                    evidence=item.get("evidence_location",""), law_ref=item.get("law_ref",""), suggestion=item.get("suggestion","")))
    except Exception:
        pass

    return issues
