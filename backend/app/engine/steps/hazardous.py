import re
from app.engine.context import build_step_context
from app.engine.llm_json import parse_llm_json
from app.llm.client import chat, get_active_profile
from app.engine.grader import build_issue
from app.engine.coefficient_db import query_waste

TARGET_CHAPTERS = ["固废", "固体废物", "危险废物", "工程分析", "污染防治"]


async def check_hazardous_waste(text_data: dict, audit_ctx: dict | None = None) -> list[dict]:
    """危废代码核查：数据库精确匹配 + LLM兜底"""
    issues = []
    profile = await get_active_profile()
    if not profile:
        raise RuntimeError("未配置启用的 LLM Profile")

    full_text = text_data.get("full_text", "")
    context = build_step_context(text_data, TARGET_CHAPTERS)

    hw_kw = ["危废", "危险废物", "HW", "离子交换树脂", "废机油", "废活性炭", "废催化剂",
             "废溶剂", "废酸", "废碱", "含油", "含铅", "废树脂", "废包装", "漆渣", "污泥"]
    has_hw = any(kw in full_text for kw in hw_kw)
    if not has_hw:
        return issues

    db_matches = []
    for kw in ["废离子交换树脂", "废机油", "废活性炭", "废催化剂", "废酸", "废碱",
               "漆渣", "含油", "废包装", "电镀", "污泥", "废矿物油", "废溶剂", "废树脂"]:
        if kw in full_text:
            results = await query_waste(name=kw)
            for r in results:
                db_matches.append(r)

    db_matched_kw = set()
    for m in db_matches:
        for kw in hw_kw:
            if kw in full_text and kw in (m.get("name", "") + m.get("source_waste", "")):
                db_matched_kw.add(kw)

    unmatched = [kw for kw in hw_kw if kw in full_text and kw not in db_matched_kw]

    llm_issues = []
    if unmatched:
        results = await query_waste(name="")
        kb_ctx = "\n".join([f"HW{r['category']} {r['code']} {r['name']}: {r['source_waste']}" for r in results[:10]])

        prompt = f"""你是危废管理专家。以下报告可能含有未匹配的危废关键词，请核查：

报告相关章节内容：
{context}

常用危废代码参考：
{kb_ctx}

未匹配的关键词: {', '.join(unmatched[:8])}

请检查是否有遗漏/错误的危废代码归类，输出JSON数组：[{{"severity":"P1","title":"...","finding":"..."}}]
如果没有问题输出 []。只输出JSON。"""

        resp = await chat(prompt, profile=profile)
        data = parse_llm_json(resp, expect="array") or []
        for item in data:
            if isinstance(item, dict) and item.get("title"):
                llm_issues.append(item)

    for m in db_matches:
        issues.append(build_issue(
            "R-HW-001", "P1", "危废核查",
            f"识别到可能危废: {m['name']} (HW{m['category']} {m['code']})",
            f"报告中含有疑似危险废物物质匹配到数据库记录。产生源: {m['source_waste']}",
            law_ref=f"《国家危险废物名录（2021年版）》HW{m['category']} 类",
            suggestion=f"请确认是否产生{m['name']}（HW{m['category']} {m['code']}），如确属危废应补充废物体积/重量估算、暂存方式及处置去向。"
        ))

    for item in llm_issues:
        issues.append(build_issue(
            "R-HW-001", item.get("severity", "P1"), "危废核查",
            item["title"], item.get("finding", ""),
            suggestion="建议对照《国家危险废物名录（2021年版）》核实。"
        ))

    return issues
