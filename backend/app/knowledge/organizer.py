"""K-Hub 知识整理器 — 投递文件夹 → MD 转换 → Obsidian 兼容 vault（双通道：extractor / MinerU）"""
import hashlib
import shutil
from datetime import datetime
from pathlib import Path

from app.config import KNOWLEDGE_INBOX_DIR, KNOWLEDGE_VAULT_DIR, MINERU_OUTPUT_DIR

SUPPORTED = {".md", ".txt", ".pdf", ".docx"}
MAX_FILE_BYTES = 50 * 1024 * 1024

CATEGORY_KEYWORDS = {
    "标准规范": ["GB", "HJ", "DB", "标准", "规范", "导则", "指南", "技术规定"],
    "政策法规": ["环发", "环办", "办法", "条例", "通知", "政策", "名录", "目录"],
    "案例报告": ["报告书", "报告表", "环评", "验收", "监测报告"],
    "技术资料": ["手册", "系数", "核算", "可行技术", "BAT"],
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


async def get_setting(key: str, default=None):
    from app.database import async_session
    from app.models.project import KVSetting
    from sqlalchemy import select
    async with async_session() as db:
        r = await db.execute(select(KVSetting).where(KVSetting.key == key))
        row = r.scalar_one_or_none()
        return row.value if row and row.value is not None else default


async def set_setting(key: str, value):
    from app.database import async_session
    from app.models.project import KVSetting
    from sqlalchemy import select
    async with async_session() as db:
        r = await db.execute(select(KVSetting).where(KVSetting.key == key))
        row = r.scalar_one_or_none()
        if row:
            row.value = value
        else:
            db.add(KVSetting(key=key, value=value))
        await db.commit()


async def get_inbox_dir() -> Path:
    v = await get_setting("knowledge_inbox_dir", KNOWLEDGE_INBOX_DIR)
    p = Path(v)
    p.mkdir(parents=True, exist_ok=True)
    return p


async def get_vault_dir() -> Path:
    v = await get_setting("knowledge_vault_dir", KNOWLEDGE_VAULT_DIR)
    p = Path(v)
    p.mkdir(parents=True, exist_ok=True)
    return p


def classify_category(name: str, text_head: str = "") -> str:
    joined = name + " " + text_head[:500]
    for cat, kws in CATEGORY_KEYWORDS.items():
        if any(kw in joined for kw in kws):
            return cat
    return "其他"


async def scan_inbox() -> list[dict]:
    """扫描投递文件夹，按 SHA256 三态分类：new / changed / duplicate"""
    from app.database import async_session
    from app.models.project import KnowledgeFile
    from sqlalchemy import select

    inbox = await get_inbox_dir()
    async with async_session() as db:
        r = await db.execute(select(KnowledgeFile))
        known = {kf.sha256: kf for kf in r.scalars().all()}
        known_paths = {kf.source_path: kf for kf in known.values()}

    items = []
    for fp in sorted(inbox.rglob("*")):
        if not fp.is_file() or fp.suffix.lower() not in SUPPORTED:
            continue
        if fp.stat().st_size > MAX_FILE_BYTES:
            items.append({"name": fp.name, "path": str(fp), "status": "too_large",
                          "size": fp.stat().st_size})
            continue
        digest = sha256_file(fp)
        rel = str(fp.relative_to(inbox))
        if digest in known:
            items.append({"name": fp.name, "path": str(fp), "rel": rel, "sha256": digest,
                          "status": "duplicate", "size": fp.stat().st_size})
        elif rel in known_paths:
            items.append({"name": fp.name, "path": str(fp), "rel": rel, "sha256": digest,
                          "status": "changed", "size": fp.stat().st_size,
                          "existing": known_paths[rel]})
        else:
            items.append({"name": fp.name, "path": str(fp), "rel": rel, "sha256": digest,
                          "status": "new", "size": fp.stat().st_size})
    return items


async def _llm_summary(name: str, text: str) -> str:
    try:
        from app.llm.client import chat, get_active_profile
        profile = await get_active_profile()
        if not profile:
            return ""
        prompt = f"""请用2-3句话概括以下文档的核心内容（文件名：{name}），指出其类别与适用范围。只输出概括文字，不要前缀。

{text[:2000]}"""
        s = (await chat(prompt, profile=profile)).strip()
        return s[:400]
    except Exception:
        return ""


def _fallback_summary(text: str) -> str:
    head = " ".join(text.strip().split())[:200]
    return head + ("…" if len(text.strip()) > 200 else "")


async def convert_file(item: dict, vault: Path) -> dict:
    """单文件转换：提取 → 摘要 → 写 vault MD（frontmatter）"""
    from app.engine.extractor import extract_text

    src = Path(item["path"])
    text_data = extract_text(str(src))
    text = text_data.get("full_text", "").strip()
    if len(text) < 20:
        raise ValueError("提取内容为空或过短")

    category = classify_category(src.name, text)
    summary = await _llm_summary(src.name, text) or _fallback_summary(text)

    cat_dir = vault / category
    cat_dir.mkdir(parents=True, exist_ok=True)
    out_name = src.stem + ".md"
    out_path = cat_dir / out_name
    if out_path.exists():
        out_path = cat_dir / f"{src.stem}-{item['sha256'][:8]}.md"

    frontmatter = f"""---
来源: {item.get('rel', src.name)}
原始文件: {src.name}
SHA256: {item['sha256']}
导入时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}
类别: {category}
转换通道: extractor
摘要: {summary}
---

# {src.stem}

> {summary}

"""
    out_path.write_text(frontmatter + text, encoding="utf-8")
    return {
        "vault_path": str(out_path.relative_to(vault)),
        "category": category,
        "summary": summary,
    }


async def _upsert_kfile(db, item: dict, conv: dict, channel: str = "extractor"):
    from app.models.project import KnowledgeFile
    from sqlalchemy import select

    r = await db.execute(select(KnowledgeFile).where(KnowledgeFile.sha256 == item["sha256"]))
    kf = r.scalar_one_or_none()
    if kf is None and item.get("existing"):
        kf = item["existing"]
        kf.sha256 = item["sha256"]
    if kf is None:
        kf = KnowledgeFile(sha256=item["sha256"])
        db.add(kf)
    kf.name = item["name"]
    kf.source_path = item.get("rel", item["name"])
    kf.vault_path = conv["vault_path"]
    kf.category = conv["category"]
    kf.size = item.get("size", 0)
    kf.summary = conv["summary"]
    kf.channel = channel
    kf.status = "active"
    kf.error = ""
    return kf


async def ingest_mineru_channel(vault: Path, report: dict):
    """MinerU 通道：输出目录中的新 MD 复制入 vault（保留分类路径）"""
    from app.database import async_session
    from app.models.project import KnowledgeFile
    from sqlalchemy import select

    mineru_root = Path(MINERU_OUTPUT_DIR)
    if not mineru_root.exists():
        report["mineru"] = {"status": "skip", "reason": "MinerU 输出目录不存在"}
        return

    async with async_session() as db:
        r = await db.execute(select(KnowledgeFile).where(KnowledgeFile.channel == "mineru"))
        known = {kf.sha256 for kf in r.scalars().all()}

        added = 0
        for md in mineru_root.rglob("*.md"):
            rel = md.relative_to(mineru_root)
            if len(rel.parts) < 2:
                continue
            category = rel.parts[0]
            try:
                digest = hashlib.sha256(md.read_bytes()).hexdigest()
            except Exception:
                continue
            if digest in known:
                continue
            text = md.read_text(encoding="utf-8", errors="ignore")
            if len(text.strip()) < 20:
                continue

            cat_dir = vault / "MinerU" / category
            cat_dir.mkdir(parents=True, exist_ok=True)
            out_path = cat_dir / md.name
            if out_path.exists():
                out_path = cat_dir / f"{md.stem}-{digest[:8]}.md"

            frontmatter = f"""---
来源: MinerU/{rel}
原始文件: {md.name}
SHA256: {digest}
导入时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}
类别: {category}
转换通道: mineru
---

"""
            out_path.write_text(frontmatter + text, encoding="utf-8")

            kf = KnowledgeFile(
                sha256=digest, name=md.name, source_path=f"MinerU/{rel}",
                vault_path=str(out_path.relative_to(vault)), category=category,
                size=md.stat().st_size, summary=_fallback_summary(text),
                channel="mineru", status="active",
            )
            db.add(kf)
            known.add(digest)
            added += 1
            if added % 200 == 0:
                await db.commit()
        await db.commit()
        report["mineru"] = {"status": "ok", "added": added}


async def organize(with_mineru: bool = True) -> dict:
    """整理主流程：扫描 → 转换 → 登记。返回处理报告。"""
    vault = await get_vault_dir()
    report = {"added": [], "updated": [], "skipped": [], "failed": []}

    await set_setting("organize_status", {"phase": "scanning", "done": 0, "total": 0})
    items = await scan_inbox()
    work = [i for i in items if i["status"] in ("new", "changed")]
    report["skipped"] = [i["name"] for i in items if i["status"] == "duplicate"]
    report["failed"] = [{"name": i["name"], "error": "文件超过 50MB"} for i in items if i["status"] == "too_large"]

    from app.database import async_session
    total = len(work)
    for idx, item in enumerate(work):
        await set_setting("organize_status", {"phase": "converting", "done": idx, "total": total,
                                              "current": item["name"]})
        try:
            conv = await convert_file(item, vault)
            async with async_session() as db:
                await _upsert_kfile(db, item, conv, channel="extractor")
                await db.commit()
            (report["updated"] if item["status"] == "changed" else report["added"]).append(item["name"])
        except Exception as e:
            report["failed"].append({"name": item["name"], "error": str(e)[:200]})

    if with_mineru:
        await set_setting("organize_status", {"phase": "mineru", "done": total, "total": total})
        await ingest_mineru_channel(vault, report)

    await set_setting("organize_status", {
        "phase": "done", "done": total, "total": total,
        "added": len(report["added"]), "updated": len(report["updated"]),
        "skipped": len(report["skipped"]), "failed": len(report["failed"]),
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    return report
