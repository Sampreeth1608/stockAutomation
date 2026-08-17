# Gold Petal desk on the VM (preferred)

No Mac sync. Streamlit reads `data/` on the trading VM.

## One operator desk: 8787

**All operator writes go on the 8787 control panel.** Streamlit (8501) is research
(trades, ticks, proposals view). Its Deploy / Ops, Live Deploy, and Capital tabs
are **read-only** so they cannot overwrite 8787.

| Job | Where |
|---|---|
| Emergency / trading / unlock live | **8787** Emergency & trading |
| ENABLE_*, DRY_RUN, LIVE_MAX_LOTS, Restart | **8787** Live money |
| live_approved | **8787** Live money checkboxes |
| Book ₹ / day-loss / per-strategy lots | **8787** Capital management |
| Streamlit | View only. May **stop** (emergency / pause / lock live), not start or unlock |

Keep `DRY_RUN=true` until you intend Angel fills. Panel LIVE_MAX_LOTS cap is 10.
Paper 100 lots on S12/S14/S15 is not live size.

Restart **only** `control_panel.py` after this pull (not supervise), unless you
meant to load a new `.env`.

## Streamlit research desk

On the VM desk you can:

- **Overview / Trades / Ticks** — research
- **Proposals** — Approve → paper (whitelist env). Restart on **8787**. Approve → live is on 8787.
- **Deploy / Ops, Live Deploy, Capital** — read-only snapshots
- **Control** — stop only (emergency / pause / lock live)
- **Login** — set `DESK_PASSWORD` (and optional `DESK_TOTP_SECRET` for dangerous actions)

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
manually (browser autofill often does not update Streamlit). Confirm you are editing the
same path shown on screen (or set `GP_ENV_PATH=~/goldpetal/.env`). Instant unblock:
`DESK_AUTH=false` then restart the desk.

Restart desk after pulling:
```bash
cd ~/goldpetal
git pull
tmux kill-session -t gp-desk 2>/dev/null || true
./scripts/run_desk_vm.sh
```

## S13 daily HH/LL same-candle (paper)

Same rule as S12 on the **day** candle: if today's high > yesterday's high, watch
the day; in the **last 15 minutes before MARKET_CLOSE**, if close > open → long.
Exit only on a later day that prints LH + red in **that day's last 15 minutes**
(not at the next open). If that exit day is also LL+red, re-enter short on the
same day (HH+green after a short exit → re-enter long). Short is the mirror
(LL + red close / HL + green close).

```bash
# in .env
ENABLE_S13=true
S13_MIN_RANGE=5
S13_ENTRY_MINUTES_BEFORE_CLOSE=15
S13_NO_FLIP=true
```

Restart supervise after pull. Look for `S13_HHHL_DAY` / `same-day` in `data/strategy_run.log`.

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

## S12 same-candle 30m HH/LL

During a 30m candle, if high > previous candle high, watch it; in that candle's
**last minute**, if close > open → long (within that 30m, not +another 30m).
Exit on a later 30m candle's last minute when high < prev high and close < open.
If that same exit candle is also a new LL+red (or HH+green), re-enter short/long
immediately. A CLOSE-only does not lock the last minute.
Never fill on the first tick of the next bar. Short mirror. S12 is **not**
EOD-flattened in the last 5m (that window overlaps `:29`).

```bash
ENABLE_S12=true
S12_BAR_MINUTES=30
S12_MIN_RANGE=5
S12_CONFIRM_MINUTES=1
```

## S14 / S15 30m wick HOLD (paper)

100-lot HOLD + fees tape (not 1-lot). **S14** is `30m:raw_strict` (43 trades,
+₹29,640) — the only real candidate. **S15** is `30m:nowick` only (other
nowick TFs were luck). Do **not** paper `3h:frac50` (6 trades, DD > pnl).

Same last-minute confirm as S12. HOLD: opposite → CLOSE, no reverse on that
candle. S14 exits only on a decisive opposite (frac50 / pin2 / bald body).
S15 ignores hammers; bald green/red only. Both skip EOD flatten (`:29`).
Keep `DRY_RUN=true`.

```bash
ENABLE_S14=true
S14_BAR_MINUTES=30
S14_MIN_RANGE=5
S14_CONFIRM_MINUTES=1
ENABLE_S15=true
S15_BAR_MINUTES=30
S15_MIN_RANGE=5
```

Restart supervise after pull. Look for `S14_WICK30_STRICT` / `S15_WICK30_NOWICK`
and heartbeat `s14=` / `s15=` in `data/strategy_run.log`.


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

On **8787 Live money**, Save ENABLE_* with only the slim books checked (S4/S5/S8/S11/S12/S13/S14/S15). That writes `ENABLE_S9=false` (and S1/S2/S3/S6/S10) then Restart supervise on 8787.

Trades tab may still show **old** S9 history — filter to slim strategies.

## Live money (real orders) — 8787 only

1. 8787 **Live money**: check Live? on the strategy. Paper 100 lots is not live size.
2. 8787 **Capital**: set ₹ / max lots.
3. Unlock live on 8787.
4. Uncheck Paper only, type `LIVE`, Save .env (`DRY_RUN=false`, `LIVE_MAX_LOTS` 1–10).
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
ENABLE_S4=true
ENABLE_S5=true
ENABLE_S6=false
ENABLE_S8=true
ENABLE_S9=false
ENABLE_S10=false
ENABLE_S11=true
ENABLE_S12=true
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
