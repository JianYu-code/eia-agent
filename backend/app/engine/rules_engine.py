import yaml
import re
from pathlib import Path

RULES_DIR = Path(__file__).resolve().parent.parent.parent / "rules"


def load_rules(domain: str = "eia", report_type: str = "报告书") -> list[dict]:
    rules = []
    files = [f"{domain}_rules.yaml"]
    if report_type == "报告表":
        files.append(f"{domain}_rules_table.yaml")
    for fname in files:
        path = RULES_DIR / fname
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if isinstance(data, list):
                for rule in data:
                    rt = rule.get("report_type", "")
                    if rt and rt != report_type:
                        continue
                    rules.append(rule)
    return rules


def run_keyword_check(rule: dict, full_text: str, chapters: list[dict] | None = None) -> list[dict]:
    """关键词匹配检查，不用 LLM。章节必备项优先按章节标题匹配。"""
    config = rule.get("check_config", {})
    issues = []

    if "required_chapters" in config:
        titles = [c.get("title", "") for c in (chapters or [])]
        use_titles = bool(titles) and not (len(titles) == 1 and titles[0] == "全文")
        compact_text = full_text.replace(" ", "")
        for ch in config["required_chapters"]:
            variants = [ch, ch.replace(" ", ""), ch.replace("与", "")]
            if use_titles:
                found = any(v in t.replace(" ", "") for v in variants for t in titles)
            else:
                found = any(v in compact_text for v in variants)
            if not found:
                issues.append({
                    "rule_id": rule.get("rule_id", ""),
                    "severity": rule.get("severity", "P2"),
                    "category": rule.get("category", ""),
                    "title": f"报告中未找到'{ch}'相关章节",
                    "finding": f"根据 {rule.get('law_ref', '相关导则')} 要求，环评报告应包含 '{ch}'。经章节结构与全文检索，均未发现相关内容。",
                    "law_ref": rule.get("law_ref", ""),
                    "suggestion": f"请补充'{ch}'章节的具体内容，或说明不适用的理由。",
                })

    if "required_keywords" in config:
        for kw in config["required_keywords"]:
            if kw not in full_text:
                issues.append({
                    "rule_id": rule.get("rule_id", ""),
                    "severity": rule.get("severity", "P2"),
                    "category": rule.get("category", ""),
                    "title": f"未找到关键词'{kw}'",
                    "finding": f"报告中未包含 '{kw}' 相关内容。",
                    "law_ref": rule.get("law_ref", ""),
                    "suggestion": f"请补充'{kw}'相关内容。",
                })

    if "required_sections" in config:
        for sec in config["required_sections"]:
            if sec not in full_text:
                issues.append({
                    "rule_id": rule.get("rule_id", ""),
                    "severity": rule.get("severity", "P2"),
                    "category": rule.get("category", ""),
                    "title": f"缺少'{sec}'",
                    "finding": f"报告中未找到 '{sec}' 相关内容。",
                    "law_ref": rule.get("law_ref", ""),
                    "suggestion": f"请补充'{sec}'相关内容。",
                })

    return issues


def normalize_standard_id(raw: str) -> str:
    """标准编号归一化：去空格、破折号统一、前缀大写（与入库侧 ingest_mineru 保持一致）"""
    s = raw.strip().upper()
    s = re.sub(r"\s+", "", s)
    s = s.replace("—", "-").replace("–", "-").replace("－", "-")
    s = re.sub(r"^GB/T", "GB/T", s)
    return s


def _clean_standard_ids(pairs: list[tuple]) -> tuple[list[tuple], list[str]]:
    """截断编号清洗（D）：(-结尾/-199三位年号/有完整前缀的短年号) → 移入截断桶
    两位年号（-93=1993）合法不误杀。返回 (有效编号对, 截断编号列表)"""
    norms = {n for _, n in pairs}
    valid, truncated = [], []
    for raw, norm in pairs:
        if norm.endswith("-"):
            truncated.append(raw)
            continue
        tail = norm.rsplit("-", 1)[-1]
        if tail.isdigit() and len(tail) == 3:
            truncated.append(raw)
            continue
        if tail.isdigit() and len(tail) == 2 and any(n != norm and n.startswith(norm) for n in norms):
            truncated.append(raw)
            continue
        valid.append((raw, norm))
    return valid, truncated


def run_cross_reference_check(rule: dict, full_text: str, kb_results: list[dict]) -> list[dict]:
    """标准有效性核查：精确标准索引优先（废止→P0），知识库语义检索兜底；
    无法确认的合并为 1 条 P2 汇总（C 降噪），截断编号归入备注（D）"""
    config = rule.get("check_config", {})
    issues = []

    if "pattern" not in config:
        return issues

    from app.engine.standards_index import load_standards_index
    index = load_standards_index()

    p = config["pattern"]
    matches = [m.group(0) for m in re.finditer(p, full_text)]
    unique = []
    seen = set()
    for raw in matches:
        norm = normalize_standard_id(raw)
        if len(norm) < 5:
            continue
        if norm and norm not in seen:
            seen.add(norm)
            unique.append((raw.strip(), norm))
    unique = unique[:25]
    unique, truncated = _clean_standard_ids(unique)

    unconfirmed = []
    for raw, norm in unique:
        entry = index.get(norm) or index.get(norm.replace(" ", ""))
        if entry:
            if entry.get("status") == "废止":
                repl = "、".join(entry.get("replaced_by") or [])
                issues.append({
                    "rule_id": rule.get("rule_id", "R-STD-001"),
                    "severity": "P0",
                    "category": rule.get("category", "标准引用"),
                    "title": f"引用已废止标准 {raw}",
                    "finding": f"报告中引用的 {raw} 已被废止。" + (f"替代标准：{repl}。" if repl else ""),
                    "evidence": raw,
                    "evidence_location": raw,
                    "law_ref": rule.get("law_ref", ""),
                    "suggestion": f"请将 {raw} 更新为现行有效版本" + (f"（{repl}）" if repl else "") + "。",
                })
            continue

        is_valid = False
        for result in kb_results:
            excerpt = result.get("excerpt", "")
            if (raw in excerpt or norm in excerpt.replace(" ", "")) and not any(kw in excerpt for kw in ["废止", "作废", "已被"]):
                is_valid = True
                break

        if not is_valid:
            unconfirmed.append(raw)

    if unconfirmed or truncated:
        detail = ""
        if unconfirmed:
            detail += f"以下 {len(unconfirmed)} 个标准未在本地标准库中收录，无法确证现行有效性：{'、'.join(unconfirmed)}。"
        if truncated:
            detail += f"另有 {len(truncated)} 个编号疑似提取截断，请核对原文：{'、'.join(truncated)}。"
        preview = "、".join((unconfirmed + truncated)[:5])
        issues.append({
            "rule_id": rule.get("rule_id", "R-STD-001"),
            "severity": "P2",
            "category": rule.get("category", "标准引用"),
            "title": f"{len(unconfirmed) + len(truncated)} 个引用标准未能确认有效性（{preview} 等）",
            "finding": detail + "（本地标准库未收录不代表标准已废止，建议人工抽查核实。）",
            "evidence": preview,
            "law_ref": rule.get("law_ref", ""),
            "suggestion": "请逐一核实上述标准是否为现行有效版本；若均已废止，请更新为现行版本并补充标准年号。",
        })

    return issues


async def run_llm_check(rule: dict, full_text: str, kb_results: list[dict],
                        context_text: str = "", kfiles_ctx: str = "") -> list[dict]:
    """LLM 复判断（仅对标记为 llm_judge 的规则）。带缓存：相同报告+规则复用结果。
    采用自问自答模式：先问'报告是否满足此要求'，再给出推理过程和结论。
    context_text 为章节路由后的上下文；kfiles_ctx 为注入的 K 文件审核知识。
    LLM/解析故障向外抛错，由 pipeline 标记步骤 error。"""
    from app.llm.client import chat, get_active_profile
    from app.engine.llm_cache import get as cache_get, set as cache_set
    from app.engine.llm_json import parse_llm_json

    rule_id = rule.get("rule_id", "R-LLM-001")

    cached = cache_get(rule_id, full_text)
    if cached is not None:
        return cached

    profile = await get_active_profile()
    if not profile:
        raise RuntimeError("未配置启用的 LLM Profile")

    config = rule.get("check_config", {})
    kb_ctx = "\n\n".join([f"[{r['title']}] {r['excerpt'][:400]}" for r in kb_results[:5]])
    body = context_text if context_text else full_text[:5000]

    kfiles_section = f"\n审核知识依据（K 文件）：\n{kfiles_ctx}\n" if kfiles_ctx else ""

    feedback_section = ""
    try:
        from app.database import async_session
        from app.models.project import AuditIssue
        from sqlalchemy import select as _sel
        async with async_session() as _db:
            _r = await _db.execute(_sel(AuditIssue).where(AuditIssue.rule_id == rule_id))
            _hist = _r.scalars().all()
        _fp = [i for i in _hist if i.feedback == "false_positive"]
        _acc = [i for i in _hist if i.feedback == "accurate"]
        if _fp or _acc:
            _notes = "；".join([i.feedback_note[:80] for i in _fp[-3:] if i.feedback_note])
            feedback_section = (
                f"\n历史反馈提示：该检查项历史判定 {len(_hist)} 次，其中被标记误报 {len(_fp)} 次、准确 {len(_acc)} 次。"
                + (f"典型误报情形：{_notes}。" if _notes else "")
                + "请据此校准判定标准，避免重复误报。\n"
            )
    except Exception:
        pass

    try:
        from app.engine.cases import get_cases_for_rule
        _cases = await get_cases_for_rule(rule_id, limit=3)
        if _cases:
            _cl = "\n".join(
                f"案例{i}【{c['category'] or c['rule_id']}】（{c['accurate_count']} 次准确）：判定要点：{c['key_points']}；典型情形：{c['typical_finding']}"
                for i, c in enumerate(_cases, 1))
            feedback_section += f"\n同类历史案例（已被专家确认为准确判定，可参照其判定要点，但须以本报告实际内容为准）：\n{_cl}\n"
    except Exception:
        pass

    prompt = f"""你是一名有10年经验的环评审核专家。请使用**自问自答**的方式审核以下报告。

审核规则：{rule.get('title', '')}
法规依据：{rule.get('law_ref', '')}
检查要点：{config.get('prompt_partial', config.get('description', ''))}
{kfiles_section}
{feedback_section}
参考标准原文：
{kb_ctx or '无相关标准'}

报告相关内容：
{body}

请按以下步骤进行审核：

【第一步：提问】
首先提出1-2个关键问题来检验报告是否满足此规则。例如："报告中是否提供了XX？""XX内容是否充分？"

【第二步：判断】
根据报告内容和标准原文，逐一回答上述问题。引用报告中的原文作为证据。

【第三步：结论】
综合判断是否存在违反此规则的问题。如果存在多个小问题，合并输出。

如果确实存在问题，输出JSON（不要带```标记）：
{{"severity":"P0/P1/P2","title":"问题标题","finding":"具体发现（引用报告原文）","evidence_location":"报告中精确的问题原文（10-50字的片段，用于在报告上高亮标记）","reasoning":"推理过程（包含：①自问→②判断依据→③标准对照→④最终判定）","law_ref":"法规依据（标准编号+条款名）","suggestion":"最小化修改指引：明确指出把哪段原文的什么数值/表述改成什么（例：将年产量755.63 t/a修正为775.61 t/a），不要泛泛而谈"}}

如果不存在问题，输出: null

注意：
- evidence_location字段必须是从报告中原文照抄的10-50字片段，用于在报告原文上做黄色高亮标记
- reasoning字段必须包含完整推理链：①自问（提出了什么问题）②判断（报告中有什么/缺什么）③对照（标准要求什么）④判定（为什么定P0/P1/P2）
- 所有标准编号必须真实，不得编造。不确定时写"相关技术导则"
- 只输出一行JSON或null，不要任何其他内容。"""

    resp = await chat(prompt, profile=profile)
    data = parse_llm_json(resp, expect="object")
    if isinstance(data, dict) and data.get("title"):
        result = [{
            "rule_id": rule.get("rule_id", "R-LLM-001"),
            "severity": data.get("severity", "P1"),
            "category": rule.get("category", ""),
            "title": data["title"],
            "finding": data.get("finding", ""),
            "reasoning": data.get("reasoning", data.get("finding", "")),
            "evidence": data.get("evidence_location", data.get("finding", "")),
            "evidence_location": data.get("evidence_location", ""),
            "law_ref": data.get("law_ref", rule.get("law_ref", "")),
            "suggestion": data.get("suggestion", ""),
        }]
        cache_set(rule_id, full_text, result)
        return result
    cache_set(rule_id, full_text, [])
    return []


async def run_llm_check_per_chapter(rule: dict, text_data: dict, kb_results: list[dict],
                                    kfiles_ctx: str = "", max_chapters: int = 10) -> list[dict]:
    """逐章 map-reduce 审核：目标章节逐章独立判定 → 汇总去重。
    解决长报告抽样审核问题，核心章节全覆盖。
    LLM/解析故障向外抛错，由 pipeline 标记步骤 error。"""
    from app.llm.client import chat, get_active_profile
    from app.engine.llm_json import parse_llm_json
    from app.engine.context import pick_target_chapters, segment_chapter

    rule_id = rule.get("rule_id", "R-LLM-001")
    full_text = text_data.get("full_text", "")

    profile = await get_active_profile()
    if not profile:
        raise RuntimeError("未配置启用的 LLM Profile")

    patterns = rule.get("target_chapters") or ["."]
    chapters = pick_target_chapters(text_data, patterns, max_chapters=max_chapters)
    if not chapters:
        return await run_llm_check(rule, full_text, kb_results, kfiles_ctx=kfiles_ctx)

    config = rule.get("check_config", {})
    kb_ctx = "\n\n".join([f"[{r['title']}] {r['excerpt'][:300]}" for r in kb_results[:3]])
    kfiles_section = f"\n审核知识依据（K 文件）：\n{kfiles_ctx}\n" if kfiles_ctx else ""

    cases_section = ""
    try:
        from app.engine.cases import get_cases_for_rule
        _cases = await get_cases_for_rule(rule_id, limit=2)
        if _cases:
            _cl = "\n".join(f"案例【{c['category'] or rule_id}】：判定要点：{c['key_points']}" for c in _cases)
            cases_section = f"\n同类历史案例（供参照判定要点，须以本节实际内容为准）：\n{_cl}\n"
    except Exception:
        pass

    async def _check_segment(ch_title: str, seg_text: str) -> list[dict]:
        prompt = f"""你是一名有10年经验的环评审核专家。请审核报告的**单个章节**。

审核规则：{rule.get('title', '')}
法规依据：{rule.get('law_ref', '')}
检查要点：{config.get('prompt_partial', config.get('description', ''))}
{kfiles_section}
{cases_section}
参考标准原文：
{kb_ctx or '无相关标准'}

当前审核章节：{ch_title}
章节内容：
{seg_text}

要求：
- 只报告**本章节内**发现的问题；本章节无法判断的（需对照其他章节）不要报
- 确实存在问题输出JSON（不要带```标记）：
{{"severity":"P0/P1/P2","title":"问题标题","finding":"具体发现（引用原文）","evidence_location":"本章节中精确的原文片段（10-50字）","reasoning":"推理过程","law_ref":"法规依据","suggestion":"最小化修改指引（明确改哪处、改成什么）"}}
- 无问题输出: null
- 所有标准编号必须真实，不得编造。只输出一行JSON或null。"""
        resp = await chat(prompt, profile=profile)
        data = parse_llm_json(resp, expect="object")
        if isinstance(data, dict) and data.get("title"):
            return [{
                "rule_id": rule_id,
                "severity": data.get("severity", "P1"),
                "category": rule.get("category", ""),
                "title": data["title"],
                "finding": data.get("finding", ""),
                "reasoning": data.get("reasoning", data.get("finding", "")),
                "evidence": data.get("evidence_location", data.get("finding", "")),
                "evidence_location": data.get("evidence_location", ""),
                "law_ref": data.get("law_ref", rule.get("law_ref", "")),
                "suggestion": data.get("suggestion", ""),
                "chapter": ch_title,
            }]
        return []

    import asyncio
    tasks = []
    for ch in chapters:
        title = ch.get("title", "")
        for seg in segment_chapter(ch.get("content", ""))[:3]:
            tasks.append(asyncio.create_task(_check_segment(title, seg)))
    results = await asyncio.gather(*tasks, return_exceptions=True)

    merged: dict[tuple, dict] = {}
    errors = []
    sev_rank = {"P0": 0, "P1": 1, "P2": 2}
    for res in results:
        if isinstance(res, Exception):
            errors.append(res)
            continue
        for iss in res:
            key = (iss["rule_id"], re.sub(r"[\s，。、：:（）()]", "", iss.get("title", ""))[:24])
            if key not in merged or sev_rank.get(iss["severity"], 3) < sev_rank.get(merged[key]["severity"], 3):
                merged[key] = iss

    if errors and not merged:
        raise errors[0]
    return sorted(merged.values(), key=lambda x: sev_rank.get(x["severity"], 3))
