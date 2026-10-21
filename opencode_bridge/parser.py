"""解析 opencode 的原始输出，分离正文与工具调用。

opencode 可能输出：
- 纯文本；
- 逐行 JSON 事件（`text` / `content` / `delta` / `part.text`）；
- 模型按提示词输出的 ```json {"tool_calls": [...]} ``` 代码块。

本模块的职责是把这些形态统一成 `{"content": str | None, "tool_calls": [...]}`。
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, List, Optional

# 匹配 ```json ... ``` 或 ``` ... ``` 包裹的 JSON 对象
_CODE_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_text(raw_output: str) -> str:
    """从逐行 JSON 事件里抽取文本；抽不到就原样返回。"""
    cleaned_lines: List[str] = []
    for line in raw_output.splitlines():
        line_str = line.strip()
        if not line_str:
            continue
        try:
            data = json.loads(line_str)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        part = data.get("part")
        part_text = part.get("text") if isinstance(part, dict) else None
        value = (
            data.get("text")
            or data.get("content")
            or data.get("delta")
            or part_text
        )
        if isinstance(value, str):
            cleaned_lines.append(value)

    return "".join(cleaned_lines).strip() if cleaned_lines else raw_output.strip()


def _find_json_blocks(full_text: str) -> List[str]:
    """找出文本里的 JSON 代码块；整段就是 JSON 时也认。"""
    blocks = _CODE_BLOCK_RE.findall(full_text)
    if not blocks and full_text.startswith("{") and full_text.endswith("}"):
        blocks = [full_text]
    return blocks


def _arguments_to_str(arguments: Any) -> str:
    """OpenAI 要求 arguments 是 JSON 字符串。"""
    if isinstance(arguments, dict):
        return json.dumps(arguments, ensure_ascii=False)
    return str(arguments)


def _make_tool_call(name: str, arguments: Any) -> Dict[str, Any]:
    return {
        "id": f"call_{uuid.uuid4().hex[:10]}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": _arguments_to_str(arguments),
        },
    }


def _parse_block(block: str) -> Optional[List[Dict[str, Any]]]:
    """解析单个 JSON 块，返回工具调用列表；不是工具调用则返回 None。"""
    try:
        parsed = json.loads(block)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None

    tool_calls: List[Dict[str, Any]] = []

    # 格式 A: {"tool_calls": [{"name": "...", "arguments": {...}}]}
    if "tool_calls" in parsed and isinstance(parsed["tool_calls"], list):
        for item in parsed["tool_calls"]:
            if isinstance(item, dict) and "name" in item:
                args = item.get("arguments", item.get("args", {}))
                tool_calls.append(_make_tool_call(item["name"], args))
        return tool_calls or None

    # 格式 B: 单工具简写 {"name": "...", "arguments": {...}}
    if "name" in parsed and ("arguments" in parsed or "args" in parsed):
        args = parsed.get("arguments", parsed.get("args", {}))
        return [_make_tool_call(parsed["name"], args)]

    return None


def _strip_blocks(full_text: str, blocks: List[str]) -> str:
    """把已经识别为工具调用的 JSON 块从正文里删掉。"""
    clean_content = full_text
    for block in blocks:
        clean_content = re.sub(
            r"```(?:json)?\s*" + re.escape(block) + r"\s*```", "", clean_content
        )
        clean_content = clean_content.replace(block, "")
    return clean_content.strip()


def parse_opencode_output_to_response(raw_output: str) -> Dict[str, Any]:
    """解析 OpenCode 输出，提取正文并识别单/多工具调用 JSON。"""
    full_text = _extract_text(raw_output)
    json_blocks = _find_json_blocks(full_text)

    tool_calls: List[Dict[str, Any]] = []
    matched_blocks: List[str] = []

    for block in json_blocks:
        parsed_calls = _parse_block(block)
        if parsed_calls:
            tool_calls.extend(parsed_calls)
            matched_blocks.append(block)

    if not tool_calls:
        return {"content": full_text, "tool_calls": None}

    clean_content = _strip_blocks(full_text, matched_blocks)
    return {
        "content": clean_content or None,
        "tool_calls": tool_calls,
    }
