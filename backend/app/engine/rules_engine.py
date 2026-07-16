import yaml
import re
from pathlib import Path

RULES_DIR = Path(__file__).resolve().parent.parent.parent / "rules"


def load_rules(domain: str = "eia", report_type: str = "报告书") -> list[dict]:
    rules = []
    files = [f"{domain}_rules.yaml"]
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


def run_keyword_check(rule: dict, full_text: str) -> list[dict]:
    """关键词匹配检查，不用 LLM"""
    config = rule.get("check_config", {})
    issues = []

    if "required_chapters" in config:
        for ch in config["required_chapters"]:
            found = (
                ch in full_text or
                ch.replace(" ", "") in full_text.replace(" ", "") or
                ch.replace("与", "") in full_text
            )
            if not found:
                issues.append({
                    "rule_id": rule.get("rule_id", ""),
                    "severity": rule.get("severity", "P2"),
                    "category": rule.get("category", ""),
                    "title": f"报告中未找到'{ch}'相关章节",
                    "finding": f"根据 {rule.get('law_ref', '相关导则')} 要求，环评报告应包含 '{ch}'。经全文检索，未发现相关内容。",
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


def run_cross_reference_check(rule: dict, full_text: str, kb_results: list[dict]) -> list[dict]:
    """知识库交叉验证检查（不用 LLM）"""
    config = rule.get("check_config", {})
    issues = []

    if "pattern" in config:
        p = config["pattern"]
        matches = re.findall(p, full_text)
        unique = list(set(matches))[:20]

        for std in unique:
            is_valid = False
            for result in kb_results:
                excerpt = result.get("excerpt", "")
                if std in excerpt and not any(kw in excerpt for kw in ["废止", "作废", "已被"]):
                    is_valid = True
                    break

            if not is_valid:
                issues.append({
                    "rule_id": rule.get("rule_id", "R-STD-001"),
                    "severity": "P1",
                    "category": rule.get("category", "标准引用"),
                    "title": f"标准 {std} 可能已废止或被替代",
                    "finding": f"报告中引用了 {std}，知识库检索未确认其现行有效性。",
                    "evidence": "",
                    "law_ref": rule.get("law_ref", ""),
                    "suggestion": f"请确认 {std} 是否为现行有效版本。",
                })

    return issues


async def run_llm_check(rule: dict, full_text: str, kb_results: list[dict]) -> list[dict]:
    """LLM 复杂判断（仅对标记为 llm_judge 的规则）。带缓存：相同报告+规则复用结果"""
    from app.llm.client import chat, get_active_profile
    from app.engine.llm_cache import get as cache_get, set as cache_set

    rule_id = rule.get("rule_id", "R-LLM-001")

    cached = cache_get(rule_id, full_text)
    if cached is not None:
        return cached

    profile = await get_active_profile()
    if not profile:
        return []

    config = rule.get("check_config", {})
    kb_ctx = "\n\n".join([f"[{r['title']}] {r['excerpt'][:400]}" for r in kb_results[:5]])

    prompt = f"""你是环评审核专家。请针对以下规则逐条检查：

规则：{rule.get('title', '')}
依据：{rule.get('law_ref', '')}
检查要点：{config.get('prompt_partial', config.get('description', ''))}

参考标准：
{kb_ctx or '无相关标准'}

报告内容（仅相关段落）：
{full_text[:5000]}

请判断是否存在违反此规则的问题。如果存在，输出JSON格式：
{{"severity": "P0/P1/P2", "title": "问题标题", "finding": "具体发现（引用报告原文）", "law_ref": "法规依据", "suggestion": "修改建议"}}
如果不存在问题，输出 null。
只输出一行JSON或null，不要其他内容。"""

    try:
        resp = await chat(prompt, profile=profile)
        resp = resp.strip()
        if resp.startswith("```"):
            resp = resp.split("```")[1]
        if not resp or resp == "null":
            cache_set(rule_id, full_text, [])
            return []
        import json
        data = json.loads(resp)
        if isinstance(data, dict) and data.get("title"):
            result = [{
                "rule_id": rule.get("rule_id", "R-LLM-001"),
                "severity": data.get("severity", "P1"),
                "category": rule.get("category", ""),
                "title": data["title"],
                "finding": data.get("finding", ""),
                "evidence": data.get("finding", ""),
                "law_ref": data.get("law_ref", rule.get("law_ref", "")),
                "suggestion": data.get("suggestion", ""),
            }]
            cache_set(rule_id, full_text, result)
            return result
        cache_set(rule_id, full_text, [])
    except Exception:
        pass

    return []
