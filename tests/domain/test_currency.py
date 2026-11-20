import pytest

from obb_terminal.data.market import UnsupportedCurrency
from obb_terminal.domain.currency import normalise
from obb_terminal.domain.models import Currency


@pytest.mark.parametrize("code", ["GBp", "GBX", "GBx"])
def test_pence_codes_become_gbp_with_factor(code):
    assert normalise(code) == (Currency.GBP, 0.01)


@pytest.mark.parametrize("code,expected", [("USD", Currency.USD), ("GBP", Currency.GBP), ("BRL", Currency.BRL)])
def test_supported_codes_pass_through(code, expected):
    assert normalise(code) == (expected, 1.0)


@pytest.mark.parametrize("code", ["EUR", None, "", "gbp"])
def test_unsupported_codes_raise(code):
    with pytest.raises(UnsupportedCurrency):
        normalise(code)
