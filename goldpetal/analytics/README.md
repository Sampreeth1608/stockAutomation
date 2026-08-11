# Gold Petal · Mac analytics

Read-only research desk on your **Mac**. The trading VM stays light (bot only).

## Why this (not Sheets / VM panel)

| Old | This |
|-----|------|
| Google Sheets row limits | Local SQLite snapshot |
| Control panel on VM (RAM) | Streamlit on Mac only |
| Slow SSH tunnel UI | Fast local browser |

## One-time setup (Mac)

```bash
# clone if needed
git clone https://github.com/Sampreeth1608/stockAutomation.git
cd stockAutomation/goldpetal
git checkout cursor/control-panel-capital-weekend-8bfa

python3 -m venv .venv-analytics
source .venv-analytics/bin/activate
pip install -r requirements.txt -r requirements-analytics.txt

chmod +x scripts/sync_analytics_mac.sh
```

Install [Google Cloud SDK](https://cloud.google.com/sdk/docs/install) and login once:

```bash
gcloud auth login
gcloud config set project sampreethlovestory
```

## Daily use

```bash
cd stockAutomation/goldpetal
source .venv-analytics/bin/activate

# pull snapshot from VM (ticks.db + proposals + discovery + log)
./scripts/sync_analytics_mac.sh

# light sync without the big DB
# ./scripts/sync_analytics_mac.sh --skip-db

streamlit run analytics/app.py
```

Browser opens at `http://localhost:8501`.

## What you get

- **PnL board** — gross / after-tax by strategy  
- **Trades** — filter by strategy / today  
- **Signals** — latest actions + reasons  
- **Discovery** — behavior report, candidates, proposals  
- **Log tail** — strategy_run.log snippet  

## Optional: DB Browser

For raw SQL on the synced file:

1. Install [DB Browser for SQLite](https://sqlitebrowser.org/)
2. Open `data/analytics_mac/ticks.db`

## Do not run this on the trading VM

Keep Streamlit off `sampreeth-love-story`. That box should only run `supervise` + `run_strategy`.
