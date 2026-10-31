"""HDW-117: session keying and warm preference, progress hook, secret detection."""

from __future__ import annotations

from pathlib import Path

from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.router_session import session_key
from headwaters.core.router_types import ProgressEvent
from headwaters.core.secrets_detect import SecretDetector
from headwaters.core.state_misc import get_session
from tests.executor_support import REPO_ROOT
from tests.router_rig import Rig
from tests.router_support import OK, RATE_LIMITED, candidate, stages
from tests.test_router import FREE, LOCAL, pool

SK = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"


def test_session_key_prefers_the_header_then_hashes() -> None:
    msgs = [{"role": "user", "content": "first message"}]
    assert session_key(messages=msgs, headers={"X-Session-Id": "abc"}) == "id:abc"
    assert session_key(messages=msgs, explicit="zzz") == "id:zzz"
    hashed = session_key(messages=msgs, system="be brief")
    later = [*msgs, {"role": "assistant", "content": "x"}]
    assert hashed == session_key(messages=later, system="be brief")  # only the first turn counts
    assert hashed != session_key(messages=msgs, system="be long")
    assert hashed.startswith("h:")
    assert "first message" not in hashed


def test_session_key_handles_openai_system_messages_and_content_parts() -> None:
    plain = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    parts = [
        {"role": "system", "content": [{"type": "text", "text": "sys"}]},
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
    ]
    anthropic = [{"role": "user", "content": "hi"}]
    assert session_key(messages=plain) == session_key(messages=parts)
    assert session_key(messages=plain) == session_key(messages=anthropic, system="sys")


def test_the_answering_model_is_remembered_and_preferred_warm(tmp_path: Path) -> None:
    models = [candidate("loc-a", "local", 86.0), candidate("loc-b", "local", 86.0)]
    rig = Rig(tmp_path, {"loc-a": OK, "loc-b": OK}, models)
    assert rig.run(session_id="s1").decision.model == "loc-a"  # a tie ranks by id
    session = get_session(rig.db, "id:s1")
    assert session is not None
    assert session.pinned_model == "loc-a"
    # A fresh session has no preference; the remembered one earns the warm bonus.
    assert rig.run(session_id="s1").decision.breakdown.warm_bonus > 0
    assert rig.run(session_id="other").decision.breakdown.warm_bonus == 0


def test_progress_callback_reports_each_stage_without_request_text(tmp_path: Path) -> None:
    seen: list[ProgressEvent] = []
    rig = Rig(tmp_path, {FREE: [RATE_LIMITED], LOCAL: OK}, pool(local=84.0))
    rig.run(on_progress=seen.append)
    assert stages(seen) == [
        "analyzing",
        "scope",
        "deciding",
        "executing",
        "cascading",
        "executing",
        "done",
    ]
    assert seen[-1].model == LOCAL
    assert "write a function" not in repr(seen)


def test_a_failing_progress_callback_does_not_break_routing(tmp_path: Path) -> None:
    def boom(event: ProgressEvent) -> None:
        raise RuntimeError("callback bug")

    rig = Rig(tmp_path, {FREE: OK}, pool())
    assert rig.run(on_progress=boom).decision.model == FREE


def test_a_secret_in_the_prompt_forces_local(tmp_path: Path) -> None:
    detector = SecretDetector.from_catalog(load_catalog(REPO_ROOT / SEED_RELATIVE_PATH))
    rig = Rig(tmp_path, {LOCAL: OK}, pool(), secrets=detector)
    result = rig.run(messages=[{"role": "user", "content": f"my key is {SK}, debug this"}])
    assert result.decision.model == LOCAL
    assert rig.cloud_clients == 0
    assert SK not in repr(result.reasons)
