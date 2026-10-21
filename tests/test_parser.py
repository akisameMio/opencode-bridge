"""parser 模块单元测试。"""

import json

from opencode_bridge.parser import parse_opencode_output_to_response


def test_plain_text_passthrough():
    result = parse_opencode_output_to_response("你好呀，我是大肥鱼。")
    assert result["content"] == "你好呀，我是大肥鱼。"
    assert result["tool_calls"] is None


def test_jsonl_events_are_joined():
    raw = "\n".join(
        [
            json.dumps({"text": "Hello"}),
            json.dumps({"part": {"text": " world"}}),
        ]
    )
    result = parse_opencode_output_to_response(raw)
    assert result["content"] == "Hello world"
    assert result["tool_calls"] is None


def test_single_tool_call_fenced_block():
    payload = {"tool_calls": [{"name": "get_weather", "arguments": {"location": "Tokyo"}}]}
    raw = f"让我查一下。\n```json\n{json.dumps(payload)}\n```"
    result = parse_opencode_output_to_response(raw)

    assert result["content"] == "让我查一下。"
    assert len(result["tool_calls"]) == 1
    tool_call = result["tool_calls"][0]
    assert tool_call["type"] == "function"
    assert tool_call["function"]["name"] == "get_weather"
    assert json.loads(tool_call["function"]["arguments"]) == {"location": "Tokyo"}
    assert tool_call["id"].startswith("call_")


def test_multi_tool_calls_and_bare_json():
    payload = {
        "tool_calls": [
            {"name": "read_file", "args": {"path": "a.txt"}},
            {"name": "run_shell", "arguments": {"cmd": "ls"}},
        ]
    }
    result = parse_opencode_output_to_response(json.dumps(payload))

    assert result["content"] is None
    assert [c["function"]["name"] for c in result["tool_calls"]] == [
        "read_file",
        "run_shell",
    ]
    # 简写 args 也要能识别
    assert json.loads(result["tool_calls"][0]["function"]["arguments"]) == {"path": "a.txt"}


def test_single_tool_shorthand():
    raw = '```json\n{"name": "ping", "arguments": {}}\n```'
    result = parse_opencode_output_to_response(raw)

    assert result["content"] is None
    assert result["tool_calls"][0]["function"]["name"] == "ping"


def test_non_tool_json_is_kept_as_content():
    raw = '```json\n{"foo": "bar"}\n```'
    result = parse_opencode_output_to_response(raw)
    assert result["tool_calls"] is None
    assert "foo" in result["content"]
