"""Classify a symbol as EQUITY, ETF or FII.

Yahoo reports Brazilian FIIs and some B3 ETFs (e.g. BOVA11) as EQUITY, so ``…11.SA`` symbols
are disambiguated by name. Share units (TAEE11, KLBN11…) are pinned in OVERRIDES.
"""

from __future__ import annotations

import re
import unicodedata

from obb_terminal.domain.models import AssetType

OVERRIDES: dict[str, AssetType] = {
    "TAEE11.SA": AssetType.EQUITY,
    "KLBN11.SA": AssetType.EQUITY,
    "SANB11.SA": AssetType.EQUITY,
    "BPAC11.SA": AssetType.EQUITY,
    "ENGI11.SA": AssetType.EQUITY,
}

_B3_UNIT = re.compile(r"^[A-Z]{4}11\.SA$")
_ETF_WORDS = re.compile(r"INDICE|ISHARES|\bETF\b")
_FII_WORDS = re.compile(r"FII|FUNDO|IMOBILI")


def _fold(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return ascii_text.upper()


def classify(symbol: str, quote_type: str | None, name: str | None) -> AssetType:
    sym = symbol.upper()
    if sym in OVERRIDES:
        return OVERRIDES[sym]
    qt = (quote_type or "").upper()
    if qt == "ETF":
        return AssetType.ETF
    if _B3_UNIT.match(sym):
        folded = _fold(name or "")
        if _ETF_WORDS.search(folded):
            return AssetType.ETF
        if _FII_WORDS.search(folded) or qt == "MUTUALFUND":
            return AssetType.FII
    return AssetType.EQUITY
