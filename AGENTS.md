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
| `control_panel.py` | Web UI: ticks, trades, capital, emergency, weekend approvals |

### Control panel (local / VM)

```bash
python3 control_panel.py --host 0.0.0.0 --port 8787
# open http://<host>:8787/
```

State files live under `data/control/`:
- `state.json` — emergency / trading / live unlock
- `capital.json` — total capital, per-strategy budgets, daily loss limit
- `proposals.json` — weekend new/improved strategies awaiting your approval

**Nothing auto-goes live.** Approve → paper or Approve → live in the panel; live still needs `live unlock` + `DRY_RUN=false` + live order module.

SQLite DB (`data/ticks.db`) is auto-created on first use. `data/` contents are gitignored.

### Linting

No linter or formatter is configured in this repo. Validation is via the `test_*.py` scripts.

### Google Sheets product

Paste `appsScriptCode/` into a Google Spreadsheet (Extensions → Apps Script). MongoDB Realm webhooks in `realmAppWebhooks/` must be deployed to Atlas App Services. Cannot be exercised end-to-end from this VM without Google/MongoDB credentials.
