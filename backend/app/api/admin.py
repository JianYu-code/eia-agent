from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.database import get_db
from app.models.project import Project, LLMProfile
from app.api.deps import require_admin

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


@router.get("/llm-config", dependencies=[Depends(require_admin)])
async def list_llm_profiles(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(LLMProfile).order_by(LLMProfile.updated_at.desc()))
    profiles = result.scalars().all()
    return {"profiles": [p.to_dict() for p in profiles]}


@router.post("/llm-config", dependencies=[Depends(require_admin)])
async def save_llm_config(body: LLMConfigBody, db: AsyncSession = Depends(get_db)):
    if body.activate:
        if body.purpose == "vision_review":
            result = await db.execute(select(LLMProfile).where(LLMProfile.vision_active == True))
            for p in result.scalars().all():
                p.vision_active = False
        else:
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
        if body.purpose == "vision_review":
            profile.vision_active = body.activate or profile.vision_active
        else:
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
            active=body.activate and body.purpose != "vision_review",
            vision_active=body.activate and body.purpose == "vision_review",
        )
        db.add(profile)

    await db.commit()
    result = await db.execute(select(LLMProfile).order_by(LLMProfile.updated_at.desc()))
    return {"profiles": [p.to_dict() for p in result.scalars().all()], "saved_id": profile.id, "active_id": profile.id if body.activate else None}


@router.delete("/llm-config/{profile_id}", dependencies=[Depends(require_admin)])
async def delete_llm_config(profile_id: str, db: AsyncSession = Depends(get_db)):
    """删除指定 LLM Profile（配错模型时可移除）"""
    result = await db.execute(select(LLMProfile).where(LLMProfile.id == profile_id))
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="配置不存在")
    await db.delete(profile)
    await db.commit()
    result = await db.execute(select(LLMProfile).order_by(LLMProfile.updated_at.desc()))
    return {"profiles": [p.to_dict() for p in result.scalars().all()], "deleted_id": profile_id}


@router.post("/llm-config/test", dependencies=[Depends(require_admin)])
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


@router.get("/llm-cache-stats", dependencies=[Depends(require_admin)])
async def llm_cache_stats():
    from app.engine.llm_cache import stats
    return stats()


@router.post("/llm-cache-clear", dependencies=[Depends(require_admin)])
async def llm_cache_clear():
    from app.engine.llm_cache import clear
    clear()
    return {"message": "LLM 缓存已清除"}


@router.get("/kfiles")
async def kfiles_list():
    """K1-K17 审核知识文件清单（对标参考系统"审核文件"页）"""
    from app.engine.kfiles import list_kfiles
    return {"kfiles": list_kfiles()}


@router.get("/rules")
async def rules_list():
    """动态返回全部审核规则（供规则页展示）"""
    from app.engine.rules_engine import load_rules
    out = {}
    for domain in ("eia", "acceptance", "emergency"):
        for rt in ("报告书", "报告表"):
            for rule in load_rules(domain, rt):
                rid = rule.get("rule_id", "")
                if any(r["rule_id"] == rid for r in out.setdefault(domain, [])):
                    continue
                out[domain].append({
                    "rule_id": rid,
                    "severity": rule.get("severity", "P2"),
                    "category": rule.get("category", ""),
                    "title": rule.get("title", ""),
                    "law_ref": rule.get("law_ref", ""),
                    "report_type": rule.get("report_type", "通用"),
                    "check_type": rule.get("check_type", "keyword_match"),
                    "kfiles": rule.get("kfiles", []),
                })
    return {"rules": out}


@router.get("/quality-feedback-summary")
async def quality_feedback_summary(db: AsyncSession = Depends(get_db)):
    """审核质量反馈汇总：按规则聚合准确率/误报率（对标参考系统管理页）"""
    from app.models.project import AuditIssue
    result = await db.execute(select(AuditIssue))
    issues = result.scalars().all()

    by_rule: dict[str, dict] = {}
    for i in issues:
        r = by_rule.setdefault(i.rule_id, {
            "rule_id": i.rule_id, "category": i.category, "total": 0,
            "accurate": 0, "false_positive": 0, "adjust": 0, "pending": 0,
            "recent_notes": [],
        })
        r["total"] += 1
        if i.feedback == "accurate":
            r["accurate"] += 1
        elif i.feedback == "false_positive":
            r["false_positive"] += 1
            if i.feedback_note:
                r["recent_notes"].append(i.feedback_note[:200])
        elif i.feedback == "adjust":
            r["adjust"] += 1
        else:
            r["pending"] += 1

    rules = sorted(by_rule.values(), key=lambda x: x["false_positive"], reverse=True)
    for r in rules:
        judged = r["accurate"] + r["false_positive"] + r["adjust"]
        r["accuracy"] = round(r["accurate"] / judged * 100, 1) if judged else None
        r["recent_notes"] = r["recent_notes"][-5:]

    total_fb = sum(1 for i in issues if i.feedback)
    return {
        "total_issues": len(issues),
        "total_feedback": total_fb,
        "rules": rules,
    }


@router.get("/cases", dependencies=[Depends(require_admin)])
async def list_cases(db: AsyncSession = Depends(get_db)):
    """历史审核案例库列表"""
    from app.models.project import AuditCase
    result = await db.execute(select(AuditCase).order_by(AuditCase.accurate_count.desc()))
    cases = [c.to_dict() for c in result.scalars().all()]
    return {"total": len(cases), "cases": cases}


@router.post("/cases/rebuild", dependencies=[Depends(require_admin)])
async def rebuild_cases_api():
    """从 feedback=accurate 的问题聚类重建案例库（有 LLM 时凝练判定要点）"""
    from app.engine.cases import rebuild_cases
    from app.llm.client import chat, get_active_profile
    llm_call = None
    profile = await get_active_profile()
    if profile:
        llm_call = lambda p: chat(p, profile=profile)
    return await rebuild_cases(llm_call=llm_call)


@router.post("/cases/{case_id}/toggle", dependencies=[Depends(require_admin)])
async def toggle_case(case_id: str, db: AsyncSession = Depends(get_db)):
    """启用/禁用某条案例（禁用后不再注入 prompt、不再作来源标注）"""
    from app.models.project import AuditCase
    result = await db.execute(select(AuditCase).where(AuditCase.id == case_id))
    case = result.scalar_one_or_none()
    if not case:
        raise HTTPException(404, "案例不存在")
    case.enabled = not case.enabled
    await db.commit()
    return {"id": case_id, "enabled": case.enabled}


@router.get("/audit-stats")
async def audit_stats(db: AsyncSession = Depends(get_db)):
    """审核统计：工单/通过率/问题分布/误报率"""
    from app.models.project import AuditIssue
    from sqlalchemy import func

    result = await db.execute(select(Project).where(Project.deleted_by_user == False))
    projects = result.scalars().all()
    by_status: dict[str, int] = {}
    by_domain: dict[str, int] = {}
    for p in projects:
        by_status[p.status] = by_status.get(p.status, 0) + 1
        by_domain[p.audit_domain] = by_domain.get(p.audit_domain, 0) + 1

    result = await db.execute(select(AuditIssue))
    issues = result.scalars().all()
    by_sev = {"P0": 0, "P1": 0, "P2": 0}
    by_cat: dict[str, int] = {}
    for i in issues:
        by_sev[i.severity] = by_sev.get(i.severity, 0) + 1
        by_cat[i.category] = by_cat.get(i.category, 0) + 1

    completed = [p for p in projects if p.status == "completed"]
    passed = [p for p in completed if (p.issues or {}).get("P0", 0) == 0]
    fb_total = sum(1 for i in issues if i.feedback)
    fb_fp = sum(1 for i in issues if i.feedback == "false_positive")

    return {
        "projects_total": len(projects),
        "by_status": by_status,
        "by_domain": by_domain,
        "completed": len(completed),
        "pass_rate": round(len(passed) / len(completed) * 100, 1) if completed else None,
        "issues_total": len(issues),
        "by_severity": by_sev,
        "by_category": dict(sorted(by_cat.items(), key=lambda x: x[1], reverse=True)[:12]),
        "feedback_total": fb_total,
        "false_positive_rate": round(fb_fp / fb_total * 100, 1) if fb_total else None,
    }


@router.get("/verify")
async def verify_admin_token(x_admin_token: str = Header(default="")):
    """口令有效性探测（前端导航解锁用）。无口令/错口令返回 401。"""
    from app.api.deps import verify_token
    if not verify_token(x_admin_token):
        raise HTTPException(status_code=401, detail="invalid")
    return {"ok": True}


class TokenBody(BaseModel):
    token: str = ""


@router.post("/token", dependencies=[Depends(require_admin)])
async def change_admin_token(body: TokenBody):
    """修改管理员口令（写回 admin_token.txt，即时生效）"""
    from app.api.deps import set_admin_token
    try:
        set_admin_token(body.token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"message": "口令已更新"}
