import json
import re
from app.llm.client import chat, get_active_profile
from app.engine.grader import build_issue


async def check_source_recalculation(full_text: str) -> list[dict]:
    """提取报告中的参数，用标准公式反算源强，对比报告声称值，标注偏差"""
    issues = []
    profile = await get_active_profile()
    if not profile:
        return issues

    # 先提取基本判断：有没有锅炉信息
    has_boiler = any(kw in full_text[:3000] for kw in ["锅炉", "燃烧", "煤", "生物质", "天然气"])
    if not has_boiler:
        return issues

    prompt = f"""你是环评源强核算专家。请从以下报告内容中提取可以进行源强反算的参数。

报告内容（摘要）：
{full_text[:6000]}

请找出报告中关于锅炉/工艺废气源强的关键参数，按JSON格式输出：
{{
  "fuel_type": "煤/生物质/天然气/油/无",
  "found_params": [
    {{"name":"耗煤量","symbol":"B","value":2000,"unit":"t/a","text":"原文片段"}},
    {{"name":"硫分","symbol":"S","value":0.8,"unit":"%","text":"原文片段"}},
    {{"name":"灰分","symbol":"A","value":15,"unit":"%","text":"原文片段"}},
    {{"name":"飞灰比","symbol":"dfh","value":15,"unit":"%","text":"原文片段"}},
    {{"name":"飞灰含碳量","symbol":"Cfh","value":15,"unit":"%","text":"原文片段"}}
  ],
  "reported_values": [
    {{"name":"SO₂排放量","value":15.0,"unit":"t/a","text":"原文片段"}},
    {{"name":"颗粒物排放量","value":3.0,"unit":"t/a","text":"原文片段"}},
    {{"name":"NOx排放量","value":4.5,"unit":"t/a","text":"原文片段"}}
  ],
  "removal_rates": [
    {{"name":"脱硫效率","symbol":"eta_SO2","value":90,"unit":"%"}},
    {{"name":"除尘效率","symbol":"eta_PM","value":99.5,"unit":"%"}},
    {{"name":"脱硝效率","symbol":"eta_NOx","value":40,"unit":"%"}}
  ]
}}

如果报告中未给出某参数，该字段 value 填 -1。
如果报告中完全没有可提取的源强数据，输出 {{"fuel_type":"无","found_params":[]}}。
只输出JSON，不要其他内容。"""

    try:
        resp = await chat(prompt, profile=profile)
        resp = resp.strip()
        if resp.startswith("```"): resp = resp.split("```")[1]
        data = json.loads(resp)
    except Exception:
        return issues

    ft = data.get("fuel_type", "无")
    if ft == "无":
        return issues

    params = data.get("found_params", [])
    if len(params) < 2:
        return issues

    p = {}
    for param in params:
        v = param.get("value", -1)
        if v > 0:
            p[param.get("symbol", "")] = v

    rr = {}
    for r in data.get("removal_rates", []):
        v = r.get("value", -1)
        if v >= 0:
            rr[r.get("symbol", "")] = v

    # 反算公式
    calc_results = {}
    B = p.get("B", 0)
    S = p.get("S", 0)
    A = p.get("A", 0)
    dfh = p.get("dfh", 15)
    Cfh = p.get("Cfh", 15)
    eta_SO2 = rr.get("eta_SO2", 0)
    eta_PM = rr.get("eta_PM", 0)
    eta_NOx = rr.get("eta_NOx", 0)
    K_NOx = p.get("K_NOx", 0)

    if B > 0 and S > 0:
        calc_results["SO₂"] = 2 * B * S / 100 * (1 - eta_SO2 / 100)

    if B > 0 and A > 0:
        calc_results["颗粒物"] = B * A / 100 * dfh / 100 * (1 - eta_PM / 100) / (1 - Cfh / 100)

    if B > 0 and K_NOx > 0:
        calc_results["NOx"] = B * K_NOx * 0.001 * (1 - eta_NOx / 100)

    if not calc_results:
        return issues

    reported = data.get("reported_values", [])

    for rv in reported:
        name = rv.get("name", "")
        reported_val = rv.get("value", 0)
        if reported_val <= 0:
            continue

        for calc_name, calc_val in calc_results.items():
            if calc_name in name or name in calc_name:
                if calc_val > 0:
                    deviation = abs(reported_val - calc_val) / max(calc_val, 0.001) * 100
                    if deviation > 20:
                        issues.append(build_issue(
                            "R-RECALC-001", "P1", "源强重算",
                            f"{name}反算结果与报告值偏差 {deviation:.0f}%",
                            f"报告数值: {reported_val:.3f} t/a，反算结果: {calc_val:.3f} t/a",
                            evidence=rv.get("text", f"报告值{reported_val}"),

                            law_ref=f"物料衡算法: G = {'2×B×S×(1-η)' if 'SO₂' in name else 'B×A×dfh×(1-η)/(1-Cfh)' if '颗粒' in name else 'B×K×10⁻³×(1-η)'}",
                            suggestion=f"请复核{name}的源强计算。若反算参数无误，说明报告中的数值可能需要调整，或在报告中补充说明计算过程。",
                        ))
                    break

    return issues
