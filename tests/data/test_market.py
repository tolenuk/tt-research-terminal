from obb_terminal.data.market import (
    DataError,
    NoData,
    SourceUnavailable,
    SymbolNotFound,
    no_data,
)


def test_data_error_carries_user_message_and_symbol():
    err = SymbolNotFound("Unknown symbol: ZZZZ", "ZZZZ")
    assert isinstance(err, DataError)
    assert err.user_message == "Unknown symbol: ZZZZ"
    assert err.symbol == "ZZZZ"
    assert str(err) == "Unknown symbol: ZZZZ"


def test_no_data_message():
    err = no_data("HGLG11.SA")
    assert isinstance(err, NoData)
    assert err.user_message == "No data available for HGLG11.SA"


def test_source_unavailable_reason_defaults_to_source():
    assert SourceUnavailable("x").reason == "source"
    assert SourceUnavailable("x", reason="network").reason == "network"
