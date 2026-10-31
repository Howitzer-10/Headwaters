"""Fixture prompts for HDW-106, part A: code, vision, agentic."""

from __future__ import annotations

from typing import Any

from headwaters.core.analyzer import AnalyzerInput

PNG = "data:image/png;base64,AAAA"
TOOL = {"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}
CLINE_SYSTEM = "You are Cline, a highly skilled software engineer. " + "Rules and tools. " * 500
IMG = {"type": "image_url", "image_url": {"url": PNG}}

Fixture = tuple[str, AnalyzerInput, dict[str, Any]]


def user(text: Any) -> dict[str, Any]:
    return {"role": "user", "content": text}


def case(**kwargs: Any) -> dict[str, Any]:
    return kwargs


FIXTURES_A: list[Fixture] = [
    (
        "code_refactor",
        AnalyzerInput([user("Please refactor this function to be shorter")]),
        case(task="code", tools=False, vision=False, json=False, agentic=False),
    ),
    (
        "code_fence_python",
        AnalyzerInput([user("Why is this slow?\n```python\nfor i in range(10):\n    pass\n```")]),
        case(task="code", languages=("python",)),
    ),
    (
        "code_fence_alias_ts",
        AnalyzerInput([user("fix:\n```ts\nconst a: number = 'x'\n```")]),
        case(task="code", languages=("typescript",)),
    ),
    (
        "code_two_languages",
        AnalyzerInput([user("```rs\nfn main(){}\n```\nand\n```sh\nls\n```\nimplement a class")]),
        case(task="code", languages=("rust", "shell")),
    ),
    (
        "code_file_path_ext",
        AnalyzerInput([user("implement the function")], file_paths=["C:\\src\\app\\main.go"]),
        case(task="code", languages=("go",)),
    ),
    (
        "code_ext_in_prose",
        AnalyzerInput([user("There is a bug in utils.py and server.ts, fix it")]),
        case(task="code", languages=("python", "typescript")),
    ),
    (
        "code_stacktrace",
        AnalyzerInput([user("Here is the stack trace, find the bug")]),
        case(task="code"),
    ),
    (
        "vision_openai_image_url",
        AnalyzerInput([user([{"type": "text", "text": "what is this"}, IMG])]),
        case(vision=True, task="vision", confidence_ok=True),
    ),
    (
        "vision_anthropic_image",
        AnalyzerInput(
            [user([{"type": "image", "source": {"type": "base64"}}, {"type": "text", "text": "x"}])]
        ),
        case(vision=True, task="vision"),
    ),
    (
        "vision_screenshot_bug",
        AnalyzerInput([user([IMG, {"type": "text", "text": "fix this bug"}])]),
        case(vision=True, task="vision"),
    ),
    (
        "vision_tokens_counted",
        AnalyzerInput([user([IMG])]),
        case(vision=True, min_tokens=800),
    ),
    (
        "agentic_cline_system_prompt",
        AnalyzerInput(
            [{"role": "system", "content": CLINE_SYSTEM}, user("add a --verbose flag to the cli")]
        ),
        case(agentic=True, tools=False),
    ),
    (
        "agentic_tools_array",
        AnalyzerInput([user("read the readme")], tools=[TOOL]),
        case(agentic=True, tools=True, complexity=1),
    ),
    (
        "agentic_anthropic_system_blocks",
        AnalyzerInput([user("hi")], system=[{"type": "text", "text": CLINE_SYSTEM}], tools=[TOOL]),
        case(agentic=True, tools=True),
    ),
    (
        "agentic_system_prompt_only",
        AnalyzerInput([user("hi")], system=CLINE_SYSTEM),
        case(agentic=True, tools=False),
    ),
    (
        "agentic_user_agent_cline",
        AnalyzerInput([user("hello there")], headers={"User-Agent": "Cline/3.2 vscode"}),
        case(agentic=True, tools=False),
    ),
    (
        "agentic_user_agent_lowercase_header",
        AnalyzerInput([user("hello there")], headers={"user-agent": "OpenCode/1.0"}),
        case(agentic=True),
    ),
    (
        "agentic_tool_result_turn",
        AnalyzerInput(
            [
                user("list files"),
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"id": "1", "function": {"name": "ls", "arguments": "{}"}}],
                },
                {"role": "tool", "content": "a.py\nb.py"},
                user("now open a.py"),
            ],
            tools=[TOOL],
        ),
        case(agentic=True, tools=True, complexity=1),
    ),
    (
        "not_agentic_plain_user_agent",
        AnalyzerInput([user("hello there")], headers={"User-Agent": "python-httpx/0.28"}),
        case(agentic=False),
    ),
]
