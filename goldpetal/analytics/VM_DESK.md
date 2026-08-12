# Gold Petal desk on the VM (preferred)

No Mac sync. Streamlit reads `data/` on the trading VM.

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

Restart the bot, then start the desk in a **second** SSH session:

```bash
pkill -9 -f 'run_strategy|supervise' || true
nohup ./supervise.sh >> data/supervise.log 2>&1 &

# other terminal / screen / tmux:
./scripts/run_desk_vm.sh
```

Open in browser: `http://VM_EXTERNAL_IP:8501`  
(GCP firewall: allow tcp:8501 to your IP, or use an SSH tunnel.)

## Cancel Mac desk

You do **not** need Finder / `open_desk_mac.command` anymore.
