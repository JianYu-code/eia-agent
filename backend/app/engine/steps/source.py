import json
from app.knowledge.retriever import search_knowledge
from app.llm.client import chat, get_active_profile
from app.engine.grader import build_issue


async def check_source_strength(full_text: str, chapters: list[dict]) -> list[dict]:
    issues = []
    profile = await get_active_profile()

    industries = []
    for kw in ["锅炉", "制糖", "淀粉", "农药", "制药", "造纸", "钢铁", "火电", "水泥", "化工", "电镀", "印染", "屠宰", "医院"]:
        if kw in full_text[:3000]:
            industries.append(kw)

    if industries:
        query = f"{' '.join(industries)} 源强核算技术指南 核算方法 优先次序 类比条件 污染因子"
        results = search_knowledge(query, top_k=8)
    else:
        results = search_knowledge("源强核算技术指南 核算方法 优先次序 类比条件 污染因子", top_k=8)
    if not results:
        issues.append(build_issue(
            "R-SRC-000", "P2", "源强核算",
            "知识库中未检索到源强核算相关依据",
            "无法检索行业源强核算指南，跳过深度源强分析。请确认知识库已入库污染源源强核算技术指南。",
        ))
        return issues

    ctx = "\n\n".join([f"[{r['title']}] {r['excerpt'][:600]}" for r in results])

    prompt = f"""你是环评审核专家。请审核以下环评报告中的污染源源强核算部分。

参考标准：
{ctx}

报告内容（摘要）：
{full_text[:8000]}

请逐项检查以下内容，每发现一个问题，按JSON格式输出：
1. 污染源和评价因子是否完整？有无遗漏？
2. 源强核算方法是否符合技术指南的优先次序（物料衡算>类比>排污系数>产污系数）？
3. 若使用类比法，是否满足类比条件（原料成分差异≤10%、规模差异≤30%、工艺相同）？
4. 源强取值是否有明确出处（类比项目名称、实测数据来源）？
5. 是否考虑了正常排放和非正常排放两种情况？
6. 污染因子是否覆盖了该行业排放标准中的全部控制项目？

以JSON数组格式输出问题列表，格式如下：
[{{"severity": "P0/P1/P2", "title": "问题标题", "finding": "具体发现", "law_ref": "法规依据（标准编号）", "suggestion": "修改建议"}}]

如果没有发现问题，输出空数组 []。
所有引用的标准编号必须与参考标准原文完全一致，不得编造标准号。
行业源强核算技术指南编号格式为 HJ 数字-年份（如 HJ 984-2018），若不确定具体编号不要强行写。
只输出JSON，不要任何解释或补充文字。"""

    try:
        resp = await chat(prompt, profile=profile)
        resp = resp.strip()
        if resp.startswith("```"):
            resp = resp.split("```")[1]
            if resp.startswith("json"):
                resp = resp[4:]
        data = json.loads(resp)
        for item in data:
            if isinstance(item, dict) and item.get("title"):
                issues.append(build_issue(
                    "R-SRC-001", item.get("severity", "P1"),
                    "源强核算",
                    item["title"],
                    item.get("finding", ""),
                    law_ref=item.get("law_ref", ""),
                    suggestion=item.get("suggestion", ""),
                ))
    except Exception:
        issues.append(build_issue(
            "R-SRC-001", "P2", "源强核算",
            "LLM 深度分析暂时不可用",
            "请配置大模型 API Key 后重新审核，以获得源强核算专业分析。",
            suggestion="在设置页面配置智谱/通义/DeepSeek 的 API Key。",
        ))

    return issues
