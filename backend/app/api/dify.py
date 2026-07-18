"""Dify 工作流集成 API

为 Dify 审核工作流提供后端数据查询接口：
- 系数查询（供步骤5/7 HTTP节点使用）
- 危废查询（供步骤8 HTTP节点使用）
- 审核结果接收（Dify工作流完成后回传）"""

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import DIFY_API_URL, DIFY_API_KEY

router = APIRouter(prefix="/api/dify", tags=["dify"])


@router.get("/coefficients")
async def dify_coefficients(fuel_type: str = "", pollutant: str = "", process: str = ""):
    """供 Dify HTTP 节点查询产污系数"""
    from app.engine.coefficient_db import query_coefficient
    results = await query_coefficient(fuel_type=fuel_type, pollutant=pollutant, process=process)
    return {"coefficients": results, "count": len(results)}


@router.get("/waste-codes")
async def dify_waste_codes(keyword: str = ""):
    """供 Dify HTTP 节点查询危废代码"""
    from app.engine.coefficient_db import query_waste
    results = await query_waste(name=keyword)
    return {"wastes": results, "count": len(results)}


@router.get("/search")
async def dify_knowledge_search(q: str = "", top_k: int = 5):
    """供 Dify HTTP 节点查询知识库"""
    from app.knowledge.retriever import search_knowledge
    results = search_knowledge(q, top_k=top_k)
    return {"results": results, "query": q}


class DifyResultBody(BaseModel):
    project_id: str = ""
    issues: list[dict] = []
    agent_review: str = ""
    summary: dict = {}


@router.post("/submit")
async def dify_submit_result(body: DifyResultBody):
    """Dify 工作流完成后回传审核结果"""
    from app.database import async_session
    from app.models.project import Project
    from datetime import datetime

    pid = body.project_id
    if not pid:
        raise HTTPException(status_code=400, detail="缺少 project_id")

    async with async_session() as db:
        from sqlalchemy import select
        r = await db.execute(select(Project).where(Project.id == pid))
        project = r.scalar_one_or_none()
        if not project:
            raise HTTPException(status_code=404, detail="项目不存在")

        all_issues = body.issues
        from app.engine.pipeline import _generate_report
        from app.engine.grader import grade_issues
        from app.engine.extractor import extract_text
        import re
        text_data = extract_text(project.file_path)
        full_text = text_data.get("full_text", "")
        standards = list(set(re.findall(r"(?:GB|GB/T|HJ|HJ/T|环发|环办|国环规)\s*[\d.\-—]+(?:\s*[—\-]\s*\d{4})?", full_text)))[:20]
        graded = grade_issues(all_issues)
        report_html = _generate_report(project.name, graded, full_text, standards)
        from app.config import UPLOAD_DIR
        report_path = UPLOAD_DIR / f"report_{pid}.html"
        report_path.write_text(report_html, encoding="utf-8")

        project.status = "completed"
        project.progress = 100
        project.step = "审核完成（Dify）"
        project.issues = {"P0": len(graded.get("P0", [])), "P1": len(graded.get("P1", [])), "P2": len(graded.get("P2", []))}
        project.report_path = str(report_path)
        project.logs = (project.logs or []) + [{"time": datetime.now().strftime("%H:%M:%S"), "message": f"Dify审核完成，共 {len(all_issues)} 个问题", "type": "success"}]
        await db.commit()

    return {"message": "审核结果已接收", "project_id": pid}
