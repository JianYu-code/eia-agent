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

    async def update_progress(progress: float, step: str, msg: str, log_type: str = "step"):
        from sqlalchemy import select as _select
        async with async_session() as db:
            result = await db.execute(_select(Project).where(Project.id == project_id))
            p = result.scalar_one_or_none()
            if p:
                p.progress = progress
                p.step = step
                p.logs = (p.logs or []) + [log(msg, log_type)]
                await db.commit()

    try:
        await update_progress(5, "提取文本", "开始提取报告文本...")
        text_data = extract_text(project.file_path)
        full_text = text_data.get("full_text", "")
        chapters = text_data.get("chapters", [])
        await update_progress(15, "提取文本", f"文本提取完成，{len(full_text)} 字符", "success")

        report_type = "报告表" if "报告表" in full_text[:2000] else "报告书"
        rules = load_rules(project.audit_domain or "eia", report_type)
        all_issues = []

        await update_progress(20, "规则检查", f"识别为{report_type}，加载 {len(rules)} 条审核规则", "step")

        from app.knowledge.retriever import search_knowledge
        from app.engine.standards_index import exact_standard_lookup

        standards_found = list(set(STANDARD_PATTERN.findall(full_text)))[:20]
        unique_standards = standards_found

        standards_kb = search_knowledge(" ".join(unique_standards[:5]) if unique_standards else "标准引用 废止 更新", top_k=10)

        total_rules = len(rules)
        for idx, rule in enumerate(rules):
            check_type = rule.get("check_type", "keyword_match")

            if check_type == "keyword_match":
                issues = run_keyword_check(rule, full_text)
            elif check_type == "cross_reference":
                issues = run_cross_reference_check(rule, full_text, standards_kb)
            elif check_type == "llm_judge":
                issues = await run_llm_check(rule, full_text, standards_kb)
            else:
                issues = []

            if issues:
                for iss in issues:
                    iss["rule_id"] = rule.get("rule_id", iss.get("rule_id", "R-UNK"))
                all_issues.extend(issues)

            progress = 25 + int(55 * (idx + 1) / total_rules)
            if (idx + 1) % 3 == 0 or idx == total_rules - 1:
                await update_progress(progress, f"规则检查 ({idx+1}/{total_rules})", f"已完成 {idx+1}/{total_rules} 条，累计 {len(all_issues)} 个问题", "step")

        await update_progress(82, "规则检查完成", f"全部 {total_rules} 条规则检查完毕，发现 {len(all_issues)} 个问题", "step")

        await update_progress(88, "生成报告", "开始生成审核报告...")
        graded = grade_issues(all_issues)

        report_html = _generate_report(project.name, graded, full_text, unique_standards)
        from app.config import UPLOAD_DIR
        report_path = UPLOAD_DIR / f"report_{project_id}.html"
        report_path.write_text(report_html, encoding="utf-8")

        async with async_session() as db:
            result = await db.execute(select(Project).where(Project.id == project_id))
            p = result.scalar_one_or_none()
            if p:
                p.status = "completed"
                p.progress = 100
                p.step = "审核完成"
                p.issues = {
                    "P0": len(graded.get("P0", [])),
                    "P1": len(graded.get("P1", [])),
                    "P2": len(graded.get("P2", [])),
                }
                p.report_path = str(report_path)
                p.logs = (p.logs or []) + [log(f"审核完成，共发现 {len(all_issues)} 个问题（P0:{len(graded.get('P0',[]))} P1:{len(graded.get('P1',[]))} P2:{len(graded.get('P2',[]))}）", "success")]
                await db.commit()

    except Exception as e:
        async with async_session() as db:
            result = await db.execute(select(Project).where(Project.id == project_id))
            p = result.scalar_one_or_none()
            if p:
                p.status = "failed"
                p.step = f"审核失败: {str(e)[:100]}"
                p.logs = (p.logs or []) + [log(str(e), "error")]
                await db.commit()


def _generate_report(project_name: str, graded: dict, full_text: str, standards: list[str]) -> str:
    p0 = graded.get("P0", [])
    p1 = graded.get("P1", [])
    p2 = graded.get("P2", [])

    issues_html = ""
    for severity, issues, color_class in [("P0", p0, "p0"), ("P1", p1, "p1"), ("P2", p2, "p2")]:
        if not issues:
            continue
        issues_html += f'<h3 class="{color_class}">{severity} 严重问题 ({len(issues)}项)</h3>'
        for i, iss in enumerate(issues, 1):
            evidence_html = f'<div class="issue-evidence"><strong>报告原文：</strong>{iss["evidence"]}</div>' if iss.get("evidence") else ""
            issues_html += f"""
            <div class="issue-item">
                <div class="issue-header">{i}. {iss['title']}</div>
                <div class="issue-finding"><strong>发现：</strong>{iss['finding']}</div>
                {evidence_html}
                {f'<div class="issue-law"><strong>依据：</strong>{iss["law_ref"]}</div>' if iss.get('law_ref') else ''}
                <div class="issue-suggestion"><strong>建议：</strong>{iss['suggestion']}</div>
                {f'<div class="issue-rule" style="color:var(--muted);font-size:11px;margin-top:6px;">规则: {iss["rule_id"]}</div>' if iss.get('rule_id') else ''}
            </div>
            """

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>审核报告 - {project_name}</title>
<style>
body {{ font-family: "PingFang SC", "Microsoft YaHei", sans-serif; max-width: 900px; margin: 0 auto; padding: 40px 20px; color: #1a2733; background: #f5f8fc; }}
h1 {{ border-bottom: 3px solid #4fc3f7; padding-bottom: 12px; }}
.summary {{ display: flex; gap: 20px; margin: 24px 0; }}
.summary-card {{ flex: 1; padding: 20px; border-radius: 8px; text-align: center; }}
.summary-card.p0 {{ background: #fef2f2; border: 1px solid #fecaca; }}
.summary-card.p1 {{ background: #fffbeb; border: 1px solid #fde68a; }}
.summary-card.p2 {{ background: #eff6ff; border: 1px solid #bfdbfe; }}
.summary-card b {{ display: block; font-size: 36px; }}
.summary-card.p0 b {{ color: #ef4444; }}
.summary-card.p1 b {{ color: #f59e0b; }}
.summary-card.p2 b {{ color: #3b82f6; }}
.issue-item {{ border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; margin: 12px 0; background: #fff; }}
.issue-header {{ font-weight: 700; font-size: 16px; color: #0f172a; margin-bottom: 8px; }}
.issue-finding, .issue-evidence, .issue-law, .issue-suggestion {{ margin: 6px 0; line-height: 1.7; }}
.issue-evidence {{ background: #f8fafc; padding: 8px 10px; border-left: 3px solid #94a3b8; font-style: italic; color: #475569; }}
.issue-suggestion {{ color: #059669; }}
h3.p0 {{ color: #ef4444; border-left: 4px solid #ef4444; padding-left: 12px; }}
h3.p1 {{ color: #f59e0b; border-left: 4px solid #f59e0b; padding-left: 12px; }}
h3.p2 {{ color: #3b82f6; border-left: 4px solid #3b82f6; padding-left: 12px; }}
.standards {{ margin-top: 24px; padding: 16px; background: #f8fafc; border-radius: 8px; font-size: 13px; color: #64748b; }}
.meta {{ color: #94a3b8; font-size: 13px; margin-top: 8px; }}
</style>
</head>
<body>
<h1>AI 环评智能审核报告</h1>
<div class="meta">项目名称：{project_name}</div>
<div class="meta">审核时间：{datetime.now().strftime("%Y-%m-%d %H:%M")}</div>
<div class="meta">审核引擎：恒新环保智能系统 v2.0（规则引擎 + LLM）</div>

<div class="summary">
    <div class="summary-card p0"><b>{len(p0)}</b>P0 严重问题</div>
    <div class="summary-card p1"><b>{len(p1)}</b>P1 一般问题</div>
    <div class="summary-card p2"><b>{len(p2)}</b>P2 建议优化</div>
</div>

{issues_html or '<p style="text-align:center;color:#059669;font-size:18px;padding:40px;">未发现明显问题</p>'}

<div class="standards">
    <strong>报告中引用的标准：</strong>
    {', '.join(standards[:20]) if standards else '未识别到标准编号'}
    <br><br>
    <strong>免责声明：</strong>本审核报告由 AI 自动生成，仅供参考。最终审核结论应以具有相应审批权限的生态环境主管部门意见为准。
</div>
</body>
</html>"""
