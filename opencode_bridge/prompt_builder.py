"""把 OpenAI 请求组装成 opencode 能读懂的单一文本 Prompt。

opencode CLI 只接受一段文本输入，所以这里做三件事：
1. 注入「别动本地工具」的系统提示；
2. 把 `tools` 的 JSON Schema 铺成工具清单，并附上调用格式约束；
3. 把多轮历史（含 assistant tool_calls / tool 结果）序列化成带角色标签的段落。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Union

from .prompts import DISABLE_TOOLS_NOTICE, TOOL_CALL_INSTRUCTION
from .schemas import Message


def _serialize_content(content: Any) -> str:
    """把 content 统一压成字符串，兼容字符串与多模态内容块列表。"""
    if isinstance(content, list):
        text_parts: List[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                text_parts.append(item.get("text", ""))
            elif item.get("type") == "tool_use":
                text_parts.append(
                    f"[Tool Call Request: {item.get('name')} "
                    f"Args: {json.dumps(item.get('input'), ensure_ascii=False)}]"
                )
        return "\n".join(text_parts)
    if isinstance(content, str):
        return content
    return str(content or "")


def _render_tools_section(
    tools: List[Dict[str, Any]],
    tool_choice: Optional[Union[str, Dict[str, Any]]],
) -> str:
    """渲染 [Available Tools] 清单 + 调用约束。"""
    lines = ["[Available Tools for Function Calling]:"]
    for tool in tools:
        func = tool.get("function", {})
        name = func.get("name", "unknown")
        desc = func.get("description", "")
        params = json.dumps(func.get("parameters", {}), ensure_ascii=False)
        lines.append(
            f"- Tool Name: {name}\n"
            f"  Description: {desc}\n"
            f"  Parameters JSON Schema: {params}"
        )

    lines.append(TOOL_CALL_INSTRUCTION)

    # tool_choice 强制指定了某个具体工具
    if isinstance(tool_choice, dict) and "function" in tool_choice:
        forced_name = tool_choice["function"].get("name")
        if forced_name:
            lines.append(
                f"\n[CRITICAL]: You MUST call the tool `{forced_name}` in this turn."
            )
    # tool_choice == "required" 表示至少调用一个工具
    elif tool_choice == "required":
        lines.append("\n[CRITICAL]: You MUST call at least one tool in this turn.")

    return "\n".join(lines)


def format_messages_with_tools_to_prompt(
    messages: List[Message],
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None,
) -> str:
    """将 OpenAI 多轮消息与 Tools 定义组装为结构化 Prompt。"""
    parts: List[str] = [DISABLE_TOOLS_NOTICE]

    # 1. 注入外部工具 Schema 与调用规范
    if tools and tool_choice != "none":
        parts.append(_render_tools_section(tools, tool_choice))

    # 2. 序列化历史对话与工具交互记录
    for msg in messages:
        content_str = _serialize_content(msg.content)

        if msg.tool_calls:
            for tool_call in msg.tool_calls:
                func = tool_call.get("function", {})
                content_str += (
                    f"\n[Assistant Tool Call (ID: {tool_call.get('id', 'unknown')}): "
                    f"name={func.get('name')}, arguments={func.get('arguments')}]"
                )

        if msg.role == "tool":
            parts.append(
                f"[TOOL_RESPONSE (tool_call_id: {msg.tool_call_id})]:\n{content_str}"
            )
        else:
            parts.append(f"[{msg.role.upper()}]:\n{content_str}")

    parts.append("[ASSISTANT]:\n")
    return "\n\n".join(parts)
