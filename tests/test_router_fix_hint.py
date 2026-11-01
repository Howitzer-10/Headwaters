"""Local fix hints name the catalog's recommended model (HDW-137, F20)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from headwaters.core.analyzer.requirements import HardRequirements, Requirements
from headwaters.core.catalog.schema import GpuProfile
from headwaters.core.router_prep import pull_hint
from headwaters.core.scope import ScopeError
from tests.router_rig import Rig
from tests.router_support import requirements

PROFILES = {
    "laptop_12gb": GpuProfile(
        vram_gb=12,
        recommended={
            "vision": ["vis:7b"],
            "agentic_coder": ["coder:14b"],
            "general": ["gen:8b"],
        },
    ),
    "desktop_16gb": GpuProfile(vram_gb=16, recommended={"vision": ["vis:big"]}),
}


def vision_reqs() -> Requirements:
    base = requirements("vision")
    return dataclasses.replace(base, hard=HardRequirements(True, False, False, 1000))


def test_vision_request_in_local_scope_names_the_catalog_vision_model(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {}, [], reqs=vision_reqs(), gpu_profiles=PROFILES)
    with pytest.raises(ScopeError) as info:
        rig.run(scope="local")
    assert info.value.code == "local_unavailable"
    assert info.value.fix_hint == "pull vis:7b"


def test_hint_follows_the_role_of_the_request(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {}, [], reqs=requirements("code"), gpu_profiles=PROFILES)
    with pytest.raises(ScopeError) as info:
        rig.run(scope="local")
    assert info.value.fix_hint == "pull coder:14b"


def test_without_catalog_profiles_the_generic_hint_stays(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {}, [], reqs=vision_reqs())
    with pytest.raises(ScopeError) as info:
        rig.run(scope="local")
    assert "Start Ollama" in str(info.value.fix_hint)


def test_pull_hint_falls_back_to_smallest_profile_listing_the_role() -> None:
    reqs = requirements("chat")
    assert pull_hint(reqs, None, PROFILES) == "pull gen:8b"
    assert pull_hint(vision_reqs(), None, {"desktop_16gb": PROFILES["desktop_16gb"]}) == (
        "pull vis:big"
    )
    assert pull_hint(reqs, None, {"desktop_16gb": PROFILES["desktop_16gb"]}) is None
