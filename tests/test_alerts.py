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
    assert "Exit level breached" in out and "No usable vendor quote" in out
    assert main(["alerts", "--pulse", str(pf), "--reports", str(rep), "--state-dir", str(tmp_path / "s")]) == 0
    assert "No new or escalated exceptions" in capsys.readouterr().out


def test_london_pence_quotes_and_dotted_tickers():
    dec = {"as_of": "2026-10-09", "calls": [
        {"symbol": "SHEL", "stance": "Accumulate", "stop": 31.02, "trigger": None},
        {"symbol": "HSBA", "stance": "Avoid", "stop": None, "trigger": 13.0},
        {"symbol": "BA.", "stance": "Hold", "stop": 15.0, "trigger": None}]}
    mon = {"metrics": {"SHEL": {"vol": 0.25, "last": 37.95}, "HSBA": {"vol": 0.25, "last": 13.96},
                       "BA.": {"vol": 0.39, "last": 18.574}}}
    kinds = {"SHEL": "stock", "HSBA": "stock", "BA.": "stock", "GBP.USD": "fx"}
    p = pulse([{"symbol": "SHEL", "last": 3050, "prior_close": 3100},      # pence: 30.50 GBP is below the 31.02 exit
               {"symbol": "HSBA", "last": 1396, "prior_close": 1390},      # pence: 13.96 is above 13.0 trigger, genuinely
               {"symbol": "BA.", "last": 1828, "prior_close": 1857.4}])    # -1.6% stock move is not an FX shock
    got = {(a["symbol"], a["rule"]) for a in A.evaluate(p, dec, mon, None, kinds)}
    assert ("SHEL", "Exit level breached") in got
    assert ("BA.", "FX shock") not in got
    assert ("HSBA", "Revisit trigger crossed") in got
    p2 = pulse([{"symbol": "HSBA", "last": 1250, "prior_close": 1260}])    # 12.50 GBP: below trigger, no false alert
    assert not [a for a in A.evaluate(p2, dec, mon, None, kinds) if a["symbol"] == "HSBA"]


def test_frozen_quotes_are_stale_not_alerts():
    p = pulse([{"symbol": "XOM", "last": 140, "prior_close": 160, "data_status": "FROZEN"}])
    assert A.evaluate(p, DEC, MON, OPT) == []
    _, stale = A.normalise_quotes(p, MON)
    assert stale == ["XOM"]


def test_freshness_gate_sets_old_quotes_aside():
    # retrieved 14:00Z; 14:00Z epoch = 1791813600
    r = 1791813600
    p = pulse([{"symbol": "XOM", "last": 140, "prior_close": 160, "ts": r - 89 * 60},          # 89 min: fresh
               {"symbol": "JPM", "last": 325, "prior_close": 324, "ts": "2026-10-12T12:29:00Z"},  # 91 min: stale
               {"symbol": "SPY", "last": 780, "prior_close": 779, "ts": r + 3600},             # future: stale
               {"symbol": "VIX", "last": 30, "prior_close": 20, "ts": "not a time"},           # unreadable: stale
               {"symbol": "USD.HKD", "last": 7.79, "prior_close": 7.79}])                      # no ts: kept
    assert A.quote_age_minutes(p["quotes"][0], p["retrieved_at_utc"]) == 89
    usable, stale = A.normalise_quotes(p, MON)
    assert sorted(usable) == ["USD.HKD", "XOM"] and stale == ["JPM", "SPY", "VIX"]
    got = {(a["symbol"], a["rule"]) for a in A.evaluate(p, DEC, MON, OPT)}
    assert ("XOM", "Exit level breached") in got and not any(s in ("JPM", "VIX") for s, _ in got)
    # the threshold is configurable: at 60 minutes XOM is stale too
    assert "XOM" in A.normalise_quotes(p, MON, max_age_minutes=60)[1]
    assert A.evaluate(p, DEC, MON, OPT, max_age_minutes=60) == []


def test_alert_text_shows_headline_context_and_cleared_retry():
    a = [{"symbol": "XOM", "rule": "Exit level breached", "severity": "URGENT", "evidence": "e", "action": "act",
          "cleared_at": "2026-10-12T15:00:00Z",
          "context": [{"title": "Exxon <cuts> outlook", "link": "https://x/1?a=1&b=2",
                       "published": "2026-10-12T13:05:00+00:00", "source": "fed_press"}]}]
    t = A.alert_text(a, "2026-10-12T16:00:00Z")
    assert '<a href="https://x/1?a=1&amp;b=2">Exxon &lt;cuts&gt; outlook</a> (2026-10-12 13:05) · fed_press' in t
    assert "Condition cleared at 2026-10-12T15:00:00Z" in t


def test_cli_alert_stays_pending_until_a_channel_delivers(tmp_path, capsys, monkeypatch):
    from global_investment_research import notify
    rep = tmp_path / "rep"
    assert main(["demo", "--out", str(rep)]) == 0
    dec = json.loads((rep / "decisions.json").read_text())
    acc = dec["calls"][0]
    acc.update(stance="Accumulate", stop=100.0, last=110.0)
    (rep / "decisions.json").write_text(json.dumps(dec))
    pf = tmp_path / "pulse.json"
    pf.write_text(json.dumps(pulse([{"symbol": acc["symbol"], "last": 90.0, "prior_close": 110.0}])))
    args = ["alerts", "--pulse", str(pf), "--reports", str(rep), "--state-dir", str(tmp_path / "s"), "--telegram"]

    def fail(text):
        raise OSError("network down")
    monkeypatch.setattr(notify, "send_telegram", fail)
    assert main(args) == 0
    assert "Telegram not sent" in capsys.readouterr().out
    sent = []
    monkeypatch.setattr(notify, "send_telegram", sent.append)
    assert main(args) == 0                      # undelivered notice is offered again
    assert "Exit level breached" in sent[0]
    assert main(args) == 0                      # delivered: not repeated
    assert len(sent) == 1


def test_heartbeat_only_judged_when_a_scheduled_run_was_missed():
    from global_investment_research.cli import _missed_scheduled_run as missed
    assert not missed("2026-10-13T10:20:00Z", "2026-10-13T11:35:00Z")    # normal hourly run, late scheduler
    assert missed("2026-10-13T10:20:00Z", "2026-10-13T13:20:00Z")        # 12:00 run missing
    assert not missed("2026-10-12T21:07:00Z", "2026-10-13T01:07:00Z")    # overnight: no runs scheduled
    assert not missed("2026-10-09T21:07:00Z", "2026-10-12T01:07:00Z")    # Friday to Monday: weekend
    assert missed("2026-10-12T21:07:00Z", "2026-10-14T09:07:00Z")        # all of Tuesday missing
    assert not missed(None, "2026-10-12T12:00:00Z")


def test_alerts_carry_the_numbers_needed_to_score_them():
    p = pulse([{"symbol": "XOM", "last": 140, "prior_close": 160}, {"symbol": "JPM", "last": 325, "prior_close": 324}])
    got = {(a["symbol"], a["rule"]): a for a in A.evaluate(p, DEC, MON, OPT)}
    assert {k: got[("XOM", "Exit level breached")][k] for k in ("observed", "reference", "kind")} == \
        {"observed": 140, "reference": 146.0, "kind": "below"}
    assert got[("JPM", "Revisit trigger crossed")]["kind"] == "above"
    assert got[("XOM", "Extreme daily move")]["reference"] == 160 and got[("XOM", "Extreme daily move")]["kind"] == "move"
    assert got[("XOM", "Option idea moving against")]["reference"] == 168.0 * 0.95


def test_alert_text_consolidates_and_labels_provenance():
    ctx = {"title": "Fed statement", "link": "https://x/1", "published": "2026-10-12T13:05:00+00:00", "source": "fed_press"}
    a = [{"symbol": "XOM", "rule": "Exit level breached", "severity": "URGENT", "evidence": "below exit", "action": "act",
          "context": [ctx]},
         {"symbol": "XOM", "rule": "Extreme daily move", "severity": "URGENT", "evidence": "-12%", "action": "check",
          "context": [ctx]},
         {"symbol": "JPM", "rule": "Revisit trigger crossed", "severity": "INFO", "evidence": "J", "action": "confirm"},
         {"symbol": "LSEG", "rule": "Revisit trigger crossed", "severity": "INFO", "evidence": "L", "action": "confirm"},
         {"symbol": "MONITOR", "rule": "Monitor heartbeat gap", "severity": "WARN", "evidence": "gap", "action": "look"}]
    t = A.alert_text(a, "2026-10-12T16:00:00Z", not_live=["2800", "ES3"], no_quote=["BARC"])
    assert t.count("<b>XOM</b>") == 1 and t.count("📰 Unverified headline:") == 1   # one block, headline once
    assert t.count("→ confirm") == 1                                                # shared INFO action once
    assert t.index("<b>Act now</b>") < t.index("<b>For the weekly close</b>") < t.index("<b>Monitor health</b>")
    assert "No usable vendor quote: BARC" in t and "2 watched lines not live" in t and "2800" not in t
    assert "unconfirmed until the official close" in t


def test_cli_alerts_record_pushes_quote_gaps_and_repeats(tmp_path, capsys, monkeypatch):
    from global_investment_research import notify
    rep = tmp_path / "rep"
    assert main(["demo", "--out", str(rep)]) == 0
    dec = json.loads((rep / "decisions.json").read_text())
    acc, watch = dec["calls"][0], next(c for c in dec["calls"] if c["stance"] == "Watch")
    acc.update(stance="Accumulate", stop=100.0, last=110.0)
    watch.update(trigger=50.0)
    (rep / "decisions.json").write_text(json.dumps(dec))
    sent = []
    monkeypatch.setattr(notify, "send_telegram", sent.append)
    pf = tmp_path / "pulse.json"
    args = ["alerts", "--pulse", str(pf), "--reports", str(rep), "--state-dir", str(tmp_path / "s"), "--telegram"]

    def run(quotes, at):
        pf.write_text(json.dumps({"retrieved_at_utc": at, "quotes": quotes, "missing": [], "missing_reasons": {}}))
        assert main(args) == 0
        return json.loads(sorted((rep / "alerts").glob("alerts-*.json"))[-1].read_text())
    info = {"symbol": watch["symbol"], "last": 55.0, "prior_close": 55.0}
    r = run([info], "2026-10-12T09:07:00Z")                     # INFO only: logged, not pushed
    assert r["pushed"] == [] and sent == [] and r["news_fetched"] is False
    assert acc["symbol"] in r["no_quote"] and r["retrieved_at_utc"] == "2026-10-12T09:07:00Z"
    r = run([{**info, "last": 45.0, "prior_close": 45.0}], "2026-10-12T09:27:00Z")  # cleared
    r = run([info], "2026-10-12T09:47:00Z")                     # crosses again in the same desk week
    assert r["sent"] == [] and [x["key"] for x in r["repeats"]] == [f"{watch['symbol']}|Revisit trigger crossed"]
    r = run([{"symbol": acc["symbol"], "last": 90.0, "prior_close": 110.0}], "2026-10-12T10:07:00Z")
    assert f"{acc['symbol']}|Exit level breached" in r["pushed"] and len(sent) == 1
