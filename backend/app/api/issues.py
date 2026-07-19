from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.project import AuditIssue

router = APIRouter(prefix="/api", tags=["issues"])

VALID_FEEDBACK = {"accurate", "false_positive", "missed", "adjust", ""}


class FeedbackBody(BaseModel):
    feedback: str = ""
    note: str = ""


@router.post("/issues/{issue_id}/feedback")
async def submit_feedback(issue_id: str, body: FeedbackBody, db: AsyncSession = Depends(get_db)):
    if body.feedback not in VALID_FEEDBACK:
        raise HTTPException(status_code=400, detail="无效的反馈类型")
    result = await db.execute(select(AuditIssue).where(AuditIssue.id == issue_id))
    issue = result.scalar_one_or_none()
    if not issue:
        raise HTTPException(status_code=404, detail="问题不存在")
    issue.feedback = body.feedback
    issue.feedback_note = body.note[:2000]
    await db.commit()
    return {"message": "反馈已记录", "issue": issue.to_dict()}


@router.get("/projects/{project_id}/issues")
async def list_project_issues(project_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AuditIssue).where(AuditIssue.project_id == project_id)
    )
    issues = result.scalars().all()
    order = {"P0": 0, "P1": 1, "P2": 2}
    items = sorted([i.to_dict() for i in issues], key=lambda x: (order.get(x["severity"], 3), x["rule_id"]))
    return {"issues": items}


@router.get("/feedback/rule/{rule_id}")
async def rule_feedback_summary(rule_id: str, db: AsyncSession = Depends(get_db)):
    """供审核 prompt 注入：某规则的历史反馈统计"""
    result = await db.execute(select(AuditIssue).where(AuditIssue.rule_id == rule_id))
    issues = result.scalars().all()
    total = len(issues)
    fp = [i for i in issues if i.feedback == "false_positive"]
    accurate = [i for i in issues if i.feedback == "accurate"]
    notes = [i.feedback_note for i in fp[-5:] if i.feedback_note]
    return {
        "rule_id": rule_id,
        "total": total,
        "accurate": len(accurate),
        "false_positive": len(fp),
        "false_positive_notes": notes,
    }
