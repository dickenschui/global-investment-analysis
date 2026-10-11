import json

import numpy as np
import pandas as pd
import pytest

from global_investment_research import analytics as an
from global_investment_research import company, income, monitor, portfolio
from global_investment_research.cli import main, synthetic
from global_investment_research.data import load_ibkr_snapshot, load_universe

WEEKS = pd.date_range("2025-10-03", periods=54, freq="W-FRI")


def series(values, idx=WEEKS):
    return pd.Series(values, index=idx[: len(values)], dtype=float)


# --- analytics -------------------------------------------------------------

def test_frequency_inference():
    assert an.infer_bars_per_year(WEEKS) == 52
    assert an.infer_bars_per_year(pd.bdate_range("2026-01-01", periods=30)) == 252
    with pytest.raises(ValueError):
        an.infer_bars_per_year(pd.date_range("2026-01-01", periods=10, freq="MS"))


def test_trailing_returns_known_answer():
    s = series(np.arange(100, 154))  # 54 bars, +1 per week
    assert an.trailing_return(s, 1) == pytest.approx(153 / 152 - 1)
    assert an.trailing_return(s, 52) == pytest.approx(153 / 101 - 1)
    assert an.trailing_return(s, 60) is None  # not enough history -> None, never a guess


def test_drawdown_and_trend():
    s = series([100, 120, 90, 95])
    assert an.max_drawdown(s) == pytest.approx(-0.25)
    assert an.drawdown_from_high(s) == pytest.approx(95 / 120 - 1)
    up = series(np.linspace(100, 200, 54))
    assert an.trend_state(up, 52)["state"] == "up"
    assert an.trend_state(series(np.linspace(200, 100, 54)), 52)["state"] == "down"
    assert an.trend_state(series(np.linspace(100, 200, 20)), 52)["state"] is None


def test_split_correlation_flags_only_consistent_patterns():
    rng = np.random.default_rng(0)
    x = rng.normal(size=100)
    df = pd.DataFrame({"a": x, "b": x + rng.normal(scale=0.3, size=100)})
    assert an.split_correlation(df, "a", "b")["stable"] is True
    flip = np.r_[x[:50], -x[50:]]
    df2 = pd.DataFrame({"a": x, "b": flip})
    assert an.split_correlation(df2, "a", "b")["stable"] is False


# --- portfolio -------------------------------------------------------------

def test_min_variance_prefers_low_vol_and_respects_cap():
    rng = np.random.default_rng(1)
    r = pd.DataFrame({"low": rng.normal(0, 0.01, 200), "high": rng.normal(0, 0.05, 200)})
    w = portfolio.min_variance_weights(r, max_weight=1.0)
    assert w.sum() == pytest.approx(1) and w["low"] > 0.8
    w2 = portfolio.min_variance_weights(r, max_weight=0.6)
    assert w2["low"] <= 0.6 + 1e-6


def test_backtest_has_no_lookahead():
    rng = np.random.default_rng(2)
    idx = pd.bdate_range("2025-01-01", periods=120)
    px = pd.DataFrame(100 * np.cumprod(1 + rng.normal(0, 0.01, (120, 3)), axis=0), index=idx, columns=list("abc"))
    base = portfolio.backtest_min_variance(px, 0.7, 0.6, 252)
    shocked = px.copy()
    shocked.iloc[90:] *= 3  # change only the future
    assert portfolio.backtest_min_variance(shocked, 0.7, 0.6, 252)["weights"] == pytest.approx(base["weights"])
    assert base["training"]["end"] < base["allocation_date"]


def test_currency_conversion_uses_same_date_fx():
    idx = pd.date_range("2026-01-02", periods=3, freq="D")
    px = pd.DataFrame({"x": [100.0, 100.0, 100.0]}, index=idx)
    fx = pd.Series([1.2, 1.3, np.nan], index=idx)
    out = portfolio.to_base_currency(px, {"x": "GBP"}, {"GBP": fx})
    assert out["x"].iloc[1] == pytest.approx(130)
    assert np.isnan(out["x"].iloc[2])  # no forward-fill of FX


# --- company / income ----------------------------------------------------

def test_company_checks_and_currency_guard():
    stmt = dict(revenue=100, gross_profit=60, operating_income=30, net_income=20, total_assets=200,
                total_liabilities=120, total_equity=80, operating_cashflow=35, capex=-10, dividends_paid=-5)
    res = company.analyse_period(stmt, prior={"revenue": 80, "total_equity": 70})
    assert res["free_cashflow"] == 25
    assert res["ratios"]["revenue_growth"] == pytest.approx(0.25)
    assert res["ratios"]["dividend_fcf_cover"] == pytest.approx(5)
    assert res["accounting_equation"]["status"] == "pass"
    bad = company.analyse_period({**stmt, "total_equity": 50})
    assert bad["accounting_equation"]["status"] == "fail"
    assert company.valuation_multiples(100, 10, 5, "HKD", "CNY")["pe"] is None


def test_income_scale_reconciliation_and_band():
    as_of = pd.Timestamp("2026-10-09")
    divs = [{"ex_date": "2026-03-01", "amount": 3.0}, {"ex_date": "2026-09-01", "amount": 3.0},
            {"ex_date": "2025-09-01", "amount": 9.0}]  # outside window
    cash, n = income.trailing_cash(divs, as_of)
    assert (cash, n) == (6.0, 2)
    # Price quoted in pence (100), dividends in pounds would give 6%; vendor says 6% -> agree.
    row = income.income_row("X", 100.0, as_of, divs, 6.0)
    assert row["yield"] == pytest.approx(0.06) and row["target_fit"] == "in target"
    # Dividends recorded in pence vs price in pounds: computed 600% vs vendor 6% -> rescale.
    row2 = income.income_row("Y", 1.0, as_of, divs, 6.0)
    assert row2["yield"] == pytest.approx(0.06) and "rescaled" in row2["yield_basis"]
    assert row["yield_after_cut"]["20%"] == pytest.approx(0.048)


# --- data / monitor / CLI -------------------------------------------------

def test_snapshot_loader_validates(tmp_path):
    uni = {"instruments": [{"symbol": "AAA", "name": "A", "market": "US", "kind": "stock", "sector": "x", "group": "g"}]}
    good = {"source": "test", "instruments": [{"symbol": "AAA", "status": "ok", "currency": "USD",
            "weekly": {"time": ["2026-10-02T13:30:00Z", "2026-10-09T13:30:00Z"], "close": [10, 11]}}]}
    (tmp_path / "g.json").write_text(json.dumps(good))
    px, meta = load_ibkr_snapshot(tmp_path, uni)
    assert px["AAA"].tolist() == [10, 11] and meta["AAA"]["currency"] == "USD"
    bad = json.loads(json.dumps(good))
    bad["instruments"][0]["weekly"]["close"] = [10]
    (tmp_path / "g.json").write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        load_ibkr_snapshot(tmp_path, uni)


def test_monitor_history_and_regime_changes(tmp_path):
    uni = load_universe()
    px, meta = synthetic(uni)
    m1 = monitor.build_monitor(px, meta)
    assert m1["data_quality"]["with_prices"] == len(uni["instruments"])
    assert all(r["evidence"] for r in m1["regime"])
    monitor.append_history(m1, tmp_path)
    monitor.append_history(m1, tmp_path)  # same date replaces, not duplicates
    assert len((tmp_path / "history.jsonl").read_text().splitlines()) == 1
    m2 = json.loads(json.dumps(m1))
    m2["as_of"] = "2026-10-16"
    m2["regime"] = [{**r, "reading": "changed"} if i == 0 else r for i, r in enumerate(m2["regime"])]
    monitor.append_history(m2, tmp_path)
    ch = monitor.regime_changes(tmp_path)
    assert len(ch) == 1 and ch[0]["to"] == "changed"


def test_cli_demo_end_to_end(tmp_path):
    assert main(["demo", "--out", str(tmp_path)]) == 0
    for f in ("monitor.html", "us-tech.html", "uk-fx.html", "sg-income.html"):
        html = (tmp_path / f).read_text()
        assert "<title>" in html and "SYNTHETIC" in html
    data = json.loads((tmp_path / "monitor.json").read_text())
    assert data["as_of"] == "2026-10-09"
