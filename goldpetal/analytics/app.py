#!/usr/bin/env python3
"""Gold Petal research & control desk (Streamlit).

Preferred: run ON the trading VM (local data/, no Mac sync):
  ./scripts/run_desk_vm.sh

Legacy Mac snapshot mode still works with analytics_mac + gcloud sync.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analytics.local_bridge import (  # noqa: E402
    decide_proposal_local,
    desk_data_dir,
    save_capital_local,
    save_live_allocation_local,
    save_paper_allowlist_local,
    set_control_local,
)
from analytics.vm_bridge import (  # noqa: E402
    VmConfig,
    decide_proposal_remote,
    save_capital_remote,
    save_live_allocation_remote,
    save_paper_allowlist_remote,
    set_control_remote,
    sync_snapshot,
)

DEFAULT_DATA = desk_data_dir()
LOCAL_DESK = os.getenv("GP_DESK_LOCAL", "").strip().lower() in {"1", "true", "yes", "y"} or (
    DEFAULT_DATA.resolve() == (ROOT / "data").resolve()
)

# Slim paper set (S4/S5/S8/S11/S12)
STRATEGIES = [
    "S4_OVERNIGHT",
    "S5_MINEDGE",
    "S8_NET_ZIGZAG",
    "S11_DISCOVERED",
    "S12_HHHL30",
]


def decide_proposal(pid: str, decision: str, note: str = "") -> dict:
    if LOCAL_DESK:
        try:
            return decide_proposal_local(pid, decision, note=note)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    return decide_proposal_remote(pid, decision, note=note)


def set_control(**kwargs):  # type: ignore[no-untyped-def]
    if LOCAL_DESK:
        try:
            return set_control_local(**kwargs)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    return set_control_remote(**kwargs)


def save_capital(payload: dict) -> dict:
    if LOCAL_DESK:
        try:
            return save_capital_local(payload)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    return save_capital_remote(payload)


def save_live_allocation(payload: dict) -> dict:
    if LOCAL_DESK:
        try:
            return save_live_allocation_local(payload)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    return save_live_allocation_remote(payload)


def save_paper_allowlist(payload: dict) -> dict:
    if LOCAL_DESK:
        try:
            return save_paper_allowlist_local(payload)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    return save_paper_allowlist_remote(payload)


def _strategies_as_rows(raw) -> list[dict]:
    if isinstance(raw, dict):
        rows = []
        for key, val in raw.items():
            if isinstance(val, dict):
                row = dict(val)
                row.setdefault("strategy", key)
                rows.append(row)
            else:
                rows.append({"strategy": str(key)})
        return rows
    if isinstance(raw, list):
        return [dict(x) for x in raw if isinstance(x, dict) and x.get("strategy")]
    return []


def data_dir() -> Path:
    return Path(
        st.session_state.get("data_dir") or DEFAULT_DATA
    ).expanduser()


def load_json(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


@st.cache_data(ttl=20)
def load_trades(db_path: str, lot_size: float = 1.0) -> pd.DataFrame:
    from storage import build_trades

    path = Path(db_path)
    if not path.exists():
        return pd.DataFrame()
    trades = build_trades(db_path=path, lot_size=lot_size)
    return pd.DataFrame(trades) if trades else pd.DataFrame()


@st.cache_data(ttl=20)
def load_scoreboard(db_path: str, lot_size: float = 1.0) -> pd.DataFrame:
    from paper_report import summarize_trades
    from storage import build_trades

    path = Path(db_path)
    if not path.exists():
        return pd.DataFrame()
    all_trades = build_trades(db_path=path, lot_size=lot_size)
    return pd.DataFrame(
        [summarize_trades(all_trades, s) for s in STRATEGIES + [None]]
    )


@st.cache_data(ttl=20)
def load_signals(db_path: str, limit: int = 400) -> pd.DataFrame:
    path = Path(db_path)
    if not path.exists():
        return pd.DataFrame()
    con = sqlite3.connect(path)
    try:
        return pd.read_sql_query(
            """
            SELECT time_label, strategy, action, position_after, cmp, reason, dry_run
            FROM signals ORDER BY id DESC LIMIT ?
            """,
            con,
            params=(limit,),
        )
    except Exception:
        return pd.DataFrame()
    finally:
        con.close()


@st.cache_data(ttl=20)
def load_ticks_tail(db_path: str, limit: int = 80) -> pd.DataFrame:
    path = Path(db_path)
    if not path.exists():
        return pd.DataFrame()
    con = sqlite3.connect(path)
    try:
        df = pd.read_sql_query(
            """
            SELECT received_at, ltp, bp, sp
            FROM ticks ORDER BY id DESC LIMIT ?
            """,
            con,
            params=(limit,),
        )
    except Exception:
        return pd.DataFrame()
    finally:
        con.close()
    if not df.empty and "ltp" in df.columns:
        med = float(df["ltp"].median())
        if med > 200_000:
            df["ltp"] = df["ltp"] / 100.0
    return df


@st.cache_data(ttl=20)
def load_tick_stats(db_path: str) -> dict:
    path = Path(db_path)
    if not path.exists():
        return {}
    con = sqlite3.connect(path)
    try:
        n = con.execute("SELECT COUNT(*) FROM ticks").fetchone()[0]
        last = con.execute(
            "SELECT received_at, ltp FROM ticks ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except Exception:
        return {}
    finally:
        con.close()
    ltp = float(last[1]) if last and last[1] is not None else None
    if ltp is not None and ltp > 200_000:
        ltp /= 100.0
    return {
        "n_ticks": int(n),
        "last_ts": last[0] if last else None,
        "last_ltp": ltp,
    }


def inject_style() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700&family=Source+Sans+3:wght@400;600&display=swap');
        html, body, [class*="css"] { font-family: 'Source Sans 3', sans-serif; }
        h1, h2, h3 { font-family: 'Fraunces', serif !important; letter-spacing: -0.02em; }
        .block-container { padding-top: 1rem; max-width: 1200px; }
        div[data-testid="stMetricValue"] { font-family: 'Fraunces', serif; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def tab_overview(dd: Path, db: Path, *, lot_size: float = 1.0) -> None:
    st.subheader("Overview")
    st.caption(
        "Strategies ride fee-free while IGNORE_FEES=true. "
        "Closed trades still show Angel charges + 30% tax below (post-trade)."
    )
    stats = load_tick_stats(str(db)) if db.exists() else {}
    state = load_json(dd / "control" / "state.json") or {}
    flags = (dd / "env.flags").read_text(encoding="utf-8") if (dd / "env.flags").exists() else ""

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Ticks", f"{stats.get('n_ticks', 0):,}")
    c2.metric("LTP", f"{stats.get('last_ltp') or '—'}")
    c3.metric(
        "Emergency",
        "OFF" if state.get("emergency_off") else "clear",
    )
    c4.metric("Trading", "ON" if state.get("trading_enabled", True) else "paused")

    c5, c6, c7 = st.columns(3)
    c5.metric("Live unlocked", "yes" if state.get("live_unlocked") else "locked")
    c6.metric("Paper approved", ", ".join(state.get("paper_approved") or []) or "—")
    c7.metric("Live approved", ", ".join(state.get("live_approved") or []) or "—")

    if flags:
        with st.expander("VM .env flags", expanded=False):
            st.code(flags, language="bash")

    if db.exists():
        board = load_scoreboard(str(db), lot_size=lot_size)
        if not board.empty:
            all_row = board[board["strategy"] == "ALL"]
            if not all_row.empty:
                r = all_row.iloc[0]
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Gross PnL ₹", f"{float(r.get('gross_pnl') or 0):,.0f}")
                m2.metric("Charges ₹", f"{float(r.get('charges') or 0):,.0f}")
                m3.metric("Tax ₹", f"{float(r.get('tax') or 0):,.0f}")
                m4.metric("After-tax ₹", f"{float(r.get('pnl_after_tax') or 0):,.0f}")
            cols = [
                c
                for c in (
                    "strategy",
                    "trades",
                    "closed",
                    "open",
                    "win_rate",
                    "gross_pnl",
                    "charges",
                    "pnl_after_charges",
                    "tax",
                    "pnl_after_tax",
                )
                if c in board.columns
            ]
            st.subheader(f"PnL board · {lot_size:g} lot(s)")
            st.dataframe(board[cols], use_container_width=True, hide_index=True)
            try:
                import plotly.express as px

                plot_df = board[board["strategy"] != "ALL"]
                if not plot_df.empty and "gross_pnl" in plot_df.columns:
                    fig = px.bar(
                        plot_df,
                        x="strategy",
                        y=["gross_pnl", "pnl_after_tax"],
                        barmode="group",
                        title="Gross vs after-tax PnL by strategy",
                    )
                    fig.update_layout(
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(0,0,0,0)",
                    )
                    st.plotly_chart(fig, use_container_width=True)
            except Exception:
                pass
    else:
        st.info("Sync ticks.db to see PnL (uncheck skip-db).")


def tab_proposals(dd: Path) -> None:
    st.subheader("Weekend proposals — ML / new & improved strategies")
    st.caption(
        "Approve → paper applies on the VM. Live approve only marks the list; "
        "live trading still needs Unlock + DRY_RUN=false on the VM."
    )
    raw = load_json(dd / "control" / "proposals.json")
    items = (raw or {}).get("proposals") if isinstance(raw, dict) else (raw or [])
    if not items:
        st.warning("No proposals synced. Run weekly jobs on VM, then Sync.")
        return

    pending = [p for p in items if p.get("status") == "pending"]
    decided = [p for p in items if p.get("status") != "pending"]

    if not pending:
        st.success("No pending proposals.")
    for p in pending:
        with st.container(border=True):
            st.markdown(f"### {p.get('title') or p.get('strategy')}")
            st.write(p.get("summary") or "")
            meta = (
                f"`{p.get('id')}` · {p.get('kind')} · {p.get('strategy')} · "
                f"safety_ok={p.get('safety_ok')} · week={p.get('week_id')}"
            )
            st.caption(meta)
            if p.get("safety_reasons"):
                st.write("Safety:", ", ".join(map(str, p["safety_reasons"])))
            paper = p.get("paper") or {}
            st.write(
                f"Paper: trades={paper.get('n_trades')} win={paper.get('win_rate')} "
                f"gross={paper.get('gross_pnl_inr')} after_tax={paper.get('after_tax_pnl_inr')}"
            )
            if p.get("env_patch"):
                st.code(json.dumps(p["env_patch"], indent=2), language="json")
            note = st.text_input(
                "Decision note",
                key=f"note_{p['id']}",
                value="",
            )
            b1, b2, b3 = st.columns(3)
            if b1.button("Approve → paper", key=f"ap_{p['id']}", type="primary"):
                if p.get("safety_ok") is False:
                    st.warning("safety_ok=False — only approve if you accept the risk.")
                res = decide_proposal(p["id"], "approved_paper", note=note)
                if res.get("ok"):
                    st.success(
                        f"Approved paper: {res.get('strategy')}. "
                        f"Apply env_patch on VM .env then restart supervise."
                    )
                    st.json(res.get("env_patch") or {})
                    st.cache_data.clear()
                else:
                    st.error(res.get("error") or res)
            if b2.button("Approve → live", key=f"al_{p['id']}"):
                ok = st.session_state.get(f"live_confirm_{p['id']}", False)
                if not ok:
                    st.session_state[f"live_confirm_{p['id']}"] = True
                    st.warning("Click again to confirm LIVE approve.")
                else:
                    res = decide_proposal(p["id"], "approved_live", note=note)
                    st.session_state[f"live_confirm_{p['id']}"] = False
                    if res.get("ok"):
                        st.success("Marked live-approved (still locked until unlock + DRY_RUN=false).")
                    else:
                        st.error(res.get("error") or res)
            if b3.button("Reject", key=f"rj_{p['id']}"):
                res = decide_proposal(p["id"], "rejected", note=note)
                if res.get("ok"):
                    st.success("Rejected on VM.")
                    st.cache_data.clear()
                else:
                    st.error(res.get("error") or res)

    if decided:
        st.subheader("Recent decisions")
        st.dataframe(pd.DataFrame(decided), use_container_width=True, hide_index=True)


def tab_control(dd: Path) -> None:
    st.subheader("Bot control (writes to VM)")
    state = load_json(dd / "control" / "state.json") or {}
    st.json(state)

    c1, c2, c3 = st.columns(3)
    if c1.button("EMERGENCY OFF", type="primary"):
        res = set_control(emergency_off=True)
        st.write(res)
    if c2.button("Clear emergency"):
        res = set_control(emergency_off=False)
        st.write(res)
    if c3.button("Pause trading"):
        res = set_control(trading_enabled=False)
        st.write(res)

    d1, d2, d3 = st.columns(3)
    if d1.button("Resume trading"):
        res = set_control(trading_enabled=True)
        st.write(res)
    if d2.button("Unlock live (dangerous)"):
        st.session_state["unlock_arm"] = True
        st.warning("Click Confirm unlock next.")
    if d3.button("Confirm unlock") and st.session_state.get("unlock_arm"):
        res = set_control(live_unlocked=True)
        st.session_state["unlock_arm"] = False
        st.write(res)
    if st.button("Lock live"):
        res = set_control(live_unlocked=False)
        st.write(res)

    st.caption("After control changes: Sync again to refresh the snapshot.")


def tab_capital(dd: Path) -> None:
    st.subheader("Capital")
    cap = load_json(dd / "control" / "capital.json")
    if not cap:
        st.info("No capital.json yet — Live Deploy or Push will create it.")
        cap = {}
    total = st.number_input(
        "Total capital ₹",
        value=float(cap.get("total_capital_inr") or 500000),
        step=1000.0,
    )
    reserve = st.number_input(
        "Cash reserve %",
        value=float(cap.get("cash_reserve_pct") or 10),
        step=1.0,
    )
    dayloss = st.number_input(
        "Day loss limit ₹",
        value=float(cap.get("daily_loss_limit_inr") or cap.get("day_loss_limit_inr") or 5000),
        step=500.0,
    )
    maxlots = st.number_input(
        "Max lots total",
        value=int(cap.get("max_lots_total") or 10),
        step=1,
    )
    strategies = _strategies_as_rows(cap.get("strategies"))
    if strategies:
        st.dataframe(pd.DataFrame(strategies), use_container_width=True, hide_index=True)
        st.caption("Edit per-strategy ₹ / lots on the Live Deploy tab.")
    if st.button("Push capital to VM", type="primary"):
        payload = {
            "total_capital_inr": total,
            "cash_reserve_pct": reserve,
            "daily_loss_limit_inr": dayloss,
            "max_lots_total": int(maxlots),
            "strategies": strategies,
        }
        res = save_capital(payload)
        if res.get("ok"):
            st.success("Capital saved.")
            st.cache_data.clear()
        else:
            st.error(res.get("error") or res)


def tab_live_deploy(dd: Path) -> None:
    st.subheader("Live deploy — pick strategies + capital")
    st.caption(
        "This sets which strategies may place **real Angel orders**, and how much ₹ / lots "
        "each may use. Paper (DRY_RUN) is separate. Still required after Save: "
        "**Unlock live** (Control tab) + `DRY_RUN=false` on the VM `.env`, then restart supervise."
    )
    state = load_json(dd / "control" / "state.json") or {}
    cap = load_json(dd / "control" / "capital.json") or {}
    live_set = set(state.get("live_approved") or [])
    force_disabled = set(state.get("force_disabled") or [])
    strat_map = {
        row["strategy"]: row for row in _strategies_as_rows(cap.get("strategies"))
    }

    st.markdown("### Paper allowlist (stop S9 / others)")
    st.caption(
        "Trades tab can still show **old** S9 rows from history. "
        "This lock blocks **new** entries for everything not checked. "
        "Also set `ENABLE_S9=false` (and S1/S2/S3/S6/S10) in `.env` + restart supervise."
    )
    paper_default = [s for s in STRATEGIES if s not in force_disabled] or list(STRATEGIES)
    paper_pick = st.multiselect(
        "Strategies allowed to paper-trade",
        options=list(STRATEGIES),
        default=paper_default,
        key="paper_allow_pick",
    )
    if st.button("Lock paper to selected only", type="primary", key="paper_lock_btn"):
        res = save_paper_allowlist(
            {
                "paper_allowlist": paper_pick,
                "note": f"desk paper lock: {', '.join(paper_pick)}",
            }
        )
        if res.get("ok"):
            st.success(
                f"Paper allowlist saved. Force-disabled: "
                f"{', '.join(res.get('force_disabled') or []) or 'none'}"
            )
            st.warning(res.get("reminder") or "")
            st.cache_data.clear()
        else:
            st.error(res.get("error") or res)
    if force_disabled:
        st.info(f"Currently force-disabled: {', '.join(sorted(force_disabled))}")

    c1, c2, c3 = st.columns(3)
    c1.metric("Live unlocked", "yes" if state.get("live_unlocked") else "locked")
    c2.metric("Live approved", ", ".join(state.get("live_approved") or []) or "—")
    c3.metric("Trading", "ON" if state.get("trading_enabled", True) else "paused")

    total = st.number_input(
        "Book capital ₹",
        value=float(cap.get("total_capital_inr") or 500000),
        step=1000.0,
        key="live_total_cap",
    )
    reserve = st.number_input(
        "Cash reserve %",
        value=float(cap.get("cash_reserve_pct") or 20),
        step=1.0,
        key="live_reserve",
    )
    dayloss = st.number_input(
        "Day loss limit ₹ (stops new entries)",
        value=float(cap.get("daily_loss_limit_inr") or 5000),
        step=500.0,
        key="live_dayloss",
    )
    max_total = st.number_input(
        "Max lots across all strategies",
        value=int(cap.get("max_lots_total") or 10),
        step=1,
        key="live_max_total",
    )

    st.markdown("### Strategies for real trades")
    st.caption(
        "Check Live → that strategy is added to `live_approved`. "
        "₹ budget gates entries; **max lots** is the live order size (hard-capped by "
        "`LIVE_MAX_LOTS` in `.env`, default 1)."
    )
    allocations: list[dict] = []
    live_names: list[str] = []
    for name in STRATEGIES:
        row = strat_map.get(name) or {}
        with st.container(border=True):
            left, mid, right = st.columns([1.4, 1.2, 1.2])
            live_on = left.checkbox(
                f"Live · {name}",
                value=name in live_set,
                key=f"live_on_{name}",
            )
            budget = mid.number_input(
                "Capital ₹",
                min_value=0.0,
                value=float(row.get("budget_inr") or 50_000),
                step=1000.0,
                key=f"live_budget_{name}",
            )
            lots = right.number_input(
                "Max lots",
                min_value=1,
                value=int(row.get("max_lots") or 1),
                step=1,
                key=f"live_lots_{name}",
            )
            if live_on:
                live_names.append(name)
                allocations.append(
                    {
                        "strategy": name,
                        "budget_inr": float(budget),
                        "max_lots": int(lots),
                        "max_open_trades": int(row.get("max_open_trades") or 1),
                        "enabled": True,
                        "live": True,
                    }
                )

    lock_paper_with_live = st.checkbox(
        "Also lock paper to the Live-checked strategies only",
        value=False,
        key="lock_paper_with_live",
    )
    disable_others = st.checkbox(
        "Disable capital for strategies not selected (blocks their new entries too)",
        value=False,
    )
    confirm = st.checkbox(
        "I understand this arms real-money strategies (still needs Unlock + DRY_RUN=false)",
        value=False,
    )

    b1, b2 = st.columns(2)
    if b1.button("Save live allocation", type="primary", disabled=not confirm):
        if not live_names:
            st.warning("No strategies checked — this clears live_approved.")
        payload = {
            "live_approved": live_names,
            "allocations": allocations,
            "total_capital_inr": float(total),
            "cash_reserve_pct": float(reserve),
            "daily_loss_limit_inr": float(dayloss),
            "max_lots_total": int(max_total),
            "disable_others": bool(disable_others),
            "note": f"desk live: {', '.join(live_names) or 'none'}",
        }
        if lock_paper_with_live:
            payload["paper_allowlist"] = list(live_names)
        res = save_live_allocation(payload)
        if res.get("ok"):
            st.success(
                f"Live approved: {', '.join(res.get('live_approved') or []) or 'none'}"
            )
            if res.get("force_disabled"):
                st.info(
                    f"Force-disabled for paper: {', '.join(res.get('force_disabled') or [])}"
                )
            if not res.get("live_mode_ok"):
                st.warning(
                    f"Gates not ready for Angel orders yet: {res.get('live_mode_reason')}. "
                    f"{res.get('reminder')}"
                )
            else:
                st.info(res.get("reminder") or "Live mode OK.")
            st.cache_data.clear()
        else:
            st.error(res.get("error") or res)

    if b2.button("Clear all live approvals"):
        res = save_live_allocation(
            {
                "live_approved": [],
                "allocations": [],
                "note": "desk cleared live_approved",
            }
        )
        if res.get("ok"):
            st.success("Cleared live_approved.")
            st.cache_data.clear()
        else:
            st.error(res.get("error") or res)

    with st.expander("How live sizing works", expanded=False):
        st.markdown(
            """
1. Check **Live** on S4 / S5 / S12 (or others) and set each **Capital ₹** + **Max lots**.
2. Click **Save live allocation**.
3. On **Control**: Unlock live (two-step).
4. On VM `.env`: `DRY_RUN=false` and set `LIVE_MAX_LOTS` to your hard ceiling (e.g. `5`).
5. Restart `supervise.sh`. Only `live_approved` strategies place Angel orders;
   size = min(strategy max_lots, LIVE_MAX_LOTS).

To stop S9/S1/etc paper completely: use **Lock paper to selected only** above, then on VM:
`ENABLE_S9=false` (and other non-slim) + restart supervise.
"""
        )


def tab_ml(dd: Path) -> None:
    st.subheader("ML models & discovery")
    for name, rel in (
        ("Tick models report", "models/report.json"),
        ("Overnight report", "models/overnight_report.json"),
        ("Behavior", "discover/behavior_report.json"),
        ("Discovery latest", "discover/latest_report.json"),
    ):
        data = load_json(dd / rel)
        with st.expander(name, expanded=(name == "Behavior")):
            if not data:
                st.caption("Not synced yet.")
            elif isinstance(data, dict) and data.get("summary"):
                st.write(data["summary"])
                st.json(data)
            else:
                st.json(data)

    st.markdown(
        """
**Weekly on VM (cron):**
- `weekly_s8_nn.sh` · `weekly_s4.sh` · `weekly_s5.sh` · `weekly_discover.sh`

They write proposals → Sync → approve here.
"""
    )


def tab_reasoning(dd: Path) -> None:
    st.subheader("Reasoning cockpit snapshot")
    data = load_json(dd / "control" / "reasoning_latest.json")
    if not data:
        st.info("No reasoning_latest.json — optional on VM panel refresh.")
        return
    st.json(data)


def tab_trades(db: Path, *, lot_size: float = 1.0) -> None:
    if not db.exists():
        st.info("Need ticks.db.")
        return
    trades = load_trades(str(db), lot_size=lot_size)
    if trades.empty:
        st.info("No trades.")
        return
    st.caption(
        f"Post-trade Angel fees + tax at {lot_size:g} lot(s). "
        "Gross = points × lots; after-tax = gross − charges − tax. "
        "Old S9/S1/… rows stay in history until you filter them out."
    )
    all_strats = sorted(trades["strategy"].dropna().unique())
    slim_present = [s for s in STRATEGIES if s in all_strats]
    strat = st.multiselect(
        "Strategy",
        options=all_strats,
        default=slim_present or None,
        key="trades_strat_filter",
    )
    view = trades if not strat else trades[trades["strategy"].isin(strat)]
    if st.checkbox("Today only"):
        tday = date.today().isoformat()
        view = view[
            view["entry_ts"].astype(str).str.startswith(tday)
            | view["exit_ts"].astype(str).str.startswith(tday)
        ]
    closed = view[view["status"].astype(str).str.startswith("CLOSED")] if "status" in view.columns else view
    if not closed.empty:
        def _sum(col: str) -> float:
            if col not in closed.columns:
                return 0.0
            return float(pd.to_numeric(closed[col], errors="coerce").fillna(0).sum())

        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Gross ₹", f"{_sum('gross_pnl'):,.0f}")
        s2.metric("Charges ₹", f"{_sum('charges'):,.0f}")
        s3.metric("Tax ₹", f"{_sum('tax'):,.0f}")
        s4.metric("After-tax ₹", f"{_sum('pnl_after_tax'):,.0f}")
    cols = [
        c
        for c in (
            "strategy",
            "status",
            "side",
            "entry_ts",
            "entry_price",
            "exit_ts",
            "exit_price",
            "gross_pnl",
            "charges",
            "pnl_after_charges",
            "tax",
            "pnl_after_tax",
            "entry_reason",
            "exit_reason",
        )
        if c in view.columns
    ]
    st.dataframe(view[cols], use_container_width=True, hide_index=True, height=480)


def tab_signals_ticks(db: Path) -> None:
    if not db.exists():
        st.info("Need ticks.db.")
        return
    a, b = st.tabs(["Signals", "Ticks"])
    with a:
        st.dataframe(
            load_signals(str(db)),
            use_container_width=True,
            hide_index=True,
            height=420,
        )
    with b:
        st.dataframe(
            load_ticks_tail(str(db)),
            use_container_width=True,
            hide_index=True,
            height=420,
        )


def tab_live_orders(dd: Path) -> None:
    path = dd / "control" / "live_orders.jsonl"
    if not path.exists():
        st.info("No live_orders.jsonl (normal while DRY_RUN).")
        return
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not rows:
        st.info("Empty.")
        return
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def main() -> None:
    st.set_page_config(
        page_title="Gold Petal Desk",
        page_icon="◆",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    inject_style()

    st.sidebar.title("Gold Petal desk")
    if LOCAL_DESK:
        st.sidebar.caption("VM local · live data/ · no Mac sync")
    else:
        st.sidebar.caption("Mac snapshot · sync from VM")
    dd = Path(
        st.sidebar.text_input("Data folder", value=str(DEFAULT_DATA))
    ).expanduser()
    st.session_state["data_dir"] = str(dd)
    db = dd / "ticks.db"

    lot_size = float(
        st.sidebar.number_input(
            "Lots (fee display)",
            min_value=1.0,
            value=100.0 if LOCAL_DESK else 1.0,
            step=1.0,
            help="Scales gross PnL and Angel fees on the desk display.",
        )
    )
    st.sidebar.caption(
        "IGNORE_FEES rides freely. Closed trades always show fees+tax here."
    )

    if LOCAL_DESK:
        if st.sidebar.button("Refresh", type="primary"):
            st.cache_data.clear()
            st.sidebar.success("Cache cleared")
    else:
        cfg = VmConfig.from_env()
        st.sidebar.text_input("VM", value=cfg.vm, key="vm_name")
        st.sidebar.text_input("Zone", value=cfg.zone, key="vm_zone")
        cfg = VmConfig(
            vm=st.session_state.get("vm_name", cfg.vm),
            zone=st.session_state.get("vm_zone", cfg.zone),
            remote_dir=cfg.remote_dir,
        )
        skip_db = st.sidebar.checkbox("Light sync (skip ticks.db)", value=True)
        if st.sidebar.button("Sync from VM", type="primary"):
            with st.spinner("Syncing…"):
                os.environ["GP_VM"] = cfg.vm
                os.environ["GP_ZONE"] = cfg.zone
                code, out = sync_snapshot(dd, cfg, skip_db=skip_db)
            st.sidebar.code(out[-2000:] if out else f"exit {code}")
            st.cache_data.clear()
            if code == 0:
                st.sidebar.success("Synced")
            else:
                st.sidebar.error(f"sync exit {code}")

    if st.sidebar.button("Clear cache"):
        st.cache_data.clear()

    st.title("Gold Petal research & control desk")
    mode = "VM local data" if LOCAL_DESK else "Mac snapshot"
    st.caption(f"{mode} · strategies S4/S5/S8/S11/S12 · {date.today().isoformat()}")

    if not dd.exists():
        st.warning("Data folder missing.")
        return

    (
        t0,
        t1,
        t2,
        t_live,
        t3,
        t4,
        t5,
        t6,
        t7,
        t8,
    ) = st.tabs(
        [
            "Overview",
            "Proposals / ML",
            "Control",
            "Live Deploy",
            "Capital",
            "Models",
            "Reasoning",
            "Trades",
            "Signals / Ticks",
            "Live orders",
        ]
    )
    with t0:
        tab_overview(dd, db, lot_size=lot_size)
    with t1:
        tab_proposals(dd)
    with t2:
        tab_control(dd)
    with t_live:
        tab_live_deploy(dd)
    with t3:
        tab_capital(dd)
    with t4:
        tab_ml(dd)
    with t5:
        tab_reasoning(dd)
    with t6:
        tab_trades(db, lot_size=lot_size)
    with t7:
        tab_signals_ticks(db)
    with t8:
        tab_live_orders(dd)

    log_path = dd / "logs" / "strategy_run.log"
    if log_path.exists():
        with st.expander("strategy_run.log (tail)", expanded=False):
            text = log_path.read_text(encoding="utf-8", errors="replace")
            st.code("\n".join(text.splitlines()[-100:]), language="text")


if __name__ == "__main__":
    main()
