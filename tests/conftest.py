import sys

import pytest

pytest_plugins = ["tests.live_support"]  # the live_home fixture (no process outlives its test)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if sys.platform == "win32":
        return
    skip = pytest.mark.skip(reason="Windows-only test")
    for item in items:
        if "windows" in item.keywords:
            item.add_marker(skip)
