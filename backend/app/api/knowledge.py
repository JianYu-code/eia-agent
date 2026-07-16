import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

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


@router.post("/sync")
async def sync_knowledge(background_tasks: BackgroundTasks):
    script = Path(__file__).resolve().parent.parent.parent.parent / "scripts" / "ingest_mineru.py"
    background_tasks.add_task(subprocess.run, [sys.executable, str(script), "sync"], capture_output=True, text=True)
    return {"message": "增量同步已在后台启动。系统将只处理新增和更新的文件，已删除的文件将标记为废止。"}


@router.post("/detect-obsolete")
async def detect_obsolete(background_tasks: BackgroundTasks):
    script = Path(__file__).resolve().parent.parent.parent.parent / "scripts" / "ingest_mineru.py"
    background_tasks.add_task(subprocess.run, [sys.executable, str(script), "detect-obsolete"], capture_output=True, text=True)
    return {"message": "废止检测已在后台启动。系统将扫描文件名和内容中的废止/替代关系。"}


@router.get("/outdated")
async def outdated_documents():
    docs = get_outdated_documents()
    return {"documents": docs, "count": len(docs)}


@router.post("/remove")
async def remove_knowledge(body: RemoveBody):
    script = Path(__file__).resolve().parent.parent.parent.parent / "scripts" / "ingest_mineru.py"
    result = subprocess.run(
        [sys.executable, str(script), "remove", "--sources"] + body.sources,
        capture_output=True, text=True, timeout=60
    )
    return {"message": result.stdout.strip() or "完成", "sources": body.sources}
