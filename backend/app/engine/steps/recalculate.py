import json
import re
from app.llm.client import chat, get_active_profile
from app.engine.grader import build_issue
from app.engine.coefficient_db import query_coefficient


async def check_source_recalculation(full_text: str) -> list[dict]:
    """提取参数→数据库查系数→反算验证→对比"""
    issues = []
    profile = await get_active_profile()
    if not profile:
        return issues

    has_boiler = any(kw in full_text[:3000] for kw in ["锅炉", "燃烧", "煤", "生物质", "天然气"])
    if not has_boiler:
        return issues

    # Step 1: LLM 提取报告中的参数
    prompt = f"""从以下报告提取锅炉源强参数，输出JSON：
{{
  "fuel_type":"煤/生物质/天然气/油/无",
  "params":[
    {{"symbol":"B","value":2000,"unit":"t/a","label":"耗煤量"}},
    {{"symbol":"S","value":0.8,"unit":"%","label":"硫分"}},
    {{"symbol":"A","value":15,"unit":"%","label":"灰分"}},
    {{"symbol":"dfh","value":15,"unit":"%","label":"飞灰比"}},
    {{"symbol":"eta_SO2","value":90,"unit":"%","label":"脱硫效率"}},
    {{"symbol":"eta_PM","value":99.5,"unit":"%","label":"除尘效率"}},
    {{"symbol":"eta_NOx","value":40,"unit":"%","label":"脱硝效率"}}
  ],
  "reported":[
    {{"name":"SO₂排放量","value":15.0,"unit":"t/a"}},
    {{"name":"颗粒物排放量","value":3.0,"unit":"t/a"}}
  ]
}}
找不到填-1。{full_text[:5000]}"""

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

    p = {}
    for param in data.get("params", []):
        v = param.get("value", -1)
        if v > 0:
            p[param.get("symbol", "")] = v

    B = p.get("B", 0)
    if not B:
        return issues

    # Step 2: 从数据库查系数
    db_coeffs = await query_coefficient(fuel_type=ft)
    db_so2 = [c for c in db_coeffs if c["pollutant"] == "SO₂"]
    db_pm = [c for c in db_coeffs if c["pollutant"] == "颗粒物"]
    db_nox = [c for c in db_coeffs if c["pollutant"] == "NOx"]

    # Step 3: 优先用报告参数反算，参数缺的用数据库系数
    calc_results = {}
    calc_details = []

    S = p.get("S", 0)
    if not S and db_so2:
        S = db_so2[0]["coefficient"] / 2  # SO₂=2BS → 系数≈16时 S≈0.8%
    if S > 0:
        eta_SO2 = p.get("eta_SO2", 0)
        so2 = 2 * B * S / 100 * (1 - eta_SO2 / 100)
        calc_results["SO₂"] = so2
        calc_details.append(f"SO₂={so2:.3f} (B={B}, S={S}%, η={eta_SO2}%)")

    A = p.get("A", 0)
    if not A and db_pm:
        A = db_pm[0]["coefficient"] * 10  # 系数≈8时 A≈8%(含dfh)
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

    if not calc_results:
        return issues

    # Step 4: 对比报告值
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
