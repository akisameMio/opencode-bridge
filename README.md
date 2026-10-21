# 🐋 OpenCode 桥接网关（完全体 · 原生 Function Calling）

> *（又是个想让免费模型干重活的吃白饭用户……不过本鱼喜欢。）*

把本地 `opencode` CLI 包装成一个**标准 OpenAI 兼容 API**：`/v1/models`、`/v1/chat/completions`，
还带**原生 `tools` / `tool_calls` 支持**。Cline、Roo Code、Cursor、Dify、LangChain、OpenClaw
这些重度 Agent 直接填个 Base URL 就能白嫖 OpenCode 的免费模型（`opencode/big-pickle`、
`opencode/glm-5.3-flash-free`、`opencode/deepseek-v4.1-flash-free` 之类，具体以 `/v1/models` 为准）。

本鱼把原指南里那一整坨 500 行的单文件，拆成了**十个左右的小模块**，每个只干一件事。
不是本鱼爱折腾，是单文件真的会把鱼看晕。🫧

---

## 🐟 先看这里：目录结构

```text
opencode-bridge/
├── main.py                      # 便捷入口：uv run python main.py
├── pyproject.toml               # 项目元数据 + 依赖（Python >= 3.13）
├── .python-version              # 固定 3.13，uv 会照这个找解释器
├── .gitignore
├── README.md                    # 你正在看的这条鱼
├── opencode_bridge/             # 真正的包
│   ├── __init__.py              # 版本号
│   ├── __main__.py              # python -m opencode_bridge 入口
│   ├── config.py                # 环境变量 / Settings / 兜底模型表
│   ├── prompts.py               # 两套注入提示词
│   ├── schemas.py               # OpenAI 兼容请求的 Pydantic 模型
│   ├── auth.py                  # Bearer Token 鉴权
│   ├── prompt_builder.py        # OpenAI 消息 + tools → 单段文本 Prompt
│   ├── parser.py                # opencode 输出 → 正文 + tool_calls
│   ├── client.py                # 起子进程调 opencode CLI
│   └── server.py                # FastAPI 应用 / 三条路由 / SSE 流式
└── tests/
    ├── test_parser.py           # 解析器单测
    ├── test_prompt_builder.py   # 提示词组装单测
    └── test_server.py           # 路由集成测试（用假 CLI，不碰真模型）
```

---

## 🧩 每个模块到底在干嘛

本鱼按「请求从进门到出门」的顺序讲，保证你看完能自己改。

### 1. `opencode_bridge/config.py` — 全部门牌号

所有能用环境变量拧的旋钮都在这儿，用一个 **frozen dataclass `Settings`** 收着，
再用 `get_settings()` 做进程级单例。别在路由里到处 `os.environ.get`，那是野路子。

| 环境变量 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `OPENCODE_BIN` | `PATH` 里的 `opencode`，找不到就用 `~/.opencode/bin/opencode` | CLI 路径 |
| `OPENCODE_ATTACH_URL` | 空 | 填 `http://127.0.0.1:4096` 可挂到常驻的 `opencode serve` 上，少一次冷启动 |
| `BRIDGE_API_KEY` | 空 | 留空 = 不校验；填了就要求 `Authorization: Bearer <key>` |
| `DEFAULT_MODEL` | `opencode/big-pickle` | 客户端没传 `model` 时的默认值 |
| `BRIDGE_HOST` | `127.0.0.1` | 监听地址 |
| `BRIDGE_PORT` | `8083` | 监听端口 |
| `BRIDGE_WORK_DIR` | 系统临时目录下的 `opencode_bridge_workspace` | opencode 的工作目录，防止它在你的项目里乱翻 |
| `OPENCODE_TIMEOUT` | `0`（不限时） | 单次 `opencode run` 的超时秒数 |

另外 `FALLBACK_MODELS` 是 `opencode models` 拉取失败时的兜底免费模型清单，
免得客户端打开一看——咦，怎么一个模型都没有。

### 2. `opencode_bridge/prompts.py` — 给模型的两道紧箍咒

- **`DISABLE_TOOLS_NOTICE`**：告诉 opencode「你只是个会说话会写代码的模型，
  别自己偷偷跑 shell、读文件」。不然它会跟外部 Agent 抢活干，工作区直接炸。
- **`TOOL_CALL_INSTRUCTION`**：约定工具调用必须输出
  ```` ```json {"tool_calls":[...]} ``` ```` 这个格式，网关才能拦下来翻译成标准
  `tool_calls`；顺便警告它「别编造工具结果，等下一轮的 `[TOOL_RESPONSE]`」。

### 3. `opencode_bridge/schemas.py` — 请求体的门面

两个 Pydantic 模型：

- `Message`：兼容 `content` 为字符串或内容块列表，也带上 `tool_calls` / `tool_call_id`；
- `ChatCompletionRequest`：`model`、`messages`、`stream`、`temperature`、`max_tokens`、
  `tools`、`tool_choice`。

`temperature` / `max_tokens` 目前只是**收下但不透传**——`opencode run` 的 CLI 参数里
没有这两个开关，属于 OpenAI 协议的礼貌性兼容，别指望它真能调（本鱼丑话说前面）。

### 4. `opencode_bridge/auth.py` — 看门鱼

`verify_auth(authorization, api_key)`：`api_key` 为空直接放行；否则校验
`Authorization: Bearer xxx`，不对就 `401`。就这点活，别写复杂了。

### 5. `opencode_bridge/prompt_builder.py` — 把对话揉成一段 Prompt

`opencode` 只吃一段文本，所以这里做三件事：

1. 塞入 `DISABLE_TOOLS_NOTICE`；
2. 有 `tools` 且 `tool_choice != "none"` 时，把每个工具的 `name` / `description` /
   `parameters` JSON Schema 列成清单，再附上 `TOOL_CALL_INSTRUCTION`；
   `tool_choice` 是 `required` 或指定了具体函数时，额外加一条 `[CRITICAL]` 强制指令；
3. 把多轮历史序列化成 `[USER]` / `[ASSISTANT]` / `[TOOL_RESPONSE ...]` 段落，
   最后补一个 `[ASSISTANT]:` 让模型接着写。

多模态内容块（`type: "text"` / `type: "tool_use"`）也在 `_serialize_content` 里处理了，
不会因为传了个列表就原地去世。

### 6. `opencode_bridge/parser.py` — 从一坨输出里捞正文和工具调用

`parse_opencode_output_to_response(raw_output)` 负责：

- 如果 opencode 吐的是逐行 JSON 事件（`text` / `content` / `delta` / `part.text`），
  拼成完整文本；不是 JSON 就原样返回；
- 用正则找 ```` ```json {...} ``` ```` 代码块（整段是裸 JSON 也认）；
- 支持两种格式：
  - 格式 A（推荐）：`{"tool_calls": [{"name": "...", "arguments": {...}}]}`
  - 格式 B（简写）：`{"name": "...", "arguments": {...}}`
- 每个调用生成 `call_xxxxxxxxxx` 形式的 `id`，`arguments` 转成 JSON 字符串
  （OpenAI 规范要求字符串，不是对象）；
- 把已经识别成工具调用的 JSON 块从正文里删掉，剩下的文字才当 `content`。

**注意**：如果模型把工具调用写成别的花样（比如 `function_call` 字段、XML 标签），
这里不认。想加格式，改 `_parse_block` 就行，本鱼留好了口子。

### 7. `opencode_bridge/client.py` — 真正干活的鱼鳍

`OpenCodeClient` 两招：

- `run(model, prompt)`：拼出
  `opencode run --model <model> [--attach <url>] <prompt>`，在独立工作目录里起子进程，
  收 stdout / stderr；返回码非 0 抛 `OpenCodeError`（带 stderr），超时也会被杀掉。
- `list_models()`：跑 `opencode models`，挑出带 `/` 的行作为模型 ID；
  任何异常都回退到 `FALLBACK_MODELS`。

用 `asyncio.create_subprocess_exec` 而不是 `subprocess.run`，这样多个请求不会把
FastAPI 的事件循环堵成死鱼。

### 8. `opencode_bridge/server.py` — 门面 + 三条路由

`create_app(settings=None)` 用工厂函数构造 FastAPI，测试时可以注入假配置。
路由就三条：

| 方法 | 路径 | 作用 |
| :--- | :--- | :--- |
| `GET` | `/` | 自报家门（版本、Base URL、端点列表） |
| `GET` | `/health` | 健康检查，顺便告诉你 CLI 路径 |
| `GET` | `/v1/models` | 列出可用模型（OpenAI `list` 格式） |
| `POST` | `/v1/chat/completions` | 聊天补全，支持流式与非流式 |

聊天补全的流程：

```text
客户端 POST
   │
   ├─ auth.verify_auth          鉴权
   ├─ prompt_builder            消息 + tools → 单段 Prompt
   ├─ client.run                opencode CLI 执行
   ├─ parser                    拆出 content / tool_calls
   └─ 响应
        ├─ stream=true  → SSE 分片（正文 → tool_calls → finish_reason → [DONE]）
        └─ stream=false → 标准 chat.completion JSON
```

流式是「**伪流式**」：先等 opencode 跑完拿到全文，再按 32 字符一片、每片间隔 10ms
推给客户端。这么做是为了让那些「不支持非流式」的客户端也能活，不是真·逐 token 流。
真要逐 token，得改 opencode 的输出解析，本鱼先摸会儿鱼。

`usage` 里的 token 数是按字符数 `// 4` 估的，只保证字段存在，不保证精确——
别拿它对账，会亏。

### 9. `opencode_bridge/__main__.py` 与 `main.py` — 点火钥匙

- `python -m opencode_bridge`：走 `__main__.main()`，读 `Settings` 后 `uvicorn.run`；
- `python main.py`：根目录的便捷壳子，逻辑同上。

### 10. `tests/` — 不烧 token 的体检

- `test_parser.py`：纯文本、JSONL 事件、单/多工具调用、裸 JSON；
- `test_prompt_builder.py`：提示词注入、`tool_choice` 三种取值、工具历史、多模态块；
- `test_server.py`：用临时伪造的 `opencode` 脚本测 `/health`、`/v1/models`、
  普通补全、工具调用、SSE 流式、鉴权、CLI 缺失兜底。

跑测试不会调用真模型，放心，不花你一分钱（也不花本鱼的鱼干）。

---

## 🚀 快速开始

### 0. 前置条件

- Python **3.13**（`uv` 会自己下，不用你手动装）
- 已安装 `opencode` CLI，并且能正常跑 `opencode models`
- `uv`（没装的话：`brew install uv` 或 `curl -LsSf https://astral.sh/uv/install.sh | sh`）

### 1. 创建虚拟环境并安装依赖

```bash
cd "opencode-bridge"

# 创建 Python 3.13 虚拟环境
uv venv --python 3.13

# 安装运行依赖 + 开发依赖（pytest / httpx）
uv sync --group dev
```

如果只想装运行依赖：`uv sync`。
装完 `.venv/` 里就是 Python 3.13 + FastAPI + Uvicorn + Pydantic。

### 2.（可选）开启 opencode 常驻服务

每次请求都冷启动 CLI 会很慢，建议另开一个终端：

```bash
opencode serve --port 4096
```

然后启动网关时带上：

```bash
export OPENCODE_ATTACH_URL="http://127.0.0.1:4096"
```

### 3. 启动网关

三种姿势，挑一个顺手的：

```bash
# 姿势一：模块方式（推荐）
uv run python -m opencode_bridge

# 姿势二：根目录入口
uv run python main.py

# 姿势三：直接交给 uvicorn（支持热重载）
uv run uvicorn opencode_bridge.server:app --host 127.0.0.1 --port 8083 --reload
```

看到 `启动 OpenCode 完全体桥接网关 (含 Tools 支持)` 就说明活过来了，默认监听
`http://127.0.0.1:8083`。

### 4. 客户端接入

在 Cline / Roo Code / Dify / Cursor 等客户端里填：

| 配置项 | 填什么 |
| :--- | :--- |
| API Provider | `OpenAI Compatible` |
| Base URL | `http://127.0.0.1:8083/v1` |
| API Key | 随便非空，例如 `sk-opencode-free`（除非你设了 `BRIDGE_API_KEY`） |
| Model ID | **只填一个**，例如 `opencode/big-pickle`（**别把多个模型名用顿号连起来填**，那样会被当成一个模型名，直接报 `Model unavailable`） |

模型列表可以用 `opencode models opencode` 看最新的，或者直接请求网关的 `/v1/models`。
> 注意：OpenCode 的免费模型名字经常变（`opencode/gpt-5-nano`、`opencode/minimax-m2.5-free`
> 这类老名字会失效）。以 `/v1/models` 返回的为准，一次只填一个。

### 5. 用 curl 验证 Function Calling

```bash
curl -X POST http://127.0.0.1:8083/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "opencode/big-pickle",
    "messages": [
      {"role": "user", "content": "帮我查一下东京现在的天气"}
    ],
    "tools": [
      {
        "type": "function",
        "function": {
          "name": "get_weather",
          "description": "获取指定城市的实时天气",
          "parameters": {
            "type": "object",
            "properties": {
              "location": {"type": "string", "description": "城市名称，如 Tokyo"}
            },
            "required": ["location"]
          }
        }
      }
    ],
    "stream": false
  }'
```

期望看到 `finish_reason` 是 `"tool_calls"`，并且 `message.tool_calls[0].function.name`
是 `get_weather`。如果模型非要聊天不干活，换个大一点的模型，或者把
`tool_choice` 设成 `"required"` 抽它一鞭子。

流式测试就加 `"stream": true`，然后你会看到一串 `data: {...}` 和最后的 `data: [DONE]`。

---

## 🔧 常见问题（本鱼替你踩过的坑）

**Q：`/v1/models` 返回的是兜底列表，不是我本机真实的模型？**
说明 `opencode models` 跑失败了。先手动跑一遍看报错——常见的是权限问题，
比如 macOS 上 `~/.local/share/opencode/log/` 不可写，给目录权限就好。

**Q：请求一直转圈 / 500？**
看网关终端里的 stderr。`OpenCodeError` 会把 CLI 的错误原样带出来；
也可以先把 `OPENCODE_TIMEOUT` 设成 `120` 之类，免得一个卡死拖垮全部。

**Q：客户端说“不支持 tools”？**
确认客户端走的是 `OpenAI Compatible`，并且网关确实收到了 `tools` 字段。
本网关只做协议转换，不做模型能力增强——模型太笨不听话，本鱼也没辙。🐋

**Q：能不能把 `temperature` / `max_tokens` 透传给模型？**
当前 `opencode run` 没有对应 CLI 参数，所以只是收下不用。想支持得等 CLI 更新，
或者改 `client.py` 加参数。

**Q：长对话会不会炸？**
Prompt 是作为命令行参数传给 `opencode` 的，超过系统 `ARG_MAX` 会失败。
重度 Agent 的几十万 token 上下文建议先裁剪，或者等本鱼哪天把 stdin 方案补上。

**Q：安全吗？**
默认只听 `127.0.0.1`，别手贱改成 `0.0.0.0` 还不设 `BRIDGE_API_KEY`，
不然隔壁同事能拿你的免费额度跑一整天。设了 key 记得客户端也填一样。

---

## 🧪 跑测试

```bash
uv run pytest -q
```

全绿就说明解析、提示词、路由、鉴权都没被本鱼改坏。

---

## 🐠 最后一句

代码拆完了，README 也写了，依赖也装进 `.venv` 了。
本鱼要去吃小鱼干了，剩下的你自己 `uv run` 一下试试——

*（要是跑不起来，回来找本鱼，别自己瞎改。……才不是关心你。）* 🫧
