"""与本地 opencode CLI 交互的薄客户端。

只负责「开进程、收输出、报错」，不掺任何 HTTP 逻辑。
"""

from __future__ import annotations

import asyncio
import os
from typing import List

from .config import FALLBACK_MODELS, Settings


class OpenCodeError(RuntimeError):
    """opencode CLI 调用失败。"""


class OpenCodeClient:
    """封装 `opencode run` 与 `opencode models`。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def _communicate(
        self, *cmd: str, cwd: str | None = None
    ) -> tuple[str, str, int]:
        """起子进程并收齐 stdout / stderr / 返回码，可选超时。"""
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        try:
            if self.settings.request_timeout > 0:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=self.settings.request_timeout
                )
            else:
                stdout, stderr = await proc.communicate()
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise OpenCodeError(
                f"OpenCode CLI 超时（>{self.settings.request_timeout:.0f}s）"
            ) from None

        return (
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
            proc.returncode or 0,
        )

    async def run(self, model: str, prompt: str) -> str:
        """执行一次 `opencode run --model <model> <prompt>`。"""
        cmd = [self.settings.opencode_bin, "run", "--model", model]
        if self.settings.opencode_attach_url:
            cmd.extend(["--attach", self.settings.opencode_attach_url])
        cmd.append(prompt)

        # 独立工作目录，避免 opencode 在用户项目里乱翻文件
        os.makedirs(self.settings.work_dir, exist_ok=True)

        try:
            stdout, stderr, returncode = await self._communicate(
                *cmd, cwd=self.settings.work_dir
            )
        except OSError as exc:
            raise OpenCodeError(
                f"无法启动 opencode CLI（{self.settings.opencode_bin}）: {exc}"
            ) from exc

        if returncode != 0:
            raise OpenCodeError(
                f"OpenCode CLI 执行失败: {stderr.strip() or stdout.strip()}"
            )

        return stdout

    async def list_models(self) -> List[str]:
        """解析 `opencode models` 的输出；失败时返回兜底免费模型列表。"""
        try:
            stdout, _, _ = await self._communicate(self.settings.opencode_bin, "models")
        except (OSError, OpenCodeError):
            return list(FALLBACK_MODELS)

        models: List[str] = []
        for line in stdout.splitlines():
            name = line.strip()
            if name and "/" in name and not name.startswith(("-", "=", "#")):
                models.append(name.split()[0])

        return models or list(FALLBACK_MODELS)
