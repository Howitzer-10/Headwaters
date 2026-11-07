import sys

import pytest

pytest_plugins = ["tests.live_support"]  # the live_home fixture (no process outlives its test)


@pytest.fixture(autouse=True)
def _wizard_paths_are_isolated(request: pytest.FixtureRequest) -> None:
    """Point every path the setup wizard can reach at ``tmp_path`` for the HDW-505 tests.

    An ``autouse`` fixture imported into a test module is not autouse there, so the guard lives
    in the conftest, where it is guaranteed to run before the test body. A wizard test that is
    not isolated edits this machine's real ``rules.yaml``.
    """
    if "wizard" not in request.node.path.name:
        return
    from tests.wizard_support import isolate_paths

    isolate_paths(request.getfixturevalue("monkeypatch"), request.getfixturevalue("tmp_path"))


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if sys.platform == "win32":
        return
    skip = pytest.mark.skip(reason="Windows-only test")
    for item in items:
        if "windows" in item.keywords:
            item.add_marker(skip)
