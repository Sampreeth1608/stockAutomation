# AGENTS.md

## Cursor Cloud specific instructions

### Repository layout

This repo has two products:

| Product | Path | Runnable locally? |
|---------|------|-----------------|
| **Gold Petal** (MCX futures paper trading via Angel One) | `goldpetal/` | Yes — offline tests + utilities; live runner needs Angel credentials |
| **Google Sheets stock automation** | `appsScriptCode/`, `realmAppWebhooks/` | No — deploy to Google Apps Script + MongoDB Atlas Realm |

Cloud agents should focus on **Gold Petal** for local dev, lint, test, and run workflows.

### Gold Petal — quick start

```bash
cd goldpetal
cp .env.example .env   # fill ANGEL_* for live trading
pip install -r requirements.txt
```

All commands below assume `cd goldpetal`.

### Tests (no external services)

There is no pytest/unittest harness. Run each script directly:

```bash
for f in test_*.py; do python3 "$f"; done
```

Ten `test_*.py` files cover charges, strategies (S1–S6), ML features, regime/portfolio, overnight, and trade export.

### Live strategy runner

```bash
python3 run_strategy.py          # DRY_RUN=true by default (signals only)
./supervise.sh                   # auto-restart wrapper
python3 collect_ticks.py         # tick collection only (no strategies)
```

Requires valid `ANGEL_CLIENT_ID`, `ANGEL_PASSWORD`, `ANGEL_API_KEY`, and `ANGEL_TOTP_SECRET` in `.env`. Without them, `run_strategy.py` starts but fails at Angel login (invalid TOTP). Market hours: Mon–Fri 09:00–23:30 IST.

### Utilities (work on `data/ticks.db`)

| Script | Purpose |
|--------|---------|
| `read_ticks.py` | Inspect/export stored ticks and signals |
| `export_trades.py` | Export trade CSVs per strategy |
| `paper_report.py` | PnL summary with Angel MCX fees + tax |
| `retrain_daily.py` | Archive ticks + retrain ML models |
| `train_models.py` / `train_overnight.py` | Model training pipelines |
| `weekly_s8_nn.sh` / `evolve_s8_ml.py` | Weekend S8 improve → Sheets + control-panel proposal |
| `weekly_s4.sh` / `evolve_s4_ml.py` | Weekend S4 overnight ML improve → proposal |
| `weekly_s5.sh` / `evolve_s5_ml.py` | Weekend S5 minedge ML improve → proposal |
| `control_panel.py` | Web UI (optional; stop to save RAM): ticks, trades, capital, approvals |

### Control panel (local / VM)

**Preferred (safer):** SSH tunnel — no public firewall rule needed.

On the VM:
```bash
cd ~/goldpetal && source venv/bin/activate
pkill -f "control_panel.py" || true
nohup python3 control_panel.py --host 127.0.0.1 --port 8787 > data/control_panel.log 2>&1 &
```

On your laptop:
```bash
ssh -N -L 8787:127.0.0.1:8787 sampreeth1608@<VM_EXTERNAL_IP>
# open http://127.0.0.1:8787/
```

You can disable/delete the GCP firewall rule for TCP 8787 after switching.

**Optional (less safe):** bind `--host 0.0.0.0` and open TCP 8787 in VPC firewall (prefer your home IP `/32`, not `0.0.0.0/0`).

```bash
python3 control_panel.py --host 0.0.0.0 --port 8787
# open http://<vm-ip>:8787/
```

State files live under `data/control/`:
- `state.json` — emergency / trading / live unlock
- `capital.json` — total capital, per-strategy budgets, daily loss limit
- `proposals.json` — weekend new/improved strategies awaiting your approval

**Nothing auto-goes live.** Approve → paper or Approve → live in the panel; live still needs `live unlock` + `DRY_RUN=false`. Orders are placed by `live_orders.py` (default `LIVE_LOTS=1`, capped by `LIVE_MAX_LOTS`).

### Live Angel orders

| Gate | How |
|------|-----|
| Paper default | `DRY_RUN=true` → `NullBroker` (no placeOrder) |
| Arm runner | `DRY_RUN=false` in `.env`, restart `supervise.sh` |
| Unlock | Panel → **Unlock live** |
| Approve strategy | Panel → weekend proposal **Approve → live** (or `live_approved` in state) |
| Size | `LIVE_LOTS=1` (hard-capped by `LIVE_MAX_LOTS`) |

Log: `data/control/live_orders.jsonl` — also shown in panel **Live Angel orders**.

### Where the control panel lives

| Item | Location |
|------|----------|
| Process | Same trading VM as `run_strategy.py` |
| URL | `http://<vm-ip>:8787/` (`--host 0.0.0.0 --port 8787`) |
| Tick tape | Panel → **Tick tape** (from `data/ticks.db`) |
| 1m → day bars | Panel → **Bars** buttons (`1m`…`1h`…`4h`…`1d`) built on the fly from ticks |
| Entry/hold/exit reasoning | Panel → **Reasoning** (also `data/control/reasoning_latest.json`) |
| Capital / emergency | Same panel; state in `data/control/` |

### Reasoning model plan (entry / hold / exit)

1. **Live heads** (`s8_reasoner.py` + `reasoning_cockpit.py`): multi-step math → logic → science/ML → planning for ENTRY, HOLD, EXIT. Regime-aware; skips entries that look like quick losses.
2. **Weekly improve** (`./weekly_s8_nn.sh`): deep neural-net bake-off (`shallow` / `deep` / `deeper` MLPs) + reasoning heads from all ticks; writes a pending proposal to the panel.
3. **Enable on VM**: set `S8_REASONING=true` in `.env` (and `S8_LOGIC=align` for S8). Paper first.
4. **You approve** weekend proposals in the panel before paper enable / live unlock.

Deep learning note: we use multi-layer ReLU MLPs (Adam, early stopping) on tick-bar features — real neural nets sized for your data. Giant Transformer/LLM traders are not added until you have much more labeled history.

### One-click export (ticks + all trades)

In the control panel section **One-click export**:
1. Pick **From** / **To** dates (IST)
2. **Download all (ZIP)** → ticks + trades_all + per-strategy CSVs to your laptop
3. Or **Copy ticks/trades → Sheets** → paste into Google Sheets (Ctrl/Cmd+V)

No SSH or manual CSV building required.

### Google Sheets pack (richer trade review)

Sheets is better for reading PnL on phone/laptop. It does **not** replace the panel for emergency / live unlock / Approve.

**From the panel:** section **Google Sheets pack** → Download ZIP or Copy scoreboard.

**From the VM:**
```bash
cd ~/goldpetal && source venv/bin/activate
python3 sheets_pack.py
# → data/sheets_pack/goldpetal_sheets_*.zip
```

Import CSVs in Google Sheets: File → Import → Upload (start with `scoreboard.csv`, then `trades_all.csv` as a new sheet).

### Phone Google Sheet (read-only monitor)

A Google Sheet is a **dashboard** (LIVE quote, mood, books, blotter, lab, risk). Python still owns ticks, strategies, and orders. Do **not** stream every tick into Sheets — push a 15–60s snapshot. Rank `after_charges₹` (tax excluded). Keep `DRY_RUN=true`. There is no single “AI LONG 73%” brain.

Tabs: LIVE, MARKET, STRATEGIES, SIGNALS, TRADES, LAB, RISK, COMMANDS.

COMMANDS may set pause / emergency (type YES). Sheets **cannot** micro-live, Unlock live, Approve, or start/stop the bot. Those stay on desk `http://127.0.0.1:8501/`.

**Easy (snapshot):**
```bash
cd ~/goldpetal && python3 monitor_sheet.py
# → data/monitor_sheet/goldpetal_monitor_*.zip
```
Drive → New spreadsheet → File → Import → LIVE.csv then STRATEGIES.csv.

Or on the desk Downloads tab: **Download phone monitor**.

**Auto-refresh (one-time Google setup, then easy):**
1. Enable Google Sheets API, create a service-account JSON, copy it to the VM (not git).
2. Create a blank Sheet, share it with the service-account email as Editor.
3. In `.env`: `GOOGLE_SERVICE_ACCOUNT_JSON` and `GOOGLE_MONITOR_SHEET_ID`.
4. `pip install gspread google-auth` then `python3 monitor_sheet.py --upload`
5. Session: `python3 monitor_sheet.py --upload --every 30` (not 1 second) or `./push_monitor_sheet.sh`

Keep `DRY_RUN=true`. Do not upload `.env` or `ticks.db` to Drive.

### How to plan the week

| When | What |
|------|------|
| Mon–Fri session | `supervise.sh` collects ticks + paper signals; panel shows tape/bars/reasoning |
| After close | `paper_report.py` — check after-tax PnL |
| Sunday | `./weekly_s8_nn.sh` + `./weekly_s4.sh` + `./weekly_s5.sh` — improve models → proposals JSON |
| Sunday night | Review `data/control/proposals.json` or panel → Approve paper / Reject |
| Only after stable paper | Unlock live + your explicit go |

SQLite DB (`data/ticks.db`) is auto-created on first use. `data/` contents are gitignored.

### Linting

No linter or formatter is configured in this repo. Validation is via the `test_*.py` scripts.

### Google Sheets product

Paste `appsScriptCode/` into a Google Spreadsheet (Extensions → Apps Script). MongoDB Realm webhooks in `realmAppWebhooks/` must be deployed to Atlas App Services. Cannot be exercised end-to-end from this VM without Google/MongoDB credentials.
