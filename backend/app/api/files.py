import os
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.project import FileIndex

router = APIRouter(prefix="/api/files", tags=["files"])


@router.get("")
async def list_files(
    q: str = Query(default=""),
    category: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=30, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(FileIndex)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(or_(FileIndex.title.ilike(pattern), FileIndex.original_name.ilike(pattern), FileIndex.standard_id.ilike(pattern)))
    if category:
        stmt = stmt.where(FileIndex.category.ilike(f"%{category}%"))

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = stmt.order_by(FileIndex.title).offset((page - 1) * limit).limit(limit)
    result = await db.execute(stmt)
    files = result.scalars().all()

    return {
        "files": [f.to_dict() for f in files],
        "total": total,
        "page": page,
        "limit": limit,
    }


@router.get("/{file_id}")
async def get_file(file_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(FileIndex).where(FileIndex.id == file_id))
    f = result.scalar_one_or_none()
    if not f:
        raise HTTPException(status_code=404, detail="文件不存在")
    return f.to_dict()


@router.get("/{file_id}/view")
async def view_file(file_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(FileIndex).where(FileIndex.id == file_id))
    f = result.scalar_one_or_none()
    if not f:
        raise HTTPException(status_code=404, detail="文件不存在")
    if not os.path.exists(f.file_path):
        raise HTTPException(status_code=404, detail="源文件丢失")
    return FileResponse(f.file_path, filename=f.original_name)


@router.get("/stats/categories")
async def get_categories(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(FileIndex.category, func.count()).group_by(FileIndex.category).order_by(FileIndex.category))
    rows = result.all()
    return [{"category": row[0], "count": row[1]} for row in rows]
