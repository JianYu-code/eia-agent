"""术语与敏感词规范检查（O4）— terminology.yaml 词表确定性扫描，命中即 P2"""
from pathlib import Path

import yaml

from app.engine.grader import build_issue

TERMS_FILE = Path(__file__).resolve().parent.parent.parent.parent / "rules" / "terminology.yaml"

APPLICABLE_DOMAINS = {"eia", "acceptance"}

_terms_cache: list[dict] | None = None


def _load_terms() -> list[dict]:
    global _terms_cache
    if _terms_cache is None:
        if TERMS_FILE.exists():
            with open(TERMS_FILE, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            _terms_cache = data if isinstance(data, list) else []
        else:
            _terms_cache = []
    return _terms_cache


async def check_terminology(text_data: dict, audit_ctx: dict | None = None) -> list[dict]:
    if audit_ctx and audit_ctx.get("domain", "eia") not in APPLICABLE_DOMAINS:
        return []
    full_text = text_data.get("full_text", "")
    chapters = text_data.get("chapters", []) or []
    title_lines = "\n".join(c.get("title", "") for c in chapters)

    issues = []
    for term in _load_terms():
        scope = term.get("scope", "full")
        haystack = title_lines if scope == "title" else full_text
        hits = [w for w in term.get("wrong", []) if w and w in haystack]
        if not hits:
            continue
        locations = []
        for ch in chapters:
            content = ch.get("title", "") if scope == "title" else ch.get("content", "")
            if any(w in content for w in hits):
                locations.append(ch.get("title", ""))
        correct = term.get("correct", "")
        issues.append(build_issue(
            "R-TERM-001", "P2", "术语规范",
            f"术语/表述不规范：{('、'.join(f'「{w}」' for w in hits))}",
            f"报告{'章节标题' if scope == 'title' else '正文'}中出现 {('、'.join(f'「{w}」' for w in hits))}。"
            f"{term.get('note', '')}。"
            + (f"建议改为：{correct}。" if correct else "建议删除或改写该表述。"),
            evidence=f"「{hits[0]}」",
            law_ref="HJ 2.1-2016 报告编制规范性要求",
            suggestion=(f"将{('、'.join(f'「{w}」' for w in hits))}规范为：{correct}。" if correct
                        else term.get("note", "请规范表述。"))
        ))
        issues[-1]["chapter"] = "、".join(locations[:3])
        issues[-1]["evidence_location"] = hits[0]
    return issues
