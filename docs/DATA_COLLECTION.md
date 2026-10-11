# Data collection brief (read-only market data)

Repo: /home/user/global-investment-analysis. Universe: config/universe.json. You handle ONLY instruments whose "group" equals <group>.

Tools: load Interactive Brokers MCP tools via ToolSearch with query
"select:mcp__Interactive_Brokers_IBKR__search_contracts,mcp__Interactive_Brokers_IBKR__get_price_history,mcp__Interactive_Brokers_IBKR__get_price_snapshot".
READ-ONLY: never call order, alert, watchlist or account tools.

For each instrument:
1. search_contracts to resolve the contract. Pick the PRIMARY listing that matches the config (US -> NASDAQ/NYSE/ARCA; UK -> LSE, currency GBP; SG -> SGX; HK -> SEHK; China A-shares -> SEHKNTL/SEHKSZSE (Stock Connect) or SSE/SZSE; FX -> security_type CASH, exchange IDEALPRO, e.g. symbol "GBP" with currency USD; VIX -> IND on CBOE). If nothing credible matches, record status "unavailable" with a note. Never substitute a different company.
2. get_price_history: period "ONE_YEAR", step "ONE_WEEK", outside_rth false, correct security_type, exchange = the contract's native exchange for non-US (omit for US stocks/ETFs). For groups "uk" and "sg" set include_corporate_actions true.
3. get_price_snapshot with market_data_names ["last","prior_close","change","dividend_yield","historical_vol","misc_statistics","top_status"] (pass native exchange for non-US).

Output: ONE file data/snapshots/<YYYY-MM-DD>/<group>.json, written by a Python script you create in the scratchpad (not in the repo), with this schema:
{"group": str, "source": "Interactive Brokers via MCP", "retrieved_at_utc": ISO time,
 "instruments": [{
   "symbol": config symbol, "status": "ok" | "partial" | "unavailable", "note": str,
   "contract_id": int|null, "exchange": str|null, "currency": str|null, "security_type": str|null, "ibkr_description": str|null,
   "snapshot": {"last": num|null, "prior_close": num|null, "change_pct": num|null, "dividend_yield_pct": num|null,
                "historical_vol_30d": num|null, "high_52w": num|null, "low_52w": num|null, "data_status": str|null},
   "weekly": {"time": [ISO strings exactly as returned], "close": [numbers exactly as returned]},
   "dividends": [{"ex_date": str, "amount": num, "currency": str|null, "type": str|null, "raw": str}]   # uk/sg only, verbatim from corporate actions; [] if none returned
 }]}

Rules (audit-grade):
- Copy numbers EXACTLY as the tool returned them. Do not round, estimate, interpolate, or compute anything. Missing -> null.
- Keep time and close arrays the same length and in returned order.
- After writing, run a check script: valid JSON, every config symbol of your group present exactly once, len(time)==len(close) for each, closes all > 0.
- Final message: a short table of symbol / status / contract_id / exchange / currency / n_weekly_bars, plus any notes. Do not git commit.
