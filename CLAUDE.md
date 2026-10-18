# CLAUDE.md — Moonshot Scanner rules

These are the owner's standing rules for this repo. Follow them in every session.
If a change would break one of them, stop and ask first.

## Security (most important)

- **This repo is PUBLIC.** Anyone on the internet can read every file and every commit.
- Never put an API key, password, token or private URL in any file, commit message,
  code comment, log line or test fixture — not even temporarily.
- Keys live only in **GitHub → Settings → Secrets and variables → Actions** and reach the
  code as environment variables (today: `BLOCKSCOUT_API_KEY`, read in `fetch/safety.py`).
- If a new source needs a key, add it as an Actions secret and pass it in through `env:`
  in `.github/workflows/daily.yml`. Tell the owner the secret's name; never ask them to paste the value into chat.

## Schedule

- Scans run at **9:30am SGT** and **8:07pm SGT** every day
  (cron `30 1 * * *` and `7 12 * * *` in UTC, in `.github/workflows/daily.yml`).
- Do not change these times without being asked.

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

## Tracking

- **No per-token 2x sell target.** Do not add one.
- **Keep** the "reached 2x / 5x / 10x" counts in the track record and in the alerts.
- Keep the **Price tracking** tab.
- Keep the **Momentum** list, including **Robinhood Chain**.
- Track record tiles show **only these five** counts: **2x, 5x, 10x, Down 50%, Down 100%**.

## To-do: where the code does not match these rules yet

Recorded 18 Oct 2026. Not fixed yet — only change these when the owner asks.

- [ ] **Pot percentages are missing.** The Playbook in `site/page.html` shows tiers A/B/C
      but no allocation %. Add pot % that sums to 100, biggest share to A.
- [ ] **Pot C shows in every mode.** It is labelled "Easy mode only" but still appears in
      Mixed and Hard mode. It should be empty / 0% unless the verdict is Easy mode.
- [x] **Gaming tagged as Utilities.** `scan/auto_nar.py` maps "gaming" and "gamefi" to
      "Utilities". Give them a new "Gaming" label instead. *(Done 18 Oct 2026.)*
- [x] **Sixth track record tile.** The page has an extra "In between −49% to +99%" tile.
      Remove it so only the five tiles remain. *(Done 18 Oct 2026.)*
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
