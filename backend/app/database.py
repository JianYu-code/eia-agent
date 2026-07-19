from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.config import DATABASE_URL

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    from app.models import project  # noqa: F401  注册全部模型
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _migrate()


async def _migrate():
    """轻量迁移：为既有 SQLite 库补列"""
    from sqlalchemy import text
    async with engine.begin() as conn:
        rows = await conn.execute(text("PRAGMA table_info(projects)"))
        cols = {r[1] for r in rows.fetchall()}
        if "result_summary" not in cols:
            await conn.execute(text("ALTER TABLE projects ADD COLUMN result_summary JSON"))
