import sys
import re
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import asyncio
from sqlalchemy import select
from app.database import async_session, engine, Base
from app.models.project import FileIndex

XIAOZHI_DIR = r"C:\Users\haobo\环保小智_文档下载"
STD_PATTERN = re.compile(r"(?:GB|GB/T|HJ|HJ/T|环发|环办|国环规)\s*[\d.\-—]+(?:\s*[—\-]\s*\d{4})?")
OBSOLETE_KW = ["废止", "作废", "失效", "已被替代", "已被代替", "（废止）", "（作废）"]

REPLACEMENT_PATTERNS = [
    re.compile(r"(?:本标准|本文件|本导则|本规范)(?:代替|替代|代替了|替代了)\s*(GB(?:/T)?|HJ(?:/T)?)\s*[\d.\-—]+(?:\s*[—\-]\s*\d{4})?"),
    re.compile(r"(?:代替|替代|取代|代替了|替代了)(?:了?\s*)(GB(?:/T)?|HJ(?:/T)?)\s*[\d.\-—]+(?:\s*[—\-]\s*\d{4})?"),
    re.compile(r"自\s*(?:本(?:标准|文件|导则|规范))?\s*(?:实施|执行|施行)之日\s*(?:起)?\s*[,，]?\s*(GB(?:/T)?|HJ(?:/T)?)\s*[\d.\-—]+(?:\s*[—\-]\s*\d{4})?\s*(?:同时)?\s*废止"),
]


def _guess_std_id(name: str) -> str:
    m = STD_PATTERN.search(name)
    return m.group().strip() if m else ""


def _norm_std_id(sid: str) -> str:
    """标准编号归一化：去空格、统一破折号、大写（与 file_index.standard_id 存储格式一致）"""
    s = re.sub(r"\s+", "", (sid or "").strip().upper())
    return s.replace("—", "-").replace("–", "-").replace("－", "-")


def extract_info(file_path: Path) -> dict:
    parts = file_path.parent.parts
    category = ""
    for i, p in enumerate(parts):
        if p in ("文档", "报告"):
            category = "/".join(parts[i+1:])
            break
    if not category:
        category = "/".join(parts[-3:])

    title = file_path.stem
    deprecated = any(kw in title for kw in OBSOLETE_KW)

    return {
        "title": title,
        "original_name": file_path.name,
        "file_path": str(file_path),
        "file_type": file_path.suffix.lower().lstrip("."),
        "file_size": file_path.stat().st_size if file_path.exists() else 0,
        "category": category,
        "standard_id": _guess_std_id(file_path.name),
        "deprecated": deprecated,
        "replaced_by": "",
    }


async def full_scan():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    exts = {".pdf", ".docx", ".doc", ".xls", ".xlsx", ".txt"}
    root = Path(XIAOZHI_DIR)
    files = [f for ext in exts for f in root.rglob(f"*{ext}") if not f.name.startswith("~")]

    print(f"扫描到 {len(files)} 个文件")

    batch = []
    total = 0
    async with async_session() as db:
        existing = set((await db.execute(select(FileIndex.file_path))).scalars().all())

        for i, f in enumerate(files):
            path_str = str(f)
            if path_str in existing:
                continue

            info = extract_info(f)
            info["id"] = str(uuid.uuid4())
            batch.append(FileIndex(**info))
            total += 1

            if len(batch) >= 500:
                for b in batch:
                    db.add(b)
                await db.commit()
                print(f"  进度: {i+1}/{len(files)}, 新增 {total}")
                batch = []

            if (i + 1) % 5000 == 0:
                print(f"  进度: {i+1}/{len(files)}")

        if batch:
            for b in batch:
                db.add(b)
            await db.commit()

    print(f"\n扫描完成: 新增 {total} 条记录")


async def detect_obsolete():
    """深度检测: 读 PDF/DOCX 前几页内容, 匹配替代关系"""
    print("=== 深度废止检测（读取文件内容） ===")

    async with async_session() as db:
        result = await db.execute(select(FileIndex).where(FileIndex.deprecated == False))
        files = result.scalars().all()
        print(f"待检测: {len(files)} 个现行文件")

        self_obsolete = 0
        replaced = 0
        rel_map = {}  # {被替代标准编号: 替代者编号} 无论目标是否在 file_index 中

        for i, f in enumerate(files):
            if not Path(f.file_path).exists():
                continue

            text = ""
            if f.file_type == "pdf":
                try:
                    import fitz
                    doc = fitz.open(f.file_path)
                    for page_idx in range(min(3, len(doc))):
                        text += doc[page_idx].get_text()
                    doc.close()
                except Exception:
                    continue
            elif f.file_type in ("docx", "doc"):
                try:
                    from docx import Document
                    doc = Document(f.file_path)
                    text = "\n".join(p.text for p in doc.paragraphs[:80])
                except Exception:
                    continue
            else:
                try:
                    text = Path(f.file_path).read_text(encoding="utf-8", errors="ignore")[:5000]
                except Exception:
                    continue

            head = text[:3000]

            # 自身废止检测
            if any(kw in head[:800] for kw in ["废止", "已废止", "作废"]):
                if not any(kw in f.title for kw in ["代替", "替代"]):
                    f.deprecated = True
                    self_obsolete += 1

            # 替代关系检测
            source_id = _norm_std_id(_guess_std_id(f.title))
            replaced_ids = set()
            for pattern in REPLACEMENT_PATTERNS:
                for match in pattern.finditer(head):
                    std_match = STD_PATTERN.search(match.group())
                    if std_match:
                        sid = _norm_std_id(std_match.group())
                        if sid and sid != source_id:
                            replaced_ids.add(sid)

            if replaced_ids:
                reason = f"{source_id} 代替了 {'、'.join(replaced_ids)}" if source_id else ""
                from sqlalchemy import func
                norm_col = func.replace(func.replace(func.replace(FileIndex.standard_id, " ", ""), "—", "-"), "–", "-")
                for target_id in replaced_ids:
                    if source_id:
                        rel_map[target_id] = source_id
                    target_result = await db.execute(
                        select(FileIndex).where(norm_col == target_id)
                    )
                    for tf in target_result.scalars().all():
                        tf.deprecated = True
                        tf.replaced_by = reason
                        replaced += 1
                        print(f"  [标记废止] {tf.title[:60]} → {reason}")

            if (i + 1) % 100 == 0:
                await db.commit()
                print(f"  进度: {i+1}/{len(files)}, 自身:{self_obsolete} 替代:{replaced}")

        await db.commit()

    if rel_map:
        import json as _json
        map_path = Path(__file__).resolve().parent.parent / "backend" / "data" / "replacement_map.json"
        existing = {}
        if map_path.exists():
            try:
                existing = _json.loads(map_path.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
        existing.update(rel_map)
        map_path.write_text(_json.dumps(existing, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"替代关系映射已保存 {len(existing)} 条 → {map_path}")

    print(f"\n深度检测完成: 自身废止 {self_obsolete}, 标记替代 {replaced}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("command", nargs="?", default="full", choices=["full", "detect"])
    args = parser.parse_args()

    if args.command == "detect":
        asyncio.run(detect_obsolete())
    else:
        asyncio.run(full_scan())
