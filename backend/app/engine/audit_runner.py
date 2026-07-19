"""审核执行核心 — 11步/7阶段/5步管线，供审核工单与生成自审核双路复用"""
import asyncio
import re

from app.engine.grader import build_issue
from app.engine.context import build_step_context
from app.engine.llm_json import parse_llm_json
from app.engine.rules_engine import (load_rules, run_keyword_check,
                                     run_cross_reference_check, run_llm_check)

STANDARD_PATTERN = re.compile(r"(?:GB|GB/T|HJ|HJ/T|DB\s*\d*/|环发|环办|国环规)\s*[\d.\-—]+(?:\s*[—\-]\s*\d{4})?")


async def execute_audit_steps(text_data: dict, audit_ctx: dict, log_cb,
                              file_path: str = "", check_stop_cb=None) -> dict:
    """审核核心管线。
    text_data: extractor 输出；audit_ctx: {domain, report_type, industry, ...}
    log_cb: async callable(pct|None, step, msg, log_type)
    check_stop_cb: async callable(step_name) -> bool（可选，停止检查）
    返回 {issues, step_statuses, review_summary, standards_found, stopped}
    """
    from app.engine.kfiles import build_kfiles_context

    domain = audit_ctx.get("domain", "eia")
    report_type = audit_ctx.get("report_type", "报告书")
    rules = load_rules(domain, report_type)
    full_text = text_data.get("full_text", "")
    all_issues = []
    step_statuses = []

    from app.knowledge.retriever import search_knowledge
    standards_found = list(set(STANDARD_PATTERN.findall(full_text)))[:20]
    try:
        standards_kb = []
        _uniq_std = list(dict.fromkeys(standards_found))[:25]
        for _i in range(0, len(_uniq_std), 5):
            standards_kb.extend(search_knowledge(" ".join(_uniq_std[_i:_i + 5]), top_k=10) or [])
        if not standards_kb:
            await log_cb(None, "0 报告识别", "知识库检索为空（Ollama 未启动或索引为空），标准核查将仅使用精确索引", "step")
    except Exception as e:
        standards_kb = []
        await log_cb(None, "0 报告识别", f"知识库不可用（{str(e)[:60]}），标准核查降级为精确索引", "step")

    async def run_step(step_name, check_fn, rule_prefixes=None):
        step_rules = [r for r in rules if any(r.get("rule_id", "").startswith(p) for p in (rule_prefixes or []))] if rule_prefixes else []

        async def _run_rule(rule):
            ct = rule.get("check_type", "keyword_match")
            if ct == "keyword_match":
                iss = run_keyword_check(rule, full_text, chapters=text_data.get("chapters", []))
            elif ct == "cross_reference":
                iss = run_cross_reference_check(rule, full_text, standards_kb)
            elif ct == "llm_judge":
                rule_kids = rule.get("kfiles", [])
                kfiles_ctx = build_kfiles_context(rule_kids, max_chars=6000) if rule_kids else ""
                if rule.get("per_chapter"):
                    from app.engine.rules_engine import run_llm_check_per_chapter
                    iss = await run_llm_check_per_chapter(rule, text_data, standards_kb,
                                                          kfiles_ctx=kfiles_ctx)
                else:
                    ctx_text = build_step_context(text_data, rule.get("target_chapters"))
                    iss = await run_llm_check(rule, full_text, standards_kb,
                                              context_text=ctx_text, kfiles_ctx=kfiles_ctx)
            else:
                iss = []
            for it in iss:
                it["rule_id"] = rule.get("rule_id", it.get("rule_id", "R-UNK"))
                it["step"] = step_name
            return iss

        tasks = [asyncio.create_task(_run_rule(r)) for r in step_rules]
        if check_fn:
            async def _run_fn():
                from app.engine.steps.figures import check_text_figure_consistency
                if check_fn is check_text_figure_consistency:
                    return await check_fn(text_data, file_path)
                return await check_fn(text_data, audit_ctx)
            tasks.append(asyncio.create_task(_run_fn()))

        step_issues = []
        partial_errors = []
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in results:
            if isinstance(res, Exception):
                partial_errors.append(res)
            else:
                for it in res:
                    it.setdefault("step", step_name)
                step_issues.extend(res)

        if partial_errors and not step_issues and (step_rules or check_fn):
            raise RuntimeError("；".join(str(e)[:60] for e in partial_errors))
        return step_issues, len(partial_errors)

    from app.engine.steps.sensitive import check_sensitive_targets
    from app.engine.steps.calculation import check_calculations
    from app.engine.steps.limits import check_emission_limits
    from app.engine.steps.coefficients import check_emission_factors
    from app.engine.steps.hazardous import check_hazardous_waste
    from app.engine.steps.figures import check_text_figure_consistency
    from app.engine.steps.recalculate import check_source_recalculation
    from app.engine.steps.acceptance_pkg import check_acceptance_package
    from app.engine.steps.emergency_pkg import check_emergency_package
    from app.engine.steps.crosscheck import check_cross_tables
    from app.engine.steps.terminology import check_terminology

    EIA_STEPS = [
        ("1 符合性检查", ["R-CLS-", "R-STRUCT-", "R-SPEC-", "R-LAW-"], check_terminology),
        ("2 标准引用", ["R-STD-"], None),
        ("3 敏感目标+环境数据", [], check_sensitive_targets),
        ("4 计算问题检查", ["R-MON-"], check_calculations),
        ("5 源强结果校核", ["R-SRC-", "R-TOT-"], check_source_recalculation),
        ("6 排放标准限值", ["R-PRD-"], check_emission_limits),
        ("7 产污系数核对", [], check_emission_factors),
        ("8 危废代码核查", ["R-HW-"], check_hazardous_waste),
        ("9 可行技术检查", ["R-MSR-"], None),
        ("10 图文一致性", ["R-ATT-"], check_text_figure_consistency),
        ("11 内容自洽性", ["R-EVL-", "R-CONS-", "R-PUB-"], None),
        ("12 交叉数据核验", [], check_cross_tables),
    ]
    ACCEPTANCE_STEPS = [
        ("1 适用性识别", ["R-ACC-FIT-"], check_terminology),
        ("2 资料完整性", [], check_acceptance_package),
        ("3 手续与责任主体", ["R-ACC-001", "R-ACC-PROC-"], None),
        ("4 验收自查", ["R-ACC-004", "R-ACC-SELF-"], None),
        ("5 监测/调查技术", ["R-ACC-002", "R-ACC-003", "R-ACC-006", "R-ACC-MON-"], check_calculations),
        ("6 后续验收", ["R-ACC-005", "R-ACC-FUP-"], None),
        ("7 不得通过情形", ["R-ACC-VETO-"], None),
        ("8 危废与固废核查", ["R-HW-"], check_hazardous_waste),
        ("9 交叉数据核验", [], check_cross_tables),
    ]
    EMERGENCY_STEPS = [
        ("1 资料包完整性", [], check_emergency_package),
        ("2 总则与编制说明", ["R-EMG-001", "R-EMG-004", "R-EMG-006", "R-EMG-008"], None),
        ("3 风险识别与评估", ["R-EMG-002", "R-EMG-003"], None),
        ("4 应急响应与处置", ["R-EMG-005"], None),
        ("5 预案管理与修订", ["R-EMG-007"], None),
    ]
    STEPS = {"eia": EIA_STEPS, "acceptance": ACCEPTANCE_STEPS,
             "emergency": EMERGENCY_STEPS}.get(domain, EIA_STEPS)

    stopped = False
    for idx, (step_name, rule_prefixes, extra_fn) in enumerate(STEPS):
        if check_stop_cb and await check_stop_cb(step_name):
            stopped = True
            break
        pct = 20 + idx * 55 // len(STEPS)
        await log_cb(pct, step_name, f"开始{step_name}...", "step")
        try:
            step_issues, partial_errs = await run_step(step_name, extra_fn, rule_prefixes)
        except Exception as e:
            step_statuses.append({"name": step_name, "status": "error", "count": 0})
            await log_cb(pct + 4, step_name, f"{step_name}: 检查失败（{str(e)[:80]}），需人工复核", "error")
            continue
        all_issues.extend(step_issues)
        status = "fail" if step_issues else "pass"
        step_statuses.append({"name": step_name, "status": status, "count": len(step_issues)})
        msg = f"{step_name}: 发现 {len(step_issues)} 个问题" if step_issues else f"{step_name}: 通过"
        if partial_errs:
            msg += f"（{partial_errs} 项检查部分失败）"
        await log_cb(pct + 4, step_name, msg, "success" if not step_issues else "step")

    if stopped:
        return {"issues": [], "step_statuses": step_statuses, "review_summary": {},
                "standards_found": standards_found, "stopped": True}

    # ── 综合审查与质量评级 ──
    review_summary = {}
    await log_cb(77, "综合审查", "AI Agent 综合审查与质量评级...", "step")
    if all_issues:
        p0c = len([i for i in all_issues if i.get('severity') == 'P0'])
        p1c = len([i for i in all_issues if i.get('severity') == 'P1'])
        p2c = len([i for i in all_issues if i.get('severity') == 'P2'])
        from app.llm.client import chat as _chat, get_active_profile as _get_profile
        try:
            profile = await _get_profile()
            if profile:
                review_prompt = f"""你是环评审核专家组组长。已完成的自动审核发现 {len(all_issues)} 个问题（P0:{p0c} P1:{p1c} P2:{p2c}）。
问题清单：
{chr(10).join([f"- [{iss.get('severity','?')}] {iss.get('title','')}" for iss in all_issues[:25]])}

请输出JSON（不要带```标记）：
{{"grade":"A/B/C/D","summary":"一句话总体评价（30字内）","top3":["优先整改项1","优先整改项2","优先整改项3"],
"contradictions":"问题之间的矛盾点（无则空串）","extra_issues":[{{"severity":"P0/P1/P2","title":"...","finding":"...","suggestion":"..."}}]}}

评级标准：A=无P0且P1≤2（质量好）；B=无P0但P1较多（基本合格）；C=有P0但≤3个（需整改）；D=P0>3或存在否决性问题（质量差）。
extra_issues 只填审核清单中遗漏的新重大问题，无则 []。只输出JSON。"""
                resp = await _chat(review_prompt, profile=profile)
                review = parse_llm_json(resp, expect="object") or {}
                review_summary = {
                    "grade": review.get("grade", ""),
                    "summary": review.get("summary", ""),
                    "top3": review.get("top3", []),
                    "contradictions": review.get("contradictions", ""),
                }
                extras = review.get("extra_issues") or []
                for iss in extras:
                    if isinstance(iss, dict) and iss.get("title"):
                        all_issues.append(build_issue(
                            "R-AGENT-REVIEW", iss.get("severity", "P1"), "综合审查",
                            iss["title"], iss.get("finding", ""),
                            law_ref="HJ 2.1-2016",
                            suggestion=iss.get("suggestion", ""),
                        ))
                        all_issues[-1]["step"] = "综合审查"
                step_statuses.append({"name": "综合审查", "status": "pass", "count": len(extras)})
        except Exception as e:
            step_statuses.append({"name": "综合审查", "status": "error", "count": 0})
            await log_cb(80, "综合审查", f"综合审查失败（{str(e)[:80]}），不影响已发现问题", "error")
        await log_cb(80, "综合审查完成", "AI Agent 综合审查完成", "step")

    # ── 去重：同规则同标题只保留一条 ──
    seen_keys = set()
    deduped = []
    for iss in all_issues:
        key = (iss.get("rule_id", ""), iss.get("title", ""))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(iss)
    all_issues = deduped

    # ── 章节定位 ──
    chapters = text_data.get("chapters", [])
    for iss in all_issues:
        loc = iss.get("evidence_location") or iss.get("evidence") or ""
        if loc and len(loc) >= 8 and not iss.get("chapter"):
            snippet = loc[:50]
            for ch in chapters:
                if snippet in ch.get("content", ""):
                    iss["chapter"] = ch.get("title", "")
                    break

    return {"issues": all_issues, "step_statuses": step_statuses,
            "review_summary": review_summary, "standards_found": standards_found,
            "stopped": False}
