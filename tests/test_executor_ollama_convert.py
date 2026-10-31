"""HDW-115: OpenAI-style messages -> Ollama native messages."""

from __future__ import annotations

from typing import Any

from headwaters.core.executor.ollama_convert import parse_line, to_ollama_messages


def test_images_are_inlined_and_remote_urls_dropped() -> None:
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what is this"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
                {"type": "image_url", "image_url": {"url": "https://remote.example/x.png"}},
            ],
        }
    ]
    assert to_ollama_messages(messages) == [
        {"role": "user", "content": "what is this", "images": ["QUJD"]}
    ]


def test_assistant_tool_calls_and_tool_results() -> None:
    call = {
        "id": "c1",
        "type": "function",
        "function": {"name": "read_file", "arguments": '{"path": "a.py"}'},
    }
    messages: list[dict[str, Any]] = [
        {"role": "assistant", "content": None, "tool_calls": [call]},
        {"role": "tool", "tool_call_id": "c1", "content": "file text"},
    ]
    out = to_ollama_messages(messages)
    assert out[0]["tool_calls"] == [
        {"function": {"name": "read_file", "arguments": {"path": "a.py"}}}
    ]
    assert out[1] == {"role": "tool", "content": "file text", "tool_name": "read_file"}


def test_bad_tool_arguments_become_empty_object() -> None:
    call = {"id": "c", "function": {"name": "f", "arguments": "{not json"}}
    out = to_ollama_messages([{"role": "assistant", "content": "", "tool_calls": [call]}])
    assert out[0]["tool_calls"][0]["function"]["arguments"] == {}


def test_parse_line_without_message_is_empty_delta() -> None:
    delta, seen = parse_line({"done": False}, 0)
    assert (delta.content, delta.reasoning, delta.tool_calls, seen) == ("", "", (), 0)
