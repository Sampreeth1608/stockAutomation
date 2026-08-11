# Gold Petal · Mac desk (replaces VM control panel for daily use)

Full panel features on your **Mac** — proposals/ML approvals, capital, emergency,
trades, ticks, discovery — while the trading VM stays light.

## Architecture

```
Mac Streamlit desk  --gcloud ssh/scp-->  GCP VM (bot + SQLite + proposals)
        ▲                                      │
        └──────── sync snapshot ←──────────────┘
```

- **Reads:** local snapshot (`data/analytics_mac/`)
- **Writes (approve / emergency / capital):** applied on the VM via `gcloud compute ssh`

## Setup (Mac) — run one block at a time

```bash
cd ~
git clone https://github.com/Sampreeth1608/stockAutomation.git
cd ~/stockAutomation/goldpetal
git fetch origin
git checkout cursor/control-panel-capital-weekend-8bfa
git pull origin cursor/control-panel-capital-weekend-8bfa

python3 -m venv .venv-analytics
source .venv-analytics/bin/activate
pip install -r requirements.txt -r requirements-analytics.txt
chmod +x scripts/sync_analytics_mac.sh
```

```bash
gcloud auth login
gcloud config set project sampreethlovestory
```

Confirm you can read VM data **as** `sampreeth1608` (Mac login `sampreeth` alone gets Permission denied):

```bash
gcloud compute ssh sampreeth1608@sampreeth-love-story --zone=asia-south1-c \
  --command 'ls -la /home/sampreeth1608/goldpetal/data | head'
```

## Daily

```bash
cd ~/stockAutomation/goldpetal
source .venv-analytics/bin/activate
git pull origin cursor/control-panel-capital-weekend-8bfa
./scripts/sync_analytics_mac.sh
streamlit run analytics/app.py
```

You should see `✓` lines (proposals, ticks.db, …), not `· missing …`.

Or use the in-app **Sync from VM** button.

Opens `http://localhost:8501`.

If sync still shows Permission denied: `export REMOTE_USER=sampreeth1608` then re-run sync. Debug with `GP_SYNC_DEBUG=1 ./scripts/sync_analytics_mac.sh --skip-db`.

## Tabs (old panel → new desk)

| Old panel | Mac desk tab |
|-----------|----------------|
| Scoreboard / trades | Overview, Trades |
| Ticks / signals | Signals / Ticks |
| Weekend proposals | Proposals / ML |
| Emergency / trading / live | Control |
| Capital | Capital |
| Reasoning | Reasoning |
| Live orders | Live orders |
| Weekly ML / discover | Models + Proposals |

## Approvals

1. Sync  
2. **Proposals / ML** → Approve → paper / Reject  
3. Decision runs on the VM  
4. If `env_patch` is shown, paste into VM `.env` and restart `supervise`  
5. Do **not** Approve → live unless you intend to unlock live later  

## Optional: DB Browser

Open `data/analytics_mac/ticks.db` in [DB Browser for SQLite](https://sqlitebrowser.org/).

## Do not run Streamlit on the trading VM
