"""环境变量与全局配置。

所有可调参数集中在这里，避免散落在路由或业务代码里。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from functools import lru_cache

# opencode models 拉取失败时使用的兜底免费模型列表
# 注意：这只是"占位"清单，OpenCode 的免费模型变动很快（旧名字会变成
# Model unavailable），真实可用的以 `opencode models` / `/v1/models` 为准。
FALLBACK_MODELS: tuple[str, ...] = (
    "opencode/big-pickle",
    "opencode/glm-5.3-flash-free",
    "opencode/minimax-m3.1-flash-free",
    "opencode/deepseek-v4.1-flash-free",
    "opencode/nemotron-3-ultra-free",
)


def default_opencode_bin() -> str:
    """按 OPENCODE_BIN -> PATH -> ~/.opencode/bin/opencode 的顺序定位 CLI。"""
    return (
        os.environ.get("OPENCODE_BIN")
        or shutil.which("opencode")
        or os.path.expanduser("~/.opencode/bin/opencode")
    )


@dataclass(frozen=True)
class Settings:
    """网关运行所需的全部配置。"""

    opencode_bin: str
    opencode_attach_url: str
    bridge_api_key: str
    default_model: str
    host: str
    port: int
    work_dir: str
    request_timeout: float

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            # opencode CLI 路径
            opencode_bin=default_opencode_bin(),
            # 若后台运行了 `opencode serve`，填 http://127.0.0.1:4096 可避免冷启动
            opencode_attach_url=os.environ.get("OPENCODE_ATTACH_URL", ""),
            # 本地网关鉴权密钥（留空则不校验）
            bridge_api_key=os.environ.get("BRIDGE_API_KEY", ""),
            # 客户端没传 model 时使用的默认模型
            default_model=os.environ.get("DEFAULT_MODEL", "opencode/big-pickle"),
            host=os.environ.get("BRIDGE_HOST", "127.0.0.1"),
            port=int(os.environ.get("BRIDGE_PORT", "8083")),
            work_dir=os.environ.get(
                "BRIDGE_WORK_DIR",
                os.path.join(tempfile.gettempdir(), "opencode_bridge_workspace"),
            ),
            # 单次 opencode run 的超时秒数，<= 0 表示不限时
            request_timeout=float(os.environ.get("OPENCODE_TIMEOUT", "0")),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程级单例配置。"""
    return Settings.from_env()
