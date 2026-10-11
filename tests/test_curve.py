import json

import pandas as pd
import pytest

from global_investment_research import curve, monitor


def _write(tmp_path, series):
    insts = [{"symbol": s, "status": "ok", "weekly": {"time": [f"{d}T00:00:00Z" for d in dates], "close": closes}}
             for s, (dates, closes) in series.items()]
    (tmp_path / "rates.json").write_text(json.dumps({"instruments": insts}))


def test_load_rates_scales_yields_and_keeps_vix3m(tmp_path):
    d = ["2026-09-28", "2026-10-05"]
    _write(tmp_path, {"IRX": (d, [40.0, 40.57]), "TNX": (d, [52.0, 52.44]), "VIX3M": (d, [18.0, 17.77])})
    r = curve.load_rates(tmp_path)
    assert list(r.index.strftime("%Y-%m-%d")) == ["2026-10-02", "2026-10-09"]   # Friday week-ends
    assert r["3m"].iloc[-1] == pytest.approx(4.057) and r["10y"].iloc[-1] == pytest.approx(5.244)
    assert r["VIX3M"].iloc[-1] == 17.77


def test_load_rates_rejects_unscaled_yields(tmp_path):
    _write(tmp_path, {"TNX": (["2026-10-05"], [524.4])})
    with pytest.raises(ValueError):
        curve.load_rates(tmp_path)


def test_curve_readings_inverted_and_backwardation():
    idx = pd.date_range("2026-01-02", periods=20, freq="W-FRI")
    rates = pd.DataFrame({"3m": 5.0, "10y": [4.9 - 0.01 * i for i in range(20)], "VIX3M": 20.0}, index=idx)
    vix = pd.Series(24.0, index=idx)
    out = {r["topic"]: r for r in curve.curve_readings(rates, vix)}
    assert out["Yield curve"]["reading"] == "Curve inverted, flattening"
    assert "spread -0.29 pts" in out["Yield curve"]["evidence"]
    assert out["Volatility term structure"]["reading"].startswith("Backwardation")


def test_monitor_skips_stale_rates_and_never_reads_ahead():
    idx = pd.date_range("2026-01-02", periods=30, freq="W-FRI")
    prices = pd.DataFrame({"SPY": range(100, 130)}, index=idx, dtype=float)
    meta = {"SPY": {"market": "US", "kind": "etf", "sector": "Broad"}}
    stale = pd.DataFrame({"3m": 4.0, "10y": 5.0}, index=idx[:10])
    mon = monitor.build_monitor(prices, meta, rates=stale)
    assert "Yield curve" not in {r["topic"] for r in mon["regime"]}
    assert "refresh rates.json" in mon["data_quality"]["rates"]
    ahead = pd.DataFrame({"3m": 4.0, "10y": [5.0] * 30 + [3.0]},
                         index=idx.append(pd.DatetimeIndex([idx[-1] + pd.Timedelta(weeks=1)])))
    mon = monitor.build_monitor(prices, meta, rates=ahead)
    yc = next(r for r in mon["regime"] if r["topic"] == "Yield curve")
    assert "spread +1.00 pts" in yc["evidence"]
