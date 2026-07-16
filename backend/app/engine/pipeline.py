import re
from datetime import datetime
from app.engine.extractor import extract_text
from app.engine.grader import grade_issues, build_issue

REQUIRED_CHAPTERS = [
    "前言", "总则",
    "编制依据", "评价因子", "评价标准",
    "工程分析",
    "环境现状调查与评价",
    "环境影响预测与评价",
    "环境保护措施及其可行性论证",
    "环境影响经济损益分析",
    "环境管理与监测计划",
    "评价结论",
]

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

        await update_progress(15, "提取文本", f"文本提取完成，共 {len(full_text)} 字符，{len(chapters)} 个章节", "success")

        await update_progress(20, "结构检查", "开始检查章节完整性...")
        all_issues = []

        for required in REQUIRED_CHAPTERS:
            found = required in full_text
            if not found:
                all_issues.append(build_issue(
                    rule_id="R-STRUCT-001",
                    severity="P0",
                    category="结构完整性",
                    title=f"报告中未找到'{required}'相关章节",
                    finding=f"根据 HJ 2.1-2016 总纲要求，环评报告应包含'{required}'。经检查，报告中未发现相关内容。",
                    law_ref="《建设项目环境影响评价技术导则 总纲》（HJ 2.1-2016）",
                    suggestion=f"请补充'{required}'章节的具体内容，或说明不适用的理由。",
                ))

        await update_progress(30, "结构检查", f"章节完整性检查完成，发现 {len(all_issues)} 个问题", "step")

        await update_progress(35, "标准引用检查", "开始检查标准引用有效性...")

        standards_found = STANDARD_PATTERN.findall(full_text)
        unique_standards = list(set(standards_found))[:30]

        if unique_standards:
            from app.knowledge.retriever import search_knowledge
            for std in unique_standards[:15]:
                results = search_knowledge(f"{std} 替代 废止 更新", top_k=3)
                if results:
                    latest = results[0]
                    if latest.get("score", 1.0) < 0.5:
                        all_issues.append(build_issue(
                            rule_id="R-STD-001",
                            severity="P1",
                            category="标准引用",
                            title=f"标准 {std} 可能已废止或被替代",
                            finding=f"报告中引用了 {std}，知识库检索发现可能存在更新版本。",
                            evidence=latest.get("excerpt", "")[:200],
                            suggestion=f"请确认 {std} 是否为现行有效版本，建议在知识库中查询最新替代标准。",
                        ))

        await update_progress(45, "标准引用检查", f"标准引用检查完成", "step")

        await update_progress(50, "源强核算检查", "开始检查源强核算...")
        from app.engine.steps.source import check_source_strength
        src_issues = await check_source_strength(full_text, chapters)
        all_issues.extend(src_issues)
        await update_progress(65, "源强核算检查", f"源强核算检查完成，发现 {len(src_issues)} 个问题", "step")

        await update_progress(70, "措施可行性检查", "开始检查污染防治措施...")
        from app.engine.steps.measures import check_measures
        msr_issues = await check_measures(full_text)
        all_issues.extend(msr_issues)
        await update_progress(75, "措施可行性检查", f"措施可行性检查完成，发现 {len(msr_issues)} 个问题", "step")

        await update_progress(80, "分类管理判定", "开始判定环评分类管理等级...")
        from app.engine.steps.classification import check_classification
        cls_issues = await check_classification(full_text)
        all_issues.extend(cls_issues)
        await update_progress(88, "分类管理判定", f"分类管理判定完成，发现 {len(cls_issues)} 个问题", "step")

        await update_progress(92, "生成报告", "开始生成审核报告...")

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
                p.logs = (p.logs or []) + [log("审核完成，报告已生成", "success")]
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
            issues_html += f"""
            <div class="issue-item">
                <div class="issue-header">{i}. {iss['title']}</div>
                <div class="issue-finding"><strong>发现：</strong>{iss['finding']}</div>
                {f'<div class="issue-evidence"><strong>原文：</strong>{iss["evidence"]}</div>' if iss.get('evidence') else ''}
                {f'<div class="issue-law"><strong>依据：</strong>{iss["law_ref"]}</div>' if iss.get('law_ref') else ''}
                <div class="issue-suggestion"><strong>建议：</strong>{iss['suggestion']}</div>
            </div>
            """

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>环评审核报告 - {project_name}</title>
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
<div class="meta">审核引擎：恒新环保智能系统 v1.0</div>

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
