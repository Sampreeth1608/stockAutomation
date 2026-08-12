# Gold Petal desk on the VM (preferred)

No Mac sync. Streamlit reads `data/` on the trading VM.

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

Streamlit **Live Deploy → Paper allowlist**:

1. Keep only S4 / S5 / S8 / S11 / S12 selected.
2. Click **Lock paper to selected only** → force-disables S1/S2/S3/S6/S9/S10.
3. On VM `.env` (required so they are not loaded at all):

```bash
ENABLE_S1=false
ENABLE_S2=false
ENABLE_S3=false
ENABLE_S6=false
ENABLE_S9=false
ENABLE_S10=false
ENABLE_S4=true
ENABLE_S5=true
ENABLE_S8=true
ENABLE_S11=true
ENABLE_S12=true
```

4. Restart supervise. Trades tab may still show **old** S9 history — filter to slim strategies.

## Live Deploy (real money)

Streamlit tab **Live Deploy**:

1. Check strategies (e.g. S4, S5, S12) and set each **Capital ₹** + **Max lots**.
2. Confirm + **Save live allocation** → writes `live_approved` + `capital.json`.
3. **Control** tab → Unlock live (two-step confirm).
4. On VM `.env`:
   ```bash
   DRY_RUN=false
   LIVE_MAX_LOTS=5   # hard ceiling; desk max_lots cannot exceed this
   ```
5. Restart supervise. Only approved strategies place Angel orders;
   size = `min(strategy max_lots, LIVE_MAX_LOTS)`.

Keep `DRY_RUN=true` until you are ready for real orders.

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
