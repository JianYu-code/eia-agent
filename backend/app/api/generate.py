import uuid
import asyncio
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, async_session
from app.models.project import Project
from app.config import UPLOAD_DIR, MAX_UPLOAD_BYTES

router = APIRouter(prefix="/api/generate", tags=["generate"])


class GenerateBody(BaseModel):
    name: str = ""
    company: str = ""
    location: str = ""
    nature: str = "新建"
    industry: str = ""
    investment: str = ""
    area: str = ""
    overview: str = ""
    materials: str = ""
    process: str = ""
    equipment: str = ""
    air_pollution: str = ""
    water_pollution: str = ""
    solid_waste: str = ""
    noise: str = ""
    sensitive_targets: str = ""
    env_function: str = ""
    notes: str = ""
    report_type: str = "报告书"


PROPOSAL_FIELDS = [
    ("name", "项目名称"), ("company", "建设单位"), ("location", "建设地点"),
    ("nature", "建设性质（新建/改扩建/技改）"), ("industry", "行业类别"),
    ("investment", "总投资（万元）"), ("area", "用地面积（平方米）"),
    ("overview", "工程概况"), ("materials", "主要原辅材料"),
    ("process", "工艺流程简述"), ("equipment", "主要设备"),
    ("air_pollution", "废气污染源及治理措施"), ("water_pollution", "废水污染源及治理措施"),
    ("solid_waste", "固废产生及处置"), ("noise", "噪声源及控制措施"),
    ("sensitive_targets", "环境敏感目标"), ("env_function", "环境功能区划"),
]


@router.post("/parse-proposal")
async def parse_proposal(file: UploadFile = File(...)):
    """上传可研/设计文件 → LLM 提取表单字段（自动填表）"""
    if file.size and file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="文件不能超过 200MB")

    ext = Path(file.filename or "doc").suffix
    tmp_path = UPLOAD_DIR / f"proposal_{uuid.uuid4().hex}{ext}"
    tmp_path.write_bytes(await file.read())

    from app.engine.extractor import extract_text
    from app.engine.llm_json import parse_llm_json
    from app.llm.client import chat, get_active_profile

    try:
        text_data = extract_text(str(tmp_path))
    finally:
        tmp_path.unlink(missing_ok=True)
    full_text = text_data.get("full_text", "")
    if len(full_text) < 100:
        raise HTTPException(status_code=400, detail="文件内容提取失败或为空")

    profile = await get_active_profile()
    if not profile:
        raise HTTPException(status_code=400, detail="未配置启用的 LLM Profile")

    fields_desc = "、".join([f"{k}({label})" for k, label in PROPOSAL_FIELDS])
    prompt = f"""请从以下项目文件内容中提取环评报告表单字段，输出JSON（不要带```标记）。
字段：{fields_desc}
找不到的字段填空字符串。只输出JSON。

文件内容：
{full_text[:12000]}"""

    resp = await chat(prompt, profile=profile)
    data = parse_llm_json(resp, expect="object") or {}
    fields = {k: str(data.get(k, "") or "") for k, _ in PROPOSAL_FIELDS}
    fields["notes"] = ""
    return {"fields": fields, "filename": file.filename}


@router.post("")
async def start_generate(body: GenerateBody, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    info = body.model_dump()
    project = Project(
        name=info.get("name") or "环评报告生成",
        filename=f"生成_{info.get('name', '报告')}.md",
        file_path="",
        status="running",
        step="启动生成...",
        progress=0.0,
        audit_domain="eia",
        logs=[{"time": datetime.now().strftime("%H:%M:%S"), "message": "开始智能生成环评报告（生成→自审核→修复双闭环）", "type": "success"}],
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    background_tasks.add_task(_run_generation, project.id, info)
    return {"project_id": project.id, "project": project.to_dict()}


@router.get("/{project_id}/download")
async def download_report(project_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    md_path = UPLOAD_DIR / f"generated_{project_id}.md"
    if not project or not md_path.exists():
        raise HTTPException(status_code=404, detail="报告尚未生成")
    return FileResponse(str(md_path), filename=f"{project.name}.md",
                        media_type="text/markdown; charset=utf-8")


@router.get("/{project_id}/download-docx")
async def download_docx(project_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    docx_path = UPLOAD_DIR / f"generated_{project_id}.docx"
    if not project or not docx_path.exists():
        raise HTTPException(status_code=404, detail="DOCX 尚未生成")
    return FileResponse(str(docx_path), filename=f"{project.name}.docx",
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


async def _run_generation(project_id: str, info: dict):
    from app.engine.report_generator import generate_report, render_markdown, render_docx
    from app.engine.extractor import extract_text
    from app.engine.kfiles import identify_report, select_kfiles
    from app.engine.audit_runner import execute_audit_steps
    from app.engine.gen_repair import repair_draft
    from app.engine.grader import grade_issues
    from app.engine.pipeline import _generate_report as render_audit_html

    async def update(pct: float, step: str, msg: str, lt="step"):
        async with async_session() as db:
            r = await db.execute(select(Project).where(Project.id == project_id))
            p = r.scalar_one_or_none()
            if p:
                p.progress = pct
                p.step = step
                p.logs = (p.logs or []) + [
                    {"time": datetime.now().strftime("%H:%M:%S"), "message": msg, "type": lt}
                ]
                await db.commit()

    try:
        # ═══ 阶段一：智能生成 ═══
        gen = await generate_report(info, progress_callback=update)
        chapters = gen["chapters"]
        report_type = gen.get("report_type", "报告书")
        industry = gen.get("industry", "")
        await update(50, "生成完成", f"{len(chapters)} 个章节生成完成，准备自审核", "success")

        md_path = UPLOAD_DIR / f"generated_{project_id}.md"
        render_markdown(info, chapters, str(md_path))

        # ═══ 阶段二：完整管线自审核 ═══
        await update(52, "自审核", "生成稿进入 11 步自审核管线...", "step")
        text_data = extract_text(str(md_path))
        identity = await identify_report(text_data, "eia", llm_call=None)
        selected_kids = select_kfiles("eia", report_type, industry)
        audit_ctx = {
            "domain": "eia", "report_type": report_type,
            "industry": industry or identity.get("industry", ""),
            "project_name": info.get("name", ""),
            "has_sensitive_area": False, "kfiles": selected_kids,
        }

        async def audit_log(pct, step, msg, lt="step"):
            mapped = 52 + (pct or 0) * 0.3
            await update(mapped, f"自审核·{step}", msg, lt)

        result = await execute_audit_steps(text_data, audit_ctx, audit_log, file_path=str(md_path))
        issues = result["issues"]
        step_statuses = result["step_statuses"]
        review_summary = result["review_summary"]
        standards_found = result["standards_found"]

        # ═══ 阶段三：自动修复（≤2 轮）═══
        repair_logs = []
        graded = grade_issues(issues)
        for round_no in (1, 2):
            need = [i for i in issues if i.get("severity") in ("P0", "P1")]
            if not need:
                break
            await update(85 + round_no * 3, "自动修复", f"第 {round_no} 轮修复 {len(need)} 个问题...", "step")
            chapters, logs = await repair_draft(chapters, need, round_no=round_no)
            repair_logs.extend(logs)
            render_markdown(info, chapters, str(md_path))
            if round_no == 1:
                text_data = extract_text(str(md_path))
                recheck = await execute_audit_steps(text_data, audit_ctx, audit_log, file_path=str(md_path))
                issues = recheck["issues"]
                step_statuses = recheck["step_statuses"]
                review_summary = recheck["review_summary"] or review_summary
                graded = grade_issues(issues)
                p0_left = len([i for i in issues if i.get("severity") == "P0"])
                if p0_left == 0:
                    break
            else:
                text_data = extract_text(str(md_path))
                recheck = await execute_audit_steps(text_data, audit_ctx, audit_log, file_path=str(md_path))
                issues = recheck["issues"]
                step_statuses = recheck["step_statuses"]
                review_summary = recheck["review_summary"] or review_summary
                graded = grade_issues(issues)

        p0n, p1n, p2n = len(graded.get("P0", [])), len(graded.get("P1", [])), len(graded.get("P2", []))
        if not review_summary.get("grade"):
            review_summary["grade"] = "A" if p0n == 0 and p1n <= 2 else ("B" if p0n == 0 else ("C" if p0n <= 3 else "D"))
        review_summary.setdefault("summary", "")
        review_summary.setdefault("top3", [])

        # ═══ 阶段四：交付物 ═══
        await update(96, "生成交付物", "渲染 MD / DOCX / 自审核报告...", "step")
        render_markdown(info, chapters, str(md_path))
        docx_path = UPLOAD_DIR / f"generated_{project_id}.docx"
        try:
            render_docx(info, chapters, str(docx_path))
        except Exception as e:
            await update(96, "生成交付物", f"DOCX 生成失败（{str(e)[:60]}），MD 不受影响", "step")

        audit_html_path = UPLOAD_DIR / f"report_{project_id}.html"
        audit_html_path.write_text(
            render_audit_html(info.get("name", "生成报告自审核"), graded, text_data.get("full_text", ""),
                              standards_found, step_statuses, review_summary),
            encoding="utf-8")

        # 自审核问题落库（汇入反馈闭环）
        from app.models.project import AuditIssue
        from sqlalchemy import delete as _delete
        async with async_session() as db:
            await db.execute(_delete(AuditIssue).where(AuditIssue.project_id == project_id))
            for sev in ("P0", "P1", "P2"):
                for iss in graded.get(sev, []):
                    db.add(AuditIssue(
                        project_id=project_id, rule_id=iss.get("rule_id", ""), severity=sev,
                        category=iss.get("category", ""), title=iss.get("title", ""),
                        finding=iss.get("finding", ""), evidence=iss.get("evidence", ""),
                        evidence_location=iss.get("evidence_location", ""),
                        reasoning=iss.get("reasoning", ""), law_ref=iss.get("law_ref", ""),
                        suggestion=iss.get("suggestion", ""), step=iss.get("step", ""),
                        chapter=iss.get("chapter", ""),
                    ))
            await db.commit()

        async with async_session() as db:
            r = await db.execute(select(Project).where(Project.id == project_id))
            p = r.scalar_one_or_none()
            if p:
                p.status = "completed"
                p.progress = 100
                p.step = "生成完成"
                p.file_path = str(md_path)
                p.report_path = str(audit_html_path)
                p.issues = {"P0": p0n, "P1": p1n, "P2": p2n}
                p.result_summary = {**review_summary, "p0_count": p0n, "p1_count": p1n,
                                    "p2_count": p2n, "repair_rounds": len([l for l in repair_logs if "修复「" in l])}
                p.logs = (p.logs or []) + [
                    {"time": datetime.now().strftime("%H:%M:%S"), "message": m, "type": "step"}
                    for m in repair_logs
                ] + [
                    {"time": datetime.now().strftime("%H:%M:%S"),
                     "message": f"双闭环完成：自审核 {len(issues)} 问题（P0:{p0n} P1:{p1n} P2:{p2n}），评级 {review_summary['grade']}。残留问题多为占位数据，请工程师核实替换【待补】内容",
                     "type": "success"}
                ]
                await db.commit()

    except Exception as e:
        async with async_session() as db:
            r = await db.execute(select(Project).where(Project.id == project_id))
            p = r.scalar_one_or_none()
            if p:
                p.status = "failed"
                p.step = f"生成失败: {str(e)[:100]}"
                p.logs = (p.logs or []) + [
                    {"time": datetime.now().strftime("%H:%M:%S"), "message": str(e), "type": "error"}
                ]
                await db.commit()
