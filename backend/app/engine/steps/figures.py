import base64
import json
from pathlib import Path
from app.llm.client import chat, get_active_profile, chat_vision
from app.engine.grader import build_issue


async def check_text_figure_consistency(full_text: str, file_path: str = "") -> list[dict]:
    """图文一致性检查。支持两种模式：
    1. 视觉模型分析（优先）：提取报告中的图片→视觉模型描述→对比文本
    2. 纯文本逻辑比对（降级）：检查文字中引用的图表编号是否一致
    """
    issues = []

    # ── 视觉模型分析 ──
    images = _extract_images(file_path) if file_path else []
    if images:
        try:
            vis_profile = None
            try:
                from app.llm.client import get_vision_profile
                vis_profile = await get_vision_profile()
            except Exception:
                pass

            if vis_profile:
                for idx, img_b64 in enumerate(images[:3]):  # 最多分析3张图片
                    resp = await chat_vision(
                        f"""你是环评审核专家。请分析这张环评报告中的图片/图表。

请描述图片内容，然后检查：
1. 图表中的数据（如有）是否与以下文字描述一致？
2. 图片标题/图例是否与文字中引用的名称一致？
3. 是否有明显的数据矛盾或标注错误？

报告文字内容（供对照）：
{full_text[:2000]}

请简要回答：1.图片内容 2.是否与文字一致 3.如有问题请指出。""",
                        img_b64,
                        profile=vis_profile
                    )
                    if "一致" not in resp[:200] and "问题" in resp[:200] or "矛盾" in resp[:200] or "不同" in resp[:200] or "错误" in resp[:200]:
                        issues.append(build_issue(
                            "R-FIG-VIS-001", "P1", "图文一致性",
                            f"视觉模型分析发现潜在不一致（图片{idx+1}）",
                            resp[:300],
                            law_ref="HJ 2.1-2016 要求报告图表应与文字内容一致",
                            suggestion="请核实该图片/图表中的数据是否与报告正文一致。"
                        ))
            return issues
        except Exception:
            pass

    # ── 降级：纯文本逻辑比对 ──
    has_fig = any(kw in full_text[:2000] for kw in ["图", "附图", "插图", "见图", "下表", "下表", "上图"])
    if not has_fig:
        return issues

    profile = await get_active_profile()
    if not profile:
        return issues

    prompt = f"""你是环评审核专家。请检查报告中文字描述与图表引用的逻辑一致性。
（注：本检查仅基于文本对比，未使用视觉模型分析图片内容。）

报告内容（摘要）：
{full_text[:5000]}

请检查：
1. 正文中引用图表编号是否连续？
2. 同一数据在不同章节描述是否一致？
3. 正文描述值与表格中数据是否一致？
4. 工艺流程文字描述与流程图标题是否匹配？

以JSON格式输出：[{{"severity":"P0/P1/P2","title":"...","finding":"...","evidence_location":"...","reasoning":"...","law_ref":"...","suggestion":"..."}}]
如果没有问题输出 []。只输出JSON。"""

    try:
        resp = await chat(prompt, profile=profile)
        resp = resp.strip()
        if resp.startswith("```"): resp = resp.split("```")[1]
        data = json.loads(resp)
        for item in data:
            if isinstance(item, dict) and item.get("title"):
                issues.append(build_issue("R-FIG-001", item.get("severity","P1"), "图文一致性", item["title"], item.get("finding",""),
                    evidence=item.get("evidence_location",""), law_ref=item.get("law_ref",""), suggestion=item.get("suggestion","")))
    except Exception:
        pass

    return issues


def _extract_images(file_path: str) -> list[str]:
    """从 DOCX 或 PDF 中提取图片，返回 base64 列表"""
    path = Path(file_path)
    if not path.exists():
        return []

    images = []

    if path.suffix.lower() == ".docx":
        try:
            from docx import Document
            from docx.opc.constants import RELATIONSHIP_TYPE as RT
            doc = Document(file_path)
            for rel in doc.part.rels.values():
                if "image" in rel.reltype:
                    img_data = rel.target_part.blob
                    if len(img_data) > 1024:  # 跳过太小的图
                        images.append(base64.b64encode(img_data).decode())
        except Exception:
            pass

    elif path.suffix.lower() == ".pdf":
        try:
            import fitz
            doc = fitz.open(file_path)
            for page_num in range(min(3, len(doc))):
                page = doc[page_num]
                for img in page.get_images(full=True):
                    xref = img[0]
                    base_image = doc.extract_image(xref)
                    img_data = base_image["image"]
                    if len(img_data) > 1024:
                        images.append(base64.b64encode(img_data).decode())
            doc.close()
        except Exception:
            pass

    return images
