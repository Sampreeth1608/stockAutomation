# Gold Petal desk on the VM (preferred)

No Mac sync. The HTML station on **8501** is the operator desk (Arm live, restart, capital).
Streamlit is research only.

## Application lock (HTML desk on 8501)

The IAP tunnel only reaches `127.0.0.1`. The desk itself still needs a password — every
`/api/*` route (Arm live, restart, flatten, capital) is behind it.

```bash
# ~/goldpetal/.env  (same file the bot reads)
DESK_AUTH=true
DESK_PASSWORD=your-strong-password
# optional Authenticator OTP for Arm live / Start bot / Restart / DRY_RUN=false:
# DESK_TOTP_SECRET=...
```

Chrome http://127.0.0.1:8501/ shows **Unlock desk**. After login, POSTs send a CSRF
header. The desk refuses `--host 0.0.0.0` unless `DESK_BIND_PUBLIC=true` (do not).

Do **not** set `DESK_AUTH=false` except recovery. Instant unblock: `DESK_AUTH=false`
then `./scripts/run_desk_vm.sh --restart`.

Sheets COMMANDS still cannot Arm live or raise lots.

## One operator desk: 8787

**8787 is the only writer.** Streamlit 8501 is research (chart, trades, ticks) and
cannot overwrite start/stop, feed, live picks, DRY_RUN, or capital.

| Job | On 8787 |
|---|---|
| Emergency / trading on-off | Stop / go |
| Start / stop / restart bot | Engine + feed |
| Tick feed only (no strategies) | Engine + feed |
| Which books in RAM + live picks | Strategies → Save strategies |
| Paper vs live money, lot cap, unlock | Live money |
| Book ₹ / day-loss | Live money |

Keep `DRY_RUN=true` until you intend Angel fills. LIVE_MAX_LOTS hard cap is 1000 (type SIZE above 10).
Paper 100 lots is not live size. Restart the **bot** (type RESTART) after Save strategies / Save money.

## ML / S11 approvals (station 8501)

The **ML** tab on http://127.0.0.1:8501/ is the operator approval desk (what
Streamlit **Proposals** used to be), plus pack inspector extras:

- Pending cards: paper trades / win / gross / after-tax / Δ vs baseline, env patch, note
- Loaded pack vs proposed pack (model, family, AUC, thresholds)
- Packs on disk under `data/discover/packs` — **Load** writes the same paper env
- Last `weekly_discover.sh` report (`data/discover/latest_report.json`)
- Approve → paper writes `ENABLE_S11` + `S11_PACK_PATH` and **keeps DRY_RUN=true**
- Approve → live is blocked here (Unlock live stays on Live money)
- `safety_ok=false` needs **Accept risk**; S4/S12/S14/S15 cannot be turned on from this tab
- Type **RESTART** on Engine to load the pack into RAM (desk restart is not enough)

Phone layout `/lite` has a compact pending list. Open `/#ml` for the full inspector.

```bash
# after Approve → paper, .env looks like:
ENABLE_S11=true
S11_PACK_PATH=data/discover/packs/<pack>.json
DRY_RUN=true
```

Weekly job (Sunday 19:00 IST):

```bash
cd ~/goldpetal-repo/goldpetal
./weekly_discover.sh
```

Then on the Mac tunnel, Chrome **http://127.0.0.1:8501/** → **ML**.
Restart the desk after pull: `./scripts/run_desk_vm.sh --restart` (does not restart the bot).

## Gold Petal chart (Streamlit 8501 — this is the one that opens)

Do **not** use 8787 for the candle chart. Use the desk you already tunnel:

On the **VM**:

The desk that is actually open is the folder Streamlit prints as **Desk code** on the S14 chart tab (often `~/goldpetal-repo/goldpetal`, not `~/goldpetal`). Pull and restart **that** folder:

```bash
cd ~/goldpetal-repo/goldpetal   # or ~/goldpetal — match the path on the S14 tab
git pull origin cursor/s14-wick-length-a4b2
./scripts/run_desk_vm.sh --restart
./daily_s14_sheet.sh
```

On your **Mac** (leave running):

```bash
gcloud compute ssh sampreeth1608@sampreeth-love-story --zone=asia-south1-c -- -N -L 8501:127.0.0.1:8501
```

Then Chrome: **http://127.0.0.1:8501/** — first tab **S14 chart**. Pull live candles there.
8787 is emergency / live / capital only — not this chart. Do **not** restart supervise.

## Streamlit research desk

On the VM desk you can:

- **S14 chart** — Angel/MCX Gold Petal candlesticks (this is the exchange chart)
- **Overview / Trades / Ticks** — research
- **Proposals** — prefer Station **ML** tab on 8501 (`/#ml`). This tab still works if you are on the VM.
- **Deploy / Ops, Live Deploy, Capital** — read-only snapshots
- **Control** — stop only (emergency / pause / lock live)
- **Login** — `DESK_PASSWORD` gates **both** the HTML station on 8501 and Streamlit.
  Optional `DESK_TOTP_SECRET` for Arm live / restart / DRY_RUN=false.

```bash
# .env  (must live where the desk reads it — usually ~/goldpetal/.env)
DESK_AUTH=true
DESK_PASSWORD=your-strong-password
# optional OTP (python):
# python3 - <<'PY'
# import pyotp; s=pyotp.random_base32(); print(s); print(pyotp.totp.TOTP(s).provisioning_uri(name='goldpetal-desk', issuer_name='GoldPetal'))
# PY
# DESK_TOTP_SECRET=...
```

If login fails: the login screen shows `Secrets file:` and `password_len`. Type the password
manually. Confirm you are editing the same path shown on screen (or set `GP_ENV_PATH=~/goldpetal/.env`).
Instant unblock: `DESK_AUTH=false` then restart the desk. Do not leave it off.

Restart desk after pulling (use the folder shown as **Desk code** on the S14 tab):
```bash
cd ~/goldpetal-repo/goldpetal   # or ~/goldpetal
git pull origin cursor/s14-wick-length-a4b2
./scripts/run_desk_vm.sh --restart
```

## S4 daily HH/LL swing (off — not on the desk)

Angel + ticks daily backtest picked **S13**, not S4. S4 is off and **not on
the station book list**. Save strategies cannot turn it on.

```bash
ENABLE_S4=false
```

Overnight ML (`weekly_s4.sh` / `strategy_overnight.py`) is research-only.
The swing module stays in the repo for backtests only.

## S13 daily S16 (paper — the day-by-day book)

S16 close-vs-prev on the **day** candle vs the previous day. Confirm only in the
**last 15 minutes before MARKET_CLOSE** (never the next day's open).
Fill at last-15m LTP. Hold the trend until the opposite S16 signal.

Monthly contract (existing `ROLLOVER_DAYS=5` rule in `symbols.py`):
- Front month until 5 calendar days before expiry, then the feed is **next month**.
- S13 **closes** the front-month book on the last front session (do not hold through the switch).
- After the switch it trades the **next-month** contract as a new trend.

```bash
# in .env
ENABLE_S13=true
ENABLE_S4=false
S13_ENTRY_MINUTES_BEFORE_CLOSE=15
S13_MIN_WICK_GAP=0
ROLLOVER_DAYS=5
DRY_RUN=true
```

Restart supervise after pull. Look for `hold-until-opposite` and `contract=` on S13, and `DRY_RUN=true`.

## Restart / orphan / EOD safety (intraday)

After a bot restart, S5/S8/S12 used to forget RAM state while SQLite still showed OPEN.

Defaults now:
- `POSITION_ON_RESTART=restore` — reload open trades into RAM so exits can fire
- set `POSITION_ON_RESTART=close` to paper-CLOSE orphans on startup instead
- `EOD_FLATTEN_INTRADAY=true` — force-CLOSE intraday books in the last 5m before `MARKET_CLOSE`
- 8787 **Live money** shows DB vs RAM mismatches from `data/control/bot_health.json`. Streamlit Deploy / Ops is a read-only copy.

```bash
POSITION_ON_RESTART=restore
EOD_FLATTEN_INTRADAY=true
EOD_FLATTEN_MINUTES=5
```

## S12 / S14 / S15 — retired from paper

S12 30m HHHL, S14 wick, and S15 nowick are **not** loaded. Leave them off:

```bash
ENABLE_S12=false
ENABLE_S14=false
ENABLE_S15=false
```

Research helpers (`backtest_s14_tick.py`, `explain_s14_candles.py`) still exist for old tape.

## S16 1h HH/LL-or-wick (paper — intraday)

Wait for the **1h** candle to finish. Same closed bar vs previous close:

  C > prevC → HH/LL only (wicks ignored): HH+green LONG, LL+red SHORT
  C < prevC → wick only, gap 0: lower>upper LONG, upper>lower SHORT
  C = prevC → skip

FLIP at that bar's close. Fill at close. **Intraday only:** first fill is the
first finished 1h of the session; flatten at `MARKET_CLOSE` (and leftover at
next `MARKET_OPEN`). Never overnight. Keep `DRY_RUN=true`.

```bash
ENABLE_S16=true
S16_BAR_MINUTES=60
S16_MIN_WICK_GAP=0
```

Restart the **bot** after pull (desk restart is not enough). Look for
`S16_HHHL_WICK_1H` and heartbeat `s16=` in `data/strategy_run.log`.

Hist (VM `ticks.db`):

```bash
cd ~/goldpetal-repo/goldpetal
./venv/bin/python backtest_s16_hhhl_wick.py --db data/ticks.db --lots 100 --session --fees
```

## S14 / S15 30m wick (research only, not paper)

**S14** (nothing else):

```
upper = high − max(open, close)
lower = min(open, close) − low
lower > upper → LONG
upper > lower → SHORT
upper = lower → skip
```

Wait for the candle to **finish**, then on that **same** candle:
  open = high (high never left open) → SHORT
  open = low  (low never left open)  → LONG
  both (flat tape) → skip this check, use the wick
  else wick: lower > upper LONG, upper > lower SHORT (FLIP if already the other side)

No range skip, no bald body, no frac50/pin2. **S15** is still last-minute bald HOLD.
Keep `DRY_RUN=true`. Restart supervise after pull.

Tick backtest (needs the VM `ticks.db`):

```bash
cd ~/goldpetal
python3 backtest_s14_tick.py --db data/ticks.db --lots 100 --session --fees
```

Exchange candles (Angel MCX chart, formula + decision + close-fill PnL):

```bash
cd ~/goldpetal
./venv/bin/python explain_s14_candles.py --tf 30m,1h,1d --from 2026-08-02
# already dumped:
./venv/bin/python explain_s14_candles.py --from-csv data/backtests/s14_candles --tf 30m,1h,1d
# if venv is .venv:
./.venv/bin/python explain_s14_candles.py --tf 30m,1h,1d --from 2026-08-02
```

Prints O/H/L/C, upper, lower, O=H, O=L, rule, LONG/SHORT/skip, then the same
100-lot Angel-fee after-tax row as the tick tape. Fill is the **signal bar
close** (not the next open). Leftover flattened at the last **finished** close.
Writes CSV under `data/backtests/s14_candles/` **and** a reopenable sheet at
`data/s14_sheet/GoldPetal_S14.html` (also on 8787 → **Open full sheet** /
http://127.0.0.1:8787/s14-sheet). Copy a tab into Google Sheets from the panel.
Angel has 1m 3m 5m 10m 15m 30m 1h 1d (not 45m/2h/3h). `--from-ticks` uses
`ticks.db` instead of the exchange. `--no-fees` is gross only.

Default TFs: 1m, 3m, 5m, 10m, 15m, 30m, 45m, 1h, 2h, 3h, 1d, then a day-by-day table.
Writes `data/backtests/s14_tick/`. `S14_OPEN_HOLD_MINUTES=0` turns off open=high/low (wick only).

S14/S15 stay **off** in paper `.env` (`ENABLE_S14=false`, `ENABLE_S15=false`).
The backtest scripts above do not load the live runner.


## Day-by-day HH/LL on S4 horizon

S12 rules on **daily** candles (prev high/low vs today), plus an S4-style overnight
sim (enter day close → exit next open):

```bash
cd ~/goldpetal
python3 backtest_s4_hhhl_daily.py --db data/ticks.db --lots 1 --fees --min-range 5
python3 backtest_s4_hhhl_daily.py --db data/ticks.db --lots 100 --fees --min-range 5
```

Prints a day table (`prevH` / `prevL` / HH / LL / signal) and writes
`data/backtests/s4_hhhl_daily/`.

## Paper allowlist (stop S9 etc.)

On **8501 Live money**, Save ENABLE_* with only the slim books checked (S5/S8/S11/S13/S16). That writes `ENABLE_S4=false` and `ENABLE_S9=false` (and S1/S2/S3/S6/S10/S12/S14/S15) then Restart the bot.

Trades tab may still show **old** S9 history — filter to slim strategies.

## Live money (real orders) — 8787 only

1. 8787 **Live money**: check Live? on the strategy. Paper 100 lots is not live size.
2. 8787 **Capital**: set ₹ / max lots.
3. Unlock live on 8787.
4. Uncheck Paper only, type `LIVE`, Save .env (`DRY_RUN=false`, `LIVE_MAX_LOTS` 1–1000; type SIZE if above 10).
5. Type `RESTART` on 8787. Size = `min(strategy max_lots, LIVE_MAX_LOTS)`.

Keep `DRY_RUN=true` until you are ready for real orders. Do not also save these on Streamlit.

## One-time on VM

```bash
cd ~/goldpetal
git fetch origin && git checkout cursor/vm-desk-slim-strategies-8bfa
source venv/bin/activate
pip install -r requirements-analytics.txt   # streamlit, plotly if missing
chmod +x scripts/run_desk_vm.sh
```

Slim `.env` (only these strategies load into RAM):

```bash
ENABLE_S1=false
ENABLE_S2=false
ENABLE_S3=false
ENABLE_S4=false
ENABLE_S5=true
ENABLE_S6=false
ENABLE_S8=true
ENABLE_S9=false
ENABLE_S10=false
ENABLE_S11=true
ENABLE_S12=false
ENABLE_S13=true
ENABLE_S14=false
ENABLE_S15=false
ENABLE_S16=true
DRY_RUN=true
IGNORE_FEES=true
```

Restart the bot, then start the desk in tmux:

```bash
pkill -9 -f 'run_strategy|supervise' || true
nohup ./supervise.sh >> data/supervise.log 2>&1 &

git pull origin cursor/vm-desk-slim-strategies-8bfa
./scripts/run_desk_vm.sh
# detach without stopping: Ctrl+B then D
# later: tmux attach -t gp-desk
```

Open desk from your Mac (localhost only on VM — tunnel required):

```bash
# Mac Terminal — leave this open:
gcloud compute ssh sampreeth1608@sampreeth-love-story --zone=asia-south1-c -- -L 8501:localhost:8501
```

Then browse **http://localhost:8501** (not the external IP).

## Cancel Mac desk

You do **not** need Finder / `open_desk_mac.command` anymore.
