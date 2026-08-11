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
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from capital import (
    capital_snapshot,
    load_capital,
    save_capital,
    update_strategy_budget,
)
from control_state import (
    entries_blocked,
    is_live_mode_allowed,
    load_state,
    save_state,
    set_emergency,
    set_live_unlocked,
    set_trading_enabled,
)
from live_orders import live_lots, recent_orders
from paper_report import _summarize
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
from proposals import decide_proposal, proposals_snapshot
from reasoning_cockpit import (
    bars_for_panel,
    load_reasoning,
    panel_timeframes,
    refresh_and_save,
)
from storage import build_trades, count_ticks, latest_ltp, latest_signals, latest_ticks

ROOT = Path(__file__).resolve().parent


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
input[type="date"] {
  background: var(--bg0); border: 1px solid var(--line);
  color: var(--text); padding: .35rem .45rem; border-radius: 3px;
  font-family: "IBM Plex Mono", monospace;
}
.flash { margin: .5rem 0 0; color: var(--gold2); font-size: .85rem; min-height: 1.2em; }
</style>
</head>
<body>
  <header class="brand">
    <h1>Gold Petal</h1>
    <p class="tag">Control + reasoning cockpit on your trading VM. See ticks, 1m→day bars, entry/hold/exit plans, capital, and weekend approvals. Nothing goes live without you.</p>
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
      <p class="muted" style="margin-top:.75rem">Host: same GCP/VM as <span class="mono">run_strategy.py</span>. Open <span class="mono">http://&lt;vm-ip&gt;:8787/</span>. Emergency blocks every new entry. Live path: Unlock live + <span class="mono">DRY_RUN=false</span> + Approve → live per strategy. Orders go through <span class="mono">live_orders.py</span> (default 1 lot).</p>
    </section>

    <section class="panel span-7">
      <h2>Capital management</h2>
      <div class="row" id="capital-stats"></div>
      <div class="scroll" style="margin-top:.85rem">
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
      <div id="proposals"></div>
    </section>
  </div>

<script>
const $ = (id) => document.getElementById(id);
const flash = (msg) => { $("flash").textContent = msg || ""; };

async function api(path, opts) {
  const res = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, opts || {}));
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
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
  $("trades-meta").textContent = meta;
  $("trades-body").innerHTML = (trades || []).map(t => `
    <tr>
      <td>${t.strategy || ""}</td>
      <td>${t.side || ""}</td>
      <td>${t.entry_price ?? ""}</td>
      <td>${t.exit_price ?? ""}</td>
      <td>${t.pnl_after_tax !== "" && t.pnl_after_tax != null ? money(t.pnl_after_tax) : (t.net_pnl ?? "")}</td>
      <td>${t.status || ""}</td>
    </tr>`).join("") || `<tr><td colspan="6">No finished trades</td></tr>`;
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
      try {
        await api(`/api/proposals/${btn.dataset.id}/decide`, {
          method: "POST",
          body: JSON.stringify({ decision: btn.dataset.dec })
        });
        flash(`Proposal ${btn.dataset.dec}`);
        await refresh();
      } catch (e) { flash(String(e.message || e)); }
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
  const data = await api("/api/dashboard");
  renderStatus(data);
  renderCapital(data.capital);
  renderTicks(data.ticks, `${data.tick_count} ticks stored · showing latest ${data.ticks.length}`);
  renderTrades(data.trades, `Closed/open from signals · showing latest ${data.trades.length}`);
  renderLiveOrders(data);
  renderProposals(data.proposals);
  renderScore(data.scoreboard);
  renderReasoning(data.reasoning);
  renderTfButtons(data.timeframes || []);
  await loadBars(currentTf);
}

$("btn-emergency").onclick = async () => {
  const s = await api("/api/status");
  const off = !s.state.emergency_off;
  await api("/api/emergency", { method: "POST", body: JSON.stringify({ off }) });
  flash(off ? "EMERGENCY OFF engaged" : "Emergency cleared");
  await refresh();
};
$("btn-trading").onclick = async () => {
  const s = await api("/api/status");
  const enabled = !s.state.trading_enabled;
  await api("/api/trading", { method: "POST", body: JSON.stringify({ enabled }) });
  flash(enabled ? "Trading enabled" : "Trading disabled");
  await refresh();
};
$("btn-live").onclick = async () => {
  const s = await api("/api/status");
  const unlocked = !s.state.live_unlocked;
  await api("/api/live", { method: "POST", body: JSON.stringify({ unlocked }) });
  flash(unlocked ? "Live unlocked (still need DRY_RUN=false + Approve → live)" : "Live locked");
  await refresh();
};
$("btn-refresh").onclick = () => refresh().catch(e => flash(String(e)));
$("btn-reason").onclick = async () => {
  try {
    const r = await api("/api/reasoning/refresh", { method: "POST", body: "{}" });
    renderReasoning(r);
    flash(`Reasoner: ${r.recommended}`);
  } catch (e) { flash(String(e.message || e)); }
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

async function initExportDates() {
  const d = await api("/api/export/defaults");
  $("exp-from").value = d.date_from;
  $("exp-to").value = d.date_to;
  await refreshExportMeta();
}

refresh().catch(e => flash(String(e)));
initExportDates().catch(e => expFlash(String(e.message || e)));
setInterval(() => refresh().catch(() => {}), 5000);
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
    all_trades = build_trades(strategy=None)
    # Prefer finished (closed) first, then open.
    closed = [t for t in all_trades if str(t.get("status", "")).startswith("CLOSED")]
    open_t = [t for t in all_trades if t.get("status") == "OPEN"]
    closed_sorted = list(reversed(closed))[:trade_limit]
    trades = closed_sorted + open_t[: max(0, trade_limit - len(closed_sorted))]

    scoreboard = []
    for strat in (
        "S1_NETDELTA",
        "S2_BALANCE",
        "S3_ML",
        "S4_OVERNIGHT",
        "S5_MINEDGE",
        "S6_MIN30",
        "S8_NET_ZIGZAG",
        "S9_STATE30",
        "S10_LEGACY30",
        None,
    ):
        scoreboard.append(_summarize(strat))

    live_ok, live_reason = is_live_mode_allowed()
    blocked = entries_blocked()
    reasoning = load_reasoning()
    if reasoning is None:
        try:
            reasoning = refresh_and_save()
        except Exception:
            reasoning = None
    dry = os.getenv("DRY_RUN", "true").strip().lower() in {"1", "true", "yes", "y"}
    return {
        "state": state.to_dict(),
        "entries_blocked": list(blocked),
        "live_allowed": [live_ok, live_reason],
        "live_orders": recent_orders(limit=40),
        "live_env": {
            "dry_run": dry,
            "lots": live_lots(),
            "max_lots": int(os.getenv("LIVE_MAX_LOTS", "1") or "1"),
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
        "where": {
            "host": "Same trading VM as run_strategy.py / supervise.sh",
            "url": "http://<vm-ip>:8787/",
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
            self._send(*_json_bytes({"error": "not found"}, 404))
        except Exception as exc:
            self._send(*_json_bytes({"error": str(exc), "trace": traceback.format_exc()}, 500))

    def do_POST(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            data = self._read_json()

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
        "/api/trades /api/proposals /api/capital",
        flush=True,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped", flush=True)


if __name__ == "__main__":
    main()
