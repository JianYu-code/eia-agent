import asyncio
import re
import html
from datetime import datetime

from app.engine.extractor import extract_text
from app.engine.grader import grade_issues, build_issue
from app.engine.audit_runner import execute_audit_steps


def esc(s) -> str:
    """HTML 转义：所有 LLM/用户可控内容进报告前必须过此函数（防 XSS）"""
    return html.escape(str(s or ""), quote=True)


async def run_audit_pipeline(project_id: str):
    from app.database import async_session
    from app.models.project import Project
    from sqlalchemy import select

    async with async_session() as db:
        result = await db.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()
        if not project:
            return
    domain = project.audit_domain or "eia"

    def log(msg: str, t: str = "info"):
        return {"time": datetime.now().strftime("%H:%M:%S"), "message": msg, "type": t}

    async def update_progress(pct: float | None, step: str, msg: str, log_type: str = "step"):
        async with async_session() as db:
            result = await db.execute(select(Project).where(Project.id == project_id))
            p = result.scalar_one_or_none()
            if p:
                if pct is not None:
                    p.progress = pct
                p.step = step
                p.logs = (p.logs or []) + [log(msg, log_type)]
                await db.commit()

    async def check_stopped(step_name: str) -> bool:
        from app.engine.queue import is_stopped
        if await is_stopped(project_id):
            await update_progress(None, step_name, f"审核已在「{step_name}」前暂停", "step")
            return True
        return False

    try:
        await update_progress(5, "1 提取文本", "开始提取报告文本...")
        text_data = extract_text(project.file_path)
        full_text = text_data.get("full_text", "")
        is_package = bool(text_data.get("is_package"))
        file_count = len(text_data.get("files", []) or [])
        pkg_msg = f"（资料包 {file_count} 个文件）" if is_package else ""
        await update_progress(10, "1 提取文本", f"文本提取完成{pkg_msg}，{len(full_text)} 字符", "success")

        report_type = "报告表" if "报告表" in full_text[:2000] else "报告书"

        # ═══ Step 0 报告识别 ═══
        await update_progress(12, "0 报告识别", "识别报告类型与行业...", "step")
        from app.engine.kfiles import identify_report, select_kfiles
        from app.llm.client import chat as _chat0, get_active_profile as _get_profile0
        identity = None
        try:
            profile0 = await _get_profile0()
            if profile0:
                llm_call0 = lambda p: _chat0(p, profile=profile0)
                identity = await identify_report(text_data, domain, llm_call=llm_call0)
        except Exception as e:
            await update_progress(12, "0 报告识别", f"LLM 识别失败（{str(e)[:60]}），使用关键词兜底", "step")
        if identity is None:
            identity = await identify_report(text_data, domain, llm_call=None)
        report_type = identity.get("report_type") or report_type
        industry = identity.get("industry", "")
        await update_progress(14, "0 报告识别",
                              f"识别完成：{report_type} / 行业：{industry or '未识别'}", "success")

        selected_kids = select_kfiles(domain, report_type, industry)
        await update_progress(15, "0 报告识别",
                              f"已注入审核知识文件：{'、'.join(selected_kids)}", "step")

        audit_ctx = {
            "domain": domain,
            "report_type": report_type,
            "industry": industry,
            "project_name": identity.get("project_name", ""),
            "has_sensitive_area": identity.get("has_sensitive_area", False),
            "kfiles": selected_kids,
        }

        # ═══ 执行审核核心管线 ═══
        result = await execute_audit_steps(
            text_data, audit_ctx, update_progress,
            file_path=project.file_path, check_stop_cb=check_stopped,
        )
        if result.get("stopped"):
            async with async_session() as db:
                r = await db.execute(select(Project).where(Project.id == project_id))
                p = r.scalar_one_or_none()
                if p:
                    p.step = "已暂停"
                    p.logs = (p.logs or []) + [log("审核已暂停，可重新开始", "step")]
                    await db.commit()
            return

        all_issues = result["issues"]
        step_statuses = [{"name": "0 报告识别", "status": "pass", "count": 0}] + result["step_statuses"]
        review_summary = result["review_summary"]
        standards_found = result["standards_found"]

        await update_progress(85, "生成报告", "开始生成审核报告...")
        graded = grade_issues(all_issues)
        p0n, p1n, p2n = len(graded.get("P0", [])), len(graded.get("P1", [])), len(graded.get("P2", []))
        if not review_summary.get("grade"):
            review_summary["grade"] = "A" if p0n == 0 and p1n <= 2 else ("B" if p0n == 0 else ("C" if p0n <= 3 else "D"))
        review_summary.setdefault("summary", "")
        review_summary.setdefault("top3", [])

        accuracy = None
        try:
            from app.models.project import AuditIssue
            from sqlalchemy import select as _sel2, func as _func
            async with async_session() as db:
                r = await db.execute(_sel2(AuditIssue.feedback, _func.count()).group_by(AuditIssue.feedback))
                fb = dict(r.all())
            acc_n = fb.get("accurate", 0)
            judged = acc_n + fb.get("false_positive", 0) + fb.get("adjust", 0)
            if judged >= 5:
                accuracy = round(acc_n / judged * 100)
        except Exception:
            pass
        review_summary["accuracy"] = accuracy

        # ── 问题结构化落库（支撑反馈闭环）──
        from app.models.project import AuditIssue
        from sqlalchemy import delete as _delete
        issue_rows = []
        async with async_session() as db:
            await db.execute(_delete(AuditIssue).where(AuditIssue.project_id == project_id))
            for sev in ("P0", "P1", "P2"):
                for iss in graded.get(sev, []):
                    row = AuditIssue(
                        project_id=project_id,
                        rule_id=iss.get("rule_id", ""),
                        severity=sev,
                        category=iss.get("category", ""),
                        title=iss.get("title", ""),
                        finding=iss.get("finding", ""),
                        evidence=iss.get("evidence", ""),
                        evidence_location=iss.get("evidence_location", ""),
                        reasoning=iss.get("reasoning", ""),
                        law_ref=iss.get("law_ref", ""),
                        suggestion=iss.get("suggestion", ""),
                        step=iss.get("step", ""),
                        chapter=iss.get("chapter", ""),
                    )
                    db.add(row)
                    issue_rows.append((row, iss))
            await db.commit()
            for row, iss in issue_rows:
                iss["issue_id"] = row.id

        from app.engine.cases import match_case_sources
        await match_case_sources(graded.get("P0", []) + graded.get("P1", []) + graded.get("P2", []))
        report_html = _generate_report(project.name, graded, full_text, standards_found, step_statuses, review_summary)
        from app.config import UPLOAD_DIR
        report_path = UPLOAD_DIR / f"report_{project_id}.html"
        report_path.write_text(report_html, encoding="utf-8")

        async with async_session() as db:
            result2 = await db.execute(select(Project).where(Project.id == project_id))
            p = result2.scalar_one_or_none()
            if p:
                p.status = "completed"; p.progress = 100; p.step = "审核完成"
                p.issues = {"P0": p0n, "P1": p1n, "P2": p2n}
                p.result_summary = {**review_summary, "p0_count": p0n, "p1_count": p1n, "p2_count": p2n}
                p.report_path = str(report_path)
                err_steps = [s["name"] for s in step_statuses if s["status"] == "error"]
                done_msg = f"审核完成，{len(all_issues)} 问题(P0:{p0n} P1:{p1n} P2:{p2n})，质量评级 {review_summary['grade']}"
                if err_steps:
                    done_msg += f"；注意：{'、'.join(err_steps)} 检查失败需人工复核"
                p.logs = (p.logs or []) + [log(done_msg, "success")]
                await db.commit()

    except Exception as e:
        async with async_session() as db:
            result = await db.execute(select(Project).where(Project.id == project_id))
            p = result.scalar_one_or_none()
            if p:
                p.status = "failed"; p.step = f"失败: {str(e)[:100]}"
                p.logs = (p.logs or []) + [log(str(e), "error")]
                await db.commit()


def _step_badges_html(step_statuses: list[dict]) -> str:
    if not step_statuses:
        return ""
    badges = '<div class="step-summary">'
    for s in step_statuses:
        st = s.get("status", "skip")
        cls = {"pass": "pass", "fail": "fail", "error": "skip"}.get(st, "skip")
        label = {"pass": "✓ 通过", "fail": f"✗ {s.get('count', 0)} 问题", "error": "⚠ 检查失败"}.get(st, "跳过")
        badges += f'<div class="step-badge {cls}">{esc(s.get("name", ""))}<br>{label}</div>'
    badges += "</div>"
    return badges


def _generate_report(project_name: str, graded: dict, full_text: str, standards: list[str],
                     step_statuses: list[dict] | None = None,
                     review_summary: dict | None = None) -> str:
    p0 = graded.get("P0", [])
    p1 = graded.get("P1", [])
    p2 = graded.get("P2", [])
    review_summary = review_summary or {}

    grade = esc(review_summary.get("grade", ""))
    grade_desc = {"A": "质量好", "B": "基本合格", "C": "需整改", "D": "质量差"}.get(grade, "")
    grade_color = {"A": "#10b981", "B": "#3b82f6", "C": "#f59e0b", "D": "#ef4444"}.get(grade, "#64748b")
    top3 = review_summary.get("top3") or []
    grade_html = ""
    if grade:
        top3_html = "".join(f"<li>{esc(t)}</li>" for t in top3 if t)
        grade_html = f"""
        <div style="display:flex;gap:20px;align-items:stretch;margin:20px 0;">
          <div style="flex:0 0 160px;border:2px solid {grade_color};border-radius:12px;text-align:center;padding:18px 10px;background:#fff;">
            <div style="font-size:52px;font-weight:800;color:{grade_color};line-height:1;">{grade}</div>
            <div style="color:{grade_color};font-weight:600;margin-top:6px;">质量评级 · {grade_desc}</div>
          </div>
          <div style="flex:1;border:1px solid #e2e8f0;border-radius:12px;padding:16px 20px;background:#fff;">
            <div style="font-weight:700;margin-bottom:8px;">专家组总结</div>
            <div style="color:#475569;line-height:1.8;">{esc(review_summary.get('summary', '—'))}</div>
            {"<div style='margin-top:10px;'><div style='font-weight:700;margin-bottom:4px;'>优先整改：</div><ol style='margin:0;padding-left:20px;color:#b91c1c;'>" + top3_html + "</ol></div>" if top3_html else ""}
            {"<div style='margin-top:8px;color:#92400e;'>⚠ 矛盾点：" + esc(review_summary.get('contradictions', '')) + "</div>" if review_summary.get('contradictions') else ""}
          </div>
        </div>"""

    issues_html = ""
    for severity, issues, color_class in [("P0", p0, "p0"), ("P1", p1, "p1"), ("P2", p2, "p2")]:
        if not issues: continue
        issues_html += f'<h3 class="{color_class}">{severity} 问题 ({len(issues)}项)</h3>'
        for i, iss in enumerate(issues, 1):
            loc = iss.get("evidence_location", "")
            if loc:
                evidence_html = f'<div class="issue-highlight"><strong>📝 报告原文定位：</strong><span class="highlight-text">"{esc(loc)}"</span></div>'
            elif iss.get("evidence"):
                evidence_html = f'<div class="issue-evidence"><strong>报告原文：</strong>{esc(iss["evidence"])}</div>'
            else:
                evidence_html = ""
            reasoning_html = f'<div class="issue-reasoning"><strong>AI推理过程：</strong><p>{esc(iss["reasoning"])}</p></div>' if iss.get("reasoning") else ""
            law_html = f'<div class="issue-law"><strong>引用法规：</strong>{esc(iss["law_ref"])}</div>' if iss.get("law_ref") else ""
            meta_bits = []
            if iss.get("step"): meta_bits.append(f"审核步骤: {esc(iss['step'])}")
            if iss.get("chapter"): meta_bits.append(f"所在章节: {esc(iss['chapter'])}")
            if iss.get("case_source"): meta_bits.append(f"意见来源: {esc(iss['case_source'])}")
            elif iss.get("rule_id"): meta_bits.append(esc(iss["rule_id"]))
            step_html = f'<div class="issue-step" style="color:#94a3b8;font-size:11px;margin-top:4px;">{" ｜ ".join(meta_bits)}</div>' if meta_bits else ""
            issue_id = esc(iss.get("issue_id", ""))
            feedback_html = ""
            if issue_id:
                feedback_html = f"""
                <div class="feedback-bar" data-issue-id="{issue_id}">
                  <span style="font-size:12px;color:#94a3b8;">此条意见是否准确？</span>
                  <button class="fb-btn" data-fb="accurate">✓ 准确</button>
                  <button class="fb-btn" data-fb="false_positive">✗ 误报</button>
                  <button class="fb-btn" data-fb="adjust">✎ 建议调整</button>
                  <span class="fb-status" style="font-size:12px;color:#059669;"></span>
                </div>"""
            issues_html += f"""
            <div class="issue-item">
                <div class="issue-header">{i}. {esc(iss['title'])}</div>
                <div class="issue-finding"><strong>发现：</strong>{esc(iss['finding'])}</div>
                {evidence_html}
                {reasoning_html}
                {law_html}
                <div class="issue-suggestion"><strong>最小化修改指引（供参考）：</strong>{esc(iss['suggestion'])}</div>
                {step_html}
                {feedback_html}
            </div>"""

    error_names = [esc(s["name"]) for s in (step_statuses or []) if s.get("status") == "error"]
    error_banner = ""
    if error_names:
        error_banner = f'<div style="background:#fef2f2;border:1px solid #fecaca;color:#b91c1c;padding:12px 16px;border-radius:8px;margin:16px 0;"><strong>⚠ 部分检查未执行成功：</strong>{"、" .join(error_names)}。上述步骤结果缺失，请人工复核或重新审核。</div>'

    accuracy = review_summary.get("accuracy")
    acc_text = f"，历史采纳率约 <b>{accuracy}%</b>" if accuracy is not None else "（暂无采纳率统计数据）"
    usage_html = f"""
        <div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:10px;padding:14px 18px;margin:16px 0;font-size:13px;color:#1e3a8a;line-height:1.9;">
          <div style="font-weight:700;margin-bottom:4px;">初审意见使用说明</div>
          <div>① 智能初审无法发现所有问题，但可以显著提升报告质量；提出的问题也不见得全部正确{acc_text}。</div>
          <div>② 使用方法：快速概览，优先处理 P0 严重问题与「交叉核验」类高置信数据矛盾；其他不确定问题可结合专业判断略过。</div>
          <div>③ 每条意见附原文定位与最小化修改指引；可点击意见下方按钮反馈"准确/误报/建议调整"，系统将据此持续校准审核质量。</div>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><title>审核报告 - {esc(project_name)}</title>
<style>
body{{font-family:"PingFang SC","Microsoft YaHei",sans-serif;max-width:900px;margin:0 auto;padding:40px 20px;color:#1a2733;background:#f5f8fc}}
h1{{border-bottom:3px solid #4fc3f7;padding-bottom:12px}}
.step-summary{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:16px 0;font-size:12px}}
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
.feedback-bar{{margin-top:10px;padding-top:8px;border-top:1px dashed #e2e8f0;display:flex;gap:8px;align-items:center;}}
.fb-btn{{font-size:12px;padding:3px 10px;border:1px solid #cbd5e1;border-radius:6px;background:#fff;cursor:pointer;color:#475569;}}
.fb-btn:hover{{background:#f1f5f9;}}
.fb-btn.active{{background:#dcfce7;border-color:#10b981;color:#047857;}}
.print-bar{{text-align:right;margin-bottom:8px;}}
.print-bar button{{padding:6px 16px;border:1px solid #cbd5e1;border-radius:6px;background:#fff;cursor:pointer;}}
@media print{{
  .feedback-bar,.print-bar{{display:none;}}
  body{{background:#fff;padding:0;}}
  .issue-item{{break-inside:avoid;}}
}}
</style></head><body>
<div class="print-bar"><button onclick="window.print()">🖨 打印 / 另存为 PDF</button></div>
<h1>AI 环评智能审核报告</h1>
<div class="meta">项目名称：{esc(project_name)}</div>
<div class="meta">审核时间：{datetime.now().strftime("%Y-%m-%d %H:%M")}</div>
<div class="meta">审核引擎：恒新环保智能系统 v4.0（K文件注入 + 章节路由 + 顺序队列 + 反馈闭环）</div>
{error_banner}
{usage_html}
{_step_badges_html(step_statuses or [])}
{grade_html}
<div class="summary"><div class="summary-card p0"><b>{len(p0)}</b>P0 严重问题</div><div class="summary-card p1"><b>{len(p1)}</b>P1 一般问题</div><div class="summary-card p2"><b>{len(p2)}</b>P2 建议优化</div></div>
{issues_html or '<p style="text-align:center;color:#059669;font-size:18px;padding:40px;">未发现明显问题</p>'}
<div class="standards"><strong>报告中引用的标准：</strong>{esc(', '.join(standards[:20])) if standards else '未识别到标准编号'}<br><br><strong>免责声明：</strong>本审核报告由 AI 自动生成，为专家复核级智能审核意见，仅供参考。最终审核结论应以具有相应审批权限的生态环境主管部门意见为准。</div>
<script>
document.querySelectorAll('.feedback-bar').forEach(bar => {{
  const issueId = bar.dataset.issueId;
  bar.querySelectorAll('.fb-btn').forEach(btn => {{
    btn.addEventListener('click', async () => {{
      const fb = btn.dataset.fb;
      let note = '';
      if (fb !== 'accurate') {{
        note = prompt('请简要说明原因（哪条原文/依据支持您的判断，可留空）：') || '';
      }}
      try {{
        const res = await fetch('/api/issues/' + issueId + '/feedback', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{feedback: fb, note: note}})
        }});
        if (res.ok) {{
          bar.querySelectorAll('.fb-btn').forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
          bar.querySelector('.fb-status').textContent = '已记录，感谢反馈';
        }} else {{
          bar.querySelector('.fb-status').textContent = '提交失败';
        }}
      }} catch(e) {{
        bar.querySelector('.fb-status').textContent = '网络错误';
      }}
    }});
  }});
}});
</script>
</body></html>"""
