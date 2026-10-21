"""OpenAI 兼容的请求数据模型。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel

from .config import get_settings


class Message(BaseModel):
    """一条对话消息，兼容纯文本、多模态内容块与工具调用记录。"""

    role: str
    content: Optional[Union[str, List[Dict[str, Any]]]] = None
    name: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    """`/v1/chat/completions` 的请求体。"""

    model: str = get_settings().default_model
    messages: List[Message]
    stream: Optional[bool] = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
