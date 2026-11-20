"""Real Yahoo/OpenBB responses captured in Spike S1 (2026-10-09), trimmed."""

AAPL_QUOTE = [{"symbol": "AAPL", "asset_type": "EQUITY", "name": "Apple Inc.", "exchange": "NMS",
               "last_price": 336.64, "open": 331.695, "high": 338.61, "low": 330.7, "volume": 37750739,
               "prev_close": 340.42, "year_high": 345.34, "year_low": 243.42,
               "volume_average": 45209217.0, "currency": "USD"}]
SHEL_QUOTE = [{"symbol": "SHEL.L", "asset_type": "EQUITY", "name": "Shell plc", "exchange": "LSE",
               "last_price": 3793.5, "open": 3752.5, "high": 3806.5, "low": 3714.0, "volume": 10913950,
               "prev_close": 3780.5, "year_high": 3806.5, "year_low": 2553.772,
               "volume_average": 10045201.0, "currency": "GBp"}]
SPY_QUOTE = [{"symbol": "SPY", "asset_type": "ETF", "name": "State Street SPDR S&P 500 ETF Trust",
              "exchange": "PCX", "open": 776.24, "high": 779.42, "low": 775.14, "volume": 21433834,
              "prev_close": 773.93, "year_high": 781.62, "year_low": 629.28,
              "volume_average": 43783673.0, "currency": "USD"}]
HGLG_QUOTE = [{"symbol": "HGLG11.SA", "asset_type": "EQUITY",
               "name": "Cshg Logistica - Fundo De Investimento Imobiliario", "exchange": "SAO",
               "last_price": 157.58, "prev_close": 155.5, "currency": "BRL"}]
BOVA_QUOTE = [{"symbol": "BOVA11.SA", "asset_type": "EQUITY",
               "name": "ISHARES IBOVESPA CLASSE DE ÍNDICE - RESPONSABILIDADE LIMITADA",
               "exchange": "SAO", "last_price": 205.55, "prev_close": 203.08, "currency": "BRL"}]

AAPL_INFO = [{"symbol": "AAPL", "name": "Apple Inc.", "stock_exchange": "NMS", "issue_type": "EQUITY",
              "currency": "USD", "market_cap": 4912984883200, "dividend_yield": 0.32, "beta": 1.069,
              "sector": "Technology", "industry_category": "Consumer Electronics",
              "hq_country": "United States", "company_url": "https://www.apple.com",
              "long_description": "Apple Inc. designs, manufactures, and markets smartphones."}]
SPY_INFO = [{"symbol": "SPY", "name": "State Street SPDR S&P 500 ETF Trust", "stock_exchange": "PCX",
             "issue_type": "ETF", "currency": "USD", "dividend_yield": 0.99}]
HGLG_INFO = [{"symbol": "HGLG11.SA", "name": "Cshg Logistica - Fundo De Investimento Imobiliario",
              "stock_exchange": "SAO", "issue_type": "EQUITY", "currency": "BRL",
              "market_cap": 5324246528, "dividend_yield": 9.03, "beta": 0.085}]
BOVA_INFO = [{"symbol": "BOVA11.SA",
              "name": "ISHARES IBOVESPA CLASSE DE ÍNDICE - RESPONSABILIDADE LIMITADA",
              "stock_exchange": "SAO", "issue_type": "EQUITY", "currency": "BRL"}]

SHEL_KEY_METRICS = [{"symbol": "SHEL.L", "currency": "USD", "market_cap": 215941365760,
                     "pe_ratio": 11.223372, "dividend_yield": 3.06, "beta": -0.192}]

SHEL_DAILY = [{"date": "2026-09-29", "close": 3700.0},
              {"date": "2026-09-25", "open": 3605.5, "high": 3634.0, "low": 3580.0, "close": 3611.0,
               "volume": 13029960},
              {"date": "2026-09-26", "close": 3650.0}]
SHEL_5M = [{"date": "2026-10-08T16:25:00", "close": 3780.0},
           {"date": "2026-10-09T08:00:00", "close": 3790.0},
           {"date": "2026-10-09T16:25:00", "close": 3795.5}]
SPY_DAILY = [{"date": "2026-10-07", "close": 770.0}, {"date": "2026-10-08", "close": 777.0}]

AAPL_INCOME = [{"period_ending": "2024-09-28", "fiscal_year": 2024, "total_revenue": 391035000000.0,
                "net_income": 93736000000.0},
               {"period_ending": "2025-09-30", "fiscal_year": 2025, "total_revenue": 416161000000.0,
                "net_income": 112010000000.0, "reported_currency": "USD"}]

SHEL_DIVIDENDS = [{"ex_dividend_date": "2025-05-15", "amount": 26.0},
                  {"ex_dividend_date": "2026-05-14", "amount": 27.5},
                  {"ex_dividend_date": "2026-02-12", "amount": 27.0}]
HGLG_DIVIDENDS = [{"ex_dividend_date": "2024-01-02", "amount": 0.9},
                  {"ex_dividend_date": "2026-08-01", "amount": 1.10},
                  {"ex_dividend_date": "2026-09-01", "amount": 1.10}]

SPY_ETF_INFO = [{"symbol": "SPY", "name": "State Street SPDR S&P 500 ETF Trust",
                 "total_assets": 809216114688, "nav_price": 777.39307, "dividend_yield": 0.0099,
                 "fund_type": "Exchange Traded Fund", "currency": "USD"}]

AAPL_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Yahoo! Finance: AAPL News</title>
<item><title>Older story</title><link>https://finance.yahoo.com/news/older.html</link>
<pubDate>Thu, 08 Oct 2026 13:10:00 +0000</pubDate><guid>a</guid><description>d</description></item>
<item><title>Newer story</title><link>https://finance.yahoo.com/news/newer.html</link>
<pubDate>Fri, 09 Oct 2026 20:00:21 +0000</pubDate><guid>b</guid><description>d</description></item>
<item><title>Unsafe link</title><link>javascript:alert(1)</link>
<pubDate>Fri, 09 Oct 2026 20:00:21 +0000</pubDate><guid>c</guid></item>
<item><title>No date</title><link>https://finance.yahoo.com/news/nodate.html</link><guid>d</guid></item>
</channel></rss>"""
EMPTY_RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>x</title></channel></rss>"""

PETROBRAS_SEARCH = [
    {"symbol": "PBR", "quoteType": "EQUITY", "exchange": "NYQ", "shortname": "Petroleo Brasileiro S.A. ",
     "longname": "Petróleo Brasileiro S.A. - Petrobras"},
    {"symbol": "PETR4.SA", "quoteType": "EQUITY", "exchange": "SAO", "shortname": "PETROBRAS   PN      N2",
     "longname": "Petróleo Brasileiro S.A. - Petrobras"},
    {"symbol": "PBRCO.CL", "quoteType": "EQUITY", "exchange": "BVC", "shortname": "PETROLEO BRASILEIRO SA PE"},
    {"symbol": "SAAPL=F", "quoteType": "FUTURE", "exchange": "CME", "shortname": "Apple Inc Stock Futures"},
    {"symbol": "PETR3.SA", "quoteType": "EQUITY", "exchange": "SAO", "shortname": "PETROBRAS   ON      N2"},
]
HGLG_SEARCH = [{"symbol": "HGLG11.SA", "quoteType": "EQUITY", "exchange": "SAO",
                "shortname": "FII HGLG PAXCI  ER"}]
