#!/usr/bin/env python3
"""Gold Petal control panel — ticks, finished trades, capital, weekend approvals.

Run on the VM (bind localhost or LAN as you prefer):
  cd ~/goldpetal && source .venv/bin/activate
  python3 control_panel.py --host 0.0.0.0 --port 8787

Open http://<vm-ip>:8787/
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from capital import (
    capital_snapshot,
    load_capital,
    save_capital,
    update_strategy_budget,
)
from storage import build_trades, count_ticks, latest_ltp, latest_signals, latest_ticks
from control_state import (
    entries_blocked,
    is_live_mode_allowed,
    load_state,
    save_state,
    set_emergency,
    set_live_approved,
    set_live_unlocked,
    set_trading_enabled,
)
from live_orders import live_lots, recent_orders
from live_readiness import (
    apply_panel_enables,
    apply_panel_live_env,
    live_readiness,
    panel_restart_allowed,
    read_live_env,
)
from position_safety import read_bot_health
from paper_report import summarize_trades
from proposals import decide_proposal, proposals_snapshot
from sheets_pack import sheets_pack_zip_bytes, build_scoreboard_rows, SCORE_FIELDS
from s14_exchange_sheet import (
    HTML_NAME,
    load_sheet_csv,
    load_sheet_meta,
    missing_sheet_html,
    refresh_status,
    rows_to_tsv as s14_rows_to_tsv,
    sheet_zip_bytes,
    start_angel_refresh,
)
from panel_export import (
    TRADE_CSV_FIELDS,
    TICK_CSV_FIELDS,
    default_date_range,
    export_pack_zip,
    export_summary,
    export_ticks_csv,
    export_trades_csv,
    rows_to_tsv,
    ticks_in_range,
    trades_in_range,
)
from reasoning_cockpit import (
    bars_for_panel,
    load_reasoning,
    panel_timeframes,
    refresh_and_save,
)
from position_safety import read_bot_health

ROOT = Path(__file__).resolve().parent
S14_SHEET_DIR = ROOT / "data" / "s14_sheet"


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Gold Petal Control</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,700&family=IBM+Plex+Mono:wght@400;500&family=Manrope:wght@400;600;700&display=swap" rel="stylesheet"/>
<style>
:root {
  --bg0: #0c1410;
  --bg1: #132019;
  --bg2: #1a2c22;
  --line: #2a4034;
  --text: #e8f0ea;
  --muted: #8aa394;
  --gold: #d4a24c;
  --gold2: #f0c674;
  --ok: #3dba7a;
  --warn: #e0a045;
  --bad: #e05a4c;
  --panel: rgba(19, 32, 25, 0.92);
}
* { box-sizing: border-box; }
html, body {
  margin: 0; min-height: 100%;
  background:
    radial-gradient(1200px 600px at 10% -10%, #1e3a2c 0%, transparent 55%),
    radial-gradient(900px 500px at 100% 0%, #2a2410 0%, transparent 45%),
    linear-gradient(165deg, var(--bg0), #0a100d 60%, #10180f);
  color: var(--text);
  font-family: "Manrope", system-ui, sans-serif;
}
body { padding: 1.25rem 1.5rem 3rem; }
.brand {
  display: flex; flex-wrap: wrap; align-items: baseline; gap: .75rem 1.25rem;
  margin-bottom: 1.25rem; border-bottom: 1px solid var(--line); padding-bottom: .9rem;
}
.brand h1 {
  margin: 0; font-family: "Fraunces", Georgia, serif;
  font-weight: 700; font-size: clamp(1.8rem, 4vw, 2.6rem);
  letter-spacing: -.02em; color: var(--gold2);
}
.brand .tag { color: var(--muted); font-size: .95rem; max-width: 42rem; }
.brand .where {
  margin-left: auto; font-family: "IBM Plex Mono", monospace; font-size: .78rem;
  color: var(--gold); border: 1px solid var(--line); padding: .35rem .6rem; border-radius: 3px;
}
.tf-btn {
  appearance: none; border: 1px solid var(--line); background: var(--bg0);
  color: var(--muted); font-family: "IBM Plex Mono", monospace; font-size: .75rem;
  padding: .3rem .55rem; border-radius: 2px; cursor: pointer;
}
.tf-btn.active { color: var(--bg0); background: var(--gold); border-color: var(--gold); font-weight: 500; }
.head-box {
  flex: 1 1 220px; background: var(--bg2); border: 1px solid var(--line);
  padding: .7rem .8rem; border-radius: 3px; min-height: 7rem;
}
.head-box h4 { margin: 0 0 .4rem; font-family: Fraunces, serif; font-weight: 500; color: var(--gold2); font-size: .95rem; }
.step {
  font-family: "IBM Plex Mono", monospace; font-size: .72rem; color: var(--muted);
  padding: .1rem 0; border-bottom: 1px dashed #24362c;
}
.step.ok { color: var(--ok); }
.step.bad { color: var(--bad); }
.grid {
  display: grid;
  grid-template-columns: repeat(12, 1fr);
  gap: 1rem;
}
.panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 4px;
  padding: 1rem 1.1rem 1.15rem;
  grid-column: span 12;
}
@media (min-width: 960px) {
  .span-4 { grid-column: span 4; }
  .span-5 { grid-column: span 5; }
  .span-6 { grid-column: span 6; }
  .span-7 { grid-column: span 7; }
  .span-8 { grid-column: span 8; }
}
h2 {
  margin: 0 0 .75rem; font-family: "Fraunces", Georgia, serif;
  font-size: 1.15rem; font-weight: 500; color: var(--gold);
}
.muted { color: var(--muted); font-size: .85rem; }
.mono { font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: .82rem; }
.row { display: flex; flex-wrap: wrap; gap: .6rem; align-items: center; }
.stat {
  flex: 1 1 120px;
  background: var(--bg2);
  border: 1px solid var(--line);
  padding: .65rem .75rem;
  border-radius: 3px;
}
.stat .k { display:block; color: var(--muted); font-size: .72rem; text-transform: uppercase; letter-spacing: .06em; }
.stat .v { display:block; margin-top: .2rem; font-family: "IBM Plex Mono", monospace; font-size: 1.05rem; }
.btn {
  appearance: none; border: 1px solid var(--line); background: var(--bg2);
  color: var(--text); font-family: inherit; font-weight: 600;
  padding: .55rem .9rem; border-radius: 3px; cursor: pointer;
}
.btn:hover { border-color: var(--gold); color: var(--gold2); }
.btn.danger { background: #3a1814; border-color: #6a2e28; color: #ffb4ab; }
.btn.danger.on { background: var(--bad); color: #1a0504; border-color: var(--bad); }
.btn.ok { background: #143224; border-color: #2f6a4a; color: #a8efc6; }
.btn.warn { background: #3a2a12; border-color: #6a5220; color: #f0d28a; }
a.btn { text-decoration: none; display: inline-block; }
.pill {
  display: inline-block; padding: .15rem .45rem; border-radius: 2px;
  font-family: "IBM Plex Mono", monospace; font-size: .75rem;
  border: 1px solid var(--line);
}
.pill.ok { color: var(--ok); border-color: #2f6a4a; }
.pill.bad { color: var(--bad); border-color: #6a2e28; }
.pill.warn { color: var(--warn); border-color: #6a5220; }
table { width: 100%; border-collapse: collapse; }
th, td {
  text-align: left; padding: .4rem .35rem; border-bottom: 1px solid var(--line);
  font-family: "IBM Plex Mono", monospace; font-size: .78rem;
}
th { color: var(--muted); font-weight: 500; font-family: Manrope, sans-serif; font-size: .72rem; text-transform: uppercase; letter-spacing: .04em; }
.scroll { max-height: 280px; overflow: auto; }
.proposal {
  border: 1px solid var(--line); background: var(--bg1);
  padding: .85rem; margin-bottom: .75rem; border-radius: 3px;
}
.proposal h3 { margin: 0 0 .35rem; font-size: 1rem; color: var(--gold2); font-family: Fraunces, serif; font-weight: 500; }
.proposal .actions { margin-top: .65rem; display: flex; flex-wrap: wrap; gap: .45rem; }
input[type="number"] {
  width: 7rem; background: var(--bg0); border: 1px solid var(--line);
  color: var(--text); padding: .35rem .45rem; border-radius: 3px;
  font-family: "IBM Plex Mono", monospace;
}
input[type="date"], input[type="text"] {
  background: var(--bg0); border: 1px solid var(--line);
  color: var(--text); padding: .35rem .45rem; border-radius: 3px;
  font-family: "IBM Plex Mono", monospace;
}
input[type="checkbox"] { width: 1rem; height: 1rem; accent-color: var(--gold); }
.flash { margin: .5rem 0 0; color: var(--gold2); font-size: .85rem; min-height: 1.2em; }
.toast {
  position: fixed; top: 1rem; right: 1rem; z-index: 1000;
  max-width: min(28rem, 92vw);
  padding: .85rem 1.1rem; border-radius: 4px;
  background: #1c2418; border: 1px solid var(--gold);
  color: var(--gold2); font-size: .9rem; line-height: 1.35;
  box-shadow: 0 8px 28px rgba(0,0,0,.45);
  display: none;
}
.toast.show { display: block; }
.toast.bad { border-color: var(--bad); color: #ffb4ab; background: #2a1412; }
.toast.ok { border-color: #2f6a4a; color: #a8efc6; background: #143224; }
.check { font-family: "IBM Plex Mono", monospace; font-size: .78rem; padding: .35rem .55rem; border: 1px solid var(--line); border-radius: 3px; }
.check.ok { color: var(--ok); }
.check.bad { color: var(--bad); }
.check.warn { color: var(--warn); }
#live-desk-banner.armed { color: var(--bad); font-weight: 700; }
#live-desk-banner.paper { color: var(--ok); }
#s14-chart { width: 100%; height: 320px; background: #0c1410; border: 1px solid var(--line); border-radius: 3px; }
</style>
</head>
<body>
  <div id="toast" class="toast" role="status" aria-live="polite"></div>
  <header class="brand">
    <h1>Gold Petal</h1>
    <p class="tag">Operator desk on the trading VM. Streamlit (8501) is research-only and cannot overwrite these switches. Ticks, bars, capital, live gates, weekend approvals. Nothing goes live without you.</p>
    <div class="where">Runs on VM · :8787</div>
  </header>

  <div class="grid">
    <section class="panel span-5">
      <h2>Emergency &amp; trading</h2>
      <div class="row" id="status-stats"></div>
      <div class="row" style="margin-top:.85rem">
        <button class="btn danger" id="btn-emergency" type="button">EMERGENCY OFF</button>
        <button class="btn ok" id="btn-trading" type="button">Trading</button>
        <button class="btn warn" id="btn-live" type="button">Live unlock</button>
        <button class="btn" id="btn-refresh" type="button">Refresh</button>
        <button class="btn" id="btn-reason" type="button">Re-run reasoner</button>
      </div>
      <p class="flash" id="flash"></p>
      <p class="muted" style="margin-top:.75rem">Open this desk through an SSH tunnel (no public 8787). On your Mac: <span class="mono">gcloud compute ssh sampreeth1608@sampreeth-love-story --zone=asia-south1-c -- -N -L 8787:127.0.0.1:8787</span> then <span class="mono">http://127.0.0.1:8787/</span>. Bind the panel with <span class="mono">--host 127.0.0.1</span>.</p>
    </section>

    <section class="panel">
      <h2>Gold Petal exchange candles</h2>
      <p class="muted">Angel/MCX Gold Petal chart. Open this panel through the SSH tunnel, then Pull live candles (or wait for the weekday job). Green/red sticks = close vs open. Table is O/H/L/C + wick + S14 side.</p>
      <canvas id="s14-chart" width="1100" height="320"></canvas>
      <div class="row" style="margin:.6rem 0 .85rem;gap:.6rem;align-items:center">
        <button class="btn ok" type="button" id="btn-s14-pull">Pull live candles</button>
        <a class="btn" href="/s14-sheet" target="_blank" rel="noopener">Open full sheet</a>
        <button class="btn" type="button" id="btn-s14-zip">Download sheet ZIP</button>
        <label class="muted">Tab
          <select id="s14-tf" style="margin-left:.35rem">
            <option value="1d">1d</option>
            <option value="1h">1h</option>
            <option value="30m">30m</option>
            <option value="pnl">pnl</option>
            <option value="trades">trades</option>
          </select>
        </label>
        <button class="btn warn" type="button" id="btn-s14-copy">Copy tab → Google Sheets</button>
      </div>
      <p class="mono" id="s14-meta">No sheet yet — restart this panel after git pull</p>
      <p class="flash" id="s14-flash"></p>
      <div class="scroll" style="max-height:22rem"><table><thead id="s14-head"></thead><tbody id="s14-body"></tbody></table></div>
    </section>

    <section class="panel">
      <h2>Live money — operator desk (only writer)</h2>
      <p class="muted">This panel is the <strong>only</strong> place that writes DRY_RUN, LIVE_MAX_LOTS, ENABLE_*, live_approved, and Restart. Streamlit Deploy/Ops, Live Deploy, and Capital are read-only. Paper 100 lots on S12/S14/S15 is <strong>not</strong> live size. Live qty = min(strategy max lots, <span class="mono">LIVE_MAX_LOTS</span>). <strong>Save .env</strong> writes the file only. <strong>Restart supervise</strong> loads it into the bot. Type <span class="mono">LIVE</span> to set <span class="mono">DRY_RUN=false</span>. Panel cap is 10 lots.</p>
      <p class="mono" id="live-desk-banner">Loading…</p>
      <div class="row" id="live-desk-steps" style="margin:.6rem 0"></div>
      <p class="muted" id="live-desk-bot"></p>
      <div class="scroll" style="margin-top:.5rem">
        <table>
          <thead><tr><th>Strategy</th><th>RAM</th><th>Paper lots</th><th>Live?</th><th>Live qty</th></tr></thead>
          <tbody id="live-desk-books"></tbody>
        </table>
      </div>
      <p class="muted" style="margin-top:.7rem">Loaded after Restart (<span class="mono">ENABLE_*</span>) — this is not live-approved. Unchecked books are not in RAM.</p>
      <div class="row" id="live-desk-enables" style="margin:.35rem 0 .5rem"></div>
      <div class="row">
        <button class="btn" type="button" id="btn-save-enables">Save ENABLE_*</button>
      </div>
      <div class="row" style="gap:.8rem;align-items:flex-end;margin-top:.75rem">
        <label class="muted" style="display:flex;align-items:center;gap:.4rem"><input type="checkbox" id="live-dry-run" checked/> Paper only (<span class="mono">DRY_RUN</span>)</label>
        <label class="muted">LIVE_MAX_LOTS (1–10)<br/><input type="number" id="live-max-lots" min="1" max="10" step="1" value="1"/></label>
        <label class="muted">Type LIVE to allow DRY_RUN=false<br/><input type="text" id="live-env-confirm" placeholder="LIVE" autocomplete="off" style="width:8rem"/></label>
        <button class="btn warn" type="button" id="btn-save-live-env">Save .env</button>
      </div>
      <div class="row" style="gap:.8rem;align-items:flex-end;margin-top:.5rem">
        <label class="muted">Type RESTART to restart the bot<br/><input type="text" id="live-restart-confirm" placeholder="RESTART" autocomplete="off" style="width:8rem"/></label>
        <button class="btn danger" type="button" id="btn-restart-supervise">Restart supervise</button>
        <button class="btn warn" type="button" id="btn-save-live-approved">Save live-approved list</button>
        <button class="btn" type="button" id="btn-clear-live-approved">Clear live-approved</button>
      </div>
      <p class="muted" id="live-env-hint"></p>
      <p class="flash" id="live-desk-flash"></p>
    </section>

    <section class="panel span-7">
      <h2>Capital management</h2>
      <p class="muted">Book ₹, day-loss, and per-strategy lots. Same <span class="mono">capital.json</span> the bot uses. Do not also Push capital from Streamlit.</p>
      <div class="row" id="capital-stats"></div>
      <div class="row" style="gap:.8rem;align-items:flex-end;margin:.75rem 0">
        <label class="muted">Total capital ₹<br/><input type="number" id="cap-total" step="1000" style="margin-top:.25rem;width:9rem"/></label>
        <label class="muted">Cash reserve %<br/><input type="number" id="cap-reserve" step="1" min="0" max="90" style="margin-top:.25rem;width:6rem"/></label>
        <label class="muted">Day loss limit ₹<br/><input type="number" id="cap-dayloss" step="500" style="margin-top:.25rem;width:8rem"/></label>
        <label class="muted">Max lots total<br/><input type="number" id="cap-maxlots" step="1" min="1" style="margin-top:.25rem;width:6rem"/></label>
        <button class="btn ok" type="button" id="btn-cap-save">Save totals</button>
      </div>
      <div class="scroll" style="margin-top:.35rem">
        <table>
          <thead><tr><th>Strategy</th><th>Budget ₹</th><th>Max lots</th><th>Open</th><th>On</th></tr></thead>
          <tbody id="capital-body"></tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <h2>Reasoning — entry / hold / exit</h2>
      <p class="muted" id="reason-meta">Multi-step math → logic → science/ML → planning. Weekly NN trains the ML heads; this cockpit applies them on live ticks.</p>
      <div class="row" id="reason-heads" style="margin-top:.5rem"></div>
      <p class="mono" id="reason-rec" style="margin-top:.75rem"></p>
    </section>

    <section class="panel span-6">
      <h2>Tick tape</h2>
      <p class="muted" id="tick-meta"></p>
      <div class="scroll">
        <table>
          <thead><tr><th>Time</th><th>LTP</th><th>Vol</th><th>TBQ</th><th>TSQ</th></tr></thead>
          <tbody id="ticks-body"></tbody>
        </table>
      </div>
    </section>

    <section class="panel span-6">
      <h2>Bars · 1m → day</h2>
      <div class="row" id="tf-row" style="margin-bottom:.55rem"></div>
      <p class="muted" id="bars-meta"></p>
      <div class="scroll">
        <table>
          <thead><tr><th>Time</th><th>O</th><th>H</th><th>L</th><th>C</th><th>NET</th><th>IMB%</th><th>ΔP</th></tr></thead>
          <tbody id="bars-body"></tbody>
        </table>
      </div>
    </section>

    <section class="panel span-6">
      <h2>Finished trades</h2>
      <p class="muted" id="trades-meta"></p>
      <div class="row" style="margin:.35rem 0 .55rem">
        <label class="muted">Filter
          <select id="trade-filter" style="margin-left:.4rem">
            <option value="">all slim + others</option>
          </select>
        </label>
      </div>
      <div class="scroll">
        <table>
          <thead><tr><th>Strat</th><th>Side</th><th>Entry</th><th>Exit</th><th>After tax</th><th>Status</th></tr></thead>
          <tbody id="trades-body"></tbody>
        </table>
      </div>
    </section>

    <section class="panel span-6">
      <h2>Live Angel orders</h2>
      <p class="muted" id="live-meta">Gates + recent placeOrder log (<span class="mono">data/control/live_orders.jsonl</span>)</p>
      <div class="row" id="live-gates" style="margin-bottom:.55rem"></div>
      <div class="scroll">
        <table>
          <thead><tr><th>Time</th><th>Strat</th><th>Tx</th><th>Qty</th><th>OK</th><th>Order</th><th>Reason</th></tr></thead>
          <tbody id="live-body"></tbody>
        </table>
      </div>
    </section>

    <section class="panel span-6">
      <h2>Paper scoreboard</h2>
      <div class="scroll">
        <table>
          <thead><tr><th>Strategy</th><th>N</th><th>Closed</th><th>Win%</th><th>Gross</th><th>Fees</th><th>After tax</th></tr></thead>
          <tbody id="score-body"></tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <h2>Google Sheets pack · richer trade review</h2>
      <p class="muted">Download a ZIP (scoreboard + all trades + open positions + signals). Import CSVs into Sheets. <strong>Sheets is for reading PnL</strong> — emergency / live unlock / Approve stay on this panel.</p>
      <div class="row" style="margin:.6rem 0 .85rem">
        <button class="btn ok" type="button" id="btn-sheets-pack">Download Sheets pack (ZIP)</button>
        <button class="btn warn" type="button" id="btn-copy-score">Copy scoreboard → Sheets</button>
      </div>
      <p class="mono" id="sheets-meta">Ready</p>
      <p class="flash" id="sheets-flash"></p>
    </section>

    <section class="panel">
      <h2>One-click export · ticks &amp; all trades</h2>
      <p class="muted">Pick dates (IST) → download CSV/ZIP to your laptop, or copy TSV and paste straight into Google Sheets. No SSH / no manual SQL.</p>
      <div class="row" style="gap:.8rem;align-items:flex-end;margin:.6rem 0 .85rem">
        <label class="muted">From<br/><input type="date" id="exp-from" style="margin-top:.25rem"/></label>
        <label class="muted">To<br/><input type="date" id="exp-to" style="margin-top:.25rem"/></label>
        <button class="btn ok" type="button" id="btn-dl-pack">Download all (ZIP)</button>
        <button class="btn" type="button" id="btn-dl-ticks">Download ticks CSV</button>
        <button class="btn" type="button" id="btn-dl-trades">Download trades CSV</button>
        <button class="btn warn" type="button" id="btn-copy-ticks">Copy ticks → Sheets</button>
        <button class="btn warn" type="button" id="btn-copy-trades">Copy trades → Sheets</button>
      </div>
      <p class="mono" id="exp-meta">Select a range…</p>
      <p class="flash" id="exp-flash"></p>
    </section>

    <section class="panel">
      <h2>Weekend proposals — new &amp; improved strategies</h2>
      <p class="muted">Every Sunday job writes paper results here. Approve for paper first; approve live only after you are happy. Reject force-disables the strategy.</p>
      <p class="flash" id="proposals-flash"></p>
      <div id="proposals"></div>
    </section>
  </div>

<script>
const $ = (id) => document.getElementById(id);
let _toastTimer = null;
function toast(msg, kind) {
  const el = $("toast");
  el.textContent = msg || "";
  el.className = "toast show" + (kind ? " " + kind : "");
  if (_toastTimer) clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { el.className = "toast"; }, 8000);
}
const flash = (msg) => {
  $("flash").textContent = msg || "";
  if (msg) toast(msg, msg.toLowerCase().includes("fail") || msg.toLowerCase().includes("error") ? "bad" : "ok");
};
const propFlash = (msg) => {
  const el = $("proposals-flash");
  if (el) el.textContent = msg || "";
  if (msg) toast(msg, msg.toLowerCase().includes("fail") || msg.toLowerCase().includes("error") ? "bad" : "ok");
};

async function api(path, opts) {
  const res = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, opts || {}));
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText || ("HTTP " + res.status));
  return data;
}

function pill(text, cls) {
  return `<span class="pill ${cls||""}">${text}</span>`;
}

function money(n) {
  const v = Number(n || 0);
  const s = v.toFixed(2);
  return (v >= 0 ? "+" : "") + s;
}

function renderStatus(s) {
  const blocked = s.entries_blocked;
  $("status-stats").innerHTML = `
    <div class="stat"><span class="k">Emergency</span><span class="v">${s.state.emergency_off ? pill("OFF","bad") : pill("clear","ok")}</span></div>
    <div class="stat"><span class="k">Trading</span><span class="v">${s.state.trading_enabled ? pill("ON","ok") : pill("OFF","warn")}</span></div>
    <div class="stat"><span class="k">Live</span><span class="v">${s.state.live_unlocked ? pill("unlocked","warn") : pill("locked","ok")}</span></div>
    <div class="stat"><span class="k">Entries</span><span class="v">${blocked[0] ? pill(blocked[1],"bad") : pill("allowed","ok")}</span></div>
    <div class="stat"><span class="k">LTP</span><span class="v">${s.ltp ?? "—"}</span></div>
    <div class="stat"><span class="k">Ticks</span><span class="v">${s.tick_count}</span></div>
  `;
  const em = $("btn-emergency");
  em.textContent = s.state.emergency_off ? "CLEAR EMERGENCY" : "EMERGENCY OFF";
  em.classList.toggle("on", !!s.state.emergency_off);
  $("btn-trading").textContent = s.state.trading_enabled ? "Turn trading OFF" : "Turn trading ON";
  $("btn-live").textContent = s.state.live_unlocked ? "Lock live" : "Unlock live";
}

function renderCapital(c) {
  $("capital-stats").innerHTML = `
    <div class="stat"><span class="k">Total</span><span class="v">₹${Number(c.total_capital_inr).toLocaleString()}</span></div>
    <div class="stat"><span class="k">Deployable</span><span class="v">₹${Number(c.deployable_inr).toLocaleString()}</span></div>
    <div class="stat"><span class="k">Today PnL</span><span class="v">${money(c.today_pnl_inr)}</span></div>
    <div class="stat"><span class="k">Day loss limit</span><span class="v">₹${Number(c.daily_loss_limit_inr).toLocaleString()}</span></div>
    <div class="stat"><span class="k">Max lots</span><span class="v">${c.max_lots_total}</span></div>
  `;
  const rows = Object.values(c.strategies || {}).map(sb => {
    const open = (c.open_lots || {})[sb.strategy] || 0;
    return `<tr>
      <td>${sb.strategy}</td>
      <td><input type="number" data-strat="${sb.strategy}" data-field="budget_inr" value="${sb.budget_inr}"/></td>
      <td><input type="number" data-strat="${sb.strategy}" data-field="max_lots" value="${sb.max_lots}"/></td>
      <td>${open}</td>
      <td>${sb.enabled ? "Y" : "N"}</td>
    </tr>`;
  }).join("");
  $("capital-body").innerHTML = rows || `<tr><td colspan="5">No budgets</td></tr>`;
  $("capital-body").querySelectorAll("input").forEach(inp => {
    inp.addEventListener("change", async () => {
      try {
        const body = { strategy: inp.dataset.strat };
        body[inp.dataset.field] = Number(inp.value);
        await api("/api/capital/strategy", { method: "POST", body: JSON.stringify(body) });
        flash(`Saved ${inp.dataset.strat} ${inp.dataset.field}`);
        await refresh();
      } catch (e) { flash(String(e.message || e)); }
    });
  });
}

function renderTicks(ticks, meta) {
  $("tick-meta").textContent = meta;
  $("ticks-body").innerHTML = (ticks || []).map(t => `
    <tr>
      <td>${t.received_at || ""}</td>
      <td>${t.ltp ?? ""}</td>
      <td>${t.volume ?? ""}</td>
      <td>${t.bp ?? ""}</td>
      <td>${t.sp ?? ""}</td>
    </tr>`).join("") || `<tr><td colspan="5">No ticks yet</td></tr>`;
}

let currentTf = "5m";
async function loadBars(tf) {
  currentTf = tf || currentTf;
  const data = await api(`/api/bars?tf=${encodeURIComponent(currentTf)}&limit=60`);
  $("bars-meta").textContent = `${data.tf} · ${data.n_bars} bars from ${data.tick_count} ticks · showing latest ${data.bars.length}`;
  $("bars-body").innerHTML = (data.bars || []).slice().reverse().map(b => `
    <tr>
      <td>${b.time || ""}</td>
      <td>${b.open ?? ""}</td>
      <td>${b.high ?? ""}</td>
      <td>${b.low ?? ""}</td>
      <td>${b.close ?? ""}</td>
      <td>${b.net != null ? Number(b.net).toFixed(0) : ""}</td>
      <td>${b.imb_pct ?? ""}</td>
      <td>${b.price_delta != null ? Number(b.price_delta).toFixed(1) : ""}</td>
    </tr>`).join("") || `<tr><td colspan="8">No bars yet — need ticks in data/ticks.db</td></tr>`;
  document.querySelectorAll(".tf-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.tf === currentTf);
  });
}

function renderTfButtons(tfs) {
  $("tf-row").innerHTML = (tfs || []).map(t =>
    `<button type="button" class="tf-btn${t.tf===currentTf?" active":""}" data-tf="${t.tf}">${t.tf}</button>`
  ).join("");
  $("tf-row").querySelectorAll(".tf-btn").forEach(btn => {
    btn.addEventListener("click", () => loadBars(btn.dataset.tf).catch(e => flash(String(e.message||e))));
  });
}

function renderReasoning(r) {
  if (!r) {
    $("reason-heads").innerHTML = `<p class="muted">No reasoning yet — click Re-run reasoner (needs ticks).</p>`;
    $("reason-rec").textContent = "";
    return;
  }
  const m = r.market || {};
  $("reason-meta").textContent =
    `regime=${m.regime_guess || "?"} LTP=${m.ltp ?? "—"} NET=${m.net ?? "—"} IMB=${m.imb_pct ?? "—"}% · asof ${m.asof_ist || ""}`;
  const head = (title, block) => {
    const steps = (block?.steps || []).slice(-6).map(s =>
      `<div class="step ${s.ok ? "ok":"bad"}">${s.domain[0].toUpperCase()}:${s.name} — ${s.detail}</div>`
    ).join("");
    return `<div class="head-box"><h4>${title} ${pill(block?.action || "—", block?.action==="HOLD"||String(block?.action||"").startsWith("ENTER")?"ok": block?.action==="EXIT"||block?.action==="SKIP"?"bad":"warn")}</h4>
      <p class="mono">score=${Number(block?.score||0).toFixed(2)}</p>
      <p class="muted">${block?.summary || ""}</p>${steps}</div>`;
  };
  $("reason-heads").innerHTML =
    head("ENTRY", r.entry) + head("HOLD", r.hold) + head("EXIT", r.exit);
  $("reason-rec").textContent =
    `RECOMMENDED: ${r.recommended || "—"} · ${r.loss_guard || ""}`;
}

function renderTrades(trades, meta) {
  window._allTrades = trades || [];
  const sel = $("trade-filter");
  if (sel && !sel.dataset.wired) {
    const names = [...new Set((trades || []).map(t => t.strategy).filter(Boolean))].sort();
    sel.innerHTML = `<option value="">all</option>` + names.map(n => `<option value="${n}">${n}</option>`).join("");
    sel.dataset.wired = "1";
    sel.addEventListener("change", () => renderTrades(window._allTrades, $("trades-meta").textContent));
  }
  const want = sel ? sel.value : "";
  const rows = (trades || []).filter(t => !want || t.strategy === want);
  $("trades-meta").textContent = meta + (want ? ` · filter ${want}` : "");
  $("trades-body").innerHTML = rows.map(t => `
    <tr>
      <td>${t.strategy || ""}</td>
      <td>${t.side || ""}</td>
      <td>${t.entry_price ?? ""}</td>
      <td>${t.exit_price ?? ""}</td>
      <td>${t.pnl_after_tax !== "" && t.pnl_after_tax != null ? money(t.pnl_after_tax) : (t.net_pnl ?? "")}</td>
      <td>${t.status || ""}</td>
    </tr>`).join("") || `<tr><td colspan="6">No finished trades</td></tr>`;
}

function renderLiveDesk(data) {
  const d = data.live_desk || {};
  const banner = $("live-desk-banner");
  if (!banner) return;
  if (d.would_place_real_orders) {
    banner.className = "mono armed";
    banner.textContent = "ARMED — next BUY/SHORT on an approved strategy will hit Angel. Lock live or set DRY_RUN=true if that is wrong.";
  } else {
    banner.className = "mono paper";
    banner.textContent = `PAPER · ${d.steps_ok || 0}/${d.steps_n || 0} gates green · DRY_RUN=${d.dry_run} · LIVE_MAX_LOTS=${d.live_max_lots} · ${d.note || ""}`;
  }
  $("live-desk-steps").innerHTML = (d.steps || []).map(s =>
    `<div class="check ${s.ok ? "ok" : (s.id === "dry_run" || s.id === "unlocked" ? "warn" : "bad")}">${s.ok ? "OK" : "NO"} · ${s.label} — ${s.detail}</div>`
  ).join("");
  const bot = d.bot_health || {};
  $("live-desk-bot").textContent =
    `Heartbeat: ${bot.alive ? "alive" : "stale/missing"} · ${bot.ts_ist || "—"} · LTP=${bot.ltp ?? "—"} · regime=${bot.regime || "—"} · RAM ${JSON.stringify(bot.positions || {})}`;
  $("live-desk-books").innerHTML = (d.books || []).map(b => `
    <tr>
      <td>${b.strategy}${b.warn_100 ? " · paper 100 lots" : ""}</td>
      <td>${b.ram}</td>
      <td>${b.paper_max_lots}</td>
      <td><input type="checkbox" data-live-strat="${b.strategy}" ${b.live_approved ? "checked" : ""}/></td>
      <td>${b.live_approved ? b.live_qty : "—"}</td>
    </tr>`).join("");
  const dryEl = $("live-dry-run");
  if (dryEl) dryEl.checked = d.dry_run !== false;
  const lotsEl = $("live-max-lots");
  if (lotsEl && document.activeElement !== lotsEl) lotsEl.value = d.live_max_lots || 1;
  const hint = $("live-env-hint");
  if (hint) {
    hint.textContent = d.dry_run !== false
      ? "Paper. Save writes .env; Restart supervise loads it into the bot."
      : "DRY_RUN is false in .env. Restart supervise if you just changed it. Lock live or check Paper only if that is wrong.";
  }
  const enBox = $("live-desk-enables");
  if (enBox) {
    const enables = d.enables || {};
    enBox.innerHTML = Object.keys(enables).map(name => {
      const on = enables[name];
      const short = name.split("_")[0];
      return `<label class="muted" style="display:flex;align-items:center;gap:.35rem"><input type="checkbox" data-enable-strat="${name}" ${on ? "checked" : ""}/> ${short}</label>`;
    }).join("");
  }
}

function selectedLiveApproved() {
  return [...document.querySelectorAll("input[data-live-strat]:checked")].map(el => el.dataset.liveStrat);
}

function renderLiveOrders(data) {
  const ok = data.live_allowed && data.live_allowed[0];
  const why = (data.live_allowed && data.live_allowed[1]) || "";
  const env = data.live_env || {};
  $("live-gates").innerHTML = `
    <div class="stat"><span class="k">Gates</span><span class="v">${ok ? pill("READY","warn") : pill(why || "blocked","ok")}</span></div>
    <div class="stat"><span class="k">DRY_RUN</span><span class="v">${env.dry_run ? "true" : "false"}</span></div>
    <div class="stat"><span class="k">Lots</span><span class="v">${env.lots ?? 1}/${env.max_lots ?? 1}</span></div>
    <div class="stat"><span class="k">Approved</span><span class="v">${(data.state.live_approved||[]).join(", ") || "—"}</span></div>
  `;
  const rows = data.live_orders || [];
  $("live-meta").textContent = `${rows.length} recent order log rows · product=${env.producttype || "CARRYFORWARD"}`;
  $("live-body").innerHTML = rows.map(o => `
    <tr>
      <td>${o.ts_ist || ""}</td>
      <td>${o.strategy || ""}</td>
      <td>${o.transaction || ""}</td>
      <td>${o.quantity ?? ""}</td>
      <td>${o.ok ? "Y" : (o.skipped ? "skip" : "N")}</td>
      <td class="mono">${o.order_id || ""}</td>
      <td>${o.reason || ""}</td>
    </tr>`).join("") || `<tr><td colspan="7">No live orders yet (paper / gates closed)</td></tr>`;
}

function renderProposals(p) {
  const pending = p.pending || [];
  const decided = (p.decided || []).slice(0, 12);
  if (!pending.length && !decided.length) {
    $("proposals").innerHTML = `<p class="muted">No weekend proposals yet. Sunday job: <span class="mono">./weekly_s8_nn.sh</span></p>`;
    return;
  }
  const card = (x, actions) => `
    <div class="proposal">
      <h3>${x.title || x.strategy} ${pill(x.kind)} ${pill(x.status, x.status.includes("approved") ? "ok" : x.status === "rejected" ? "bad" : "warn")}</h3>
      <p class="muted">${x.summary || ""}</p>
      <p class="mono">week=${x.week_id} trades=${x.paper?.n_trades ?? 0} afterTax=${money(x.paper?.after_tax_pnl_inr)} vsBase=${money(x.paper?.delta_vs_baseline_inr)} safety=${x.safety_ok}</p>
      ${x.safety_reasons?.length ? `<p class="muted">${(x.safety_reasons||[]).join(" | ")}</p>` : ""}
      ${actions}
    </div>`;
  let html = pending.map(x => card(x, `
    <div class="actions">
      <button class="btn ok" data-id="${x.id}" data-dec="approved_paper">Approve → paper</button>
      <button class="btn warn" data-id="${x.id}" data-dec="approved_live">Approve → live</button>
      <button class="btn danger" data-id="${x.id}" data-dec="rejected">Reject</button>
    </div>`)).join("");
  if (decided.length) {
    html += `<p class="muted" style="margin-top:1rem">Recent decisions</p>` + decided.map(x => card(x, "")).join("");
  }
  $("proposals").innerHTML = html;
  $("proposals").querySelectorAll("button[data-dec]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.id;
      const dec = btn.dataset.dec;
      const label = dec === "approved_paper" ? "Approve → paper"
        : dec === "approved_live" ? "Approve → live" : "Reject";
      if (dec === "approved_live") {
        const ok = confirm("Approve this strategy for LIVE orders?\n\nStill needs: Unlock live + DRY_RUN=false + restart supervise.");
        if (!ok) { propFlash("Live approve cancelled"); return; }
      }
      const siblings = btn.parentElement ? btn.parentElement.querySelectorAll("button") : [btn];
      siblings.forEach(b => { b.disabled = true; });
      btn.textContent = "Working…";
      propFlash(`${label}…`);
      try {
        const res = await api(`/api/proposals/${id}/decide`, {
          method: "POST",
          body: JSON.stringify({ decision: dec })
        });
        const strat = (res.proposal && res.proposal.strategy) || id;
        let msg = "";
        if (dec === "approved_paper") {
          msg = `✓ ${strat} approved for PAPER. Keep DRY_RUN=true. Set ENABLE flags in .env if needed, then restart supervise.`;
        } else if (dec === "approved_live") {
          msg = `✓ ${strat} approved for LIVE list. Still locked until Unlock live + DRY_RUN=false.`;
        } else {
          msg = `✓ ${strat} rejected / force-disabled.`;
        }
        propFlash(msg);
        flash(msg);
        await refresh();
      } catch (e) {
        const err = String(e.message || e);
        propFlash("Failed: " + err);
        flash("Failed: " + err);
        siblings.forEach(b => { b.disabled = false; });
        btn.textContent = label;
      }
    });
  });
}

function renderScore(rows) {
  $("score-body").innerHTML = (rows || []).map(s => `
    <tr>
      <td>${s.strategy}</td>
      <td>${s.trades}</td>
      <td>${s.closed}</td>
      <td>${Number(s.win_rate).toFixed(1)}</td>
      <td>${money(s.gross_pnl)}</td>
      <td>${Number(s.charges).toFixed(2)}</td>
      <td>${money(s.pnl_after_tax)}</td>
    </tr>`).join("");
}

async function refresh() {
  flash("Loading dashboard…");
  const data = await api("/api/dashboard");
  renderStatus(data);
  renderCapital(data.capital);
  renderTicks(data.ticks, `${data.tick_count} ticks stored · showing latest ${data.ticks.length}`);
  renderTrades(data.trades, `Closed/open from signals · showing latest ${data.trades.length}`);
  renderLiveOrders(data);
  renderLiveDesk(data);
  renderProposals(data.proposals);
  renderScore(data.scoreboard);
  renderReasoning(data.reasoning);
  renderTfButtons(data.timeframes || []);
  flash(`✓ Updated · LTP=${data.ltp ?? "—"} · ticks=${data.tick_count}`);
}

async function refreshLight() {
  // Fast path: status + ticks only (no full trade rebuild / bars).
  const s = await api("/api/status");
  renderStatus(s);
  renderLiveDesk(s);
  const t = await api("/api/ticks?limit=40");
  renderTicks(t.ticks, `${t.count} ticks stored · showing latest ${(t.ticks||[]).length}`);
}

$("btn-emergency").onclick = async () => {
  try {
    const s = await api("/api/status");
    const off = !s.state.emergency_off;
    await api("/api/emergency", { method: "POST", body: JSON.stringify({ off }) });
    flash(off ? "✓ EMERGENCY OFF engaged — new entries blocked" : "✓ Emergency cleared — entries allowed again");
    await refresh();
  } catch (e) { flash("Failed: " + (e.message || e)); }
};
$("btn-trading").onclick = async () => {
  try {
    const s = await api("/api/status");
    const enabled = !s.state.trading_enabled;
    await api("/api/trading", { method: "POST", body: JSON.stringify({ enabled }) });
    flash(enabled ? "✓ Trading enabled" : "✓ Trading disabled");
    await refresh();
  } catch (e) { flash("Failed: " + (e.message || e)); }
};
$("btn-live").onclick = async () => {
  try {
    const s = await api("/api/status");
    const unlocked = !s.state.live_unlocked;
    if (unlocked) {
      const ok = confirm("Unlock LIVE path?\n\nOrders still need DRY_RUN=false + Approve → live per strategy.");
      if (!ok) { flash("Live unlock cancelled"); return; }
    }
    await api("/api/live", { method: "POST", body: JSON.stringify({ unlocked }) });
    flash(unlocked ? "✓ Live unlocked (still need DRY_RUN=false + Approve → live)" : "✓ Live locked");
    await refresh();
  } catch (e) { flash("Failed: " + (e.message || e)); }
};
$("btn-refresh").onclick = async () => {
  try {
    await refresh();
    flash("✓ Refreshed");
  } catch (e) { flash("Failed: " + (e.message || e)); }
};
const liveDeskFlash = (msg) => {
  const el = $("live-desk-flash");
  if (el) el.textContent = msg || "";
  if (msg) toast(msg, msg.toLowerCase().includes("fail") ? "bad" : "ok");
};
$("btn-save-live-approved").onclick = async () => {
  try {
    const names = selectedLiveApproved();
    if (names.some(n => n === "S14_WICK30_STRICT" || n === "S15_WICK30_NOWICK" || n === "S12_HHHL30" || n === "S13_HHHL_DAY")) {
      const ok = confirm("These books paper at 100 lots. Live qty will be min(100, LIVE_MAX_LOTS), currently often 1.\n\nThis only adds them to live_approved. DRY_RUN stays whatever is in .env. Continue?");
      if (!ok) { liveDeskFlash("cancelled"); return; }
    } else if (!names.length) {
      const ok = confirm("Clear live_approved? No strategy will place Angel orders.");
      if (!ok) return;
    }
    const res = await api("/api/live/approved", { method: "POST", body: JSON.stringify({ strategies: names }) });
    liveDeskFlash("Saved live_approved: " + ((res.live_approved || []).join(", ") || "(none)"));
    await refresh();
  } catch (e) { liveDeskFlash("Failed: " + (e.message || e)); }
};
$("btn-clear-live-approved").onclick = async () => {
  try {
    const ok = confirm("Clear live_approved for every strategy?");
    if (!ok) return;
    await api("/api/live/approved", { method: "POST", body: JSON.stringify({ strategies: [] }) });
    liveDeskFlash("Cleared live_approved");
    await refresh();
  } catch (e) { liveDeskFlash("Failed: " + (e.message || e)); }
};
$("btn-save-enables").onclick = async () => {
  try {
    const names = [...document.querySelectorAll("input[data-enable-strat]:checked")].map(el => el.dataset.enableStrat);
    const ok = confirm("Write ENABLE_* to .env? Unchecked slim books (and S1/S2/S3/S6/S9/S10) become false. Restart supervise after this. This does not approve live.");
    if (!ok) { liveDeskFlash("cancelled"); return; }
    const res = await api("/api/live/enables", {
      method: "POST",
      body: JSON.stringify({ strategies: names })
    });
    liveDeskFlash("Saved ENABLE_*: " + ((res.enabled || []).join(", ") || "(none)") + " — Restart supervise to load into RAM.");
    await refresh();
  } catch (e) { liveDeskFlash("Failed: " + (e.message || e)); }
};
$("btn-save-live-env").onclick = async () => {
  try {
    const dry = $("live-dry-run").checked;
    const lots = Number($("live-max-lots").value || 1);
    const confirmWord = ($("live-env-confirm").value || "").trim();
    if (!dry) {
      if (confirmWord !== "LIVE") {
        liveDeskFlash("Type LIVE to set DRY_RUN=false");
        return;
      }
      const ok = confirm("Write DRY_RUN=false to .env? Real Angel fills still need Unlock live + live_approved + Restart supervise. LIVE_MAX_LOTS is the hard ceiling, not paper 100. Continue?");
      if (!ok) { liveDeskFlash("cancelled"); return; }
    }
    const res = await api("/api/live/env", {
      method: "POST",
      body: JSON.stringify({ dry_run: dry, live_max_lots: lots, confirm: confirmWord })
    });
    const applied = res.applied || {};
    liveDeskFlash("Saved .env: DRY_RUN=" + (applied.DRY_RUN || "?") + " LIVE_MAX_LOTS=" + (applied.LIVE_MAX_LOTS || "?") + " — Restart supervise to load into the bot.");
    $("live-env-confirm").value = "";
    await refresh();
  } catch (e) { liveDeskFlash("Failed: " + (e.message || e)); }
};
$("btn-restart-supervise").onclick = async () => {
  try {
    const word = ($("live-restart-confirm").value || "").trim();
    if (word !== "RESTART") {
      liveDeskFlash("Type RESTART to restart supervise");
      return;
    }
    const dry = $("live-dry-run").checked;
    const msg = dry
      ? "Restart supervise? This kills run_strategy and starts it again. The control panel stays up."
      : "DRY_RUN is unchecked. Restart will load whatever is in .env (including DRY_RUN=false) into the bot. Continue?";
    const ok = confirm(msg);
    if (!ok) { liveDeskFlash("cancelled"); return; }
    liveDeskFlash("Restarting supervise…");
    const res = await api("/api/bot/restart", {
      method: "POST",
      body: JSON.stringify({ confirm: word })
    });
    $("live-restart-confirm").value = "";
    liveDeskFlash(res.ok
      ? "Supervise restarted. Check heartbeat below."
      : ("Restart reported a problem: " + (res.error || "bot not running yet")));
    await refresh();
  } catch (e) { liveDeskFlash("Failed: " + (e.message || e)); }
};
$("btn-reason").onclick = async () => {
  try {
    const r = await api("/api/reasoning/refresh", { method: "POST", body: "{}" });
    renderReasoning(r);
    flash(`✓ Reasoner: ${r.recommended}`);
  } catch (e) { flash("Failed: " + (e.message || e)); }
};

function expRange() {
  const from = $("exp-from").value;
  const to = $("exp-to").value;
  if (!from || !to) throw new Error("Pick From and To dates");
  if (from > to) throw new Error("From date must be ≤ To date");
  return { from, to };
}
function expFlash(msg) { $("exp-flash").textContent = msg || ""; }

async function refreshExportMeta() {
  try {
    const { from, to } = expRange();
    const s = await api(`/api/export/summary?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`);
    const parts = Object.entries(s.trades_by_strategy || {}).map(([k,v]) => `${k}:${v}`).join(" · ");
    $("exp-meta").textContent =
      `${s.date_from} → ${s.date_to} · ticks=${s.tick_count} trades=${s.trade_count}` +
      (parts ? ` · ${parts}` : "");
  } catch (e) {
    $("exp-meta").textContent = String(e.message || e);
  }
}

function downloadUrl(path) {
  const a = document.createElement("a");
  a.href = path;
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

async function copyExport(kind) {
  const { from, to } = expRange();
  const data = await api(`/api/export/tsv?kind=${encodeURIComponent(kind)}&from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`);
  await navigator.clipboard.writeText(data.tsv || "");
  expFlash(`Copied ${data.rows} ${kind} rows — open Google Sheets and paste (Ctrl/Cmd+V)`);
  flash(`Copied ${kind} for Sheets`);
}

$("btn-dl-pack").onclick = () => {
  try {
    const { from, to } = expRange();
    downloadUrl(`/api/export/pack.zip?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`);
    expFlash(`Downloading ZIP ${from} → ${to} (ticks + all strategy trades)`);
  } catch (e) { expFlash(String(e.message || e)); }
};
$("btn-dl-ticks").onclick = () => {
  try {
    const { from, to } = expRange();
    downloadUrl(`/api/export/ticks.csv?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`);
    expFlash(`Downloading ticks.csv ${from} → ${to}`);
  } catch (e) { expFlash(String(e.message || e)); }
};
$("btn-dl-trades").onclick = () => {
  try {
    const { from, to } = expRange();
    downloadUrl(`/api/export/trades.csv?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`);
    expFlash(`Downloading trades.csv (all strategies) ${from} → ${to}`);
  } catch (e) { expFlash(String(e.message || e)); }
};
$("btn-copy-ticks").onclick = () => copyExport("ticks").catch(e => expFlash(String(e.message || e)));
$("btn-copy-trades").onclick = () => copyExport("trades").catch(e => expFlash(String(e.message || e)));
$("exp-from").onchange = () => refreshExportMeta();
$("exp-to").onchange = () => refreshExportMeta();

const sheetsFlash = (msg) => { $("sheets-flash").textContent = msg || ""; };
$("btn-sheets-pack").onclick = () => {
  downloadUrl("/api/sheets/pack.zip");
  sheetsFlash("Downloading Sheets pack ZIP (scoreboard + trades + opens + signals)");
  $("sheets-meta").textContent = "Download started — Import CSVs in Google Sheets (File → Import)";
};
$("btn-copy-score").onclick = async () => {
  try {
    const data = await api("/api/sheets/scoreboard.tsv");
    await navigator.clipboard.writeText(data.tsv || "");
    sheetsFlash(`Copied ${data.rows} scoreboard rows — paste into Google Sheets`);
  } catch (e) { sheetsFlash(String(e.message || e)); }
};

const s14Flash = (msg) => { $("s14-flash").textContent = msg || ""; };
function paintS14Table(fields, rows) {
  $("s14-head").innerHTML = "<tr>" + fields.map((c) => `<th>${c}</th>`).join("") + "</tr>";
  const body = rows.slice(0, 80).map((r) => {
    const side = String(r.side || "");
    const cls = side === "LONG" ? "ok" : side === "SHORT" ? "bad" : "";
    return "<tr>" + fields.map((c) => {
      const v = r[c] == null ? "" : String(r[c]);
      return `<td class="${c==="side"?cls:""}">${v}</td>`;
    }).join("") + "</tr>";
  }).join("");
  $("s14-body").innerHTML = body || `<tr><td>No rows. Run the dump command above.</td></tr>`;
}
function drawS14Chart(rows) {
  const canvas = $("s14-chart");
  if (!canvas) return;
  const bars = (rows || []).map((r) => ({
    t: String(r.time || ""),
    o: +r.open, h: +r.high, l: +r.low, c: +r.close
  })).filter((b) => Number.isFinite(b.o) && Number.isFinite(b.h) && Number.isFinite(b.l) && Number.isFinite(b.c));
  const cssW = Math.max(canvas.clientWidth || 900, 320);
  const cssH = 320;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.floor(cssW * dpr);
  canvas.height = Math.floor(cssH * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = "#0c1410";
  ctx.fillRect(0, 0, cssW, cssH);
  if (!bars.length) {
    ctx.fillStyle = "#8aa394";
    ctx.font = "13px IBM Plex Mono, monospace";
    ctx.fillText("No OHLC in this tab — pick 1d / 1h / 30m", 16, 40);
    return;
  }
  const padL = 56, padR = 10, padT = 12, padB = 28;
  const w = cssW - padL - padR, h = cssH - padT - padB;
  const lo = Math.min.apply(null, bars.map((b) => b.l));
  const hi = Math.max.apply(null, bars.map((b) => b.h));
  const span = (hi - lo) || 1;
  const y = (px) => padT + (hi - px) / span * h;
  ctx.font = "11px IBM Plex Mono, monospace";
  ctx.fillStyle = "#8aa394";
  ctx.strokeStyle = "#2a4034";
  for (let i = 0; i <= 4; i++) {
    const px = hi - span * i / 4;
    const yy = y(px);
    ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(cssW - padR, yy); ctx.stroke();
    ctx.fillText(String(Math.round(px)), 4, yy + 4);
  }
  const slot = w / bars.length;
  bars.forEach((b, i) => {
    const x = padL + (i + 0.5) * slot;
    const up = b.c >= b.o;
    ctx.strokeStyle = up ? "#3dba7a" : "#e05a4c";
    ctx.fillStyle = ctx.strokeStyle;
    ctx.beginPath(); ctx.moveTo(x, y(b.h)); ctx.lineTo(x, y(b.l)); ctx.stroke();
    const top = y(Math.max(b.o, b.c)), bot = y(Math.min(b.o, b.c));
    const bw = Math.max(2, Math.min(16, slot * 0.62));
    ctx.fillRect(x - bw / 2, top, bw, Math.max(1, bot - top));
  });
  const labels = [0, Math.floor(bars.length / 2), bars.length - 1];
  ctx.fillStyle = "#8aa394";
  labels.forEach((i) => {
    ctx.fillText(String(bars[i].t || "").slice(0, 16), Math.max(padL, padL + (i + 0.5) * slot - 40), cssH - 8);
  });
}
async function loadS14Preview() {
  const tf = $("s14-tf").value || "1d";
  try {
    const meta = await api("/api/s14/meta");
    if (!meta.ok) {
      $("s14-meta").textContent = meta.hint || "Sheet not built yet";
      $("s14-head").innerHTML = "";
      $("s14-body").innerHTML = "";
      drawS14Chart([]);
      return;
    }
    $("s14-meta").textContent =
      `${meta.generated_at || ""} · ${meta.symbol || ""} · source=${meta.source || ""} · tabs=${(meta.tfs||[]).join(",")}`;
    const data = await api(`/api/s14/sheet?tf=${encodeURIComponent(tf)}`);
    paintS14Table(data.fields || [], data.rows || []);
    drawS14Chart(data.rows || []);
    if ((data.rows || []).length > 80) {
      s14Flash(`Showing first 80 of ${data.rows.length} rows — Open full sheet for all`);
    } else {
      s14Flash("");
    }
  } catch (e) {
    $("s14-meta").textContent = String(e.message || e);
    drawS14Chart([]);
  }
}
$("btn-s14-zip").onclick = () => {
  downloadUrl("/api/s14/sheet.zip");
  s14Flash("Downloading GoldPetal_S14 sheet ZIP (HTML + CSVs)");
};
$("btn-s14-copy").onclick = async () => {
  try {
    const tf = $("s14-tf").value || "1d";
    const data = await api(`/api/s14/sheet?tf=${encodeURIComponent(tf)}`);
    await navigator.clipboard.writeText(data.tsv || "");
    s14Flash(`Copied ${data.rows.length} ${tf} rows — paste into Google Sheets`);
  } catch (e) { s14Flash(String(e.message || e)); }
};
$("btn-s14-pull").onclick = async () => {
  try {
    s14Flash("Pulling Angel/MCX Gold Petal candles… keep this tab open");
    await api("/api/s14/refresh", { method: "POST", body: "{}" });
    for (let i = 0; i < 90; i++) {
      await new Promise((r) => setTimeout(r, 2000));
      const st = await api("/api/s14/refresh");
      if (!st.running) {
        await loadS14Preview();
        s14Flash(st.generated_at ? `Chart updated ${st.generated_at}` : "Pull finished");
        return;
      }
      s14Flash(`Still pulling from exchange… ${i * 2}s`);
    }
    s14Flash("Still running — wait and hard-refresh");
  } catch (e) { s14Flash(String(e.message || e)); }
};
$("s14-tf").onchange = () => loadS14Preview();
window.addEventListener("resize", () => loadS14Preview());
setInterval(() => loadS14Preview().catch(() => {}), 60000);

async function initExportDates() {
  const d = await api("/api/export/defaults");
  $("exp-from").value = d.date_from;
  $("exp-to").value = d.date_to;
  await refreshExportMeta();
}

refresh().then(() => loadBars(currentTf).catch(() => {})).catch(e => flash("Failed: " + e));
initExportDates().catch(e => expFlash(String(e.message || e)));
loadS14Preview().catch(() => {});
// Light poll often; full dashboard less often (tunnel-friendly).
setInterval(() => refreshLight().catch(() => {}), 5000);
setInterval(() => refresh().catch(() => {}), 30000);
</script>
</body>
</html>
"""


def _json_bytes(payload: Any, status: int = 200) -> tuple[int, bytes, str]:
    body = json.dumps(payload, default=str).encode("utf-8")
    return status, body, "application/json; charset=utf-8"


def _row_to_dict(row: Any) -> dict[str, Any]:
    if hasattr(row, "keys"):
        return {k: row[k] for k in row.keys()}
    return dict(row)


def dashboard_payload(tick_limit: int = 40, trade_limit: int = 40) -> dict[str, Any]:
    state = load_state()
    ticks = [_row_to_dict(r) for r in latest_ticks(limit=tick_limit)]
    # One DB trade rebuild for the whole dashboard (was 10× before — timed out over tunnel).
    all_trades = build_trades(strategy=None)
    closed = [t for t in all_trades if str(t.get("status", "")).startswith("CLOSED")]
    open_t = [t for t in all_trades if t.get("status") == "OPEN"]
    closed_sorted = list(reversed(closed))[:trade_limit]
    trades = closed_sorted + open_t[: max(0, trade_limit - len(closed_sorted))]

    strat_names = (
        "S1_NETDELTA",
        "S2_BALANCE",
        "S3_ML",
        "S4_OVERNIGHT",
        "S5_MINEDGE",
        "S6_MIN30",
        "S8_NET_ZIGZAG",
        "S9_STATE30",
        "S10_LEGACY30",
        "S11_DISCOVERED",
        "S12_HHHL30",
        "S13_HHHL_DAY",
        "S14_WICK30_STRICT",
        "S15_WICK30_NOWICK",
    )
    scoreboard = [summarize_trades(all_trades, s) for s in strat_names]
    scoreboard.append(summarize_trades(all_trades, None))

    live_ok, live_reason = is_live_mode_allowed()
    blocked = entries_blocked()
    # Cached only — do not re-run reasoner on every poll (slow).
    reasoning = load_reasoning()
    live_file = read_live_env()
    return {
        "state": state.to_dict(),
        "entries_blocked": list(blocked),
        "live_allowed": [live_ok, live_reason],
        "live_orders": recent_orders(limit=40),
        "live_env": {
            "dry_run": live_file["dry_run"],
            "lots": live_lots(),
            "max_lots": live_file["live_max_lots"],
            "producttype": (os.getenv("LIVE_PRODUCTTYPE", "CARRYFORWARD") or "CARRYFORWARD"),
        },
        "ltp": latest_ltp(),
        "tick_count": count_ticks(),
        "ticks": ticks,
        "trades": trades,
        "signals": [_row_to_dict(r) for r in latest_signals(limit=25)],
        "capital": capital_snapshot(),
        "proposals": proposals_snapshot(),
        "scoreboard": scoreboard,
        "reasoning": reasoning,
        "timeframes": panel_timeframes(),
        "live_desk": live_readiness(),
        "open_positions": [t for t in all_trades if t.get("status") == "OPEN"][:20],
        "where": {
            "host": "Same trading VM as run_strategy.py / supervise.sh",
            "url": "SSH tunnel → http://127.0.0.1:8788/",
            "ticks_db": "goldpetal/data/ticks.db",
            "bars": "Built on the fly from ticks: 1m,2m,3m,5m,10m,15m,30m,1h,4h,1d",
            "reasoning": "data/control/reasoning_latest.json",
        },
    }


class ControlHandler(BaseHTTPRequestHandler):
    server_version = "GoldPetalControl/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep stdout quiet; runner already logs heavily.
        return

    def _send(self, status: int, body: bytes, content_type: str, extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)
            if path in {"/", "/index.html"}:
                body = HTML_PAGE.encode("utf-8")
                self._send(200, body, "text/html; charset=utf-8")
                return
            if path in {"/s14-sheet", "/s14-sheet.html"}:
                html_path = S14_SHEET_DIR / HTML_NAME
                if html_path.is_file():
                    self._send(200, html_path.read_bytes(), "text/html; charset=utf-8")
                else:
                    self._send(
                        200,
                        missing_sheet_html().encode("utf-8"),
                        "text/html; charset=utf-8",
                    )
                return
            if path == "/api/s14/meta":
                status, body, ctype = _json_bytes(load_sheet_meta(S14_SHEET_DIR))
                self._send(status, body, ctype)
                return
            if path == "/api/s14/refresh":
                status, body, ctype = _json_bytes(refresh_status(S14_SHEET_DIR))
                self._send(status, body, ctype)
                return
            if path == "/api/s14/sheet":
                tf = ((qs.get("tf") or ["1d"])[0] or "1d").strip().lower()
                fields, rows = load_sheet_csv(f"{tf}.csv", S14_SHEET_DIR)
                tsv = s14_rows_to_tsv(rows, fields) if fields else ""
                status, body, ctype = _json_bytes(
                    {
                        "tf": tf,
                        "fields": fields,
                        "rows": rows,
                        "tsv": tsv,
                    }
                )
                self._send(status, body, ctype)
                return
            if path == "/api/s14/sheet.zip":
                if not (S14_SHEET_DIR / HTML_NAME).is_file() and not any(
                    S14_SHEET_DIR.glob("*.csv")
                ):
                    status, body, ctype = _json_bytes(
                        load_sheet_meta(S14_SHEET_DIR), 404
                    )
                    self._send(status, body, ctype)
                    return
                blob = sheet_zip_bytes(S14_SHEET_DIR)
                name = "GoldPetal_S14_sheet.zip"
                self._send(
                    200,
                    blob,
                    "application/zip",
                    {"Content-Disposition": f'attachment; filename="{name}"'},
                )
                return
            if path == "/api/dashboard":
                status, body, ctype = _json_bytes(dashboard_payload())
                self._send(status, body, ctype)
                return
            if path == "/api/status":
                status, body, ctype = _json_bytes(
                    {
                        "state": load_state().to_dict(),
                        "entries_blocked": list(entries_blocked()),
                        "live_allowed": list(is_live_mode_allowed()),
                        "live_desk": live_readiness(),
                        "ltp": latest_ltp(),
                        "tick_count": count_ticks(),
                    }
                )
                self._send(status, body, ctype)
                return
            if path == "/api/ticks":
                limit = int((qs.get("limit") or ["40"])[0])
                rows = [_row_to_dict(r) for r in latest_ticks(limit=limit)]
                status, body, ctype = _json_bytes({"ticks": rows, "count": count_ticks()})
                self._send(status, body, ctype)
                return
            if path == "/api/trades":
                limit = int((qs.get("limit") or ["40"])[0])
                trades = build_trades(strategy=None)
                closed = [t for t in trades if str(t.get("status", "")).startswith("CLOSED")]
                status, body, ctype = _json_bytes(
                    {"trades": list(reversed(closed))[:limit], "total_closed": len(closed)}
                )
                self._send(status, body, ctype)
                return
            if path == "/api/proposals":
                status, body, ctype = _json_bytes(proposals_snapshot())
                self._send(status, body, ctype)
                return
            if path == "/api/capital":
                status, body, ctype = _json_bytes(capital_snapshot())
                self._send(status, body, ctype)
                return
            if path == "/api/bars":
                tf = (qs.get("tf") or ["5m"])[0]
                limit = int((qs.get("limit") or ["60"])[0])
                status, body, ctype = _json_bytes(bars_for_panel(tf, limit=limit))
                self._send(status, body, ctype)
                return
            if path == "/api/reasoning":
                payload = load_reasoning() or {}
                status, body, ctype = _json_bytes(payload)
                self._send(status, body, ctype)
                return
            if path == "/api/timeframes":
                status, body, ctype = _json_bytes({"timeframes": panel_timeframes()})
                self._send(status, body, ctype)
                return
            if path == "/api/export/defaults":
                d0, d1 = default_date_range()
                status, body, ctype = _json_bytes({"date_from": d0, "date_to": d1})
                self._send(status, body, ctype)
                return
            if path == "/api/export/summary":
                d_from = (qs.get("from") or [""])[0]
                d_to = (qs.get("to") or [""])[0]
                status, body, ctype = _json_bytes(export_summary(d_from, d_to))
                self._send(status, body, ctype)
                return
            if path == "/api/export/ticks.csv":
                d_from = (qs.get("from") or [""])[0]
                d_to = (qs.get("to") or [""])[0]
                csv_text = export_ticks_csv(d_from, d_to)
                name = f"goldpetal_ticks_{d_from}_to_{d_to}.csv"
                self._send(
                    200,
                    csv_text.encode("utf-8"),
                    "text/csv; charset=utf-8",
                    {"Content-Disposition": f'attachment; filename="{name}"'},
                )
                return
            if path == "/api/export/trades.csv":
                d_from = (qs.get("from") or [""])[0]
                d_to = (qs.get("to") or [""])[0]
                csv_text = export_trades_csv(d_from, d_to)
                name = f"goldpetal_trades_{d_from}_to_{d_to}.csv"
                self._send(
                    200,
                    csv_text.encode("utf-8"),
                    "text/csv; charset=utf-8",
                    {"Content-Disposition": f'attachment; filename="{name}"'},
                )
                return
            if path == "/api/export/pack.zip":
                d_from = (qs.get("from") or [""])[0]
                d_to = (qs.get("to") or [""])[0]
                blob = export_pack_zip(d_from, d_to)
                name = f"goldpetal_export_{d_from}_to_{d_to}.zip"
                self._send(
                    200,
                    blob,
                    "application/zip",
                    {"Content-Disposition": f'attachment; filename="{name}"'},
                )
                return
            if path == "/api/export/tsv":
                d_from = (qs.get("from") or [""])[0]
                d_to = (qs.get("to") or [""])[0]
                kind = ((qs.get("kind") or ["trades"])[0] or "trades").strip().lower()
                if kind == "ticks":
                    rows = ticks_in_range(d_from, d_to)
                    tsv = rows_to_tsv(rows, TICK_CSV_FIELDS)
                else:
                    rows = trades_in_range(d_from, d_to)
                    tsv = rows_to_tsv(rows, TRADE_CSV_FIELDS)
                status, body, ctype = _json_bytes(
                    {"kind": kind, "rows": len(rows), "tsv": tsv, "from": d_from, "to": d_to}
                )
                self._send(status, body, ctype)
                return
            if path == "/api/sheets/pack.zip":
                blob = sheets_pack_zip_bytes()
                stamp = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y%m%d_%H%M%S")
                name = f"goldpetal_sheets_{stamp}.zip"
                self._send(
                    200,
                    blob,
                    "application/zip",
                    {"Content-Disposition": f'attachment; filename="{name}"'},
                )
                return
            if path == "/api/sheets/scoreboard.tsv":
                rows = build_scoreboard_rows()
                tsv = rows_to_tsv(rows, SCORE_FIELDS)
                status, body, ctype = _json_bytes(
                    {"kind": "scoreboard", "rows": len(rows), "tsv": tsv}
                )
                self._send(status, body, ctype)
                return
            self._send(*_json_bytes({"error": "not found"}, 404))
        except Exception as exc:
            self._send(*_json_bytes({"error": str(exc), "trace": traceback.format_exc()}, 500))

    def do_POST(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            data = self._read_json()

            if path == "/api/s14/refresh":
                res = start_angel_refresh(
                    root=ROOT,
                    python=sys.executable,
                    sheet_dir=S14_SHEET_DIR,
                )
                self._send(*_json_bytes(res))
                return
            if path == "/api/emergency":
                st = set_emergency(bool(data.get("off")))
                self._send(*_json_bytes({"ok": True, "state": st.to_dict()}))
                return
            if path == "/api/trading":
                st = set_trading_enabled(bool(data.get("enabled")))
                self._send(*_json_bytes({"ok": True, "state": st.to_dict()}))
                return
            if path == "/api/live":
                st = set_live_unlocked(bool(data.get("unlocked")))
                self._send(*_json_bytes({"ok": True, "state": st.to_dict()}))
                return
            if path == "/api/live/approved":
                names = [
                    str(s).strip()
                    for s in (data.get("strategies") or [])
                    if str(s).strip()
                ]
                st = set_live_approved(names, note="control panel live_approved")
                self._send(
                    *_json_bytes(
                        {
                            "ok": True,
                            "live_approved": list(st.live_approved),
                            "state": st.to_dict(),
                            "live_desk": live_readiness(),
                        }
                    )
                )
                return
            if path == "/api/live/enables":
                names = [
                    str(s).strip()
                    for s in (data.get("strategies") or [])
                    if str(s).strip()
                ]
                res = apply_panel_enables(names)
                status = 200 if res.get("ok") else 400
                res = {**res, "live_desk": live_readiness()}
                self._send(*_json_bytes(res, status))
                return
            if path == "/api/live/env":
                dry_raw = data.get("dry_run")
                dry_run = True if dry_raw is None else bool(dry_raw)
                try:
                    lots = int(data.get("live_max_lots") or 1)
                except (TypeError, ValueError):
                    self._send(*_json_bytes({"ok": False, "error": "LIVE_MAX_LOTS must be an integer"}, 400))
                    return
                res = apply_panel_live_env(
                    dry_run=dry_run,
                    live_max_lots=lots,
                    confirm=str(data.get("confirm") or ""),
                )
                status = 200 if res.get("ok") else 400
                res = {**res, "live_desk": live_readiness()}
                self._send(*_json_bytes(res, status))
                return
            if path == "/api/bot/restart":
                ok, why = panel_restart_allowed(str(data.get("confirm") or ""))
                if not ok:
                    self._send(*_json_bytes({"ok": False, "error": why}, 400))
                    return
                from analytics.bot_ops import restart_bot

                res = restart_bot()
                status_info = dict(res.get("status") or {})
                status_info.pop("log_tail", None)
                res["status"] = status_info
                res["live_desk"] = live_readiness()
                self._send(*_json_bytes(res))
                return
            if path == "/api/reasoning/refresh":
                payload = refresh_and_save(
                    in_position=bool(data.get("in_position", False)),
                    position_side=str(data.get("position_side") or "flat"),
                    open_pnl_pts=float(data.get("open_pnl_pts") or 0),
                )
                self._send(*_json_bytes(payload))
                return
            if path == "/api/capital":
                plan = load_capital()
                if "total_capital_inr" in data:
                    plan.total_capital_inr = float(data["total_capital_inr"])
                if "cash_reserve_pct" in data:
                    plan.cash_reserve_pct = float(data["cash_reserve_pct"])
                if "daily_loss_limit_inr" in data:
                    plan.daily_loss_limit_inr = float(data["daily_loss_limit_inr"])
                if "max_lots_total" in data:
                    plan.max_lots_total = int(data["max_lots_total"])
                save_capital(plan)
                self._send(*_json_bytes({"ok": True, "capital": capital_snapshot()}))
                return
            if path == "/api/capital/strategy":
                strategy = str(data.get("strategy") or "")
                if not strategy:
                    self._send(*_json_bytes({"error": "strategy required"}, 400))
                    return
                update_strategy_budget(
                    strategy,
                    budget_inr=float(data["budget_inr"]) if "budget_inr" in data else None,
                    max_lots=int(data["max_lots"]) if "max_lots" in data else None,
                    max_open_trades=int(data["max_open_trades"])
                    if "max_open_trades" in data
                    else None,
                    enabled=bool(data["enabled"]) if "enabled" in data else None,
                )
                self._send(*_json_bytes({"ok": True, "capital": capital_snapshot()}))
                return
            if path.startswith("/api/proposals/") and path.endswith("/decide"):
                proposal_id = path[len("/api/proposals/") : -len("/decide")]
                decision = str(data.get("decision") or "")
                note = str(data.get("note") or "")
                prop = decide_proposal(proposal_id, decision, note=note)
                self._send(*_json_bytes({"ok": True, "proposal": prop.to_dict()}))
                return

            self._send(*_json_bytes({"error": "not found"}, 404))
        except Exception as exc:
            self._send(*_json_bytes({"error": str(exc), "trace": traceback.format_exc()}, 500))


def main() -> None:
    ap = argparse.ArgumentParser(description="Gold Petal control panel")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()
    # Ensure default control files exist.
    load_state()
    load_capital()
    httpd = ThreadingHTTPServer((args.host, args.port), ControlHandler)
    print(f"Gold Petal control panel → http://{args.host}:{args.port}/", flush=True)
    print(
        "Endpoints: /api/dashboard /api/ticks /api/bars /api/reasoning "
        "/api/trades /api/proposals /api/capital /api/live/env /api/live/enables /api/bot/restart",
        flush=True,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped", flush=True)


if __name__ == "__main__":
    main()
