"""生成稿自审核修复回路 — 按问题定位章节 → LLM 修订 → ≤2 轮"""
import asyncio

from app.llm.client import chat, get_active_profile
from app.engine.kfiles import build_kfiles_context

MAX_ROUNDS = 2
REPAIR_SEV = {"P0", "P1"}

STEP_TO_KFILES = {
    "源强": ["K04", "K05"], "措施": ["K06"], "标准": ["K01"],
    "危废": ["K12"], "自洽": ["K07"], "预测": ["K11"], "监测": ["K10"],
    "总量": ["K13"], "验收": ["K16"],
}


def _kfiles_for_issue(issue: dict) -> str:
    for kw, kids in STEP_TO_KFILES.items():
        if kw in (issue.get("step", "") + issue.get("category", "")):
            return build_kfiles_context(kids, max_chars=3000)
    return ""


def _find_chapter(chapters: list[dict], issue: dict) -> dict | None:
    """按 issue 的 chapter 字段或标题关键词定位生成章节"""
    target = (issue.get("chapter") or "").strip()
    if target:
        for ch in chapters:
            if target in ch.get("title", "") or ch.get("title", "") in target:
                return ch
    ev = issue.get("evidence_location") or ""
    if len(ev) >= 8:
        for ch in chapters:
            if ev[:30] in ch.get("content", ""):
                return ch
    return None


async def repair_chapter(chapter: dict, issues: list[dict], profile) -> str:
    """针对问题清单修订单个章节，返回修订后内容"""
    issue_text = "\n".join([
        f"{i+1}. [{iss.get('severity')}] {iss.get('title')}：{iss.get('finding', '')}（建议：{iss.get('suggestion', '')}）"
        for i, iss in enumerate(issues[:6])
    ])
    kfiles_ctx = _kfiles_for_issue(issues[0]) if issues else ""
    kf_section = f"\n写作规范：\n{kfiles_ctx}\n" if kfiles_ctx else ""

    prompt = f"""你是环评工程师。以下章节在自审核中发现问题，请**针对性修订**，保留其余合格内容与整体结构。

章节标题：{chapter.get('title', '')}

发现的问题：
{issue_text}
{kf_section}
章节原文：
{chapter.get('content', '')[:12000]}

要求：
1. 只修订与上述问题相关的内容，其余部分原样保留
2. 引用标准须带年号且不编造
3. 缺真实数据仍用【待补：xxx】占位
4. 直接输出修订后的完整章节正文，不要解释"""

    revised = await chat(prompt, profile=profile)
    return revised.strip()


async def repair_draft(chapters: list[dict], issues: list[dict], log_cb=None, round_no: int = 1) -> tuple[list[dict], list[str]]:
    """一轮修复：按章节聚合问题 → 并发修复 → 返回 (新章节, 修复日志)"""
    profile = await get_active_profile()
    if not profile:
        raise RuntimeError("未配置启用的 LLM Profile")

    targets = [i for i in issues if i.get("severity") in REPAIR_SEV]
    by_chapter: dict[int, list[dict]] = {}
    for iss in targets:
        ch = _find_chapter(chapters, iss)
        if ch is not None:
            by_chapter.setdefault(id(ch), []).append(iss)

    if not by_chapter:
        return chapters, ["无需要修复的问题"]

    repair_log = []
    tasks = {}
    for ch in chapters:
        if id(ch) in by_chapter:
            iss_list = by_chapter[id(ch)]
            tasks[ch["id"]] = asyncio.create_task(repair_chapter(ch, iss_list, profile))
            repair_log.append(f"第{round_no}轮：修复「{ch['title']}」（{len(iss_list)} 个问题）")

    new_chapters = []
    for ch in chapters:
        if ch["id"] in tasks:
            try:
                new_content = await tasks[ch["id"]]
                new_chapters.append({**ch, "content": new_content})
            except Exception as e:
                repair_log.append(f"「{ch['title']}」修复失败：{str(e)[:60]}，保留原文")
                new_chapters.append(ch)
        else:
            new_chapters.append(ch)

    if log_cb:
        for msg in repair_log:
            await log_cb(msg)
    return new_chapters, repair_log
