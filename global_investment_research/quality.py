"""Weekly data-quality review of an IBKR snapshot against the previous week's snapshot.

Answers Improvement Log questions 1 (accuracy: did a price fail a cross-check?) and 6 (coverage:
which instruments had no data or stale data, and why?) from the saved files alone. It reads the raw
rows rather than `data.load_ibkr_snapshot`, so a failed manifest is reported instead of stopping the
review. Adapted from the reference app's research_quality audit, fitted to our snapshot JSON.

Nothing is estimated: an instrument without a value is listed with the reason, never filled in.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from . import data, identity

STALE_DAYS = 10          # last weekly bar stamped more than this many days before the snapshot date
MOVE_LIMIT = 0.25        # |weekly move| above this is listed for a manual check
MISMATCH_LIMIT = 0.10    # |snapshot last / last weekly close - 1| above this fails the cross-check
LONDON_VENUES = ("LSE", "LSEETF")   # quote the snapshot in pence and weekly closes in pounds


def _date(stamp: str) -> dt.date:
    return dt.date.fromisoformat(str(stamp)[:10])


def snapshot_date(directory: str | Path) -> dt.date:
    """The snapshot's date: the directory name (data/snapshots/<YYYY-MM-DD>), else the latest retrieval date."""
    directory = Path(directory)
    try:
        return dt.date.fromisoformat(directory.name)
    except ValueError:
        stamps = [json.loads(f.read_text(encoding="utf-8")).get("retrieved_at_utc") for f in data.snapshot_files(directory)]
        stamps = [s for s in stamps if s]
        if not stamps:
            raise ValueError(f"{directory}: no date in the directory name and no retrieved_at_utc") from None
        return max(_date(s) for s in stamps)


def _bars(row: dict) -> list[tuple[str, float]]:
    """(time stamp, close) pairs with a positive numeric close, in file order."""
    w = row.get("weekly") or {}
    out = []
    for t, c in zip(w.get("time") or [], w.get("close") or []):
        if isinstance(c, (int, float)) and not isinstance(c, bool) and c > 0:
            out.append((t, float(c)))
    return out


def price_check(last: float, close: float, london: bool) -> dict:
    """Snapshot last vs last weekly close. London lines may differ by the pence factor of 100.

    Returns ratio (last/close), the unit factor applied (100 or 1) and the gap after the factor."""
    ratio = last / close
    factor = 100 if london and abs(ratio / 100 - 1) <= MISMATCH_LIMIT else 1
    gap = ratio / factor - 1
    return {"ratio": ratio, "factor": factor, "gap": gap, "fails": abs(gap) > MISMATCH_LIMIT}


def _dividend_keys(divs: list) -> set:
    return {(d.get("ex_date"), d.get("amount"), d.get("currency"), d.get("type")) for d in divs or []}


def _fmt_div(key: tuple) -> dict:
    return dict(zip(("ex_date", "amount", "currency", "type"), key))


def _options(directory: Path, rows: dict) -> dict | None:
    path = directory / "options.json"
    if not path.exists():
        return None
    chains = json.loads(path.read_text(encoding="utf-8"))
    under, issues = [], []
    for u in chains.get("underlyings") or []:
        sym = u.get("symbol")
        n = sum(len(e.get("contracts") or []) for e in u.get("expiries") or [])
        under.append({"symbol": sym, "contracts": n, "expiries": [e.get("expiry") for e in u.get("expiries") or []]})
        row = rows.get(sym)
        if row is not None and u.get("underlying_contract_id") != row.get("contract_id"):
            issues.append({"symbol": sym, "issue": f"option underlying contract_id {u.get('underlying_contract_id')} "
                                                   f"differs from snapshot row {row.get('contract_id')}"})
        if n == 0:
            issues.append({"symbol": sym, "issue": "no option contracts collected"})
    return {"underlyings": under, "contracts": sum(u["contracts"] for u in under), "issues": issues,
            "retrieved_at_utc": chains.get("retrieved_at_utc")}


def review(current_dir: str | Path, previous_dir: str | Path | None, universe: dict) -> dict:
    """Quality review of `current_dir` (and changes since `previous_dir`, None in the first week).

    Returns a JSON-ready dict: coverage, missing, stale, moves, mismatches, contract_changes,
    dividend_changes (SG), options, identity, manifest and the thresholds used."""
    current = Path(current_dir)
    as_of = snapshot_date(current)
    instruments = universe["instruments"]
    rows: dict[str, dict] = {}
    for _, row in data.snapshot_rows(current):
        rows.setdefault(row.get("symbol"), row)
    prev_rows: dict[str, dict] = {}
    if previous_dir is not None:
        for _, row in data.snapshot_rows(previous_dir):
            prev_rows.setdefault(row.get("symbol"), row)

    missing, stale, moves, mismatches, not_compared, status_counts = [], [], [], [], [], {}
    with_prices = 0
    for inst in instruments:
        sym = inst["symbol"]
        row = rows.get(sym)
        if row is None:
            missing.append({"symbol": sym, "reason": "no row in any snapshot file"})
            continue
        status_counts[row.get("status") or "unknown"] = status_counts.get(row.get("status") or "unknown", 0) + 1
        bars = _bars(row)
        if not bars:
            reason = f"no positive weekly closes (status {row.get('status')})"
            if row.get("note"):
                reason += f": {row['note'][:160]}"
            missing.append({"symbol": sym, "reason": reason})
            continue
        with_prices += 1
        last_t, last_c = bars[-1]
        age = (as_of - _date(last_t)).days
        if age > STALE_DAYS:
            stale.append({"symbol": sym, "last_bar": _date(last_t).isoformat(), "age_days": age})
        if len(bars) >= 2:
            prev_t, prev_c = bars[-2]
            move = last_c / prev_c - 1
            if abs(move) > MOVE_LIMIT:
                moves.append({"symbol": sym, "from_bar": _date(prev_t).isoformat(), "from_close": prev_c,
                              "to_bar": _date(last_t).isoformat(), "to_close": last_c, "move": move})
        last = (row.get("snapshot") or {}).get("last")
        if isinstance(last, (int, float)) and not isinstance(last, bool) and last > 0:
            chk = price_check(float(last), last_c, row.get("exchange") in LONDON_VENUES)
            if chk["fails"]:
                mismatches.append({"symbol": sym, "snapshot_last": float(last), "weekly_close": last_c,
                                   "weekly_bar": _date(last_t).isoformat(), "ratio": chk["ratio"],
                                   "factor": chk["factor"], "gap": chk["gap"]})
        else:
            not_compared.append(sym)

    contract_changes, dividend_changes = None, None
    if previous_dir is not None:
        contract_changes = [{"symbol": s, "previous": prev_rows[s].get("contract_id"), "current": r.get("contract_id")}
                            for s, r in rows.items() if s in prev_rows
                            and prev_rows[s].get("contract_id") != r.get("contract_id")]
        dividend_changes = []
        for inst in instruments:
            sym = inst["symbol"]
            if inst.get("market") != "SG" or sym not in rows or sym not in prev_rows:
                continue
            if "dividends" not in rows[sym] or "dividends" not in prev_rows[sym]:
                continue    # not collected on one side (e.g. a history file): nothing to compare
            now, before = _dividend_keys(rows[sym].get("dividends")), _dividend_keys(prev_rows[sym].get("dividends"))
            if now != before:
                dividend_changes.append({"symbol": sym,
                                         "added": [_fmt_div(k) for k in sorted(now - before, key=str)],
                                         "removed": [_fmt_div(k) for k in sorted(before - now, key=str)]})

    configured = len(instruments)
    return {
        "as_of": as_of.isoformat(),
        "current": str(current),
        "previous": str(previous_dir) if previous_dir is not None else None,
        "configured": configured,
        "with_prices": with_prices,
        "coverage": with_prices / configured if configured else None,
        "status_counts": status_counts,
        "missing": missing,
        "stale": stale,
        "moves": moves,
        "mismatches": mismatches,
        "not_compared": not_compared,
        "contract_changes": contract_changes,
        "dividend_changes": dividend_changes,
        "options": _options(current, rows),
        "identity": identity.check(current, universe, previous_dir),
        "manifest": data.snapshot_integrity(current),
        "thresholds": {"stale_days": STALE_DAYS, "move": MOVE_LIMIT, "mismatch": MISMATCH_LIMIT},
    }


def _pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def to_markdown(rev: dict) -> str:
    """A short Improvement Log entry answering question 1 (accuracy) and 6 (coverage)."""
    m = rev["manifest"]
    lines = [f"#### Data quality {rev['as_of']}" + (f" (vs {Path(rev['previous']).name})" if rev["previous"] else
                                                   " (first week: no previous snapshot)"), ""]
    acc = []
    manifest = {"verified": f"manifest verified ({m['files']} files)", "no manifest": "no manifest (files not verified)",
                "failed": "manifest FAILED: " + "; ".join(m["problems"])}[m["status"]]
    acc.append(manifest)
    acc.append("identity: all rows on expected venue, currency and type" if not rev["identity"] else
               f"identity: {len(rev['identity'])} issue(s): "
               + "; ".join(f"{i['symbol']} {i['issue']}" for i in rev["identity"]))
    acc.append(f"snapshot vs weekly close: no gap over {rev['thresholds']['mismatch']:.0%} (London x100 for pence)"
               if not rev["mismatches"] else
               f"snapshot vs weekly close over {rev['thresholds']['mismatch']:.0%}: " + "; ".join(
                   f"{x['symbol']} {x['snapshot_last']:g} vs {x['weekly_close']:g} ({_pct(x['gap'])})"
                   for x in rev["mismatches"]))
    if rev["not_compared"]:
        acc.append(f"no snapshot last to compare: {', '.join(rev['not_compared'])}")
    acc.append(f"no weekly move over {rev['thresholds']['move']:.0%}" if not rev["moves"] else
               f"weekly moves over {rev['thresholds']['move']:.0%} (check before acting): " + "; ".join(
                   f"{x['symbol']} {x['from_close']:g} -> {x['to_close']:g} ({_pct(x['move'])}, {x['to_bar']})"
                   for x in rev["moves"]))
    if rev["contract_changes"] is not None:
        acc.append("contract ids unchanged" if not rev["contract_changes"] else "contract ids changed: " + "; ".join(
            f"{x['symbol']} {x['previous']} -> {x['current']}" for x in rev["contract_changes"]))
    if rev["dividend_changes"] is not None:
        acc.append("SG dividend records unchanged" if not rev["dividend_changes"] else
                   "SG dividend records changed: " + "; ".join(
                       f"{x['symbol']} +{len(x['added'])}/-{len(x['removed'])}"
                       + (" (" + ", ".join(f"{d['ex_date']} {d['amount']}" for d in x["added"]) + ")" if x["added"] else "")
                       + (" removed " + ", ".join(f"{d['ex_date']} {d['amount']}" for d in x["removed"]) if x["removed"] else "")
                       for x in rev["dividend_changes"]))
    o = rev["options"]
    if o is not None:
        acc.append(f"options: {len(o['underlyings'])} underlyings, {o['contracts']} contracts"
                   + ("" if not o["issues"] else "; " + "; ".join(f"{i['symbol']} {i['issue']}" for i in o["issues"])))
    lines.append("- **Accuracy:** " + acc[0] + ".")
    lines += [f"  - {a}" for a in acc[1:]]
    cov = rev["coverage"]
    lines.append(f"- **Coverage:** {rev['with_prices']}/{rev['configured']} instruments with prices"
                 + (f" ({cov:.1%})" if cov is not None else "") + ".")
    lines.append("  - missing: none" if not rev["missing"] else
                 "  - missing: " + "; ".join(f"{x['symbol']} ({x['reason']})" for x in rev["missing"]))
    lines.append(f"  - stale (last bar over {rev['thresholds']['stale_days']} days old): none" if not rev["stale"] else
                 f"  - stale (last bar over {rev['thresholds']['stale_days']} days old): " + "; ".join(
                     f"{x['symbol']} {x['last_bar']} ({x['age_days']} days)" for x in rev["stale"]))
    partial = rev["status_counts"].get("partial")
    if partial:
        lines.append(f"  - {partial} row(s) marked partial by the collector (see row notes)")
    return "\n".join(lines) + "\n"
