# CLAUDE.md — Moonshot Scanner rules

These are the owner's standing rules for this repo. Follow them in every session.
If a change would break one of them, stop and ask first.

## Security (most important)

- **This repo is PUBLIC.** Anyone on the internet can read every file and every commit.
- Never put an API key, password, token or private URL in any file, commit message,
  code comment, log line or test fixture — not even temporarily.
- Keys live only in **GitHub → Settings → Secrets and variables → Actions** and reach the
  code as environment variables (today: `BLOCKSCOUT_API_KEY`, read in `fetch/safety.py`, and
  `GDRIVE_SERVICE_ACCOUNT_JSON` + `GDRIVE_FOLDER_ID`, read in `scan/upload_drive.py`).
- If a new source needs a key, add it as an Actions secret and pass it in through `env:`
  in `.github/workflows/daily.yml`. Tell the owner the secret's name; never ask them to paste the value into chat.

## Schedule

- Scans run at **9:30am SGT** and **8:07pm SGT** every day
  (cron `30 1 * * *` and `7 12 * * *` in UTC, in `.github/workflows/daily.yml`).
- Do not change these times without being asked.

## Google Drive copy

- After **every** scan (9:30am, 8:07pm and manual "Run workflow" runs), the last step of
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

## Tracking

- **No per-token 2x sell target.** Do not add one.
- **Keep** the "reached 2x / 5x / 10x" counts in the track record and in the alerts.
- Keep the **Price tracking** tab.
- Keep the **Momentum** list, including **Robinhood Chain**.
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

## Working in this repo

- Explain changes to the owner in plain English — they are not a programmer.
- Do not change code unless the owner asks for it in that session.
- `snapshots/` and `latest/` are written by the bot. Do not hand-edit them.
- `site/page.html`: the page design (layout, labels, tiles, the code that draws the page) may be
  edited **only when the owner asks**. Never touch the data section between `/*DATA-START*/` and
  `/*DATA-END*/` (or `/*PATTERN-START*/` … `/*PATTERN-END*/`) — the bot replaces it every scan.
- The other files in `site/` (CSVs, `summary.json`, `nar_auto.json`, `pattern-nar.json`) are written
  by the bot. Do not hand-edit them.
