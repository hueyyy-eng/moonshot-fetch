# moonshot-fetch

Daily data collector for Wai Keong's **Moonshot Scanner** dashboard. Runs on GitHub Actions at
**10:45 SGT** (02:45 UTC) with no laptop involved, pulls every external source the scan needs, and
commits the result to this repository. The cloud scan at 11:00 SGT reads `latest/scan.json` from
`raw.githubusercontent.com`, applies its own judgement (narrative tags, track-record advancement,
carry-forward decisions) and republishes the dashboard.

## What it fetches

| Stage | Source | Output |
|---|---|---|
| Chains | DefiLlama API + defillama.com embedded JSON | `DATA` rows: DEX vol/ratios, TVL, stablecoins, perps + OI, bridge gross flows, fees; `NETFLOW`, `BRIDGES`, `MAJORS` |
| Discovery | DexScreener screener pages **and** GeckoTerminal pools API (two independent routes, merged) | candidate pairs per chain |
| Pair rows | DexScreener pairs API | `TOKENS` numeric fields, band `m`/`l` from market cap |
| Candles + wallets | GeckoTerminal | `run`, `retr`, `vvp`, `nd`, `cs`, `ub/us/ubr/tpw`, `lpk` — with the three guards (wrong side, too young, negative retracement) |
| Safety | RugCheck (Solana), GoPlus + Honeypot.is (EVM), **Blockscout gateway** (Robinhood Chain explorer tier) | `sec`, `t10`, `hld`, taxes, mint/freeze/tb, `ver/own/cnm` |
| Re-price | DexScreener pairs API | `PRICES` for every pair seen in the last 60 days of snapshots + `seed_pairs.json` |

Every stage is best-effort. `latest/status.json` records `ok` / `partial` / `fail` per source with a
reason, and the scan uses that to decide what to carry forward from the previous page rather than
blanking fields — a partial snapshot with an honest status is always written; the job never exits
without one.

## Layout

```
.github/workflows/daily.yml   cron 02:45 UTC + manual "Run workflow" button
fetch/                        the job (python -m fetch.main <repo-root>)
seed_pairs.json               track-record pairs at setup time, so re-pricing has history from day one
snapshots/YYYY-MM-DD/         scan.json + status.json, one folder per SGT day (permanent archive)
latest/                       copy of the newest day — what the scan reads
tests/                        offline end-to-end test against a fake network (no internet needed)
```

## Setup (once)

1. Create this repository (**public** — nothing sensitive is committed; the key below lives only in
   Actions secrets, and a public repo lets the cloud scan read `latest/scan.json` without credentials).
2. Upload these files.
3. **Settings → Secrets and variables → Actions → New repository secret**
   name `BLOCKSCOUT_API_KEY`, value = the read-only Blockscout Cloud key. Without it the Robinhood
   explorer tier still runs, just rate-limited.
4. **Actions → Moonshot daily fetch → Run workflow** to produce the first snapshot, then open the run's
   "Show source status" step to see which sources answered from a GitHub runner.

## Reading the output

```
latest/scan.json
  generated_at_sgt, day_sgt
  status            {source: {state, note|error, counts...}}
  LAST_DAY          unix ts of the last settled DefiLlama daily bucket
  passing_chains    slugs clearing the chain gate (dr≥1.3 or dr30≥1.3, dv≥5)
  NO_DEXSCREENER    passing chains with no DexScreener id
  DATA              chain rows (dashboard CHAIN ROW shape)
  NETFLOW / BRIDGES / MAJORS
  TOKENS            token rows (dashboard TOKEN ROW shape, minus `nar`), each with `px` = DexScreener price
  PRICES            {"<chainId>:<pairAddress>": priceUsd}  (0 = DexScreener no longer knows the pair)
```

## Testing offline

```
pip install -r requirements.txt
python tests/run_offline.py               # happy path, asserts the output contract
python tests/run_offline.py --block-gt    # GeckoTerminal behind a bot wall
python tests/run_offline.py --block-ds    # DexScreener screener pages challenged
```

## Known risks (first live run will settle these)

- GeckoTerminal has served a Cloudflare Turnstile to datacenter traffic at least once. If it walls the
  runner, discovery still works through DexScreener and candle/wallet fields are absent with
  `status.geckoterminal = fail`; the scan then carries the previous day's candle fields forward.
- DexScreener's screener HTML may challenge a headless client. If so, discovery runs on GeckoTerminal
  alone (`status.discovery = partial`).
- `defillama.com` page JSON (perps OI, net flows, bridges) is a scrape of `__NEXT_DATA__`; if the page
  shape changes those three blocks report `fail` and the scan keeps the previous ones.
# moonshot-fetch
