"""The local market each call is measured against, by trading currency (spike, 2026-10-27:
each returns ~250 daily bars for Range.Y1)."""

from obb_terminal.domain.models import Currency

BENCHMARK: dict[Currency, str] = {
    Currency.USD: "SPY",
    Currency.GBP: "^FTAS",  # FTSE All-Share
    Currency.BRL: "^BVSP",  # Ibovespa
}
