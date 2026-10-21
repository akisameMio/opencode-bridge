"""FastAPI 应用与 OpenAI 兼容路由。"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from . import __version__
from .auth import verify_auth
from .client import OpenCodeClient, OpenCodeError
from .config import Settings, get_settings
from .parser import parse_opencode_output_to_response
from .prompt_builder import format_messages_with_tools_to_prompt
from .schemas import ChatCompletionRequest

# 流式输出时每个 SSE 分片的字符数
STREAM_CHUNK_SIZE = 32


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    """构造应用，允许注入自定义配置（测试时很方便）。"""
    settings = settings or get_settings()
    app = FastAPI(
        title="OpenCode to OpenAI Bridge (Full Tools Support)",
        version=__version__,
    )
    app.state.settings = settings
    client = OpenCodeClient(settings)

    def require_auth(authorization: Optional[str] = Header(None)) -> None:
        """FastAPI 依赖：校验 Bearer Token。"""
        verify_auth(authorization, settings.bridge_api_key)

    # ================= 只读接口 =================
    @app.get("/")
    async def root() -> Dict[str, Any]:
        return {
            "name": "opencode-bridge",
            "version": __version__,
            "base_url": f"http://{settings.host}:{settings.port}/v1",
            "endpoints": ["/health", "/v1/models", "/v1/chat/completions"],
        }

    @app.get("/health")
    async def health() -> Dict[str, Any]:
        return {"status": "ok", "opencode_bin": settings.opencode_bin}

    @app.get("/v1/models")
    async def list_models(
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        require_auth(authorization)

        names = await client.list_models()
        created = int(time.time())
        models = [
            {
                "id": name,
                "object": "model",
                "created": created,
                "owned_by": "opencode",
            }
            for name in names
        ]
        return {"object": "list", "data": models}

    # ================= 聊天补全 =================
    def build_stream(
        completion_id: str,
        created_time: int,
        model: str,
        parsed_result: Dict[str, Any],
    ) -> AsyncIterator[str]:
        """按 OpenAI SSE 协议切片推送正文、tool_calls 与结束帧。"""
        finish_reason = "tool_calls" if parsed_result["tool_calls"] else "stop"

        def frame(payload: Dict[str, Any]) -> str:
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        def chunk(delta: Dict[str, Any], finish: Optional[str] = None) -> Dict[str, Any]:
            return {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created_time,
                "model": model,
                "choices": [
                    {"index": 0, "delta": delta, "finish_reason": finish}
                ],
            }

        async def event_generator() -> AsyncIterator[str]:
            text_to_stream = parsed_result["content"] or ""
            for start in range(0, len(text_to_stream), STREAM_CHUNK_SIZE):
                piece = text_to_stream[start : start + STREAM_CHUNK_SIZE]
                yield frame(
                    chunk({"role": "assistant", "content": piece})
                )
                await asyncio.sleep(0.01)

            # 流式 tool_calls 需要 index 字段，OpenAI SDK 会校验
            if parsed_result["tool_calls"]:
                stream_tool_calls = []
                for index, tool_call in enumerate(parsed_result["tool_calls"]):
                    stream_tool_calls.append({**tool_call, "index": index})
                yield frame(
                    chunk(
                        {
                            "role": "assistant",
                            "tool_calls": stream_tool_calls,
                        }
                    )
                )

            yield frame(chunk({}, finish_reason))
            yield "data: [DONE]\n\n"

        return event_generator()

    @app.post("/v1/chat/completions")
    async def chat_completions(
        req: ChatCompletionRequest,
        authorization: Optional[str] = Header(None),
    ):
        require_auth(authorization)

        prompt = format_messages_with_tools_to_prompt(
            req.messages, req.tools, req.tool_choice
        )
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created_time = int(time.time())

        try:
            raw_output = await client.run(req.model, prompt)
        except OpenCodeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        parsed_result = parse_opencode_output_to_response(raw_output)
        finish_reason = "tool_calls" if parsed_result["tool_calls"] else "stop"

        if req.stream:
            return StreamingResponse(
                build_stream(completion_id, created_time, req.model, parsed_result),
                media_type="text/event-stream",
            )

        # 非流式响应
        response_message: Dict[str, Any] = {
            "role": "assistant",
            "content": parsed_result["content"],
        }
        if parsed_result["tool_calls"]:
            response_message["tool_calls"] = parsed_result["tool_calls"]

        return JSONResponse(
            {
                "id": completion_id,
                "object": "chat.completion",
                "created": created_time,
                "model": req.model,
                "choices": [
                    {
                        "index": 0,
                        "message": response_message,
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": {
                    "prompt_tokens": len(prompt) // 4,
                    "completion_tokens": len(raw_output) // 4,
                    "total_tokens": (len(prompt) + len(raw_output)) // 4,
                },
            }
        )

    return app


# `uvicorn opencode_bridge.server:app` 可直接使用
app = create_app()
