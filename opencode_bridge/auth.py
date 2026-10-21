"""Bearer Token 鉴权。"""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException


def verify_auth(authorization: Optional[str], api_key: str) -> None:
    """BRIDGE_API_KEY 为空时放行；否则校验 `Authorization: Bearer <key>`。"""
    if not api_key:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = authorization.split(" ", 1)[1]
    if token != api_key:
        raise HTTPException(status_code=401, detail="Invalid API Key")
