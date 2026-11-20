from datetime import date

import pytest

from obb_terminal.domain.dividends import cagr, ttm_total, ttm_yield
from obb_terminal.domain.models import Dividend

TODAY = date(2026, 10, 9)
DIVS = (
    Dividend(date(2026, 9, 1), 1.10),
    Dividend(date(2026, 8, 1), 1.10),
    Dividend(date(2025, 10, 9), 5.00),  # exactly 365 days ago: excluded
    Dividend(date(2024, 1, 2), 0.90),
)


def test_ttm_yield_sums_last_365_days_over_price():
    assert ttm_yield(DIVS, 157.58, TODAY) == pytest.approx(2.20 / 157.58)


@pytest.mark.parametrize("price", [None, 0.0])
def test_ttm_yield_none_without_price(price):
    assert ttm_yield(DIVS, price, TODAY) is None


def test_ttm_yield_zero_when_no_recent_dividends():
    assert ttm_yield((Dividend(date(2020, 1, 1), 1.0),), 10.0, TODAY) == 0.0


def test_ttm_total_sums_last_365_days():
    assert ttm_total(DIVS, TODAY) == pytest.approx(2.20)


def test_ttm_total_zero_without_recent_dividends():
    assert ttm_total((), TODAY) == 0.0


def test_cagr_over_five_years():
    divs = (Dividend(date(2026, 6, 1), 2.0), Dividend(date(2021, 6, 1), 1.0))
    assert cagr(divs, TODAY) == pytest.approx(2.0 ** 0.2 - 1)  # 0.148698


def test_cagr_none_when_history_shorter_than_five_years():
    divs = (Dividend(date(2026, 6, 1), 2.0), Dividend(date(2022, 6, 1), 1.0))
    assert cagr(divs, TODAY) is None


def test_cagr_none_when_a_total_is_not_positive():
    divs = (Dividend(date(2026, 6, 1), 2.0), Dividend(date(2019, 6, 1), 1.0))
    assert cagr(divs, TODAY) is None  # nothing paid in the 12 months before 2021-10-10
    assert cagr((), TODAY) is None
