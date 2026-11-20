"""The Analyst boundary. ClaudeCodeAnalyst implements it; a Claude API backend could later."""

from __future__ import annotations

from typing import Protocol

from obb_terminal.ai.models import AnalystContext, CachedReport


class Analyst(Protocol):
    def availability(self) -> str | None:
        """None when ready; otherwise a hint to show. Cheap and GUI-safe."""
        ...

    def last(self, symbol: str) -> CachedReport | None:
        """The latest stored report for `symbol`, of any age. Never raises; GUI-safe."""
        ...

    def analyse(self, ctx: AnalystContext) -> CachedReport:
        """Blocking. ALWAYS runs a fresh analysis. Raises AnalystError subclasses."""
        ...

    def debate(self, ctx: AnalystContext) -> CachedReport:
        """Blocking. Bull, bear and risk passes, then a judge. Raises AnalystError subclasses."""
        ...

    def cancel(self) -> None:
        """Kill any in-flight analysis (called before the Loader shuts down)."""
        ...
