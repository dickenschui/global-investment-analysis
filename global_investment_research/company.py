"""Company fundamentals: ratios with explicit missing-value reasons and integrity checks."""
from __future__ import annotations

FIELDS = ("revenue", "gross_profit", "operating_income", "net_income", "total_assets",
          "total_liabilities", "total_equity", "operating_cashflow", "capex", "dividends_paid")


def _div(a, b):
    if a is None or b in (None, 0):
        return None
    return a / b


def analyse_period(stmt: dict, prior: dict | None = None) -> dict:
    """stmt: one fiscal period, values in reporting currency. capex is a positive outflow."""
    missing = [f for f in FIELDS if stmt.get(f) is None]
    ocf, capex = stmt.get("operating_cashflow"), stmt.get("capex")
    fcf = None if ocf is None or capex is None else ocf - abs(capex)
    ratios = {
        "gross_margin": _div(stmt.get("gross_profit"), stmt.get("revenue")),
        "operating_margin": _div(stmt.get("operating_income"), stmt.get("revenue")),
        "net_margin": _div(stmt.get("net_income"), stmt.get("revenue")),
        "fcf_margin": _div(fcf, stmt.get("revenue")),
        "cash_conversion": _div(fcf, stmt.get("net_income")),
        "debt_ratio": _div(stmt.get("total_liabilities"), stmt.get("total_assets")),
        "dividend_fcf_cover": _div(fcf, abs(stmt["dividends_paid"]) if stmt.get("dividends_paid") else None),
    }
    if prior and prior.get("total_equity") is not None and stmt.get("total_equity") is not None:
        ratios["roe_avg_equity"] = _div(stmt.get("net_income"), (stmt["total_equity"] + prior["total_equity"]) / 2)
    else:
        ratios["roe_avg_equity"] = None
    growth = _div(stmt.get("revenue"), (prior or {}).get("revenue"))
    ratios["revenue_growth"] = None if growth is None else growth - 1
    a, l, e = stmt.get("total_assets"), stmt.get("total_liabilities"), stmt.get("total_equity")
    if None in (a, l, e):
        check = {"status": "unavailable", "gap": None}
    else:
        gap = abs(a - (l + e)) / abs(a) if a else None
        check = {"status": "pass" if gap is not None and gap <= 0.005 else "fail", "gap": gap}
    return {"free_cashflow": fcf, "ratios": ratios, "accounting_equation": check, "missing_fields": missing}


def valuation_multiples(price: float, shares: float, eps: float | None, quote_ccy: str, report_ccy: str) -> dict:
    """Withhold multiples when quote and reporting currencies differ (e.g. Tencent HKD vs CNY)."""
    if quote_ccy != report_ccy:
        return {"pe": None, "market_cap": price * shares,
                "note": f"P/E withheld: quoted in {quote_ccy}, reports in {report_ccy}"}
    return {"pe": None if not eps or eps <= 0 else price / eps, "market_cap": price * shares, "note": ""}
