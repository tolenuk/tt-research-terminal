"""AI errors. They subclass DataError so the Loader forwards them unchanged to the UI."""

from __future__ import annotations

from obb_terminal.data.market import DataError


class AnalystError(DataError):
    default_message = "Analysis failed"

    def __init__(self, user_message: str | None = None, symbol: str | None = None) -> None:
        super().__init__(user_message or self.default_message, symbol)


class ClaudeNotFound(AnalystError):
    default_message = "Claude Code not found — install it and run `claude` once to log in"


class AnalystFailed(AnalystError):
    default_message = "Analysis failed"


class AnalystTimeout(AnalystError):
    default_message = "Analysis timed out"


class AnalystBadOutput(AnalystError):
    default_message = "Analysis returned an unexpected format"


class AnalystCancelled(AnalystError):
    default_message = "Analysis cancelled"
