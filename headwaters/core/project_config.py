"""Per-project config (``.headwaters.yaml``), discovered by walking up from the workspace.

``privacy`` can only tighten routing: ``local`` > ``free`` > ``any``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from headwaters.core import paths
from headwaters.core.rules_io import ConfigError, format_validation_error, read_yaml_mapping

PROJECT_FILE_NAME = ".headwaters.yaml"

Privacy = Literal["local", "free", "any"]
_SCOPE_RANK: dict[str, int] = {"auto": 0, "cloud": 0, "free": 1, "local": 2}


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    privacy: Privacy = "any"
    checks: list[str] = Field(default_factory=list)


def find_project_file(start: Path) -> Path | None:
    """Walk up from ``start`` (a directory or a file) to the nearest ``.headwaters.yaml``."""
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for directory in (current, *current.parents):
        candidate = directory / PROJECT_FILE_NAME
        if paths.long_safe(candidate).is_file():
            return candidate
    return None


def load_project_config(workspace: Path) -> tuple[ProjectConfig, Path | None]:
    """Return the nearest project config and its path, or defaults and ``None``."""
    found = find_project_file(workspace)
    if found is None:
        return ProjectConfig(), None
    data = read_yaml_mapping(found)
    try:
        return ProjectConfig.model_validate(data), found
    except ValidationError as exc:
        raise ConfigError(found, format_validation_error(exc)) from exc
