# CLAUDE.md — Moonshot Scanner rules

These are the owner's standing rules for this repo. Follow them in every session.
If a change would break one of them, stop and ask first.

## Security (most important)

- **This repo is PUBLIC.** Anyone on the internet can read every file and every commit.
- Never put an API key, password, token or private URL in any file, commit message,
  code comment, log line or test fixture — not even temporarily.
- Keys live only in **GitHub → Settings → Secrets and variables → Actions** and reach the
  code as environment variables (today: `BLOCKSCOUT_API_KEY`, read in `fetch/safety.py`,
  `GDRIVE_SERVICE_ACCOUNT_JSON` + `GDRIVE_FOLDER_ID`, read in `scan/upload_drive.py`, and
  `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`, read in `scan/telegram.py`).
- If a new source needs a key, add it as an Actions secret and pass it in through `env:`
  in `.github/workflows/daily.yml`. Tell the owner the secret's name; never ask them to paste the value into chat.

## Schedule

- Two scans a day, a **morning** and an **evening** scan. Since 1 Nov 2026 (owner's request) they are
  scheduled at **2:53am SGT** and **12:37pm SGT** (cron `53 18 * * *` and `37 4 * * *` in UTC, in
  `.github/workflows/daily.yml`), because GitHub starts scheduled runs 5-6 hours late and a scan takes
  1.5-2 hours. Aim: morning page ready **~10-10:30am**, evening page **~8pm**.
- The morning scan **never fetches before 8:15am SGT** (00:15 UTC): CoinGecko/GeckoTerminal and DefiLlama close
  their daily figures at 8am SGT, and the scanner only uses closed days. If GitHub starts it early, the `wait` job
  in the workflow waits until then (owner's choice, 2 Nov 2026). Keep this.
- Each run knows which scan it is from its cron line (`SCAN_SLOT` → `SLOT` in `latest/scan.json`, `slot` in
  `site/summary.json`): morning → `snapshots/<day>/`, evening → `snapshots/<day>-pm/`. Manual runs: before 3pm SGT = morning.
  If a cron line changes, change the matching `SCAN_SLOT` line in the workflow too.
- Do not change these times without being asked.

## Quick Solana check + Telegram alerts (owner, 9 Nov 2026)

- `.github/workflows/quick.yml` runs `fetch/quick.py` every 2 hours from 7:23am to 9:23pm SGT (GitHub may start it late).
  Solana only, same code as the daily scan, then the dashboard's own rules from `scan/build_block.py`.
- It sends a Telegram message (`scan/telegram.py`) for each token that would be on **Igniting, Runners or Momentum**
  AND scores **60+**, is not already on the dashboard's lists and was not alerted in the last 3 days (remembered in
  `.alerts/sent.json` through the workflow cache, never committed). At most 5 alerts per check.
- **Quiet 11pm–7am SGT:** a run that lands in those hours sends nothing. Never put a sell target in an alert
  (no per-token 2x target, see Tracking). The quick check never changes the dashboard.
- The token is never printed: error messages are cleaned of it before they reach the public log.

## Google Drive copy

- After **every** scan (morning, evening and manual "Run workflow" runs), the last step of
  `.github/workflows/daily.yml` runs `scan/upload_drive.py`. It copies these six files from `site/` to the
  owner's Drive folder **"Claude Projects/Moonshot Scanner"**, overwriting the files already there (same names):
  `track-record.csv`, `price-tracking.csv`, `daily-lists.csv`, `near-miss-log.csv`, `narrative-log.csv`, `holder-log.csv`.
- It signs in as a Google **service account** (a robot login) that has Editor access to that one folder only.
  Secrets: `GDRIVE_SERVICE_ACCOUNT_JSON` (the key file) and `GDRIVE_FOLDER_ID`. Never write the folder ID,
  file IDs or Drive links into the repo or print them in the logs.
- The robot can only **replace** files, not create them (Google's rule for personal Gmail Drives).
  If a file is deleted from Drive, the owner uploads one copy by hand once.
- If any file is not copied, the step **fails the run (red)** so the owner gets GitHub's failure email.
  The page is already published by then. Keep it that way.
- The owner's Claude scheduled task also uploads these files today. The owner will remove that step
  themselves after a few days. Do not edit that scheduled task.

## Playbook: three pots

| Pot | Name | Who goes in it |
|---|---|---|
| A | **Leader of the Leaders** | Survived a drawdown and still trades. Safest. |
| B | **Emerging Leaders** | Past the launch window, not yet proven through a cycle. |
| C | **Itch Hand Early Plays** | Very new, no history, or already ran. **Easy mode only.** |

- Pot percentages always **add up to 100%**.
- The **biggest share goes to the safest pot** (A ≥ B ≥ C). No fixed numbers — this rule only.
- Pot C is used **only in Easy mode**. In Mixed and Hard mode, C gets 0%.
- Every token shows **one line beside it saying why it is in its pot**.
- Tokens are listed **per chain AND grouped by narrative** (both views must exist).

## Discovery

- Keep the **separate higher-cap discovery pass** (the "leader" band, market cap ≥ $2m)
  that feeds pot A. Do not merge it into the $500k–$2m moonshot pass or remove it.
- Tokens are searched on: the 6 **core** chains (Solana, Base, BSC, Hyperliquid, Robinhood Chain, **Elysium**), every
  **hot** chain, and the **3 chains with the biggest 7-day net bridge inflow** that are not core
  (`top_inflow_chains` in `fetch/main.py`, picked fresh every scan). A chain with no token data source
  (e.g. Arc) is skipped and the next one taken. These show a **"top inflow"** tag on the page.
- **Elysium** (added 8 Nov 2026, owner's request) is the new HYPE-paired chain GeckoTerminal and DexScreener call
  `elysium` — not Vulcan Forged's older "Elysium" (PYR), which is what DefiLlama tracks under that name. So it is
  token-search only (`TOKEN_ONLY_CORE` in `fetch/chains.py`): no DefiLlama chain row, never "hot" or "top inflow".
- **No rug check on Elysium:** GoPlus and Honeypot.is do not cover it yet. By the owner's choice its tokens may still
  reach Shortlist, Pullback, Igniting and Momentum; with no rug data the safety gate counts as passed, and every such
  token shows a red **"no rug check"** tag and a red "not checked" safety pill (`UNCHECKED` in `scan/build_block.py`,
  mirrored in `site/page.html`). Keep the tag whenever this exception applies. If a rug service adds Elysium, wire it
  into `fetch/safety.py` and the exception stops applying by itself (it only covers tokens with no rug data).

## Lists, score and tags (owner's review of the back-tests, 8 Nov 2026)

- **Five lists:** Shortlist, Pullback, Igniting, Momentum and **» Runners** (experimental): already ran 3×+ but under
  3 days old, buyer share 70–90%, trading 10×+ its liquidity, usual liquidity / mcap / wash / honeypot checks, unknown
  rug data allowed (as Momentum). A token on another list stays there. Runners are logged (`RUNHIST`, list code `U`)
  to Price tracking, the CSVs and the pattern study like the other lists. Keep it until a review decides otherwise.
- **Score (0–100)** on every token, lists sorted highest first: buyer share 70–90% 25, age 1–7 days 20, 24h move
  +100–300% 15, Solana/BSC/Ethereum 15, mcap $1–2m 10, turnover 10×+ 10, has X/site 5. Same rules in
  `scan/build_block.py` (`score`) and `site/page.html` (`score`); the points table and back-test results are shown
  under "How the Score works" in the How to use tab. Change the points only after a review, in both places.
- **"Has an X or a site" is a warning, not a gate** (`SOFT_GATES`): it is still shown, with a grey "no X / site" tag,
  but never keeps a token off a list.
- **"tracks an asset"** (grey tag): price stayed within ~15% over 7 days (`run` ≤ 1.15 and `retr` ≤ 13). Never removed;
  always sorted to the bottom of every list and of the token table. Decided by price behaviour, not by type label
  (type labels wrongly caught PSTR, FLOAT and HYDX, which pumped).
- **Robinhood Chain:** no extra hard bar (back-test: it would have dropped 28 of its 30 winners). The score ranks
  weak Robinhood tokens lower instead.
- **Bi-weekly review:** the owner reviews the dashboard every other Sunday (summary around 11am SGT). Rule changes
  only after the owner approves.
- **Review tab** (`#review`): every back-test from the 8 Nov 2026 review, recalculated from all snapshots at every scan
  by `scan/review_study.py` (run after the pattern study in the workflow; injected between `/*REVIEW-START*/` and
  `/*REVIEW-END*/`). It also writes `site/review.json` (read by the review task) and, on the first scan on or after
  each review Sunday (every 14 days from 8 Nov 2026), appends the headline numbers to `site/review-history.json`,
  shown in brackets on the tab. When a rule changes after a review, update the matching test there too.
- **Days to 2× / 5× / 10×** columns in Track record, near-misses and Price tracking come from the same study.

## Narrative labels

- **"Utilities"** = tools, DeFi, trading infrastructure, data, and general infrastructure. Not launchpads.
- **"Launchpad / infra"** = launchpads only.
- **"Gaming"** = gaming / GameFi tokens get their own label, not Utilities.
  Detected from GeckoTerminal's category tags only — no keyword matching on names or descriptions.
- Every token shows its **type tag** (dashed chip) beside its chain, in **every** section that lists tokens
  (Shortlist, Pullback, Igniting, Momentum, token list, age cards, Narrative, Playbook, Track record,
  near-misses, Price tracking). No type → the tag says **"Unclassified"**.
- A token that plainly fits two types shows **both tags** (e.g. PUMP: Launchpad / infra + Classic memes).
  The first tag is the one used for grouping (Playbook "by narrative") and for the narrative counts.
- Well-known projects whose name misleads are fixed in `KNOWN_PROJECTS` in `scan/auto_nar.py`; those
  labels replace any older label on the next scan.
- **Where labels come from** (`scan/auto_nar.py`, each step only if the one before found nothing — owner's order,
  8 Nov 2026): 1. ticker / name / website address; 2. the creator's launch description (Solana: the token's own
  on-chain metadata link, read through IPFS gateways that answer GitHub — `IPFS_GATEWAYS`); 3. the DexScreener
  profile description; 4. GeckoTerminal profile + categories and the token's website title. Vague text stays
  "Unclassified" — never guess. EVM launchpads (four.meme, Clanker) block GitHub or need a paid key, so they are
  not used.

## Tracking

- **No per-token 2x sell target.** Do not add one.
- **Keep** the "reached 2x / 5x / 10x" counts in the track record and in the alerts.
- Keep the **Price tracking** tab.
- Keep the **Momentum** list, including **Robinhood Chain** (chains: Solana, BSC, Robinhood Chain, Elysium).
- Track record and Price tracking show these six outcome tiles: **2x, 5x, 10x, Down 50%, Down 100%**
  and **"In between −49% to +99%"**. Keep all six.

## To-do: where the code does not match these rules yet

Recorded 18 Oct 2026. Not fixed yet — only change these when the owner asks.

- [ ] **Pot percentages are missing.** The Playbook in `site/page.html` shows tiers A/B/C
      but no allocation %. Add pot % that sums to 100, biggest share to A.
- [ ] **Pot C shows in every mode.** It is labelled "Easy mode only" but still appears in
      Mixed and Hard mode. It should be empty / 0% unless the verdict is Easy mode.
- [x] **Gaming tagged as Utilities.** `scan/auto_nar.py` maps "gaming" and "gamefi" to
      "Utilities". Give them a new "Gaming" label instead. *(Done 18 Oct 2026.)*
- [x] **Out-of-date schedule notes.** `README.md` says 10:45 SGT and a comment in
      `fetch/main.py` says 21:30 SGT. The real times are 9:30am and 8:07pm (the workflow file is correct). *(Done 18 Oct 2026.)*
- [ ] **Retag existing gaming tokens.** Tokens tagged "Utilities" before 18 Oct 2026 keep that
      label, because the page builder reuses yesterday's narrative. Retag the gaming ones to "Gaming".
- [x] **Offline test fails after 3pm SGT.** `tests/run_offline.py` looks for `snapshots/<day>/`,
      but runs after 3pm SGT save to `snapshots/<day>-pm/`. Make the test look in the right folder. *(Done 18 Oct 2026.)*

## Explanations on the page

- Keep the **"How to use" tab** (`#howto` in `site/page.html`): what the page is for, a daily checklist,
  the **live scoreboard** (worked out in the browser from the track-record data, so it updates every scan)
  and one card per section.
- Every section title and table column heading shows a **hover explanation** (what it is + how to use it),
  from `SECTION_TIPS` and `COL_TIPS` in `site/page.html`. When a section or column is added or renamed,
  add or update its explanation there in plain English.

## Working in this repo

- Explain changes to the owner in plain English — they are not a programmer.
- Do not change code unless the owner asks for it in that session.
- `snapshots/` and `latest/` are written by the bot. Do not hand-edit them.
- `site/page.html`: the page design (layout, labels, tiles, the code that draws the page) may be
  edited **only when the owner asks**. Never touch the data section between `/*DATA-START*/` and
  `/*DATA-END*/` (or `/*PATTERN-START*/` … `/*PATTERN-END*/`, `/*REVIEW-START*/` … `/*REVIEW-END*/`) — the bot replaces it every scan.
- The other files in `site/` (CSVs, `summary.json`, `nar_auto.json`, `pattern-nar.json`, `review.json`,
  `review-history.json`) are written by the bot. Do not hand-edit them.
