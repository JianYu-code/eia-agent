import uuid
import asyncio
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, async_session
from app.models.project import Project
from app.config import UPLOAD_DIR

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
        logs=[{"time": datetime.now().strftime("%H:%M:%S"), "message": "开始智能生成环评报告", "type": "success"}],
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
    if not project or not project.report_path:
        raise HTTPException(status_code=404, detail="报告尚未生成")
    return FileResponse(project.report_path, filename=f"{project.name}.md",
                        media_type="text/markdown; charset=utf-8")


async def _run_generation(project_id: str, info: dict):
    from app.engine.report_generator import generate_report, render_markdown, CHAPTERS

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
        chapters = await generate_report(info, progress_callback=update)

        output_path = UPLOAD_DIR / f"generated_{project_id}.md"
        render_markdown(info, chapters, str(output_path))

        async with async_session() as db:
            r = await db.execute(select(Project).where(Project.id == project_id))
            p = r.scalar_one_or_none()
            if p:
                p.status = "completed"
                p.progress = 100
                p.step = "生成完成"
                p.report_path = str(output_path)
                p.file_path = str(output_path)
                p.logs = (p.logs or []) + [
                    {"time": datetime.now().strftime("%H:%M:%S"),
                     "message": f"报告生成完成，共 {len(chapters)} 个章节，已保存为 Markdown", "type": "success"}
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
