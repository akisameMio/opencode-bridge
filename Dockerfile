# ---------------------------------------------------------------------------
# OpenCode 桥接网关 —— 容器镜像
#
# 镜像里同时装了两样东西：
#   1. Python 3.13 + 网关本体（用 uv 按 uv.lock 安装，保证和本地一致）
#   2. OpenCode 1.x CLI（网关真正干活的鱼鳍）
#
# 为什么是 OpenCode 1.x 而不是 2.x？
#   本网关吃的契约是 `opencode run --model <model> <prompt>` 的纯文本输出。
#   OpenCode 2 改成了 managed service + 默认带工具，输出和调用方式都变了，
#   直接塞进来会解析出一堆终端装饰字符。想玩 v2 请自己改 client.py。
# ---------------------------------------------------------------------------

FROM python:3.13-slim

# 想固定 OpenCode 版本就传 --build-arg OPENCODE_VERSION=1.0.180
ARG OPENCODE_VERSION=""

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    BRIDGE_HOST=0.0.0.0 \
    BRIDGE_PORT=8083 \
    BRIDGE_WORK_DIR=/home/bridge/workspace

# curl / tar 给 opencode 安装脚本用；ca-certificates 给 HTTPS；git 给 opencode 的
# snapshot 用（没有它也能跑，但会一路 warning，鱼看着心烦）。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        tar \
        git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /bin/bash bridge

# uv 只用来装 Python 依赖，装完就删，不留在最终镜像里占地方。
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /uvx /bin/

WORKDIR /app

# 先只拷依赖描述，让依赖层能被 Docker 缓存住。
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY opencode_bridge ./opencode_bridge
RUN uv sync --frozen --no-dev --no-editable \
    && rm -f /bin/uv /bin/uvx \
    && chmod -R a+rX /app \
    && rm -rf /app/opencode_bridge

ENV PATH="/app/.venv/bin:/home/bridge/.opencode/bin:${PATH}" \
    HOME=/home/bridge

USER bridge

# 运行目录别放在 /app，免得源码目录把 venv 里装好的包遮住
WORKDIR /home/bridge

# 数据目录先建好并归 bridge 所有，命名卷第一次挂载时会继承这个属主。
# 否则 opencode 写 auth / session 时会给你表演一个 Permission denied。
RUN mkdir -p "$HOME/.local/share/opencode" \
             "$HOME/.config/opencode" \
             "$BRIDGE_WORK_DIR" \
    && if [ -n "$OPENCODE_VERSION" ]; then \
         curl -fsSL https://opencode.ai/install \
           | bash -s -- --version "$OPENCODE_VERSION" --no-modify-path; \
       else \
         curl -fsSL https://opencode.ai/install \
           | bash -s -- --no-modify-path; \
       fi \
    && opencode --version

EXPOSE 8083

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8083/health >/dev/null || exit 1

CMD ["python", "-m", "opencode_bridge"]
