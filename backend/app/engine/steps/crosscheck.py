"""交叉数据核验 — O1a 确定性检查（表内加和/表格编号重复/经纬度误填/列数不齐）
+ O1b 跨表/跨章同一指标一致性（确定性候选配对 + LLM 仲裁控误报）。
证据定位到表号/章节与原文片段。"""
import re

from app.engine.grader import build_issue

APPLICABLE_DOMAINS = {"eia", "acceptance"}

TOTAL_LABELS = ("合计", "总计")
_NUM_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")
_TABLE_NO_RE = re.compile(r"表\s*(\d+(?:\s*[.\-—]\s*\d+)+)\s*([^\n，。；;]{0,40})")
_DMS_RE = re.compile(r"(\d{1,3})\s*[°º]\s*(\d{1,2})\s*[′'´]\s*(\d{1,2}(?:\.\d+)?)\s*[″\"”]")
_DECIMAL_COORD_RE = re.compile(r"(?<![\d.])(\d{2,3}\.\d{4,})\s*[°º]")


def _parse_num(cell: str):
    """从单元格文本提取首个数值（去千分位）；无数值返回 None"""
    if not isinstance(cell, str):
        return None
    s = cell.replace(",", "").replace("，", "")
    m = _NUM_RE.search(s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _decimals(cell: str) -> int:
    m = _NUM_RE.search((cell or "").replace(",", ""))
    if not m:
        return 0
    s = m.group(0)
    return len(s.split(".")[-1]) if "." in s else 0


async def check_cross_tables(text_data: dict, audit_ctx: dict | None = None) -> list[dict]:
    if audit_ctx and audit_ctx.get("domain", "eia") not in APPLICABLE_DOMAINS:
        return []
    tables = text_data.get("tables", []) or []
    full_text = text_data.get("full_text", "")
    issues = []
    issues += _check_table_sums(tables)
    issues += _check_table_numbering(full_text)
    issues += _check_coordinates(full_text)
    issues += _check_table_structure(tables)
    issues += _check_batch_recalc(tables)
    issues += _check_limit_interpolation(tables, full_text)
    issues += _check_rate_total(tables, full_text)
    try:
        issues += await _check_metric_consistency(text_data)
    except Exception as e:
        print(f"[crosscheck] 指标一致性仲裁跳过（不影响确定性结果）: {str(e)[:80]}")
    return issues


def _check_table_sums(tables: list[dict]) -> list[dict]:
    """表内加和校验：含「合计/总计」行的数值列，分项累加 vs 合计值（纵向单列同单位，安全）"""
    issues = []
    for t in tables:
        rows = t.get("rows", [])
        if len(rows) < 3:
            continue
        total_idx = None
        for i, row in enumerate(rows):
            first = next((c for c in row if c.strip()), "")
            if any(lb in first for lb in TOTAL_LABELS):
                total_idx = i
        if total_idx is None or total_idx == 0:
            continue
        data_rows = rows[:total_idx]
        total_row = rows[total_idx]
        cap = t.get("caption") or "未命名表"
        found = 0
        for c in range(len(total_row)):
            if found >= 3:
                break
            total_val = _parse_num(total_row[c]) if c < len(total_row) else None
            if total_val is None:
                continue
            vals = [_parse_num(r[c]) for r in data_rows if c < len(r)]
            vals = [v for v in vals if v is not None]
            if len(vals) < 2:
                continue
            actual = sum(vals)
            tol = 0.5 * (10 ** -_decimals(total_row[c])) * len(vals) + 1e-9
            diff = abs(actual - total_val)
            if diff <= tol or (abs(total_val) > 1e-9 and diff / abs(total_val) <= 0.01):
                continue
            header = ""
            if t.get("headers") and c < len(t["headers"]):
                header = t["headers"][c].strip()
            col_name = f"「{header}」列" if header else f"第{c + 1}列"
            rel = diff / max(abs(total_val), 1e-9) * 100
            issues.append(build_issue(
                "R-XCHK-SUM", "P1", "交叉核验",
                f"{cap} {col_name}合计值与分项累加不符（偏差{rel:.0f}%）".replace("  ", " "),
                f"{cap}{col_name}：各分项累加为 {actual:g}，表内合计值为 {total_val:g}，差值 {diff:g}。"
                f"（分项数 {len(vals)} 个）",
                evidence=f"{cap} 合计行: {' | '.join(x for x in total_row if x.strip())[:80]}",
                law_ref="HJ 2.1-2016 数据真实准确性要求",
                suggestion=f"请复核{cap}{col_name}各分项数值或合计公式，修正累加错误。"
            ))
            issues[-1]["chapter"] = t.get("chapter", "")
            issues[-1]["evidence_location"] = issues[-1]["evidence"][:50]
            found += 1
    return issues


def _check_table_numbering(full_text: str) -> list[dict]:
    """表格编号重复：同一表号对应两个不同表题（目录点线行自动排除）"""
    issues = []
    by_no: dict[str, list[str]] = {}
    for line in full_text.split("\n"):
        stripped = line.strip()
        if re.search(r"\.{4,}", stripped):
            continue
        m = _TABLE_NO_RE.match(stripped)
        if not m:
            continue
        num = re.sub(r"\s+", "", m.group(1)).replace("—", "-").replace("–", "-")
        title = re.sub(r"[\s　]", "", m.group(2))
        by_no.setdefault(num, []).append(title)
    for num, titles in by_no.items():
        distinct = {t for t in titles if t}
        if len(distinct) > 1:
            sample = "；".join(f"表{num}{t[:20]}" for t in sorted(distinct)[:3])
            issues.append(build_issue(
                "R-XCHK-TBN", "P2", "交叉核验",
                f"表格编号重复：表{num} 被用于 {len(distinct)} 个不同表格",
                f"报告中出现 {len(distinct)} 个编号均为表{num}、但表题不同的表格：{sample}。",
                evidence=f"表{num}",
                law_ref="GB/T 1.1 标准化文件编号唯一性",
                suggestion=f"请为重复的表{num}重新编号（如顺延为下一号），并同步更新目录与正文交叉引用。"
            ))
            issues[-1]["evidence_location"] = f"表{num}"
    return issues


def _check_coordinates(full_text: str) -> list[dict]:
    """经纬度误填：度分秒坐标与十进制坐标同文出现时，检测「分」被当作小数位填入
    （如 126°27′58″ 误填为 126.2758°，正确十进制应为 126.466°，偏差可达十余公里）"""
    issues = []
    dms_list = []
    for m in _DMS_RE.finditer(full_text):
        deg, minute, sec = int(m.group(1)), int(m.group(2)), float(m.group(3))
        if minute > 59 or sec >= 60:
            issues.append(build_issue(
                "R-XCHK-GEO", "P2", "交叉核验",
                f"经纬度度分秒数值无效：{m.group(0)}",
                f"坐标 {m.group(0)} 中分/秒数值超出 0-59 范围，不是合法的度分秒坐标。",
                evidence=m.group(0),
                law_ref="HJ 2.1-2016 基础数据准确性要求",
                suggestion="请核实原始坐标数据，修正为度分秒（分、秒均小于60）或换算为十进制坐标。"
            ))
            issues[-1]["evidence_location"] = m.group(0)[:50]
            continue
        if 3 <= deg <= 135:
            dms_list.append((deg, minute, sec, deg + minute / 60 + sec / 3600))
    seen_dec = set()
    for m in _DECIMAL_COORD_RE.finditer(full_text):
        val = float(m.group(1))
        deg = int(val)
        if not (3 <= deg <= 135) or val in seen_dec:
            continue
        frac_str = f"{val:.8f}".split(".")[1]
        for d_deg, d_min, d_sec, d_val in dms_list:
            if d_deg != deg:
                continue
            m_str = str(d_min)
            if not (frac_str.startswith(m_str) or frac_str.startswith(m_str.zfill(2))):
                continue
            deviation = abs(val - d_val)
            if deviation <= 0.008:
                continue
            seen_dec.add(val)
            issues.append(build_issue(
                "R-XCHK-GEO", "P1", "交叉核验",
                f"经纬度格式疑似误填：{val}° 与 {d_deg}°{d_min}′{d_sec:g}″ 不一致",
                f"报告中十进制坐标 {val}° 的小数位与度分秒坐标 {d_deg}°{d_min}′{d_sec:g}″ 的「分」数值相同，"
                f"疑似将「分」直接当作小数位填入。该度分秒坐标正确十进制值约为 {d_val:.4f}°，"
                f"两者偏差约 {deviation * 111:.1f} 公里。",
                evidence=m.group(0),
                law_ref="HJ 2.1-2016 基础数据准确性要求",
                suggestion=f"请将度分秒坐标正确换算为十进制（约 {d_val:.4f}°）后再填入模型或正文。"
            ))
            issues[-1]["evidence_location"] = m.group(0)[:50]
            break
    return issues


def _check_table_structure(tables: list[dict]) -> list[dict]:
    """列数不齐：数据行列数与表头列数不一致（列定义不清/错位风险）"""
    issues = []
    for t in tables:
        headers = t.get("headers", [])
        rows = t.get("rows", [])
        if not headers or len(rows) < 2:
            continue
        ncol = len(headers)
        bad = [r for r in rows if any(c.strip() for c in r) and len(r) != ncol]
        if not bad or len(bad) < max(1, len(rows) // 3):
            continue
        cap = t.get("caption") or (headers[0][:20] if headers else "未命名表")
        issues.append(build_issue(
            "R-XCHK-TBL", "P2", "交叉核验",
            f"{cap} 存在 {len(bad)} 行列数与表头不符".replace("  ", " "),
            f"{cap}：表头为 {ncol} 列，但有 {len(bad)} 行数据为其他列数，可能存在列定义不清或数据错位。",
            evidence=f"{cap}（{ncol}列）",
            law_ref="HJ 2.1-2016 数据真实准确性要求",
            suggestion="请核对该表各列含义与每行数据的对应关系，明确列定义。"
        ))
        issues[-1]["chapter"] = t.get("chapter", "")
        issues[-1]["evidence_location"] = issues[-1]["evidence"][:50]
    return issues


# ═══ O1b 跨表/跨章同一指标一致性（确定性候选 + LLM 仲裁）═══

_METRIC_CANON = {
    "总投资": ["项目总投资", "总投资", "投资额"],
    "环保投资": ["环保投资"],
    "新水量": ["新水用量", "新鲜水用量", "新水量", "新鲜水量", "新水输入量"],
    "用水量": ["总用水量", "年用水量", "用水量"],
    "排水量": ["废水排放量", "排水量", "废水量"],
    "用电量": ["年用电量", "用电量"],
    "蒸汽量": ["蒸汽用量", "蒸汽量", "蒸汽消耗量"],
    "危废量": ["危险废物产生量", "危废产生量"],
    "固废量": ["一般固废产生量", "固废产生量"],
    "储存量": ["最大储存量", "最大存在总量", "最大存在量", "最大在线量"],
}
_QUALIFIERS = ("现有", "全厂", "拟建", "一期", "二期", "本期")
_EMISSION_RE = re.compile(
    r"(SO₂|SO2|NOx|NO₂|颗粒物|PM10|PM2\.5|COD|CODcr|氨氮|NH3-N|VOCs|非甲烷总烃|NMHC)排放量")
_NUM_UNIT_RE = re.compile(
    r"([-+]?\d[\d,]*\.?\d*)\s*(亿元|万元|元|t/a|kg/a|t/d|m³/a|m3/a|m³/d|m3/d|m³|m3|t|kg|万kWh|kWh|tce)?")
_DATE_LABELS = ("监测时间", "监测日期", "现状监测时间", "采样时间", "采样日期")
_DATE_RE = re.compile(r"(20\d{2})\s*[年.\-/]\s*(\d{1,2})\s*[月.\-/]\s*(\d{1,2})\s*日?")


def _norm_unit_value(canon: str, num: float, unit: str):
    """按指标类别归一化数值与单位；返回 (value, norm_unit)，无法归一返回 (num, unit)"""
    u = (unit or "").replace("m3", "m³")
    if canon in ("总投资", "环保投资"):
        conv = {"亿元": 1e4, "万元": 1.0, "元": 1e-4}
        return (num * conv[u], "万元") if u in conv else (num, u)
    if canon in ("新水量", "用水量", "排水量"):
        conv = {"t": 1.0, "m³": 1.0, "t/a": 1.0, "m³/a": 1.0, "t/d": 1.0, "m³/d": 1.0}
        if u in conv:
            base = {"t": "m³", "m³": "m³", "t/a": "m³/a", "m³/a": "m³/a", "t/d": "m³/d", "m³/d": "m³/d"}
            return num * conv[u], base[u]
        return num, u
    if canon == "用电量":
        conv = {"万kWh": 1.0, "kWh": 1e-4}
        return (num * conv[u], "万kWh") if u in conv else (num, u)
    conv = {"t": 1.0, "kg": 1e-3, "t/a": 1.0, "kg/a": 1e-3, "tce": 1.0}
    return (num * conv[u], u.replace("kg/a", "t/a").replace("kg", "t")) if u in conv else (num, u)


def _qualifier_of(prefix: str) -> str:
    for q in _QUALIFIERS:
        if q in prefix:
            return q
    return ""


def _chapter_of(chapters: list[dict], core: str) -> str:
    """用核心片段（标签+数值，不含上下文）定位所属章节"""
    if len(core) < 4:
        return ""
    for ch in chapters:
        if core[:20] in ch.get("content", ""):
            return ch.get("title", "")
    return ""


def _extract_metrics(text_data: dict) -> list[dict]:
    """从正文与表格抽取指标实体 {canon,label,qual,value,nunit,location,snippet}"""
    full_text = text_data.get("full_text", "")
    chapters = text_data.get("chapters", []) or []
    tables = text_data.get("tables", []) or []
    metrics = []
    seen = set()

    def add(canon, label, qual, num, unit, location, snippet, raw_num=""):
        val, nu = _norm_unit_value(canon, num, unit)
        key = (canon, label, qual, round(val, 6), nu, location)
        if key in seen:
            return
        seen.add(key)
        raw_s = raw_num.replace(",", "")
        raw_dec = len(raw_s.split(".")[-1]) if "." in raw_s else 0
        metrics.append({"canon": canon, "label": label, "qual": qual, "value": val,
                        "nunit": nu, "raw_dec": raw_dec,
                        "location": location, "snippet": snippet.strip()[:60]})

    for canon, labels in _METRIC_CANON.items():
        for lb in labels:
            for m in re.finditer(re.escape(lb), full_text):
                seg = full_text[m.end():m.end() + 30]
                nm = _NUM_UNIT_RE.search(seg)
                if not nm or not nm.group(2):
                    continue
                snippet = full_text[max(0, m.start() - 10):m.end() + 25]
                core = full_text[m.start():m.end() + nm.end()]
                add(canon, lb, _qualifier_of(full_text[max(0, m.start() - 10):m.start()]),
                    float(nm.group(1).replace(",", "")), nm.group(2),
                    _chapter_of(chapters, core), snippet, nm.group(1))

    for m in _EMISSION_RE.finditer(full_text):
        canon = f"排放量:{m.group(1)}"
        seg = full_text[m.end():m.end() + 30]
        nm = _NUM_UNIT_RE.search(seg)
        if not nm or not nm.group(2):
            continue
        snippet = full_text[max(0, m.start() - 10):m.end() + 25]
        core = full_text[m.start():m.end() + nm.end()]
        add(canon, m.group(0), _qualifier_of(full_text[max(0, m.start() - 10):m.start()]),
            float(nm.group(1).replace(",", "")), nm.group(2),
            _chapter_of(chapters, core), snippet, nm.group(1))

    for lb in _DATE_LABELS:
        for m in re.finditer(re.escape(lb), full_text):
            seg = full_text[m.end():m.end() + 25]
            dm = _DATE_RE.search(seg)
            if not dm:
                continue
            snippet = full_text[max(0, m.start() - 6):m.end() + 20]
            core = full_text[m.start():m.end() + dm.end()]
            key = ("监测时间", dm.group(0), core[:12])
            if key in seen:
                continue
            seen.add(key)
            metrics.append({"canon": "监测时间", "label": lb, "qual": "",
                            "value": dm.group(0), "nunit": "date",
                            "location": _chapter_of(chapters, core),
                            "snippet": snippet.strip()[:60]})

    for t in tables:
        loc = " / ".join(x for x in (t.get("chapter", ""), t.get("caption", "")) if x)
        for row in t.get("rows", []):
            head = " ".join(c for c in row[:2])
            for canon, labels in _METRIC_CANON.items():
                hit = next((lb for lb in labels if lb in head), None)
                if not hit:
                    continue
                for c in row[1:]:
                    nm = _NUM_UNIT_RE.search((c or "").replace(",", ""))
                    if nm and nm.group(2):
                        add(canon, hit, _qualifier_of(head + t.get("caption", "")),
                            float(nm.group(1).replace(",", "")), nm.group(2), loc,
                            " | ".join(x for x in row if x.strip()), nm.group(1))
                        break
    return metrics


def _metric_candidates(metrics: list[dict], max_candidates: int = 15) -> list[dict]:
    """同 canon + 同限定词 + 可归一单位 → 配对；数值偏差>5%（日期不等）→ 候选矛盾"""
    by_canon: dict[str, list[dict]] = {}
    for m in metrics:
        by_canon.setdefault(m["canon"], []).append(m)
    candidates = []
    for canon, items in by_canon.items():
        if len(items) < 2:
            continue
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i], items[j]
                if a["qual"] != b["qual"] or a["snippet"] == b["snippet"]:
                    continue
                if canon == "监测时间":
                    dev = 1.0 if a["value"] != b["value"] else 0.0
                else:
                    if a["nunit"] != b["nunit"]:
                        continue
                    try:
                        va, vb = float(a["value"]), float(b["value"])
                    except (TypeError, ValueError):
                        continue
                    if max(abs(va), abs(vb)) < 1e-9:
                        continue
                    dev = abs(va - vb) / max(abs(va), abs(vb), 1e-9)
                    tol = 0.5 * (10 ** -min(a.get("raw_dec", 0), b.get("raw_dec", 0)))
                    if abs(va - vb) <= tol:
                        continue
                if dev > 0.01:
                    candidates.append({"canon": canon, "dev": dev, "a": a, "b": b})
    candidates.sort(key=lambda c: -c["dev"])
    deduped, seen_pair = [], set()
    for c in candidates:
        k = (c["canon"], frozenset(((str(c["a"]["value"]), c["a"]["location"]),
                                    (str(c["b"]["value"]), c["b"]["location"]))))
        if k in seen_pair:
            continue
        seen_pair.add(k)
        deduped.append(c)
    return deduped[:max_candidates]


async def _check_metric_consistency(text_data: dict) -> list[dict]:
    """候选矛盾对 → 单次 LLM 仲裁是否同一口径真矛盾 → 输出问题（无 LLM 时跳过，不产误报）"""
    metrics = _extract_metrics(text_data)
    candidates = _metric_candidates(metrics)
    if not candidates:
        return []

    from app.llm.client import chat, get_active_profile
    from app.engine.llm_json import parse_llm_json
    profile = await get_active_profile()
    if not profile:
        return []

    lines = []
    for idx, c in enumerate(candidates, 1):
        a, b = c["a"], c["b"]
        pair_label = a["label"] if a["label"] == b["label"] else f"{a['label']} / {b['label']}"
        va = f"{a['value']:g}" if isinstance(a["value"], (int, float)) else str(a["value"])
        vb = f"{b['value']:g}" if isinstance(b["value"], (int, float)) else str(b["value"])
        lines.append(
            f"{idx}. 指标「{pair_label}」：\n"
            f"   A（{a['location'] or '正文'}）：“{a['snippet']}” → {va} {a['nunit']}\n"
            f"   B（{b['location'] or '正文'}）：“{b['snippet']}” → {vb} {b['nunit']}")

    prompt = f"""你是环评审核专家。以下候选对是从同一报告不同位置提取的同类指标的数值，请判断哪些构成**真正的数据前后矛盾**（同一指标、同一口径、同一时间基准，却数值不一致）。

必须排除的情形（不算矛盾）：
- 设计值 vs 实际值、现状值 vs 预测值、保证值 vs 监测值
- 不同年份/不同生产负荷下的数据
- 边界不同（如仅指某车间/某装置 vs 全厂；含水率/状态不同的固废量）
- 文字描述与表格细化数据（如"约5万"与精确值 5.2万）
- 合理舍入差异

候选对：
{chr(10).join(lines)}

对判定为真矛盾的对，输出JSON数组（不要带```标记）：
[{{"idx":1,"reason":"矛盾原因（20字内）","suggestion":"最小化修改指引：明确把哪处的什么值改成什么"}}]
无真矛盾输出 []。只输出JSON。"""

    resp = await chat(prompt, profile=profile)
    data = parse_llm_json(resp, expect="array") or []

    issues = []
    for item in data:
        if not isinstance(item, dict):
            continue
        idx = item.get("idx", 0)
        if not (1 <= idx <= len(candidates)):
            continue
        c = candidates[idx - 1]
        a, b = c["a"], c["b"]
        if c["canon"] == "监测时间":
            finding = (f"两处监测时间不一致：{a['location'] or '正文'}记载“{a['value']}”，"
                       f"{b['location'] or '正文'}记载“{b['value']}”。{item.get('reason', '')}")
            title = f"监测时间前后不一致：{a['value']} vs {b['value']}"
        else:
            finding = (f"指标「{a['label']}」两处数值不一致：{a['location'] or '正文'}为 {a['value']:g} {a['nunit']}，"
                       f"{b['location'] or '正文'}为 {b['value']:g} {b['nunit']}，相对偏差 {c['dev'] * 100:.0f}%。"
                       f"{item.get('reason', '')}")
            title = f"数据前后不一致：{a['label']} {a['value']:g} vs {b['value']:g} {a['nunit']}"
        issues.append(build_issue(
            "R-XCHK-CONS", "P1", "交叉核验",
            title, finding,
            evidence=f"A: {a['snippet']} ｜ B: {b['snippet']}",
            law_ref="HJ 2.1-2016 数据真实准确、前后一致性要求",
            suggestion=item.get("suggestion", "") or "请核实两处数据来源，统一为该指标的真实值。"
        ))
        issues[-1]["chapter"] = a.get("location", "")
        issues[-1]["evidence_location"] = (a.get("snippet") or "")[:50]
    return issues



# ═══ O1c 重算类核验（全确定性）═══

_UNIT_KEYS = ("kg/批", "t/批", "kg/a", "t/a", "kg/h", "t/h", "kg", "t", "批/a", "批")


def _cell_value_unit(cell: str, header: str):
    """单元格数值 + 单位（单元格优先，表头兜底）；返回 (value, unit)"""
    v = _parse_num(cell)
    if v is None:
        return None, ""
    s = (cell or "") + " " + (header or "")
    for u in _UNIT_KEYS:
        if u in s:
            return v, u
    return v, ""


def _check_batch_recalc(tables: list[dict]) -> list[dict]:
    """批次重算：同行「单批产量 × 年批次数」vs「年产量」列（物料平衡表典型错误）"""
    issues = []
    for t in tables:
        headers = t.get("headers", [])
        if len(headers) < 3:
            continue
        col_batch = col_count = col_annual = None
        for i, h in enumerate(headers):
            hs = h.replace(" ", "")
            if col_batch is None and ("单批" in hs or "批产" in hs or "kg/批" in hs or "t/批" in hs):
                col_batch = i
            elif col_count is None and ("批次" in hs or "批数" in hs):
                col_count = i
            elif col_annual is None and ("年产" in hs or "年排放" in hs or "年产生" in hs):
                col_annual = i
        if None in (col_batch, col_count, col_annual):
            continue
        cap = t.get("caption") or "未命名表"
        found = 0
        for row in t.get("rows", []):
            if found >= 3:
                break
            if max(col_batch, col_count, col_annual) >= len(row):
                continue
            b, bu = _cell_value_unit(row[col_batch], headers[col_batch])
            n, _ = _cell_value_unit(row[col_count], headers[col_count])
            ann, au = _cell_value_unit(row[col_annual], headers[col_annual])
            if not b or not n or not ann or n <= 0 or n > 100000:
                continue
            b_t = b / 1000 if bu == "kg/批" or (bu == "kg" and au in ("t/a", "t")) else b
            ann_t = ann / 1000 if au == "kg/a" else ann
            calc = b_t * n
            if calc <= 0:
                continue
            dev = abs(calc - ann_t) / calc
            tol = max(0.5 * (10 ** -_decimals(row[col_annual])) , calc * 0.005)
            if dev <= 0.02 or abs(calc - ann_t) <= tol:
                continue
            label = next((c for c in row[:2] if c.strip() and re.search(r"[一-龥A-Za-z]", c)), "")
            rel = dev * 100
            issues.append(build_issue(
                "R-XCHK-BATCH", "P1", "交叉核验",
                f"{cap}「{label[:15]}」年产量与单批×批次数不符（偏差{rel:.0f}%）",
                f"{cap}：{label}单批产量 {b:g}{bu or ''} × 年批次 {n:g} 批 = {calc:.3f} t/a，"
                f"但年产量列为 {ann:g}{au or ''}。",
                evidence=" | ".join(x for x in row if x.strip())[:80],
                law_ref="HJ 2.1-2016 物料衡算数据准确性要求",
                suggestion=f"请复核{label}年产量：按单批×批次应为 {calc:.3f} t/a，修正年产量或批次/单批数据，并同步更新相关汇总。"
            ))
            issues[-1]["chapter"] = t.get("chapter", "")
            issues[-1]["evidence_location"] = issues[-1]["evidence"][:50]
            found += 1
    return issues


_HEIGHT_COL_RE = re.compile(r"^(\d{2,3})\s*m$")
_HEIGHT_CTX_RE = re.compile(r"排气筒[^。\n]{0,40}?(\d{2,3})\s*m")
_RATE_RE = re.compile(r"([\d.]+)\s*kg/h")


def _check_limit_interpolation(tables: list[dict], full_text: str) -> list[dict]:
    """标准限值内插：报告自带标准表给出 h1/h2 两档限值，排气筒高度居中 → 线性内插 vs 报告引用值
    （自参照核验，不使用外部标准数据，零编造）"""
    issues = []
    standards: dict[str, dict[int, float]] = {}
    for t in tables:
        headers = [str(h).strip() for h in t.get("headers", [])]
        hcols = {}
        for i, h in enumerate(headers):
            m = _HEIGHT_COL_RE.match(h)
            if m:
                hcols[i] = int(m.group(1))
        if len(hcols) < 2:
            continue
        for row in t.get("rows", []):
            if not row:
                continue
            name = re.sub(r"\s", "", row[0])
            name_core = re.sub(r"[（(].*?[）)]", "", name)
            if not (2 <= len(name_core) <= 12):
                continue
            limits = {}
            for i, h in hcols.items():
                if i < len(row):
                    v = _parse_num(row[i])
                    if v is not None:
                        limits[h] = v
            if len(limits) >= 2:
                standards.setdefault(name_core, {}).update(limits)
    if not standards:
        return issues

    for m in _HEIGHT_CTX_RE.finditer(full_text):
        H = int(m.group(1))
        window = full_text[max(0, m.start() - 60):m.end() + 60]
        pollutant = next((k for k in standards if k in window), None)
        if not pollutant:
            continue
        rm = _RATE_RE.search(window)
        if not rm:
            continue
        stated = float(rm.group(1))
        limits = standards[pollutant]
        hs = sorted(limits)
        bracket = [(h1, h2) for h1, h2 in zip(hs, hs[1:]) if h1 < H < h2]
        if not bracket:
            continue
        h1, h2 = bracket[0]
        interp = limits[h1] + (limits[h2] - limits[h1]) * (H - h1) / (h2 - h1)
        if interp <= 0:
            continue
        dev = abs(stated - interp) / interp
        if dev <= 0.10:
            continue
        issues.append(build_issue(
            "R-XCHK-INT", "P1", "交叉核验",
            f"{pollutant} {H}m 排气筒排放速率限值疑似计算错误（内插值 {interp:.3g} vs 报告 {stated:g} kg/h）",
            f"报告引用标准表中{pollutant}限值：{h1}m 为 {limits[h1]:g} kg/h、{h2}m 为 {limits[h2]:g} kg/h。"
            f"排气筒高度 {H}m 位于两档之间，按内插法应为 {interp:.3g} kg/h，但报告标注 {stated:g} kg/h，偏差 {dev * 100:.0f}%。",
            evidence=window.strip()[:80],
            law_ref="GB 16297 等排放标准内插法计算规则",
            suggestion=f"请将{pollutant} {H}m 排气筒排放速率限值修正为内插计算值约 {interp:.3g} kg/h，或说明取值依据。"
        ))
        issues[-1]["evidence_location"] = m.group(0)[:50]
        break
    return issues


_HOURS_RE = re.compile(r"年(?:运行|工作|生产)[^0-9]{0,6}(\d{3,4})\s*(?:h|小时)")


def _check_rate_total(tables: list[dict], full_text: str) -> list[dict]:
    """速率↔总量换算：排放速率(kg/h) × 年运行小时 vs 排放量(t/a)"""
    issues = []
    hm = _HOURS_RE.search(full_text)
    if not hm:
        return issues
    hours = int(hm.group(1))
    if not (100 <= hours <= 9000):
        return issues
    for t in tables:
        headers = t.get("headers", [])
        if len(headers) < 2:
            continue
        rate_col = tot_col = None
        for i, h in enumerate(headers):
            hs = h.replace(" ", "")
            if rate_col is None and ("排放速率" in hs or "产生速率" in hs):
                rate_col = i
            elif tot_col is None and "速率" not in hs and ("排放量" in hs or "产生量" in hs):
                tot_col = i
        if None in (rate_col, tot_col):
            continue
        cap = t.get("caption") or "未命名表"
        found = 0
        for row in t.get("rows", []):
            if found >= 3:
                break
            if max(rate_col, tot_col) >= len(row):
                continue
            first = next((c for c in row[:2] if c.strip()), "")
            if any(lb in first for lb in TOTAL_LABELS):
                continue
            r = _parse_num(row[rate_col])
            tot, tu = _cell_value_unit(row[tot_col], headers[tot_col])
            if not r or not tot or r <= 0:
                continue
            tot_t = tot / 1000 if tu == "kg/a" else tot
            calc = r * hours / 1000
            if calc <= 0:
                continue
            dev = abs(calc - tot_t) / calc
            tol = max(0.5 * (10 ** -_decimals(row[tot_col])), calc * 0.01)
            if dev <= 0.10 or abs(calc - tot_t) <= tol:
                continue
            label = next((c for c in row[:2] if c.strip() and re.search(r"[一-龥A-Za-z]", c)), "")
            issues.append(build_issue(
                "R-XCHK-RATE", "P1", "交叉核验",
                f"{cap}「{label[:15]}」排放量与速率×运行时间不符（偏差{dev * 100:.0f}%）",
                f"{cap}：{label}排放速率 {r:g} kg/h × 年运行 {hours} h = {calc:.3f} t/a，"
                f"但排放量列为 {tot:g}{tu or 't/a'}。",
                evidence=" | ".join(x for x in row if x.strip())[:80],
                law_ref="HJ 2.1-2016 数据一致性要求",
                suggestion=f"请复核{label}排放量或排放速率：按 {r:g} kg/h × {hours} h 应为 {calc:.3f} t/a，核实年运行时间或修正数据。"
            ))
            issues[-1]["chapter"] = t.get("chapter", "")
            issues[-1]["evidence_location"] = issues[-1]["evidence"][:50]
            found += 1
    return issues
