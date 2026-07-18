import re
from datetime import datetime
from app.engine.extractor import extract_text
from app.engine.grader import grade_issues, build_issue
from app.engine.rules_engine import load_rules, run_keyword_check, run_cross_reference_check, run_llm_check

STANDARD_PATTERN = re.compile(r"(?:GB|GB/T|HJ|HJ/T|环发|环办|国环规)\s*[\d.\-—]+(?:\s*[—\-]\s*\d{4})?")


async def run_audit_pipeline(project_id: str):
    from app.database import async_session
    from app.models.project import Project

    async with async_session() as db:
        from sqlalchemy import select
        result = await db.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()
        if not project:
            return

    def log(msg: str, t: str = "info"):
        return {"time": datetime.now().strftime("%H:%M:%S"), "message": msg, "type": t}

    async def update_progress(pct: float, step: str, msg: str, log_type: str = "step"):
        from sqlalchemy import select as _select
        async with async_session() as db:
            result = await db.execute(_select(Project).where(Project.id == project_id))
            p = result.scalar_one_or_none()
            if p:
                p.progress = pct
                p.step = step
                p.logs = (p.logs or []) + [log(msg, log_type)]
                await db.commit()

    try:
        await update_progress(5, "1 提取文本", "开始提取报告文本...")
        text_data = extract_text(project.file_path)
        full_text = text_data.get("full_text", "")
        chapters = text_data.get("chapters", [])
        await update_progress(15, "1 提取文本", f"文本提取完成，{len(full_text)} 字符", "success")

        report_type = "报告表" if "报告表" in full_text[:2000] else "报告书"
        rules = load_rules(project.audit_domain or "eia", report_type)
        all_issues = []

        from app.knowledge.retriever import search_knowledge
        from app.engine.standards_index import exact_standard_lookup

        standards_found = list(set(STANDARD_PATTERN.findall(full_text)))[:20]
        standards_kb = search_knowledge(" ".join(standards_found[:5]) if standards_found else "标准引用 废止 更新", top_k=10)

        # ═══ 11步审核管线 ═══
        STEPS = [
            ("1 符合性检查", "环评级别/报告类型/规划符合性", ["R-CLS-", "R-STRUCT-"]),
            ("2 语言文字+标准引用", "字词/语法/术语规范、标准编号有效性", ["R-STD-"]),
            ("3 敏感目标+环境数据", "周边环境敏感点和环境质量数据", []),
            ("4 计算问题检查", "核验报告数值计算", []),
            ("5 源强结果校核", "源强核算方法和结果准确性", ["R-SRC-"]),
            ("6 排放标准限值", "排放标准限值引用和适用性", []),
            ("7 产污系数核对", "产排污系数手册核对", []),
            ("8 危废代码核查", "危废代码归类准确性", []),
            ("9 可行技术检查", "污染治理技术可行性", ["R-MSR-"]),
            ("10 图文一致性", "报告图文矛盾检查", []),
            ("11 内容自洽性", "关键信息跨章节一致性", ["R-EVL-"]),
        ]

        for idx, (step_name, step_desc, step_prefixes) in enumerate(STEPS):
            pct = 20 + idx * 55 // len(STEPS)
            await update_progress(pct, step_name, step_desc, "step")
            step_rules = [r for r in rules if any(r.get("rule_id","").startswith(p) for p in step_prefixes)]
            if not step_rules and not step_prefixes:
                await update_progress(pct + 3, step_name, f"{step_desc}（暂未深度实现，跳过）", "step")
                continue
            step_issues = []
            for rule in step_rules:
                ct = rule.get("check_type", "keyword_match")
                issues = []
                if ct == "keyword_match":
                    issues = run_keyword_check(rule, full_text)
                elif ct == "cross_reference":
                    issues = run_cross_reference_check(rule, full_text, standards_kb)
                elif ct == "llm_judge":
                    issues = await run_llm_check(rule, full_text, standards_kb)
                for iss in issues:
                    iss["rule_id"] = rule.get("rule_id", iss.get("rule_id", "R-UNK"))
                    iss["step"] = step_name
                step_issues.extend(issues)
            all_issues.extend(step_issues)
            msg = f"{step_name}: 发现 {len(step_issues)} 个问题" if step_issues else f"{step_name}: 通过"
            await update_progress(pct + 4, step_name, msg, "success" if not step_issues else "step")

        await update_progress(77, "综合审查", "AI Agent 综合审查所有发现...", "step")
        if all_issues:
            p0c = len([i for i in all_issues if i.get('severity') == 'P0'])
            p1c = len([i for i in all_issues if i.get('severity') == 'P1'])
            p2c = len([i for i in all_issues if i.get('severity') == 'P2'])
            from app.engine.rules_engine import run_llm_check as _agent
            try:
                agent_rule = {
                    "rule_id": "R-AGENT-REVIEW", "category": "综合审查",
                    "title": "AI Agent 综合审查与质量评级",
                    "law_ref": "HJ 2.1-2016",
                    "check_config": {"description":
                        f"已审核完毕，发现 {len(all_issues)} 个问题（P0:{p0c} P1:{p1c} P2:{p2c}）。"
                        f"请：1.检查矛盾问题 2.合并同类项 3.给A/B/C/D质量评级+一句话总结 4.列Top3优先整改\n"
                        + "\n".join([f"- [{iss.get('severity','?')}] {iss.get('title','')}" for iss in all_issues[:25]])
                    }
                }
                review = await _agent(agent_rule, full_text, standards_kb)
                if review:
                    for iss in review: iss["rule_id"] = "R-AGENT-REVIEW"; iss["category"] = "综合审查"
                    all_issues.extend(review)
            except Exception: pass
            await update_progress(80, "综合审查完成", "AI Agent 综合审查完成", "step")

        await update_progress(85, "生成报告", "开始生成审核报告...")
        graded = grade_issues(all_issues)
        unique_standards = standards_found
        report_html = _generate_report(project.name, graded, full_text, unique_standards)
        from app.config import UPLOAD_DIR
        report_path = UPLOAD_DIR / f"report_{project_id}.html"
        report_path.write_text(report_html, encoding="utf-8")

        async with async_session() as db:
            result = await db.execute(select(Project).where(Project.id == project_id))
            p = result.scalar_one_or_none()
            if p:
                p.status = "completed"; p.progress = 100; p.step = "审核完成"
                p.issues = {"P0": len(graded.get("P0",[])), "P1": len(graded.get("P1",[])), "P2": len(graded.get("P2",[]))}
                p.report_path = str(report_path)
                p.logs = (p.logs or []) + [log(f"审核完成，{len(all_issues)} 问题(P0:{len(graded.get('P0',[]))} P1:{len(graded.get('P1',[]))} P2:{len(graded.get('P2',[]))})", "success")]
                await db.commit()

    except Exception as e:
        async with async_session() as db:
            result = await db.execute(select(Project).where(Project.id == project_id))
            p = result.scalar_one_or_none()
            if p:
                p.status = "failed"; p.step = f"失败: {str(e)[:100]}"
                p.logs = (p.logs or []) + [log(str(e), "error")]
                await db.commit()


def _generate_report(project_name: str, graded: dict, full_text: str, standards: list[str]) -> str:
    p0 = graded.get("P0", [])
    p1 = graded.get("P1", [])
    p2 = graded.get("P2", [])

    issues_html = ""
    for severity, issues, color_class in [("P0", p0, "p0"), ("P1", p1, "p1"), ("P2", p2, "p2")]:
        if not issues: continue
        issues_html += f'<h3 class="{color_class}">{severity} 严重问题 ({len(issues)}项)</h3>'
        for i, iss in enumerate(issues, 1):
            loc = iss.get("evidence_location", "")
            if loc:
                evidence_html = f'<div class="issue-highlight"><strong>📝 报告原文定位：</strong><span class="highlight-text">"{loc}"</span></div>'
            elif iss.get("evidence"):
                evidence_html = f'<div class="issue-evidence"><strong>报告原文：</strong>{iss["evidence"]}</div>'
            else:
                evidence_html = ""
            reasoning_html = f'<div class="issue-reasoning"><strong>AI推理过程：</strong><p>{iss["reasoning"]}</p></div>' if iss.get("reasoning") else ""
            law_html = f'<div class="issue-law"><strong>引用法规：</strong>{iss["law_ref"]}</div>' if iss.get("law_ref") else ""
            step_html = f'<div class="issue-step" style="color:var(--muted);font-size:11px;margin-top:4px;">审核步骤: {iss.get("step","")}</div>' if iss.get("step") else ""
            issues_html += f"""
            <div class="issue-item">
                <div class="issue-header">{i}. {iss['title']}</div>
                <div class="issue-finding"><strong>发现：</strong>{iss['finding']}</div>
                {evidence_html}
                {reasoning_html}
                {law_html}
                <div class="issue-suggestion"><strong>建议：</strong>{iss['suggestion']}</div>
                {step_html}
            </div>"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><title>审核报告 - {project_name}</title>
<style>
body{{font-family:"PingFang SC","Microsoft YaHei",sans-serif;max-width:900px;margin:0 auto;padding:40px 20px;color:#1a2733;background:#f5f8fc}}
h1{{border-bottom:3px solid #4fc3f7;padding-bottom:12px}}
.step-summary{{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:16px 0;font-size:12px}}
.step-badge{{padding:6px 8px;border-radius:6px;text-align:center;border:1px solid #e2e8f0;background:#fff}}
.step-badge.fail{{border-color:#fecaca;background:#fef2f2;color:#dc2626}}
.step-badge.pass{{border-color:#bbf7d0;background:#f0fdf4;color:#16a34a}}
.step-badge.skip{{border-color:#e2e8f0;background:#f8fafc;color:#94a3b8}}
.summary{{display:flex;gap:20px;margin:24px 0}}
.summary-card{{flex:1;padding:20px;border-radius:8px;text-align:center}}
.summary-card.p0{{background:#fef2f2;border:1px solid #fecaca}}
.summary-card.p1{{background:#fffbeb;border:1px solid #fde68a}}
.summary-card.p2{{background:#eff6ff;border:1px solid #bfdbfe}}
.summary-card b{{display:block;font-size:36px}}
.summary-card.p0 b{{color:#ef4444}}.summary-card.p1 b{{color:#f59e0b}}.summary-card.p2 b{{color:#3b82f6}}
.issue-item{{border:1px solid #e2e8f0;border-radius:8px;padding:16px;margin:12px 0;background:#fff}}
.issue-header{{font-weight:700;font-size:16px;color:#0f172a;margin-bottom:8px}}
.issue-finding,.issue-evidence,.issue-law,.issue-suggestion{{margin:6px 0;line-height:1.7}}
.issue-evidence{{background:#f8fafc;padding:8px 10px;border-left:3px solid #94a3b8;font-style:italic;color:#475569}}
.issue-highlight{{background:#fffbeb;padding:10px 12px;border-left:3px solid #f59e0b;margin:8px 0}}
.issue-highlight .highlight-text{{background:#fef08a;padding:2px 4px;border-radius:3px;font-style:normal;color:#92400e;font-weight:600}}
.issue-reasoning{{background:#f0fdf4;padding:10px 12px;border-left:3px solid #10b981;margin:8px 0;font-size:13px;line-height:1.8;color:#475569}}
.issue-reasoning p{{margin:4px 0}}.issue-suggestion{{color:#059669}}
h3.p0{{color:#ef4444;border-left:4px solid #ef4444;padding-left:12px}}
h3.p1{{color:#f59e0b;border-left:4px solid #f59e0b;padding-left:12px}}
h3.p2{{color:#3b82f6;border-left:4px solid #3b82f6;padding-left:12px}}
.standards{{margin-top:24px;padding:16px;background:#f8fafc;border-radius:8px;font-size:13px;color:#64748b}}
.meta{{color:#94a3b8;font-size:13px;margin-top:8px}}
</style></head><body>
<h1>AI 环评智能审核报告</h1>
<div class="meta">项目名称：{project_name}</div>
<div class="meta">审核时间：{datetime.now().strftime("%Y-%m-%d %H:%M")}</div>
<div class="meta">审核引擎：恒新环保智能系统 v3.2（11步审核管线 + 原文定位 + AI Agent）</div>
<div class="summary"><div class="summary-card p0"><b>{len(p0)}</b>P0 严重问题</div><div class="summary-card p1"><b>{len(p1)}</b>P1 一般问题</div><div class="summary-card p2"><b>{len(p2)}</b>P2 建议优化</div></div>
{issues_html or '<p style="text-align:center;color:#059669;font-size:18px;padding:40px;">未发现明显问题</p>'}
<div class="standards"><strong>报告中引用的标准：</strong>{', '.join(standards[:20]) if standards else '未识别到标准编号'}<br><br><strong>免责声明：</strong>本审核报告由 AI 自动生成，仅供参考。最终审核结论应以具有相应审批权限的生态环境主管部门意见为准。</div>
</body></html>"""
