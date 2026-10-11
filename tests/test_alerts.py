import json

from global_investment_research import alerts as A
from global_investment_research.cli import main

DEC = {"as_of": "2026-10-09", "calls": [
    {"symbol": "XOM", "stance": "Accumulate", "stop": 146.0, "trigger": None},
    {"symbol": "JPM", "stance": "Watch", "stop": None, "trigger": 320.0},
]}
MON = {"metrics": {"XOM": {"vol": 0.25}, "JPM": {"vol": 0.20}, "SPY": {"vol": 0.12}}}
OPT = {"ideas": [{"symbol": "XOM", "direction": "bull", "underlying": 168.0, "structure": "Bull call spread"}]}


def pulse(quotes):
    return {"retrieved_at_utc": "2026-10-12T14:00:00Z", "quotes": quotes}


def test_quiet_market_gives_no_alerts():
    p = pulse([{"symbol": "XOM", "last": 169, "prior_close": 168.5}, {"symbol": "SPY", "last": 780, "prior_close": 779},
               {"symbol": "VIX", "last": 15, "prior_close": 14.8}, {"symbol": "USD.HKD", "last": 7.79, "prior_close": 7.79}])
    assert A.evaluate(p, DEC, MON, OPT) == []


def test_exceptions_fire_with_evidence():
    p = pulse([{"symbol": "XOM", "last": 140, "prior_close": 160},          # exit breach + extreme move + option
               {"symbol": "JPM", "last": 325, "prior_close": 324},          # revisit trigger
               {"symbol": "VIX", "last": 27, "prior_close": 20},            # stress + spike
               {"symbol": "USD.HKD", "last": 7.86, "prior_close": 7.84},     # peg band
               {"symbol": "GBP.USD", "last": 1.30, "prior_close": 1.33}])    # FX shock
    got = {(a["symbol"], a["rule"]): a["severity"] for a in A.evaluate(p, DEC, MON, OPT)}
    assert got[("XOM", "Exit level breached")] == "URGENT"
    assert got[("XOM", "Extreme daily move")] == "URGENT"
    assert got[("XOM", "Option idea moving against")] == "WARN"
    assert got[("JPM", "Revisit trigger crossed")] == "INFO"
    assert got[("VIX", "Volatility stress")] == "URGENT" and got[("VIX", "Volatility spike")] == "WARN"
    assert got[("USD.HKD", "HKD peg band breached")] == "URGENT"
    assert got[("GBP.USD", "FX shock")] == "URGENT"


def test_dedupe_within_day_and_reset_next_day(tmp_path):
    a = [{"symbol": "XOM", "rule": "Exit level breached", "severity": "URGENT", "evidence": "", "action": ""}]
    assert len(A.new_since_last(a, tmp_path, "2026-10-12")) == 1
    assert A.new_since_last(a, tmp_path, "2026-10-12") == []
    assert len(A.new_since_last(a, tmp_path, "2026-10-13")) == 1


def test_cli_alerts_end_to_end(tmp_path, capsys):
    rep = tmp_path / "rep"
    assert main(["demo", "--out", str(rep)]) == 0
    dec = json.loads((rep / "decisions.json").read_text())
    acc = dec["calls"][0]
    acc.update(stance="Accumulate", stop=100.0, last=110.0)  # force a known exit level
    (rep / "decisions.json").write_text(json.dumps(dec))
    pf = tmp_path / "pulse.json"
    pf.write_text(json.dumps(pulse([{"symbol": acc["symbol"], "last": acc["stop"] * 0.9, "prior_close": acc["last"]}])))
    assert main(["alerts", "--pulse", str(pf), "--reports", str(rep), "--state-dir", str(tmp_path / "s")]) == 0
    out = capsys.readouterr().out
    assert "Exit level breached" in out and "No live quote for" in out
    assert main(["alerts", "--pulse", str(pf), "--reports", str(rep), "--state-dir", str(tmp_path / "s")]) == 0
    assert "No new exceptions" in capsys.readouterr().out
