"""HDW-204: compact results and the ``headwaters://result/<id>`` resource."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from mcp import MCPError

from headwaters.mcp.tools.context import ToolSettings
from headwaters.mcp.tools.store import MAX_RESULTS, ResultStore
from tests.frontdoor_support import CODE_TASK, Rig, make_repo, structured


def test_a_long_answer_is_cut_inline_and_complete_at_result_uri(tmp_path: Path) -> None:
    long = "\n".join(f"line {n} of a long explanation" for n in range(400))
    rig = Rig(tmp_path, reply=long, settings=ToolSettings(answer_max_chars=500))

    async def go() -> tuple[dict[str, object], str]:
        async with rig.client() as client:
            data = structured(await client.call_tool("headwaters_do", {"task": "t"}))
            read = await client.read_resource(str(data["result_uri"]))
            return data, read.contents[0].text  # type: ignore[union-attr]

    data, body = asyncio.run(go())
    assert data["answer_truncated"] is True
    assert len(str(data["answer"])) < 700
    assert str(data["result_uri"]).startswith("headwaters://result/")
    assert json.loads(body)["answer"] == long  # nothing is lost


def test_the_resource_template_is_listed(tmp_path: Path) -> None:
    async def go() -> list[str]:
        async with Rig(tmp_path).client() as client:
            listed = await client.list_resource_templates()
            return [t.uri_template for t in listed.resource_templates]

    assert asyncio.run(go()) == ["headwaters://result/{result_id}"]


def test_an_unknown_result_id_is_a_clean_resource_error(tmp_path: Path) -> None:
    async def go() -> str:
        async with Rig(tmp_path).client() as client:
            try:
                await client.read_resource("headwaters://result/nope")
            except MCPError as exc:  # caught inside: the in-process client wraps it in a group
                return str(exc)
        return ""

    assert "Unknown or expired" in asyncio.run(go())


def test_the_result_detail_never_holds_the_task_or_the_file(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "p", {"app.py": "def run():\n    return 1\n"})
    rig = Rig(tmp_path, reply="ok")
    asyncio.run(rig.call("headwaters_do", {"task": "SECRET-TASK-WORDS", "paths": ["app.py"]}, repo))
    (detail,) = list(rig.context.store._items.values())
    stored = json.dumps(detail)
    assert "SECRET-TASK-WORDS" not in stored
    assert "return 1" not in stored
    assert detail["model"] == "qwen:14b"


def test_async_is_accepted_and_starts_a_background_job(tmp_path: Path) -> None:
    data = structured(
        asyncio.run(Rig(tmp_path).call("headwaters_do", {"task": CODE_TASK, "async": True}))
    )
    assert data["ok"] is True
    assert data["job_id"]  # HDW-206: the work runs in the background; the full lifecycle is in
    assert data["answer"] is None  # tests/test_run_jobs.py


def test_the_store_drops_the_oldest_entry_past_its_bound() -> None:
    store = ResultStore(max_results=2)
    ids = [store.put({"n": n}) for n in range(3)]
    assert store.get(ids[0]) is None
    assert store.get(ids[1]) is not None
    assert store.get(ids[2]) is not None
    assert len(store) == 2
    assert MAX_RESULTS > 2
    with pytest.raises(ValueError, match="at least 1"):
        ResultStore(max_results=0)
