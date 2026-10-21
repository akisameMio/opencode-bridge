"""注入给 opencode 的系统级提示词。

两套提示词分别负责：
1. 关掉 opencode 自带的本地工具，避免它抢外部 Agent 的活；
2. 约定工具调用的 JSON 输出格式，方便网关解析成 OpenAI `tool_calls`。
"""

DISABLE_TOOLS_NOTICE = (
    "[System Notice: Act strictly as a text/code generation and reasoning model. "
    "Do NOT execute any built-in OpenCode local shell, file, or workspace tools yourself. "
    "If external tools are provided below, strictly follow the JSON tool calling instructions.]"
)

TOOL_CALL_INSTRUCTION = (
    "\n[Instruction for Function Calling]:\n"
    "1. When you need to call one or more tools from the [Available Tools] list, "
    "you MUST output a JSON block wrapped in ```json ... ``` using this EXACT schema:\n"
    "```json\n"
    '{\n  "tool_calls": [\n    {\n      "name": "tool_name_here",\n      "arguments": {\n        "param1": "value1"\n      }\n    }\n  ]\n}\n'
    "```\n"
    "2. Do NOT fabricate tool results. Wait for `[TOOL_RESPONSE]` in the next turn.\n"
    "3. If no tool call is needed, reply normally in plain text/markdown."
)
