"""LLM 输出 JSON 解析 — 剥标记、提取对象/数组、常见格式修复；失败抛 LLMParseError"""
import json
import re


class LLMParseError(Exception):
    pass


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json|JSON)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _fix_trailing_commas(text: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", text)


def parse_llm_json(resp: str, expect: str = "any"):
    """解析 LLM 输出的 JSON。expect: 'object' | 'array' | 'any'。
    输出 'null'/空串 时返回 None（object）或 []（array）。
    输出被截断（未闭合）时打捞最后一个完整键值对/元素后补齐解析。
    彻底无法解析时抛 LLMParseError。"""
    if resp is None:
        raise LLMParseError("空响应")
    text = _strip_code_fence(resp)
    if not text or text.lower() == "null":
        return [] if expect == "array" else None

    try:
        candidate = _extract_json_span(text, expect)
    except LLMParseError:
        candidate = _salvage_truncated(text)

    for raw in (candidate, _fix_trailing_commas(candidate)):
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if expect == "array" and isinstance(data, dict):
            data = [data]
        if expect == "object" and isinstance(data, list):
            data = data[0] if data else None
        return data

    raise LLMParseError(f"JSON 解析失败: {text[:120]}")


def _salvage_truncated(text: str) -> str:
    """截断打捞：截到最后一个完整顶层值，补齐未闭合的引号/括号"""
    start = -1
    for i, ch in enumerate(text):
        if ch in "{[":
            start = i
            break
    if start < 0:
        raise LLMParseError(f"未找到 JSON 起始符: {text[:120]}")

    stack = []
    in_str = False
    escape = False
    last_safe = -1  # 最后一个完整顶层值结束的位置
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if not stack:
                raise LLMParseError(f"JSON 结构异常: {text[start:start+80]}")
            opener = stack.pop()
            if not stack:
                return text[start:i + 1]  # 本来就完整
            last_safe = i + 1
        elif ch == "," and len(stack) >= 1:
            last_safe = i

    if last_safe <= start:
        raise LLMParseError(f"JSON 无法打捞: {text[start:start+80]}")

    frag = text[start:last_safe].rstrip().rstrip(",")
    # 重新扫描片段，补齐未闭合的括号
    stack = []
    in_str = False
    escape = False
    for ch in frag:
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in "{[":
            stack.append(ch)
        elif ch in "}]" and stack:
            stack.pop()
    if in_str:
        frag += '"'
    closers = {"{": "}", "[": "]"}
    for opener in reversed(stack):
        frag += closers[opener]
    return frag


def _extract_json_span(text: str, expect: str) -> str:
    """从混杂文本中提取第一个完整 JSON 对象/数组"""
    if expect == "array":
        start = text.find("[")
        if start < 0:
            start = text.find("{")
    elif expect == "object":
        start = text.find("{")
        if start < 0:
            start = text.find("[")
    else:
        starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
        start = min(starts) if starts else -1
    if start < 0:
        raise LLMParseError(f"未找到 JSON 起始符: {text[:120]}")

    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise LLMParseError(f"JSON 未闭合: {text[start:start + 120]}")
