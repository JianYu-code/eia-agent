import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.api.deps import require_admin
from app.knowledge.retriever import search_knowledge, get_outdated_documents
from app.knowledge.rag import ask_knowledge

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class AskBody(BaseModel):
    question: str
    history: list[dict] = []


class RemoveBody(BaseModel):
    sources: list[str]


@router.get("/search")
async def search(q: str):
    results = search_knowledge(q, top_k=10)
    return {"results": results, "query": q}


@router.post("/ask")
async def ask(body: AskBody):
    answer, sources = await ask_knowledge(body.question, body.history)
    return {
        "answer": answer,
        "sources": sources,
        "mode": "rag",
    }


@router.get("/stats")
async def stats():
    from app.knowledge.retriever import table_count
    from app.knowledge.retriever import get_source_index
    from app.config import LANCE_TABLE, OLLAMA_EMBED_MODEL
    source_idx = get_source_index()
    return {
        "name": LANCE_TABLE,
        "count": table_count(),
        "files": len(source_idx),
        "embedding_model": OLLAMA_EMBED_MODEL,
    }


@router.post("/sync", dependencies=[Depends(require_admin)])
async def sync_knowledge(background_tasks: BackgroundTasks):
    script = Path(__file__).resolve().parent.parent.parent.parent / "scripts" / "ingest_mineru.py"
    background_tasks.add_task(subprocess.run, [sys.executable, str(script), "sync"], capture_output=True, text=True)
    return {"message": "增量同步已在后台启动。系统将只处理新增和更新的文件，已删除的文件将标记为废止。"}


@router.post("/detect-obsolete", dependencies=[Depends(require_admin)])
async def detect_obsolete(background_tasks: BackgroundTasks):
    script = Path(__file__).resolve().parent.parent.parent.parent / "scripts" / "ingest_mineru.py"
    background_tasks.add_task(subprocess.run, [sys.executable, str(script), "detect-obsolete"], capture_output=True, text=True)
    return {"message": "废止检测已在后台启动。系统将扫描文件名和内容中的废止/替代关系。"}


@router.get("/outdated")
async def outdated_documents():
    docs = get_outdated_documents()
    return {"documents": docs, "count": len(docs)}


@router.post("/remove", dependencies=[Depends(require_admin)])
async def remove_knowledge(body: RemoveBody):
    script = Path(__file__).resolve().parent.parent.parent.parent / "scripts" / "ingest_mineru.py"
    result = subprocess.run(
        [sys.executable, str(script), "remove", "--sources"] + body.sources,
        capture_output=True, text=True, timeout=60
    )
    return {"message": result.stdout.strip() or "完成", "sources": body.sources}


# ═══ K-Hub 知识入库（对标 Obsidian 模式）═══

class HubSettingsBody(BaseModel):
    inbox_dir: str = ""
    vault_dir: str = ""


@router.get("/health", dependencies=[Depends(require_admin)])
async def knowledge_health():
    """向量化服务 + 索引健康灯"""
    from app.knowledge.retriever import check_embed_health, table_count
    embed = check_embed_health()
    return {
        "embed": embed,
        "lance_rows": table_count(),
        "ok": embed["available"],
    }


@router.get("/hub-settings", dependencies=[Depends(require_admin)])
async def get_hub_settings():
    from app.knowledge.organizer import get_inbox_dir, get_vault_dir
    return {
        "inbox_dir": str(await get_inbox_dir()),
        "vault_dir": str(await get_vault_dir()),
    }


@router.post("/hub-settings", dependencies=[Depends(require_admin)])
async def save_hub_settings(body: HubSettingsBody):
    from app.knowledge.organizer import set_setting, get_inbox_dir, get_vault_dir
    if body.inbox_dir:
        Path(body.inbox_dir).mkdir(parents=True, exist_ok=True)
        await set_setting("knowledge_inbox_dir", body.inbox_dir)
    if body.vault_dir:
        Path(body.vault_dir).mkdir(parents=True, exist_ok=True)
        await set_setting("knowledge_vault_dir", body.vault_dir)
    return {
        "inbox_dir": str(await get_inbox_dir()),
        "vault_dir": str(await get_vault_dir()),
    }


@router.post("/organize", dependencies=[Depends(require_admin)])
async def organize_now(with_mineru: bool = True):
    import asyncio
    from app.knowledge.organizer import get_setting, organize
    st = await get_setting("organize_status", {})
    if st.get("phase") in ("scanning", "converting", "mineru"):
        return {"message": "整理任务正在进行中", "status": st}
    asyncio.create_task(organize(with_mineru=with_mineru))
    return {"message": "整理任务已启动"}


@router.post("/reindex", dependencies=[Depends(require_admin)])
async def reindex_now():
    import asyncio
    from app.knowledge.organizer import get_setting, set_setting
    from app.knowledge.reindex import reindex_vault
    st = await get_setting("reindex_status", {})
    if st.get("phase") in ("clearing", "indexing"):
        return {"message": "重建索引正在进行中", "status": st}

    async def _run():
        try:
            await reindex_vault()
        except Exception as e:
            await set_setting("reindex_status", {"phase": "error", "error": str(e)[:200]})

    asyncio.create_task(_run())
    return {"message": "重建索引已启动"}


@router.get("/organize-status", dependencies=[Depends(require_admin)])
async def organize_status():
    from app.knowledge.organizer import get_setting
    return {
        "organize": await get_setting("organize_status", {}),
        "reindex": await get_setting("reindex_status", {}),
    }


@router.get("/files", dependencies=[Depends(require_admin)])
async def list_hub_files(category: str = "", db: AsyncSession = Depends(get_db)):
    from app.models.project import KnowledgeFile
    q = select(KnowledgeFile).where(KnowledgeFile.status == "active").order_by(KnowledgeFile.updated_at.desc())
    if category:
        q = select(KnowledgeFile).where(
            KnowledgeFile.status == "active", KnowledgeFile.category == category
        ).order_by(KnowledgeFile.updated_at.desc())
    result = await db.execute(q.limit(500))
    files = result.scalars().all()
    return {"files": [f.to_dict() for f in files]}


@router.delete("/files/{file_id}", dependencies=[Depends(require_admin)])
async def delete_hub_file(file_id: str, db: AsyncSession = Depends(get_db)):
    from app.models.project import KnowledgeFile
    from app.knowledge.reindex import delete_vault_file
    from app.knowledge.organizer import get_vault_dir
    result = await db.execute(select(KnowledgeFile).where(KnowledgeFile.id == file_id))
    kf = result.scalar_one_or_none()
    if not kf:
        raise HTTPException(status_code=404, detail="文件不存在")

    vault = await get_vault_dir()
    md_path = vault / kf.vault_path
    if md_path.exists():
        md_path.unlink()
    removed_chunks = await delete_vault_file(kf.vault_path)
    kf.status = "deleted"
    await db.commit()
    return {"message": f"已删除（索引行 {removed_chunks} 条）"}
