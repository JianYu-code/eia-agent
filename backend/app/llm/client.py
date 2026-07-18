from typing import Optional, AsyncIterator
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from app.config import LLM_DEFAULT_BASE_URL, LLM_DEFAULT_MODEL, LLM_DEFAULT_API_KEY
from app.database import async_session
from app.models.project import LLMProfile


async def get_active_profile() -> Optional[LLMProfile]:
    from sqlalchemy import select
    async with async_session() as db:
        r = await db.execute(select(LLMProfile).where(LLMProfile.active == True))
        return r.scalar_one_or_none()


def build_llm(profile: Optional[LLMProfile] = None) -> ChatOpenAI:
    base_url = LLM_DEFAULT_BASE_URL
    model = LLM_DEFAULT_MODEL
    api_key = LLM_DEFAULT_API_KEY
    max_retries = 3
    extra_body = None

    if profile:
        base_url = profile.base_url or base_url
        model = profile.model or model
        if profile.api_key:
            api_key = profile.api_key
        max_retries = profile.max_retries or max_retries
        extra_body = profile.extra_body

    kwargs = dict(
        model=model,
        base_url=base_url,
        api_key=api_key,
        temperature=0.1,
        max_retries=max_retries,
    )
    if extra_body:
        kwargs["model_kwargs"] = {"extra_body": extra_body}

    return ChatOpenAI(**kwargs)


async def chat(prompt: str, system: str = "", profile: Optional[LLMProfile] = None) -> str:
    llm = build_llm(profile)
    messages = []
    if system:
        messages.append(SystemMessage(content=system))
    messages.append(HumanMessage(content=prompt))
    response = await llm.ainvoke(messages)
    return response.content


async def chat_stream(prompt: str, system: str = "", profile: Optional[LLMProfile] = None) -> AsyncIterator[str]:
    llm = build_llm(profile)
    messages = []
    if system:
        messages.append(SystemMessage(content=system))
    messages.append(HumanMessage(content=prompt))
    async for chunk in llm.astream(messages):
        if chunk.content:
            yield chunk.content


async def get_vision_profile() -> Optional[LLMProfile]:
    from sqlalchemy import select
    async with async_session() as db:
        r = await db.execute(select(LLMProfile).where(LLMProfile.vision_active == True))
        vp = r.scalar_one_or_none()
        if vp:
            return vp
        r2 = await db.execute(select(LLMProfile).where(LLMProfile.purpose == "vision_review", LLMProfile.api_key != ""))
        return r2.scalar_one_or_none()


async def chat_vision(prompt: str, image_base64: str, system: str = "", profile: Optional[LLMProfile] = None) -> str:
    """调用视觉模型，传入图片 base64 + 文本 prompt"""
    if not profile:
        profile = await get_vision_profile()
    if not profile:
        raise ValueError("未配置视觉模型。请在设置中添加 purpose=vision_review 的 LLM Profile。")

    base_url = profile.base_url or LLM_DEFAULT_BASE_URL
    model = profile.model or "glm-4v-flash"
    api_key = profile.api_key or LLM_DEFAULT_API_KEY

    import httpx
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system} if system else None,
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}},
                {"type": "text", "text": prompt},
            ]},
        ],
        "max_tokens": 2000,
        "temperature": 0.1,
    }
    if not system:
        payload["messages"].pop(0)

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, json=payload, headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
