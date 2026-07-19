"""图文一致性检查 v2 — 多模态视觉审核：图件提取 → 图型分类 → 分型检查 → 结构化裁决
PDF 按图注页渲染（矢量图全覆盖）；DOCX 内嵌图与图注配对。
无视觉模型时降级为纯文本逻辑比对。"""
import base64
import os
import re
from pathlib import Path

from app.engine.context import build_step_context
from app.engine.llm_json import parse_llm_json
from app.llm.client import chat, get_active_profile, chat_vision
from app.engine.grader import build_issue

TARGET_CHAPTERS = ["图", "表", "工艺", "平面布置", "监测布点"]
MAX_FIGURES = int(os.getenv("VISION_MAX_FIGURES", "20"))
MIN_IMAGE_BYTES = 10 * 1024

CAPTION_RE = re.compile(r"图\s*\d+[\-–\.]?\d*\s*[：: ]?\s*[^\n，。；]{2,30}")

FIGURE_TYPE_CHECKS = {
    "工艺流程图": "工序顺序与正文工艺描述是否一致；产污节点（废气/废水/固废产生点）是否标注",
    "平面布置图": "环保设施（除尘/污水站/危废间）位置与措施章节描述是否矛盾；厂区布局与正文是否一致",
    "监测布点图": "点位数量、方位与监测布点表是否一致；是否覆盖厂界与敏感点",
    "敏感目标分布图": "敏感目标与正文敏感目标表是否一致；距离标注是否与正文相符",
    "数据图表": "图中数值与正文表格/文字数值是否一致；单位是否标注",
    "地理位置图": "项目位置与正文建设地点描述是否一致",
    "其他": "图片内容与正文相关描述是否存在明显矛盾",
}


def _find_captions(full_text: str) -> list[dict]:
    """从全文找图注及其上下文段落"""
    captions = []
    for m in CAPTION_RE.finditer(full_text):
        cap = m.group(0).strip()
        if len(cap) < 5:
            continue
        ctx_start = max(0, m.start() - 150)
        ctx_end = min(len(full_text), m.end() + 150)
        captions.append({
            "caption": cap,
            "context": full_text[ctx_start:ctx_end].replace("\n", " "),
        })
        if len(captions) >= MAX_FIGURES:
            break
    return captions


def _extract_pdf_pages(file_path: str) -> list[dict]:
    """PDF：定位含图注的页 → 渲染整页 PNG（矢量图全覆盖）。
    扫描件（无文本层）回退为均匀抽样渲染。"""
    out = []
    try:
        import fitz
        doc = fitz.open(file_path)
        total = len(doc)
        # Pass 1: 图注页
        for pno in range(total):
            page = doc[pno]
            text = page.get_text()
            m = CAPTION_RE.search(text)
            if not m:
                continue
            pix = page.get_pixmap(dpi=120)
            out.append({
                "image_b64": base64.b64encode(pix.tobytes("png")).decode(),
                "caption": m.group(0).strip(),
                "page": pno + 1,
            })
            if len(out) >= MAX_FIGURES:
                break
        # Pass 2: 无图注命中时，检测扫描件并均匀抽样
        if not out and total > 0:
            text_len = sum(len(doc[p].get_text().strip()) for p in range(min(total, 10)))
            if text_len < 200:  # 基本是扫描件
                step = max(1, total // MAX_FIGURES)
                for pno in range(0, total, step):
                    pix = doc[pno].get_pixmap(dpi=100)
                    out.append({
                        "image_b64": base64.b64encode(pix.tobytes("png")).decode(),
                        "caption": f"扫描件第 {pno + 1} 页",
                        "page": pno + 1,
                    })
                    if len(out) >= MAX_FIGURES:
                        break
        doc.close()
    except Exception:
        pass
    return out


def _extract_docx_images(file_path: str) -> list[dict]:
    """DOCX：内嵌图（>10KB）按文档顺序提取"""
    out = []
    try:
        from docx import Document
        doc = Document(file_path)
        for rel in doc.part.rels.values():
            if "image" in rel.reltype:
                img_data = rel.target_part.blob
                if len(img_data) > MIN_IMAGE_BYTES:
                    out.append({
                        "image_b64": base64.b64encode(img_data).decode(),
                        "caption": "",
                        "page": 0,
                    })
                if len(out) >= MAX_FIGURES:
                    break
    except Exception:
        pass
    return out


def extract_figures(file_path: str) -> list[dict]:
    """统一图件提取入口：PDF 渲染图注页 / DOCX 内嵌图"""
    path = Path(file_path)
    if not path.exists() or path.is_dir():
        return []
    ext = path.suffix.lower()
    if ext == ".pdf":
        figs = _extract_pdf_pages(file_path)
        if figs:
            return figs
        return []
    elif ext == ".docx":
        return _extract_docx_images(file_path)
    return []


VISION_PROMPT = """你是环评审核专家。这是一张环评报告中的图片，请分析并检查图文一致性。

图注（报告中对该图的标注）：{caption}

报告正文相关上下文：
{context}

请先判断图型（工艺流程图/平面布置图/监测布点图/敏感目标分布图/数据图表/地理位置图/其他），再检查：
{type_check}
以及：图片标题/图例与图注是否一致；有无明显数据矛盾或标注错误。

输出JSON（不要带```标记）：
{{"type":"图型","consistent":true/false,"issues":[{{"severity":"P0/P1/P2","title":"问题标题","finding":"具体发现","suggestion":"修改建议"}}]}}
consistent 为 true 时 issues 输出 []。只输出JSON。"""


async def _check_one_figure(fig: dict, full_text: str, vis_profile, caption_hint: str = "") -> tuple[list[dict], str | None]:
    """单张图视觉检查，返回 (issues, error)。图型驱动分型检查。"""
    caption = fig.get("caption") or caption_hint or "（无图注）"
    context = ""
    for cap in _find_captions(full_text):
        if cap["caption"][:10] in caption or caption[:10] in cap["caption"]:
            context = cap["context"]
            break
    if not context:
        context = full_text[:1500]

    prompt = VISION_PROMPT.format(
        caption=caption,
        context=context,
        type_check="\n".join([f"- 若为{k}：{v}" for k, v in list(FIGURE_TYPE_CHECKS.items())[:6]]),
    )
    try:
        resp = await chat_vision(prompt, fig["image_b64"], profile=vis_profile)
        data = parse_llm_json(resp, expect="object") or {}
    except Exception as e:
        return [], str(e)[:100]

    issues = []
    fig_type = data.get("type", "图片")
    loc = f"图注「{caption[:30]}」" + (f"（第{fig['page']}页）" if fig.get("page") else "")
    for item in (data.get("issues") or []):
        if isinstance(item, dict) and item.get("title"):
            issues.append(build_issue(
                "R-FIG-VIS-001", item.get("severity", "P1"), "图文一致性",
                f"[{fig_type}] {item['title']}",
                item.get("finding", ""),
                evidence=loc,
                law_ref="HJ 2.1-2016 要求报告图表应与文字内容一致",
                suggestion=item.get("suggestion", "请核实该图与正文的一致性。"),
            ))
            issues[-1]["chapter"] = caption[:50]
    return issues, None


async def check_text_figure_consistency(text_data: dict, file_path: str = "") -> list[dict]:
    """图文一致性检查主入口：视觉模型优先（全部图件分型检查）；无视觉模型降级纯文本比对。"""
    issues = []
    full_text = text_data.get("full_text", "")

    figures = extract_figures(file_path) if file_path else []
    if figures:
        vis_profile = None
        try:
            from app.llm.client import get_vision_profile
            vis_profile = await get_vision_profile()
        except Exception:
            vis_profile = None

        if vis_profile:
            import asyncio
            tasks = [asyncio.create_task(_check_one_figure(fig, full_text, vis_profile))
                     for fig in figures]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            errs = 0
            for res in results:
                if isinstance(res, Exception):
                    errs += 1
                    continue
                fig_issues, err = res
                if err:
                    errs += 1
                issues.extend(fig_issues)
            if errs and not issues:
                raise RuntimeError(f"{errs} 张图片视觉检查全部失败")
            if errs:
                issues.append(build_issue(
                    "R-FIG-VIS-ERR", "P2", "图文一致性",
                    f"{errs}/{len(figures)} 张图片视觉检查未完成",
                    "部分图片因视觉模型调用异常未能检查，建议人工核对这些图件。",
                    suggestion="检查视觉模型配置后重新审核，或人工核对图件。"))
            return issues

    # ── 降级：纯文本逻辑比对 ──
    has_fig = any(kw in full_text[:2000] for kw in ["图", "附图", "插图", "见图", "下表", "上图"])
    if not has_fig:
        return issues

    profile = await get_active_profile()
    if not profile:
        raise RuntimeError("未配置启用的 LLM Profile")

    context = build_step_context(text_data, TARGET_CHAPTERS)

    prompt = f"""你是环评审核专家。请检查报告中文字描述与图表引用的逻辑一致性。
（注：本检查仅基于文本对比，未使用视觉模型分析图片内容。）

报告相关章节内容：
{context}

请检查：
1. 正文中引用图表编号是否连续？
2. 同一数据在不同章节描述是否一致？
3. 正文描述值与表格中数据是否一致？
4. 工艺流程文字描述与流程图标题是否匹配？

以JSON数组格式输出：[{{"severity":"P0/P1/P2","title":"...","finding":"...","evidence_location":"...","reasoning":"...","law_ref":"...","suggestion":"..."}}]
如果没有问题输出 []。只输出JSON。"""

    resp = await chat(prompt, profile=profile)
    data = parse_llm_json(resp, expect="array") or []
    for item in data:
        if isinstance(item, dict) and item.get("title"):
            issues.append(build_issue("R-FIG-001", item.get("severity", "P1"), "图文一致性", item["title"], item.get("finding", ""),
                evidence=item.get("evidence_location", ""), law_ref=item.get("law_ref", ""), suggestion=item.get("suggestion", "")))

    return issues
