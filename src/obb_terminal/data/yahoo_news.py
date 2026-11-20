"""Headlines from Yahoo Finance's RSS feed (OpenBB CompanyNews returns nothing — Spike S1)."""

from __future__ import annotations

import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import UTC
from email.utils import parsedate_to_datetime

from obb_terminal.data.market import no_data
from obb_terminal.domain.models import NewsItem

RSS_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"


def _http_get(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.read().decode("utf-8", "replace")


def parse_rss(text: str) -> list[NewsItem]:
    root = ET.fromstring(text)
    items: list[NewsItem] = []
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        pub = node.findtext("pubDate")
        if not title or not link.startswith(("https://", "http://")) or not pub:
            continue
        try:
            published = parsedate_to_datetime(pub).astimezone(UTC)
        except (TypeError, ValueError):
            continue
        items.append(NewsItem(title=title, url=link, published=published, source=None))
    items.sort(key=lambda item: item.published, reverse=True)
    return items


class YahooNews:
    def __init__(self, http_get: Callable[[str], str] | None = None) -> None:
        self._get = http_get or _http_get

    def fetch(self, symbol: str, limit: int = 20) -> list[NewsItem]:
        url = RSS_URL.format(symbol=urllib.parse.quote(symbol))
        items = parse_rss(self._get(url))[:limit]
        if not items:
            raise no_data(symbol)
        return items
