"""K 文件体系 — 对标参考系统 K1-K17：审核知识按文件组织，按域/报告类型/行业选择注入"""
import re
from pathlib import Path

KFILES_DIR = Path(__file__).resolve().parent.parent.parent / "kfiles"

KFILE_META = {
    "K01": {"name": "标准有效性", "file": "K01-标准有效性.md", "domains": ["eia", "acceptance", "emergency"], "topics": ["标准", "废止", "版本"]},
    "K02": {"name": "分类管理名录", "file": "K02-分类管理名录.md", "domains": ["eia"], "topics": ["分类", "名录", "报告书", "报告表"]},
    "K03": {"name": "专项设置", "file": "K03-专项设置.md", "domains": ["eia"], "topics": ["专项", "专题"]},
    "K04": {"name": "工程分析源强", "file": "K04-工程分析源强.md", "domains": ["eia", "acceptance"], "topics": ["源强", "工程分析", "类比"]},
    "K05": {"name": "产污系数手册", "file": "K05-产污系数手册.md", "domains": ["eia", "acceptance"], "topics": ["系数", "产污", "排污"]},
    "K06": {"name": "污染防治措施", "file": "K06-污染防治措施.md", "domains": ["eia", "acceptance"], "topics": ["措施", "可行技术", "达标"]},
    "K07": {"name": "报告结构", "file": "K07-报告结构.md", "domains": ["eia"], "topics": ["结构", "章节", "完整性"]},
    "K08": {"name": "法规符合性", "file": "K08-法规符合性.md", "domains": ["eia"], "topics": ["产业政策", "三线一单", "规划", "选址"]},
    "K09": {"name": "评价等级范围", "file": "K09-评价等级范围.md", "domains": ["eia"], "topics": ["评价等级", "评价范围"]},
    "K10": {"name": "环境现状监测", "file": "K10-环境现状监测.md", "domains": ["eia", "acceptance"], "topics": ["现状", "监测", "布点"]},
    "K11": {"name": "预测模型", "file": "K11-预测模型.md", "domains": ["eia"], "topics": ["预测", "模型", "AERMOD"]},
    "K12": {"name": "危废管理", "file": "K12-危废管理.md", "domains": ["eia", "acceptance", "emergency"], "topics": ["危废", "固废", "HW"]},
    "K13": {"name": "总量控制", "file": "K13-总量控制.md", "domains": ["eia", "acceptance"], "topics": ["总量", "指标", "削减"]},
    "K14": {"name": "公众参与", "file": "K14-公众参与.md", "domains": ["eia"], "topics": ["公众参与", "公示"], "report_types": ["报告书"]},
    "K15": {"name": "附图附件", "file": "K15-附图附件.md", "domains": ["eia", "acceptance", "emergency"], "topics": ["附图", "附件"]},
    "K16": {"name": "验收程序", "file": "K16-验收程序.md", "domains": ["acceptance"], "topics": ["验收", "不得通过", "三同时"]},
    "K17": {"name": "应急预案", "file": "K17-应急预案.md", "domains": ["emergency"], "topics": ["应急", "预案", "风险"]},
}

INDUSTRY_KFILE_HINTS = {
    "锅炉": ["K05"], "热电": ["K05"], "火电": ["K05"],
    "化工": ["K04", "K12"], "石化": ["K04", "K12"], "医药": ["K04", "K12"],
    "电镀": ["K12"], "喷涂": ["K12"], "涂装": ["K12"], "印刷": ["K12"],
    "污水处理": ["K10"], "垃圾": ["K03", "K12"],
}

_cache: dict[str, str] = {}


def load_kfile(kfile_id: str) -> str:
    if kfile_id in _cache:
        return _cache[kfile_id]
    meta = KFILE_META.get(kfile_id)
    if not meta:
        return ""
    path = KFILES_DIR / meta["file"]
    if not path.exists():
        return ""
    content = path.read_text(encoding="utf-8")
    _cache[kfile_id] = content
    return content


def select_kfiles(domain: str = "eia", report_type: str = "报告书", industry: str = "") -> list[str]:
    """按域/报告类型/行业选择适用的 K 文件 ID 列表"""
    selected = []
    for kid, meta in KFILE_META.items():
        if domain not in meta.get("domains", []):
            continue
        rts = meta.get("report_types")
        if rts and report_type not in rts:
            continue
        selected.append(kid)
    for kw, kids in INDUSTRY_KFILE_HINTS.items():
        if kw and kw in industry:
            for kid in kids:
                if kid not in selected:
                    selected.append(kid)
    return selected


def build_kfiles_context(kfile_ids: list[str], max_chars: int = 8000) -> str:
    """拼接 K 文件全文作为审核依据上下文"""
    parts = []
    used = 0
    for kid in kfile_ids:
        meta = KFILE_META.get(kid, {})
        content = load_kfile(kid)
        if not content:
            continue
        block = f"\n\n===== 审核知识文件 {kid}（{meta.get('name', '')}）=====\n{content}"
        if used + len(block) > max_chars:
            remaining = max_chars - used
            if remaining > 500:
                parts.append(block[:remaining] + "\n...[截断]")
            break
        parts.append(block)
        used += len(block)
    return "".join(parts).strip()


def list_kfiles() -> list[dict]:
    """K 文件清单（管理页展示用）"""
    items = []
    for kid, meta in KFILE_META.items():
        path = KFILES_DIR / meta["file"]
        size = path.stat().st_size if path.exists() else 0
        mtime = path.stat().st_mtime if path.exists() else 0
        from datetime import datetime
        items.append({
            "id": kid,
            "name": meta["name"],
            "file": meta["file"],
            "domains": meta["domains"],
            "size": size,
            "updated_at": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime else "",
        })
    return items


def guess_industry(text_head: str, chapter_titles: list[str]) -> str:
    """行业关键词快判（LLM 前置的廉价兜底）"""
    joined = text_head + " " + " ".join(chapter_titles)
    for kw in ["锅炉", "热电", "火电", "化工", "石化", "医药", "电镀", "喷涂", "涂装",
               "印刷", "污水处理", "垃圾", "水泥", "钢铁", "铸造", "食品", "养殖",
               "矿山", "玻璃", "陶瓷", "纺织", "印染", "造纸", "家具", "电子"]:
        if kw in joined:
            return kw
    return ""


async def identify_report(text_data: dict, domain: str, llm_call=None) -> dict:
    """Step 0 报告识别：判定报告类型 + 行业。LLM 优先，关键词兜底。
    llm_call: async callable(prompt) -> str"""
    full_text = text_data.get("full_text", "")
    titles = [c.get("title", "") for c in text_data.get("chapters", [])][:40]
    head = full_text[:3000]

    fallback_type = "报告表" if "报告表" in full_text[:2000] else "报告书"
    industry = guess_industry(head, titles)

    if llm_call is not None:
        from app.engine.llm_json import parse_llm_json
        prompt = f"""请识别以下环境类报告的基本信息，输出JSON：
{{"report_type":"报告书/报告表/验收报告/应急预案/其他","industry":"行业关键词（如：化工、锅炉、电镀、养殖、家具制造）","project_name":"项目名称","has_sensitive_area":true/false}}

章节标题：{'; '.join(titles[:20])}
报告开头：
{head[:2500]}

只输出JSON。"""
        resp = await llm_call(prompt)
        data = parse_llm_json(resp, expect="object") or {}
        rt = data.get("report_type", "")
        if domain == "eia" and rt in ("报告书", "报告表"):
            report_type = rt
        elif domain == "eia":
            report_type = fallback_type
        else:
            report_type = rt or fallback_type
        return {
            "report_type": report_type,
            "industry": data.get("industry", "") or industry,
            "project_name": data.get("project_name", ""),
            "has_sensitive_area": bool(data.get("has_sensitive_area")),
        }

    return {"report_type": fallback_type, "industry": industry, "project_name": "", "has_sensitive_area": False}
