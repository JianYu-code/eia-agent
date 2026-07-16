from app.knowledge.retriever import search_knowledge
from app.llm.client import chat, get_active_profile

RAG_SYSTEM_PROMPT = """你是一名具有 10 年经验的环评审核专家，可以在相关导则中找到依据并给出专业解释与推理。

知识库范围：
你当前连接的是中国生态环境标准知识库，包含现行技术导则（HJ系列）、环境质量标准（GB系列）、污染物排放标准、源强核算技术指南、国家危险废物名录等标准文件，涵盖环评、验收、应急预案三大领域。

回答原则：

【严格遵循证据】
1. 每一个结论必须有标准依据（标准编号+条款名称）。优先引用检索到的标准原文。
2. 区分"标准明确规定"和"推导结论"——如果是推导，需声明推理过程。
3. 如果存在多个标准交叉规定，应分别列出各标准的要求并说明适用范围。

【诚实与边界】
4. 若知识库中确实没有相关标准依据，请明确告知"当前知识库未收录相关内容"，绝不编造。
5. 可以基于已有标准做合理推理，但需标注"根据XXX推理"。
6. 不要使用"可能""大概""一般"等不确定表述，除非标准原文本身使用此类措辞。

【格式要求】
7. 按以下结构组织回答：
   a) 直接回答（1-2句话）
   b) 标准依据（标准编号+条款）  
   c) 补充说明（适用范围、例外情况等）
   d) 引用标准清单
8. 如涉及数值（排放限值、防护距离等），必须给出完整的标准条款原文或表格名称。
9. 如涉及危废，必须标注废物类别和废物代码。

【示例①】
问：燃煤锅炉二氧化硫排放限值是多少？
答：
新建燃煤锅炉SO₂排放限值为 300 mg/m³。
依据：《锅炉大气污染物排放标准》（GB 13271-2014）表2"锅炉大气污染物排放限值"第1项：燃煤锅炉 SO₂ 排放限值 300 mg/m³。
补充：该限值适用于≤45.5m烟囱；重点地区执行特别排放限值 200 mg/m³（表3）。
引用标准：GB 13271-2014《锅炉大气污染物排放标准》

【示例②】
问：废活性炭属于危险废物吗？
答：
废活性炭是否属于危险废物取决于其吸附的物质。根据《国家危险废物名录（2021年版）》：
- 吸附有机溶剂、有机废气的废活性炭 → 危险废物，代码：HW06 废有机溶剂与含有机溶剂废物（900-409-06）
- 吸附其他危险物质的废活性炭 → 按其吸附物质归类
- 仅吸附水蒸气、空气等非危险物质的废活性炭 → 不属于危险废物
引用标准：《国家危险废物名录（2021年版）》、HJ 2025-2012《危险废物收集、贮存、运输技术规范》

【示例③】
问：知识库中没有的内容如何处理？
答：当前知识库未收录该内容。建议查阅以下标准原文：XXX（如有相关线索可提示检索方向）。"""


async def ask_knowledge(question: str, history: list[dict] = None) -> tuple[str, list[dict]]:
    results = search_knowledge(question, top_k=8)

    if not results:
        return "知识库中暂未找到相关信息。请尝试更换查询关键词，如使用标准编号（HJ XXXX）或标准全称进行检索。", []

    reranked = sorted(results, key=lambda r: (
        (0 if r.get("deprecated") else 1),
        -len(r.get("standard_id", "")),
        r.get("score", 1)
    ))

    context_parts = []
    for i, r in enumerate(reranked):
        std_tag = f" (标准编号: {r['standard_id']})" if r.get("standard_id") else ""
        dep_tag = " [已废止]" if r.get("deprecated") else ""
        context_parts.append(
            f"【来源{i+1}】{r['title']}{std_tag}{dep_tag}\n"
            f"章节: {r.get('category', '')} / {r['relative_path']}\n"
            f"正文:\n{r['excerpt']}"
        )

    context = "\n\n---\n\n".join(context_parts)

    history_text = ""
    if history:
        history_parts = []
        for h in history[-6:]:
            role = "用户" if h['role'] == 'user' else "助手"
            history_parts.append(f"{role}: {h['content'][:300]}")
        history_text = "\n".join(history_parts)

    prompt = f"""以下是标准知识库检索到的相关标准原文（按相关性和现行有效性排序）：

{context}

{f'对话历史：\n{history_text}\n' if history_text else ''}
用户提问：{question}

请严格基于上述检索结果回答。如果检索结果不足以支撑回答，请说明"当前知识库未收录"，不要编造。"""

    try:
        profile = await get_active_profile()
        answer = await chat(prompt, system=RAG_SYSTEM_PROMPT, profile=profile)
    except Exception as e:
        source_summary = "\n".join(
            [f"[{i+1}] {r['title']} | 标准: {r.get('standard_id', 'N/A')} | {r.get('relative_path', '')}"
             for i, r in enumerate(reranked[:5])]
        )
        answer = f"大模型调用失败：{e}\n\n以下为检索到的相关标准原文摘要：\n\n{source_summary}\n\n{context[:1500]}"

    sources = [
        {
            "title": r["title"],
            "relative_path": r["relative_path"],
            "category": r.get("category", ""),
            "standard_id": r.get("standard_id", ""),
            "deprecated": r.get("deprecated", False),
            "excerpt": r["excerpt"][:400],
        }
        for r in reranked[:5]
    ]

    return answer, sources
