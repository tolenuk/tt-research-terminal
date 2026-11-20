from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from obb_terminal.domain.models import (
    RANGE_SPEC,
    Currency,
    PricePoint,
    PriceSeries,
    Quote,
    Range,
)


def test_price_series_arrays_returns_epoch_seconds_and_closes():
    t1 = datetime(2026, 10, 1, tzinfo=UTC)
    t2 = datetime(2026, 10, 2, tzinfo=UTC)
    series = PriceSeries(
        "AAPL", Range.Y1, Currency.USD, (PricePoint(t1, 1.5), PricePoint(t2, 2.5))
    )
    xs, ys = series.arrays()
    assert xs == [t1.timestamp(), t2.timestamp()]
    assert ys == [1.5, 2.5]


def test_models_are_frozen():
    quote = Quote(symbol="AAPL", currency=Currency.USD, last=1.0)
    with pytest.raises(FrozenInstanceError):
        quote.last = 2.0  # type: ignore[misc]


def test_range_spec_covers_every_range():
    assert set(RANGE_SPEC) == set(Range)
    assert RANGE_SPEC[Range.D1] == ("5m", 5)
    assert RANGE_SPEC[Range.Y5] == ("1W", 1828)


def test_valuation_defaults():
    from datetime import date

    from obb_terminal.domain.models import (
        METHOD_LABELS,
        AssetType,
        Assumptions,
        Method,
        MethodWeights,
        ScoreWeights,
        ValuationInputs,
    )

    inputs = ValuationInputs("AAPL", "Apple", AssetType.EQUITY, Currency.USD, date(2026, 10, 9), 100.0)
    assert inputs.fx_rate == 1.0
    assert inputs.fcf_history == () and inputs.gaps == () and inputs.analyst is None
    a = Assumptions(0.09, 0.05, 0.025, 0.03)
    assert a.method_weights == MethodWeights(0.35, 0.30, 0.20, 0.15)
    assert a.score_weights == ScoreWeights(0.30, 0.20, 0.20, 0.15, 0.15)
    assert set(METHOD_LABELS) == set(Method)
