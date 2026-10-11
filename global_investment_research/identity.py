"""Instrument identity checks: is each IBKR row the listing the universe means?

A ticker on the wrong venue (an OTC line, a US ADR instead of the home listing), the wrong security
type or currency, or a contract id that silently changes between weeks would feed the wrong prices
into every call. Adapted from the reference app's provider identity gate (symbol, quote currency,
instrument type), using the fields IBKR returns: exchange, currency, security_type, contract_id.

Every rule below is taken from what the 2026-10-11 snapshot (data/snapshots/2026-10-11) holds:
- US stocks and ETFs, and US-listed cross-asset ETFs (credit, commodities, rates, dollar):
  NYSE, NASDAQ, ARCA or BATS; USD; STK (e.g. SPY ARCA, QQQ NASDAQ, IGV BATS, JPM NYSE, TLT NASDAQ).
- UK: LSE for shares, LSEETF for the ISF ETF; GBP; STK.
- SG: SGX; SGD; STK.
- HK: SEHK; STK; currency not returned by search_contracts (null), which the row notes say is HKD.
- China A-shares: Stock Connect lines SEHKNTL (Shanghai) and SEHKSZSE (Shenzhen) or CHINEXT;
  STK; currency null (notes: CNY). China ETF/H-share lines (3188) trade on SEHK.
- FX pairs: IDEALPRO; CASH; the currency is the quote (second) currency of the pair.
- VIX: CBOE; IND; USD.
"""
from __future__ import annotations

from pathlib import Path

from . import data

US_VENUES = frozenset({"NYSE", "NASDAQ", "ARCA", "BATS"})
A_SHARE_VENUES = frozenset({"SEHKNTL", "SEHKSZSE", "CHINEXT"})

# (allowed exchanges, allowed currencies (None = not returned), allowed security type)
Rule = tuple[frozenset, frozenset, str]

_US = (US_VENUES, frozenset({"USD"}), "STK")
_SEHK = (frozenset({"SEHK"}), frozenset({None, "HKD"}), "STK")
RULES: dict[str, list[Rule]] = {
    "US": [_US],
    "UK": [(frozenset({"LSE", "LSEETF"}), frozenset({"GBP"}), "STK")],
    "SG": [(frozenset({"SGX"}), frozenset({"SGD"}), "STK")],
    "HK": [_SEHK],
    "China": [(A_SHARE_VENUES, frozenset({None, "CNY"}), "STK"), _SEHK],
    "Cross-asset": [_US],
}
SYMBOL_RULES: dict[str, list[Rule]] = {"VIX": [(frozenset({"CBOE"}), frozenset({"USD"}), "IND")]}


def expected_rules(inst: dict) -> list[Rule]:
    """Identity rules for one universe instrument; FX pairs quote in their second currency."""
    if inst["symbol"] in SYMBOL_RULES:
        return SYMBOL_RULES[inst["symbol"]]
    if inst.get("kind") == "fx":
        quote = inst["symbol"].split(".")[-1]
        return [(frozenset({"IDEALPRO"}), frozenset({quote}), "CASH")]
    return RULES.get(inst.get("market"), [])


def _row_issues(inst: dict, row: dict, group_file: str, groups: set) -> list[str]:
    issues = []
    group = Path(group_file).stem
    # Weekly snapshots name each file after a universe group; history sets may group differently.
    if group in groups and inst.get("group") and inst["group"] != group:
        issues.append(f"filed in {group_file}, universe group is {inst['group']}")
    cid = row.get("contract_id")
    if not isinstance(cid, int) or isinstance(cid, bool) or cid <= 0:
        issues.append(f"contract_id missing or not a positive integer ({cid!r})")
    rules = expected_rules(inst)
    if not rules:
        issues.append(f"no identity rule for market {inst.get('market')!r} kind {inst.get('kind')!r}")
        return issues
    exch, cur, sectype = row.get("exchange"), row.get("currency"), row.get("security_type")
    matched = [r for r in rules if exch in r[0]]
    if not matched:
        venues = sorted(v for r in rules for v in r[0])
        issues.append(f"exchange {exch} not an expected venue ({', '.join(venues)})")
        return issues
    venues, currencies, want_type = matched[0]
    if cur not in currencies:
        shown = ", ".join("not returned" if c is None else c for c in sorted(currencies, key=str))
        issues.append(f"currency {cur} on {exch}, expected {shown}")
    if sectype != want_type:
        issues.append(f"security_type {sectype}, expected {want_type}")
    return issues


def contract_ids(directory: str | Path) -> dict[str, int]:
    """symbol -> IBKR contract id in a snapshot or history directory (last row wins)."""
    return {row["symbol"]: row.get("contract_id") for _, row in data.snapshot_rows(directory)}


def check(snapshot_dir: str | Path, universe: dict, previous_dir: str | Path | None = None) -> list[dict]:
    """Identity problems as [{"symbol", "issue"}]; [] means every row is the expected listing.

    Flags rows not in the universe, duplicate rows, rows filed under another universe group's file, a contract id
    shared by two symbols, venue/currency/security-type breaches, and (with `previous_dir`) a
    contract id that differs from the previous snapshot. Instruments with no row are not identity
    problems; `quality.review` reports them as missing coverage."""
    by_symbol = {i["symbol"]: i for i in universe["instruments"]}
    groups = {i.get("group") for i in universe["instruments"]}
    out: list[dict] = []
    seen: dict[str, str] = {}
    owner: dict[int, str] = {}
    current: dict[str, object] = {}
    for group_file, row in data.snapshot_rows(snapshot_dir):
        sym = row.get("symbol")
        if sym in seen:
            out.append({"symbol": sym, "issue": f"duplicate row in {group_file} (first in {seen[sym]})"})
            continue
        seen[sym] = group_file
        if sym not in by_symbol:
            out.append({"symbol": sym, "issue": f"not in the universe ({group_file})"})
            continue
        out += [{"symbol": sym, "issue": i} for i in _row_issues(by_symbol[sym], row, group_file, groups)]
        cid = current[sym] = row.get("contract_id")
        if isinstance(cid, int):
            if cid in owner:
                out.append({"symbol": sym, "issue": f"contract_id {cid} also used by {owner[cid]}"})
            else:
                owner[cid] = sym
    if previous_dir is not None:
        before = contract_ids(previous_dir)
        for sym, cid in current.items():
            if sym in before and before[sym] != cid:
                out.append({"symbol": sym, "issue": f"contract_id changed: {before[sym]} -> {cid}"})
    return out
