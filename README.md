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
| Discovery | GeckoTerminal pools API (8 volume pages + 4 trending windows per chain) **and** DexScreener's public boosted/profiled-token API; the DexScreener screener HTML is tried too but Cloudflare refuses it from GitHub runners | candidate pairs per chain |
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
scan/                         the cloud scan's deterministic half (see below) — fetched by the 11:00 task each day
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

## The cloud scan (11:00 SGT scheduled task, no laptop)

The scheduled task downloads `latest/scan.json`, `latest/status.json` and the two scripts below from
`raw.githubusercontent.com`, reads the live dashboard with its Artifact tool, and runs:

```
python scan/build_block.py --page page.html --scan scan.json --status status.json --out out/ [--nar nar.json]
python scan/verify_page.py out/page_new.html out/summary.json
```

`build_block.py` is the whole rulebook of the old browser-based prompt, made deterministic: carry-forward
per source status (`fail`/`partial` → previous block's fields kept and `CAND_AT` not advanced), the nine
gates + pullback + igniting evaluated exactly as the page's own JS does (verified field-for-field against
the page on 20 Sep), and the four logs advanced (HISTORY, REJECTS control group, NARHIST, HOLDHIST). It
exits 3 with `out/nar_todo.json` when new tokens need a narrative tag — the one judgement call left to the
model — then writes `page_new.html`, `track-record.csv` and `summary.json` (lists, diffs vs yesterday,
stale parts, track-record stats) on the next run. `verify_page.py` renders the result headless and checks
what the prompt always checked by hand: no script errors, no horizontal overflow, header/row cell counts,
the three lists match the builder, the Playbook renders in both groupings, the NetFlow tab has bars and
rows. Only then does the task publish.

## Testing offline

```
pip install -r requirements.txt
python tests/run_offline.py               # happy path, asserts the output contract
python tests/run_offline.py --block-gt    # GeckoTerminal behind a bot wall
python tests/run_offline.py --block-ds    # DexScreener screener pages challenged
```

## What the first live runs settled (19–20 Sep)

- DexScreener's **screener HTML pages** return 403 to GitHub runners (Cloudflare bot management) — every
  chain, every pass. The **API** (`api.dexscreener.com`) works fine, so pair rows, prices, the boosted /
  profiled token lists and re-pricing are unaffected. Discovery therefore rides on GeckoTerminal plus the
  DexScreener promoted-token route, and `status.discovery` reads `partial` by design.
- GeckoTerminal is **not** Turnstile-walled from the runners, but the free tier's per-IP bucket is shared
  with every other job on the same egress IP, so it 429s hard. The job paces at 12 calls/min, backs off
  20→35→50→65s (honouring `Retry-After`), enriches the moonshot band before the leaders, and never falls
  back to per-pool calls except on the two chains where the multi endpoint is unsupported.
- `defillama.com` page JSON (perps OI, net flows, bridges) is a scrape of `__NEXT_DATA__`; if the page
  shape changes those blocks report `fail` and the scan keeps the previous ones.
