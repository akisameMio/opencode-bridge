"""prompt_builder 模块单元测试。"""

from opencode_bridge.prompts import DISABLE_TOOLS_NOTICE, TOOL_CALL_INSTRUCTION
from opencode_bridge.prompt_builder import format_messages_with_tools_to_prompt
from opencode_bridge.schemas import Message

WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "获取指定城市的实时天气",
        "parameters": {
            "type": "object",
            "properties": {"location": {"type": "string"}},
            "required": ["location"],
        },
    },
}


def test_system_notice_always_present():
    prompt = format_messages_with_tools_to_prompt(
        [Message(role="user", content="hi")]
    )
    assert DISABLE_TOOLS_NOTICE in prompt
    assert "[USER]:\nhi" in prompt
    assert prompt.endswith("[ASSISTANT]:\n")


def test_tools_section_and_instruction():
    prompt = format_messages_with_tools_to_prompt(
        [Message(role="user", content="东京天气？")], tools=[WEATHER_TOOL]
    )
    assert "[Available Tools for Function Calling]:" in prompt
    assert "Tool Name: get_weather" in prompt
    assert TOOL_CALL_INSTRUCTION in prompt


def test_tool_choice_none_skips_tools():
    prompt = format_messages_with_tools_to_prompt(
        [Message(role="user", content="hi")], tools=[WEATHER_TOOL], tool_choice="none"
    )
    assert "[Available Tools for Function Calling]:" not in prompt


def test_forced_tool_choice():
    prompt = format_messages_with_tools_to_prompt(
        [Message(role="user", content="hi")],
        tools=[WEATHER_TOOL],
        tool_choice={"type": "function", "function": {"name": "get_weather"}},
    )
    assert "You MUST call the tool `get_weather`" in prompt


def test_required_tool_choice():
    prompt = format_messages_with_tools_to_prompt(
        [Message(role="user", content="hi")],
        tools=[WEATHER_TOOL],
        tool_choice="required",
    )
    assert "You MUST call at least one tool" in prompt


def test_tool_history_serialization():
    messages = [
        Message(role="user", content="查天气"),
        Message(
            role="assistant",
            content=None,
            tool_calls=[
                {
                    "id": "call_abc",
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "arguments": '{"location": "Tokyo"}',
                    },
                }
            ],
        ),
        Message(role="tool", tool_call_id="call_abc", content="晴，25°C"),
    ]
    prompt = format_messages_with_tools_to_prompt(messages)

    assert "[Assistant Tool Call (ID: call_abc)" in prompt
    assert "name=get_weather" in prompt
    assert "[TOOL_RESPONSE (tool_call_id: call_abc)]:\n晴，25°C" in prompt


def test_multimodal_content_blocks():
    message = Message(
        role="user",
        content=[
            {"type": "text", "text": "看这个"},
            {"type": "tool_use", "name": "read_file", "input": {"path": "a.txt"}},
        ],
    )
    prompt = format_messages_with_tools_to_prompt([message])
    assert "看这个" in prompt
    assert "[Tool Call Request: read_file" in prompt
