"""Trimmed copies of fundamentus.com.br/resultado.php (ISO-8859-1, Brazilian number format)."""

HEADERS = [
    "Papel",
    "Cotação",
    "P/L",
    "P/VP",
    "PSR",
    "Div.Yield",
    "P/Ativo",
    "P/Cap.Giro",
    "P/EBIT",
    "P/Ativ Circ.Liq",
    "EV/EBIT",
    "EV/EBITDA",
    "Mrg Bruta",
    "Mrg Ebit",
    "Mrg. Líq.",
    "Liq. Corr.",
    "ROIC",
    "ROE",
    "Liq.2meses",
    "Patrim. Líq",
    "Dív.Líq/ Patrim.",
    "Cresc. Rec.5a",
]


def _row(ticker, name, cells):
    tds = "".join(f"<td>{c}</td>" for c in cells)
    return (
        f'<tr class=""><td ckass="res_papel"><span class="tips" title="{name}">'
        f'<a href="detalhes.php?papel={ticker}">{ticker}</a></span></td>{tds}</tr>'
    )


PETR4 = [
    "44,70",
    "4,10",
    "1,14",
    "0,977",
    "8,15%",
    "0,434",
    "-27,93",
    "2,19",
    "-0,90",
    "3,34",
    "2,50",
    "52,09%",
    "44,51%",
    "23,85%",
    "0,90",
    "21,41%",
    "27,82%",
    "2.710.580.000,00",
    "504.698.000.000,00",
    "\n\t\t\t\t0,59\t\t\t",
    "-1,75%",
]
CLAN3 = (
    ["0,00"] * 11
    + ["0,00%"] * 3
    + ["0,00", "0,00%", "-1,05%", "0,00", "1.012.240.000,00", "0,08", "-63,96%"]
)


def page(rows):
    heads = "".join(f'<th class="sortable-numeric"><a href="#">{h}</a></th>' for h in HEADERS)
    body = "".join(_row(*r) for r in rows)
    return (
        '<html><body><table id="resultado" class="resultado"><thead><tr>'
        f"{heads}</tr></thead><tbody>{body}</tbody></table></body></html>"
    )


RESULT = page([("PETR4", "PETROBRAS", PETR4), ("CLAN3", "CLARION", CLAN3)])
BANKS = page([("BBAS3", "BANCO DO BRASIL", PETR4), ("ITSA4", "ITAUSA", PETR4)])
NO_TABLE = "<html><body><p>Manutenção</p></body></html>"
