"""Read-only user configuration: <config dir>/obb-terminal/config.toml (section [ai]); the config
dir is $XDG_CONFIG_HOME, ~/.config or %APPDATA% (see osdirs)."""

from __future__ import annotations

import logging
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from obb_terminal.osdirs import config_base

log = logging.getLogger(__name__)

EFFORTS = ("low", "medium", "high", "xhigh", "max")
MIN_TIMEOUT_S, MAX_TIMEOUT_S = 30.0, 900.0
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\[\]-]{0,63}$")


@dataclass(frozen=True, slots=True)
class AiConfig:
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    timeout_s: float = 300.0
    claude_path: str | None = None


@dataclass(frozen=True, slots=True)
class AppConfig:
    ai: AiConfig = field(default_factory=AiConfig)


def config_dir() -> Path:
    return config_base() / "obb-terminal"


def _ai(section: object) -> AiConfig:
    default = AiConfig()
    if not isinstance(section, dict):
        return default
    model = section.get("model", default.model)
    if not isinstance(model, str) or not _MODEL_RE.match(model):
        log.warning("Ignoring invalid ai.model %r", model)
        model = default.model
    effort = section.get("effort", default.effort)
    if effort not in EFFORTS:
        log.warning("Ignoring invalid ai.effort %r", effort)
        effort = default.effort
    timeout = section.get("timeout_s", default.timeout_s)
    if isinstance(timeout, bool) or not isinstance(timeout, int | float):
        log.warning("Ignoring invalid ai.timeout_s %r", timeout)
        timeout = default.timeout_s
    timeout = min(max(float(timeout), MIN_TIMEOUT_S), MAX_TIMEOUT_S)
    path = section.get("claude_path")
    if not isinstance(path, str) or not path.strip():
        path = None
    return AiConfig(model=model, effort=effort, timeout_s=timeout, claude_path=path)


def load_config(path: Path | None = None) -> AppConfig:
    """Never raises: a missing or broken file gives the defaults (and a log line)."""
    path = path or config_dir() / "config.toml"
    if not path.exists():
        return AppConfig()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        log.warning("Unreadable config %s (%s); using defaults", path, exc)
        return AppConfig()
    return AppConfig(ai=_ai(data.get("ai")))
