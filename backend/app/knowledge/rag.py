from app.knowledge.retriever import search_knowledge
from app.llm.client import chat

RAG_SYSTEM_PROMPT = """你是一个环评审核知识助手。请基于提供的标准文档内容回答用户问题。
要求：
1. 如果文档中有明确答案，请直接引用并标注来源。
2. 如果文档中没有明确答案，请诚实说明"未在标准库中找到明确依据"。
3. 回答要简洁专业，使用环评术语。
4. 如果涉及标准编号（如 GB、HJ），请完整列出。
"""


async def ask_knowledge(question: str, history: list[dict] = None) -> tuple[str, list[dict]]:
    results = search_knowledge(question, top_k=5)

    if not results:
        return "知识库中暂未找到相关信息。请尝试更换关键词或等待知识库扩容。", []

    context_parts = []
    for i, r in enumerate(results):
        context_parts.append(f"[来源{i+1}] {r['title']} | {r['relative_path']}\n{r['excerpt']}")

    context = "\n\n---\n\n".join(context_parts)

    history_text = ""
    if history:
        history_text = "\n".join([
            f"{'用户' if h['role'] == 'user' else '助手'}: {h['content']}"
            for h in history[-8:]
        ])

    prompt = f"""参考文档内容：
{context}

历史对话：
{history_text or '无'}

用户问题：{question}

请基于以上文档内容回答用户问题。"""

    try:
        answer = await chat(prompt, system=RAG_SYSTEM_PROMPT)
    except Exception as e:
        answer = f"大模型调用失败：{str(e)}。以下为检索到的相关内容摘要：\n\n{context[:2000]}"

    sources = [
        {
            "title": r["title"],
            "relative_path": r["relative_path"],
            "category": r.get("category", ""),
            "excerpt": r["excerpt"][:300],
        }
        for r in results[:5]
    ]

    return answer, sources
