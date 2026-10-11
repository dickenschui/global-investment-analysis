# Option-chain collection brief (read-only market data)

Repo: /home/user/global-investment-analysis. Output: data/snapshots/<YYYY-MM-DD>/options.json (write via a Python script in the scratchpad dir a scratch directory outside the repo, not in the repo).

Load tools with ToolSearch "+Interactive_Brokers" queries as needed (search_contracts, get_option_parameters, get_option_data, get_price_snapshot). READ-ONLY: never call order, alert, watchlist or account tools. IBKR limit ~30 requests/minute: pace and retry.

Underlyings (US listings, SMART): NVDA, GOOGL, AAPL, XOM, XLE, SPY, QQQ (bullish ideas) and ORCL, GLD, FXI, KWEB (bearish ideas).

For each underlying:
1. Resolve the contract and get the underlying last price (get_price_snapshot "last","prior_close").
2. Get option parameters; pick the standard MONTHLY expiry closest to 70 days out (about 10 weeks out) and also the one closest to 100 days (about 14 weeks out). If only one is available, use it.
3. For each chosen expiry, collect calls and puts at these strikes (nearest listed strikes): 90%, 95%, 100%, 105%, 110%, 115%, 120% of the underlying price. Bullish names need calls; bearish names need puts; collect BOTH rights if the tool returns them together, otherwise only the relevant right.
4. For each contract record exactly as returned: bid, ask, last, implied vol (option_midpoint_iv or iv field), delta, gamma, theta, vega, open interest, volume, contract_id. Missing -> null.

Schema:
{"source":"Interactive Brokers via MCP","retrieved_at_utc":ISO,"data_status_note":str,
 "underlyings":[{"symbol":str,"underlying_contract_id":int,"last":num,"prior_close":num|null,"note":str,
   "expiries":[{"expiry":"YYYY-MM-DD","contracts":[{"right":"C"|"P","strike":num,"contract_id":int|null,"bid":num|null,"ask":num|null,"last":num|null,"iv":num|null,"delta":num|null,"gamma":num|null,"theta":num|null,"vega":num|null,"open_interest":num|null,"volume":num|null}]}]}]}

Rules: copy numbers EXACTLY as returned; never estimate, compute or fill gaps. State in data_status_note whether quotes are frozen/delayed . Note IV units as returned (fraction vs percent). Validate the JSON with a check script (every underlying present, strikes numeric, bid<=ask where both present). Final message: compact table symbol / last / expiries / n contracts / IV range / notes. Do not git commit.
