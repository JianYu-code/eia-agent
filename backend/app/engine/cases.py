"""历史审核案例库（O2）— 准确判定聚类凝练为案例卡，few-shot 注入审核 prompt"""
import re

CASE_MIN_ACCURATE = 2


def _norm_title(title: str) -> str:
    return re.sub(r"[\s，。、：:（）()【】]", "", title or "")[:24]


async def get_cases_for_rule(rule_id: str, limit: int = 3) -> list[dict]:
    """取规则下启用的案例卡（按准确次数降序）；无表/无数据返回 []"""
    try:
        from app.database import async_session
        from app.models.project import AuditCase
        from sqlalchemy import select
        async with async_session() as db:
            r = await db.execute(
                select(AuditCase)
                .where(AuditCase.rule_id == rule_id, AuditCase.enabled == True)  # noqa: E712
                .order_by(AuditCase.accurate_count.desc())
                .limit(limit))
            return [c.to_dict() for c in r.scalars().all()]
    except Exception:
        return []


async def find_case_source(rule_id: str, title: str) -> dict | None:
    """匹配问题来源案例：同 rule_id 且归一化标题前缀一致 → 返回案例（用于报告来源标注）"""
    cases = await get_cases_for_rule(rule_id, limit=20)
    nt = _norm_title(title)
    for c in cases:
        if nt and (_norm_title(c["title_pattern"])[:12] == nt[:12]):
            return c
    return cases[0] if cases else None


async def match_case_sources(issues: list[dict]) -> None:
    """批量为问题标注来源（单次查询）：命中案例 → '历史案例【类别】（N 次准确）'，否则 → '规则 R-XXX'"""
    by_rule: dict[str, list[dict]] = {}
    try:
        from app.database import async_session
        from app.models.project import AuditCase
        from sqlalchemy import select
        async with async_session() as db:
            r = await db.execute(select(AuditCase).where(AuditCase.enabled == True))  # noqa: E712
            for c in r.scalars().all():
                by_rule.setdefault(c.rule_id, []).append(c.to_dict())
    except Exception:
        by_rule = {}
    for iss in issues:
        rid = iss.get("rule_id", "")
        nt = _norm_title(iss.get("title", ""))
        hit = None
        for c in by_rule.get(rid, []):
            if nt and _norm_title(c["title_pattern"])[:12] == nt[:12]:
                hit = c
                break
        if hit:
            iss["case_source"] = f"历史案例【{hit['category'] or rid}】（{hit['accurate_count']} 次准确）"
        else:
            iss["case_source"] = f"规则 {rid}" if rid else ""


async def rebuild_cases(llm_call=None, min_accurate: int = CASE_MIN_ACCURATE) -> dict:
    """从 feedback=accurate 的 AuditIssue 聚类生成/更新案例卡。
    同 (rule_id, 归一化标题) 准确数 ≥ min_accurate 成案；LLM 可用时凝练判定要点。"""
    from app.database import async_session
    from app.models.project import AuditIssue, AuditCase
    from sqlalchemy import select

    async with async_session() as db:
        r = await db.execute(select(AuditIssue).where(AuditIssue.feedback == "accurate"))
        issues = r.scalars().all()

    groups: dict[tuple, list] = {}
    for i in issues:
        groups.setdefault((i.rule_id, _norm_title(i.title)), []).append(i)
    groups = {k: v for k, v in groups.items() if len(v) >= min_accurate}

    created = updated = 0
    async with async_session() as db:
        for (rule_id, ntitle), items in groups.items():
            rep = items[0]
            findings = [i.finding[:200] for i in items[:5] if i.finding]
            suggestions = [i.suggestion[:120] for i in items[:3] if i.suggestion]
            key_points = ""
            if llm_call and findings:
                try:
                    prompt = (
                        "以下是同一类环评审核问题的多条准确判定记录。请凝练一张案例卡，输出JSON（不要带```标记）：\n"
                        '{"key_points":"判定要点（何时应报此问题，60字内）","typical_finding":"典型问题情形（80字内）"}\n'
                        "只输出JSON。\n\n记录：\n" + "\n".join(f"- {f}" for f in findings))
                    from app.engine.llm_json import parse_llm_json
                    data = parse_llm_json(await llm_call(prompt), expect="object") or {}
                    key_points = data.get("key_points", "")
                    typical = data.get("typical_finding", "")
                except Exception:
                    key_points, typical = "", ""
            if not key_points:
                key_points = (suggestions[0] if suggestions else findings[0][:100] if findings else "")
                typical = findings[0] if findings else rep.title

            r = await db.execute(
                select(AuditCase).where(AuditCase.rule_id == rule_id,
                                        AuditCase.title_pattern == rep.title))
            case = r.scalar_one_or_none()
            if case:
                case.accurate_count = len(items)
                case.typical_finding = typical[:1900]
                case.key_points = key_points[:1900]
                case.category = rep.category
                updated += 1
            else:
                db.add(AuditCase(rule_id=rule_id, category=rep.category,
                                 title_pattern=rep.title, typical_finding=typical[:1900],
                                 key_points=key_points[:1900], accurate_count=len(items)))
                created += 1
        await db.commit()
    return {"created": created, "updated": updated, "groups": len(groups)}
