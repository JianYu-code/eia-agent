import re
from app.engine.context import build_step_context
from app.engine.llm_json import parse_llm_json
from app.llm.client import chat, get_active_profile
from app.engine.grader import build_issue
from app.engine.coefficient_db import query_coefficient

TARGET_CHAPTERS = ["工程分析", "源强", "锅炉", "燃烧", "污染", "排放"]

APPLICABLE_DOMAINS = {"eia", "acceptance"}


async def check_source_recalculation(text_data: dict, audit_ctx: dict | None = None) -> list[dict]:
    """提取参数→数据库查系数→反算验证→对比（仅环评/验收域）"""
    issues = []
    if audit_ctx and audit_ctx.get("domain", "eia") not in APPLICABLE_DOMAINS:
        return issues
    profile = await get_active_profile()
    if not profile:
        raise RuntimeError("未配置启用的 LLM Profile")

    full_text = text_data.get("full_text", "")
    industry = (audit_ctx or {}).get("industry", "")
    context = build_step_context(text_data, TARGET_CHAPTERS)

    has_boiler = any(kw in full_text[:5000] for kw in ["锅炉", "燃烧", "耗煤", "生物质", "天然气"]) \
        or any(kw in industry for kw in ["锅炉", "热电", "火电"])
    has_wastewater = any(kw in full_text for kw in ["废水", "污水", "COD", "氨氮", "排水量"])
    if not has_boiler and not has_wastewater:
        return issues

    prompt = f"""从以下报告内容提取源强核算参数，输出JSON：
{{
  "fuel_type":"煤/生物质/天然气/油/无",
  "params":[
    {{"symbol":"B","value":2000,"unit":"t/a","label":"耗煤量"}},
    {{"symbol":"S","value":0.8,"unit":"%","label":"硫分"}},
    {{"symbol":"A","value":15,"unit":"%","label":"灰分"}},
    {{"symbol":"dfh","value":15,"unit":"%","label":"飞灰比"}},
    {{"symbol":"eta_SO2","value":90,"unit":"%","label":"脱硫效率"}},
    {{"symbol":"eta_PM","value":99.5,"unit":"%","label":"除尘效率"}},
    {{"symbol":"eta_NOx","value":40,"unit":"%","label":"脱硝效率"}},
    {{"symbol":"Q_water","value":12000,"unit":"m³/a","label":"废水排放量"}},
    {{"symbol":"C_COD","value":300,"unit":"mg/L","label":"COD排放浓度"}},
    {{"symbol":"C_NH3N","value":25,"unit":"mg/L","label":"氨氮排放浓度"}}
  ],
  "reported":[
    {{"name":"SO₂排放量","value":15.0,"unit":"t/a"}},
    {{"name":"颗粒物排放量","value":3.0,"unit":"t/a"}},
    {{"name":"COD排放量","value":3.6,"unit":"t/a"}},
    {{"name":"氨氮排放量","value":0.3,"unit":"t/a"}}
  ]
}}
找不到填-1，无锅炉时 fuel_type 填"无"。

{context}"""

    resp = await chat(prompt, profile=profile)
    data = parse_llm_json(resp, expect="object") or {}

    p = {}
    for param in data.get("params", []):
        v = param.get("value", -1)
        if v > 0:
            p[param.get("symbol", "")] = v

    calc_results = {}
    calc_details = []

    ft = data.get("fuel_type", "无")
    B = p.get("B", 0)
    if ft != "无" and B:
        db_coeffs = await query_coefficient(fuel_type=ft)
        db_so2 = [c for c in db_coeffs if c["pollutant"] == "SO₂"]
        db_pm = [c for c in db_coeffs if c["pollutant"] == "颗粒物"]
        db_nox = [c for c in db_coeffs if c["pollutant"] == "NOx"]

        S = p.get("S", 0)
        if not S and db_so2:
            S = db_so2[0]["coefficient"] / 2
        if S > 0:
            eta_SO2 = p.get("eta_SO2", 0)
            so2 = 2 * B * S / 100 * (1 - eta_SO2 / 100)
            calc_results["SO₂"] = so2
            calc_details.append(f"SO₂={so2:.3f} (B={B}, S={S}%, η={eta_SO2}%)")

        A = p.get("A", 0)
        if not A and db_pm:
            A = db_pm[0]["coefficient"] * 10
        if A > 0:
            dfh = p.get("dfh", 15)
            Cfh = p.get("Cfh", 15)
            eta_PM = p.get("eta_PM", 0)
            pm = B * A / 100 * dfh / 100 * (1 - eta_PM / 100) / (1 - Cfh / 100)
            calc_results["颗粒物"] = pm
            calc_details.append(f"颗粒物={pm:.3f} (B={B}, A={A}%, dfh={dfh}%, η={eta_PM}%)")

        eta_NOx = p.get("eta_NOx", 0)
        nox_coeffs = [c for c in db_nox if "低氮" not in c.get("notes", "")] or db_nox
        K_NOx = p.get("K_NOx", 0) or (nox_coeffs[0]["coefficient"] if nox_coeffs else 3.5)
        if K_NOx > 0:
            nox = B * K_NOx * 0.001 * (1 - eta_NOx / 100)
            calc_results["NOx"] = nox
            calc_details.append(f"NOx={nox:.3f} (B={B}, K={K_NOx}kg/t, η={eta_NOx}%)")

    # ── 废水线：浓度×水量反算 ──
    Q = p.get("Q_water", 0)
    if Q > 0:
        for sym, name in (("C_COD", "COD"), ("C_NH3N", "氨氮")):
            C = p.get(sym, 0)
            if C > 0:
                val = Q * C * 1e-6
                calc_results[name] = val
                calc_details.append(f"{name}={val:.3f} t/a (Q={Q}m³/a × C={C}mg/L)")

    if not calc_results:
        return issues

    for rv in data.get("reported", []):
        name = rv.get("name", "")
        reported_val = rv.get("value", 0)
        if reported_val <= 0:
            continue
        for calc_name, calc_val in calc_results.items():
            if calc_name in name:
                if calc_val > 0.001:
                    deviation = abs(reported_val - calc_val) / calc_val * 100
                    if deviation > 20:
                        issues.append(build_issue(
                            "R-RECALC-001", "P1", "源强重算",
                            f"{name}数据库反算偏差{deviation:.0f}%",
                            f"报告值: {reported_val:.3f} t/a, 反算值: {calc_val:.3f} t/a (参数: {'; '.join(calc_details)})",
                            law_ref=f"HJ 953/884 物料衡算 + 产污系数查表",
                            suggestion=f"请复核{name}。若报告参数无误，反算结果应为{calc_val:.3f} t/a，与报告值偏差{deviation:.0f}%。请核实消耗量、原料成分或治理效率数据。"
                        ))
                break

    return issues
