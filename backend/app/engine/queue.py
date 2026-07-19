"""统一顺序审核队列 — 单 worker 逐个执行，避免并发打爆 LLM API；支持真停止"""
import asyncio
from datetime import datetime

_queue: asyncio.Queue | None = None
_worker_task: asyncio.Task | None = None
_running_project_id: str | None = None


def _log(msg: str, t: str = "info") -> dict:
    return {"time": datetime.now().strftime("%H:%M:%S"), "message": msg, "type": t}


async def _update(project_id: str, **fields):
    from app.database import async_session
    from app.models.project import Project
    from sqlalchemy import select
    async with async_session() as db:
        result = await db.execute(select(Project).where(Project.id == project_id))
        p = result.scalar_one_or_none()
        if p:
            for k, v in fields.items():
                if k == "append_log":
                    p.logs = (p.logs or []) + [v]
                else:
                    setattr(p, k, v)
            await db.commit()


async def enqueue(project_id: str) -> int:
    """项目入队，返回队列位置（1=立即执行）"""
    global _queue, _worker_task
    if _queue is None:
        _queue = asyncio.Queue()
    await _update(
        project_id,
        status="queued",
        step="排队中",
        append_log=_log("已加入审核队列，等待执行", "step"),
    )
    await _queue.put(project_id)
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_worker())
    return _queue.qsize()


async def cancel(project_id: str) -> str:
    """停止任务：排队中→直接取消；运行中→置 stopped 由 pipeline 在下一步中断"""
    global _running_project_id
    if _running_project_id == project_id:
        await _update(project_id, status="stopped", step="已暂停",
                      append_log=_log("用户暂停审核，将在当前步骤完成后停止", "step"))
        return "stopping"
    if _queue is not None:
        kept = []
        removed = False
        while not _queue.empty():
            try:
                pid = _queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if pid == project_id:
                removed = True
            else:
                kept.append(pid)
        for pid in kept:
            _queue.put_nowait(pid)
        if removed:
            await _update(project_id, status="stopped", step="已取消排队",
                          append_log=_log("已取消排队", "step"))
            return "dequeued"
    await _update(project_id, status="stopped", step="已暂停")
    return "stopped"


async def is_stopped(project_id: str) -> bool:
    from app.database import async_session
    from app.models.project import Project
    from sqlalchemy import select
    async with async_session() as db:
        result = await db.execute(select(Project.status).where(Project.id == project_id))
        row = result.first()
        return bool(row and row[0] == "stopped")


async def _worker():
    global _running_project_id
    from app.engine.pipeline import run_audit_pipeline
    while True:
        try:
            project_id = await asyncio.wait_for(_queue.get(), timeout=5.0)
        except asyncio.TimeoutError:
            return
        if await is_stopped(project_id):
            continue
        _running_project_id = project_id
        await _update(project_id, status="running", step="启动审核...",
                      append_log=_log("审核任务开始执行", "step"))
        try:
            await run_audit_pipeline(project_id)
        except Exception as e:
            await _update(project_id, status="failed", step=f"失败: {str(e)[:100]}",
                          append_log=_log(str(e), "error"))
        finally:
            _running_project_id = None
