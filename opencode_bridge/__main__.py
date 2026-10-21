"""`python -m opencode_bridge` 入口。"""

from __future__ import annotations

import uvicorn

from .config import get_settings
from .server import create_app


def main() -> None:
    settings = get_settings()
    print(
        f"启动 OpenCode 完全体桥接网关 (含 Tools 支持) | CLI路径: {settings.opencode_bin}"
    )
    print(f"监听地址: http://{settings.host}:{settings.port}")
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
