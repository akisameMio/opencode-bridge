"""server 路由集成测试（用假 CLI 替代 opencode）。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from opencode_bridge.config import Settings
from opencode_bridge.server import create_app


def make_settings(tmp_path, opencode_bin: str, **overrides) -> Settings:
    base = dict(
        opencode_bin=opencode_bin,
        opencode_attach_url="",
        bridge_api_key="",
        default_model="opencode/big-pickle",
        host="127.0.0.1",
        port=8083,
        work_dir=str(tmp_path / "work"),
        request_timeout=0,
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture()
def fake_cli(tmp_path):
    """伪造一个 opencode 可执行文件，从 output.txt 读取要输出的文本。"""
    output_file = tmp_path / "output.txt"
    output_file.write_text("Hello from fake opencode.", encoding="utf-8")

    script = tmp_path / "fake_opencode"
    script.write_text(
        """#!/bin/sh
if [ "$1" = "models" ]; then
    echo "opencode/big-pickle"
    echo "opencode/gpt-5-nano"
    exit 0
fi
cat "$(dirname "$0")/output.txt"
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script, output_file


def test_health_and_models(fake_cli, tmp_path):
    script, _ = fake_cli
    client = TestClient(create_app(make_settings(tmp_path, str(script))))

    assert client.get("/health").json()["status"] == "ok"

    models = client.get("/v1/models").json()
    assert [m["id"] for m in models["data"]] == [
        "opencode/big-pickle",
        "opencode/gpt-5-nano",
    ]
    assert models["object"] == "list"


def test_chat_completion_plain_text(fake_cli, tmp_path):
    script, output_file = fake_cli
    client = TestClient(create_app(make_settings(tmp_path, str(script))))

    response = client.post(
        "/v1/chat/completions",
        json={"model": "opencode/big-pickle", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"] == "Hello from fake opencode."
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["total_tokens"] > 0


def test_chat_completion_tool_calls(fake_cli, tmp_path):
    script, output_file = fake_cli
    output_file.write_text(
        "马上查。\n```json\n"
        + json.dumps(
            {
                "tool_calls": [
                    {"name": "get_weather", "arguments": {"location": "Tokyo"}}
                ]
            }
        )
        + "\n```",
        encoding="utf-8",
    )
    client = TestClient(create_app(make_settings(tmp_path, str(script))))

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "opencode/big-pickle",
            "messages": [{"role": "user", "content": "东京天气"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "查天气",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        },
    )
    body = response.json()
    choice = body["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["tool_calls"][0]["function"]["name"] == "get_weather"


def test_chat_completion_stream(fake_cli, tmp_path):
    script, _ = fake_cli
    client = TestClient(create_app(make_settings(tmp_path, str(script))))

    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "opencode/big-pickle",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    ) as response:
        assert response.status_code == 200
        text = "".join(response.iter_text())

    assert "data: [DONE]" in text
    assert "chat.completion.chunk" in text
    assert "finish_reason" in text


def test_auth_required(fake_cli, tmp_path):
    script, _ = fake_cli
    app = create_app(make_settings(tmp_path, str(script), bridge_api_key="secret"))
    client = TestClient(app)

    assert client.get("/v1/models").status_code == 401
    assert client.get("/v1/models", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert (
        client.get("/v1/models", headers={"Authorization": "Bearer secret"}).status_code
        == 200
    )


def test_missing_cli_falls_back_to_models(tmp_path):
    app = create_app(make_settings(tmp_path, "/nonexistent/opencode-xyz"))
    client = TestClient(app)

    models = client.get("/v1/models").json()["data"]
    assert len(models) == 5
    assert "opencode/big-pickle" in [m["id"] for m in models]


def test_missing_cli_returns_500(tmp_path):
    app = create_app(make_settings(tmp_path, "/nonexistent/opencode-xyz"))
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={"model": "opencode/big-pickle", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 500
    assert "无法启动 opencode CLI" in response.json()["detail"]


def test_multiple_model_ids_rejected(fake_cli, tmp_path):
    """把三个模型名用顿号塞进 model 字段时，应该给出人话提示而不是 CLI 原始报错。"""
    script, _ = fake_cli
    client = TestClient(create_app(make_settings(tmp_path, str(script))))

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "opencode/big-pickle、opencode/minimax-m2.5-free、opencode/gpt-5-nano",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "只能填一个模型 ID" in detail
    assert "3 个" in detail


def test_split_model_ids():
    from opencode_bridge.server import split_model_ids

    assert split_model_ids("opencode/big-pickle") == ["opencode/big-pickle"]
    assert split_model_ids("a、b") == ["a", "b"]
    assert split_model_ids("a, b") == ["a", "b"]
    assert split_model_ids(" a ；b\nc ") == ["a", "b", "c"]


def test_error_detail_strips_ansi(tmp_path):
    """opencode 的彩色错误码不应该原样漏进 JSON。"""
    script = tmp_path / "fake_opencode"
    script.write_text(
        "#!/bin/sh\n"
        "printf '\\033[91mError: \\033[0mmodel unavailable\\n' >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    client = TestClient(create_app(make_settings(tmp_path, str(script))))

    response = client.post(
        "/v1/chat/completions",
        json={"model": "opencode/nope", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "\x1b" not in detail
    assert "model unavailable" in detail
