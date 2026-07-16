import json
import uuid
from datetime import datetime
from app.knowledge.retriever import search_knowledge
from app.llm.client import chat, get_active_profile

CHAPTERS = [
    {"id": "ch01", "title": "前言", "query": "环境影响评价 前言 编制背景 技术路线"},
    {"id": "ch02", "title": "总则", "query": "环境影响评价 编制依据 评价因子 评价标准 评价等级"},
    {"id": "ch03", "title": "建设项目工程分析", "query": "工程分析 工艺流程 污染源源强核算 物料平衡"},
    {"id": "ch04", "title": "环境现状调查与评价", "query": "环境现状调查 环境质量监测 评价方法"},
    {"id": "ch05", "title": "环境影响预测与评价", "query": "环境影响预测 大气预测 地表水预测 噪声预测"},
    {"id": "ch06", "title": "环境保护措施及其可行性论证", "query": "污染防治措施 可行性论证 技术经济分析"},
    {"id": "ch07", "title": "环境影响经济损益分析", "query": "环境影响经济损益 环保投资 经济效益"},
    {"id": "ch08", "title": "环境管理与监测计划", "query": "环境管理 监测计划 竣工验收 排污许可"},
    {"id": "ch09", "title": "环境影响评价结论", "query": "环境影响评价结论 综合评价 建议"},
]


async def generate_report(project_info: dict, progress_callback=None) -> dict:
    """逐章生成环评报告，返回 {chapter_id: content}"""
    profile = await get_active_profile()
    results = {}
    info_text = _format_project_info(project_info)

    for idx, ch in enumerate(CHAPTERS):
        if progress_callback:
            await progress_callback(
                int(10 + 80 * idx / len(CHAPTERS)),
                f"生成{ch['title']}...",
                f"正在生成第 {idx+1}/{len(CHAPTERS)} 章：{ch['title']}"
            )

        kb = search_knowledge(ch["query"], top_k=4)
        kb_ctx = "\n\n".join([f"[{r['title']}] {r['excerpt'][:400]}" for r in kb])

        prompt = f"""你是具有多年经验的环评工程师。请撰写环评报告"{ch['title']}"章节。

项目信息：
{info_text}

参考标准（请在正文中适当引用）：
{kb_ctx}

要求：
1. 使用专业的环评术语和规范格式
2. 如果参考标准中有相关条款，请标注标准编号
3. 内容要具体，不要空洞的套话
4. 如涉及数值，应给出参考范围或计算依据
5. 章节标题使用"{ch['title']}"
6. 直接输出章节正文，不要写"以下是XX章节"之类的引导语"""

        try:
            content = await chat(prompt, profile=profile)
            results[ch["id"]] = content
        except Exception as e:
            results[ch["id"]] = f"[生成失败: {str(e)}]"

    return results


def render_docx(project_info: dict, chapters: dict, output_path: str):
    """将生成的各章节渲染为 DOCX 文件"""
    from docx import Document
    from docx.shared import Pt, Cm, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()

    style = doc.styles["Normal"]
    font = style.font
    font.name = "宋体"
    font.size = Pt(12)

    title = doc.add_heading(project_info.get("name", "建设项目环境影响报告"), level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph(f"建设单位：{project_info.get('company', '')}")
    doc.add_paragraph(f"编制日期：{datetime.now().strftime('%Y年%m月')}")

    for ch in CHAPTERS:
        content = chapters.get(ch["id"], "")
        if not content or content.startswith("["):
            continue
        doc.add_heading(ch["title"], level=1)

        for para_text in content.split("\n"):
            para_text = para_text.strip()
            if not para_text:
                continue
            if para_text.startswith("#"):
                continue
            p = doc.add_paragraph(para_text)

    doc.save(output_path)
    return output_path


def _format_project_info(info: dict) -> str:
    return f"""项目名称：{info.get('name', '未命名')}
建设单位：{info.get('company', '')}
建设地点：{info.get('location', '')}
建设性质：{info.get('nature', '新建')}
行业类别：{info.get('industry', '')}
总投资：{info.get('investment', '')} 万元
用地面积：{info.get('area', '')} 平方米

工程概况：
{info.get('overview', '')}

主要原辅材料：
{info.get('materials', '')}

工艺流程简述：
{info.get('process', '')}

主要设备：
{info.get('equipment', '')}

废气污染源及治理措施：
{info.get('air_pollution', '')}

废水污染源及治理措施：
{info.get('water_pollution', '')}

固废产生及处置：
{info.get('solid_waste', '')}

噪声源及控制措施：
{info.get('noise', '')}

环境敏感目标：
{info.get('sensitive_targets', '')}

环境功能区划：
{info.get('env_function', '')}

补充说明：
{info.get('notes', '')}"""
