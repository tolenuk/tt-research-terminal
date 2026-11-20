import pytest

from obb_terminal.domain.classify import classify
from obb_terminal.domain.models import AssetType


@pytest.mark.parametrize(
    "symbol,quote_type,name,expected",
    [
        ("AAPL", "EQUITY", "Apple Inc.", AssetType.EQUITY),
        ("SPY", "ETF", "State Street SPDR S&P 500 ETF Trust", AssetType.ETF),
        ("SHEL.L", "EQUITY", "Shell plc", AssetType.EQUITY),
        ("PETR4.SA", "EQUITY", "Petróleo Brasileiro S.A. - Petrobras", AssetType.EQUITY),
        ("HGLG11.SA", "EQUITY", "Cshg Logistica - Fundo De Investimento Imobiliario", AssetType.FII),
        ("HGLG11.SA", "EQUITY", "FII HGLG PAXCI  ER", AssetType.FII),
        ("BOVA11.SA", "EQUITY", "ISHARES IBOVESPA CLASSE DE ÍNDICE - RESPONSABILIDADE LIMITADA", AssetType.ETF),
        ("IVVB11.SA", "ETF", "iShares S&P 500", AssetType.ETF),
        ("TAEE11.SA", "EQUITY", "Transmissora Aliança de Energia Elétrica S.A.", AssetType.EQUITY),
        ("KLBN11.SA", "EQUITY", "Klabin S.A. Fundo", AssetType.EQUITY),  # override wins
        ("XPML11.SA", "MUTUALFUND", "XP Malls", AssetType.FII),
        ("ABCD11.SA", None, None, AssetType.EQUITY),
    ],
)
def test_classify(symbol, quote_type, name, expected):
    assert classify(symbol, quote_type, name) is expected
