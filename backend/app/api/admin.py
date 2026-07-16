from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.database import get_db
from app.models.project import Project, LLMProfile

router = APIRouter(prefix="/api/admin", tags=["admin"])


class LLMConfigBody(BaseModel):
    action: str = "save"
    id: str = ""
    name: str = ""
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    purpose: str = "audit"
    pool_enabled: bool = True
    max_retries: int = 3
    extra_body: dict | None = None
    activate: bool = False


@router.get("/llm-config")
async def list_llm_profiles(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(LLMProfile).order_by(LLMProfile.updated_at.desc()))
    profiles = result.scalars().all()
    return {"profiles": [p.to_dict() for p in profiles]}


@router.post("/llm-config")
async def save_llm_config(body: LLMConfigBody, db: AsyncSession = Depends(get_db)):
    if body.activate:
        result = await db.execute(select(LLMProfile).where(LLMProfile.active == True))
        for p in result.scalars().all():
            p.active = False

    result = await db.execute(select(LLMProfile).where(LLMProfile.id == body.id))
    profile = result.scalar_one_or_none()

    if profile:
        profile.name = body.name
        profile.base_url = body.base_url
        profile.model = body.model
        if body.api_key:
            profile.api_key = body.api_key
        profile.purpose = body.purpose
        profile.pool_enabled = body.pool_enabled
        profile.max_retries = body.max_retries
        profile.extra_body = body.extra_body
        profile.active = body.activate or profile.active
    else:
        profile = LLMProfile(
            id=body.id,
            name=body.name,
            base_url=body.base_url,
            model=body.model,
            api_key=body.api_key,
            purpose=body.purpose,
            pool_enabled=body.pool_enabled,
            max_retries=body.max_retries,
            extra_body=body.extra_body,
            active=body.activate,
        )
        db.add(profile)

    await db.commit()
    result = await db.execute(select(LLMProfile).order_by(LLMProfile.updated_at.desc()))
    return {"profiles": [p.to_dict() for p in result.scalars().all()], "saved_id": profile.id, "active_id": profile.id if body.activate else None}


@router.post("/llm-config/test")
async def test_llm_config(body: dict):
    from app.llm.client import build_llm
    from langchain_core.messages import HumanMessage

    from app.models.project import LLMProfile as ProfileModel
    profile = ProfileModel(
        base_url=body.get("base_url", ""),
        model=body.get("model", ""),
        api_key=body.get("api_key", ""),
        max_retries=3,
    )

    try:
        llm = build_llm(profile)
        resp = await llm.ainvoke([HumanMessage(content="你好，请用1-2句话回复。")])
        return {"status": "ok", "reply": resp.content}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"连接失败: {str(e)}")


@router.get("/logs")
async def list_logs(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Project).order_by(Project.updated_at.desc()).limit(100))
    projects = result.scalars().all()
    logs = []
    for p in projects:
        for entry in (p.logs or [])[-5:]:
            logs.append({
                "time": entry.get("time", ""),
                "action": entry.get("type", "info"),
                "detail": {"project_id": p.id, "filename": p.filename, "message": entry.get("message", "")},
                "user": "system",
            })
    return {"logs": logs}


@router.post("/projects/{project_id}/purge")
async def purge_project(project_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    import os, shutil
    if project.file_path and os.path.exists(project.file_path):
        if os.path.isdir(project.file_path):
            shutil.rmtree(project.file_path, ignore_errors=True)
        else:
            os.remove(project.file_path)
    if project.report_path and os.path.exists(project.report_path):
        os.remove(project.report_path)
    await db.execute(delete(Project).where(Project.id == project_id))
    await db.commit()
    return {"message": "已清除"}


@router.get("/llm-cache-stats")
async def llm_cache_stats():
    from app.engine.llm_cache import stats
    return stats()


@router.post("/llm-cache-clear")
async def llm_cache_clear():
    from app.engine.llm_cache import clear
    clear()
    return {"message": "LLM 缓存已清除"}
