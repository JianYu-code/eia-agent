"""章节感知上下文路由 — 让每个审核步骤读到报告的相关章节，而非全文前 N 字符"""
import re

DEFAULT_BUDGET = 30000
FALLBACK_BUDGET = 12000


def route_chapters(chapters: list[dict], patterns: list[str], budget: int = DEFAULT_BUDGET) -> str:
    """按标题正则挑选相关章节，拼接全文，超 budget 时按段落截断"""
    if not chapters:
        return ""
    compiled = [re.compile(p) for p in patterns]
    picked = []
    for ch in chapters:
        title = ch.get("title", "")
        if any(p.search(title) for p in compiled):
            picked.append(ch)
    if not picked:
        return ""

    parts = []
    used = 0
    for ch in picked:
        header = f"\n\n## {ch.get('title', '')}\n"
        body = ch.get("content", "")
        remaining = budget - used - len(header)
        if remaining <= 200:
            break
        if len(body) > remaining:
            body = _truncate_by_paragraph(body, remaining)
        parts.append(header + body)
        used += len(header) + len(body)
    return "".join(parts).strip()


def build_step_context(text_data: dict, target_patterns: list[str] | None = None,
                       budget: int = DEFAULT_BUDGET) -> str:
    """构建单步审核的上下文：优先相关章节，无匹配回退全文摘要"""
    full_text = text_data.get("full_text", "") if isinstance(text_data, dict) else str(text_data)
    chapters = text_data.get("chapters", []) if isinstance(text_data, dict) else []

    if target_patterns and chapters:
        routed = route_chapters(chapters, target_patterns, budget)
        if len(routed) >= 500:
            return routed

    if len(full_text) <= budget:
        return full_text
    head = full_text[: budget // 2]
    tail = full_text[-budget // 4:]
    return head + "\n\n...[中间内容省略]...\n\n" + tail


def chapter_titles(text_data: dict) -> list[str]:
    chapters = text_data.get("chapters", []) if isinstance(text_data, dict) else []
    return [c.get("title", "") for c in chapters]


def segment_chapter(content: str, size: int = 15000, overlap: int = 500) -> list[str]:
    """超长章节按段落切段（map-reduce 逐章审核用）"""
    if len(content) <= size:
        return [content] if content.strip() else []
    segments = []
    start = 0
    while start < len(content):
        end = min(start + size, len(content))
        seg = content[start:end]
        if end < len(content):
            last_break = max(seg.rfind("\n\n"), seg.rfind("。"))
            if last_break > size * 0.5:
                seg = seg[:last_break + 1]
        segments.append(seg)
        if end >= len(content):
            break
        start += len(seg) - overlap
    return segments


def pick_target_chapters(text_data: dict, patterns: list[str],
                         max_chapters: int = 10, max_total_chars: int = 200000) -> list[dict]:
    """逐章审核：按标题正则挑选章节（带数量/总量上限）。无匹配返回 []。"""
    chapters = text_data.get("chapters", []) if isinstance(text_data, dict) else []
    if not chapters:
        return []
    compiled = [re.compile(p) for p in patterns]
    picked = [ch for ch in chapters
              if any(p.search(ch.get("title", "")) for p in compiled)
              and len(ch.get("content", "").strip()) > 50]
    picked.sort(key=lambda c: len(c.get("content", "")), reverse=True)
    picked = picked[:max_chapters]
    total = 0
    out = []
    for ch in picked:
        clen = len(ch.get("content", ""))
        if total + clen > max_total_chars and out:
            break
        out.append(ch)
        total += clen
    return out


def _truncate_by_paragraph(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    last_break = max(cut.rfind("\n\n"), cut.rfind("。"))
    if last_break > limit * 0.6:
        cut = cut[:last_break]
    return cut + "\n...[本章内容过长已截断]..."
