#!/usr/bin/env python3
"""Gold Petal Mac desk — full control-panel replacement (runs on Mac only).

Covers: PnL, trades, signals, ticks summary, capital, weekend proposals
(approve/reject via VM), emergency/trading switches, ML/discovery, reasoning.

  ./scripts/sync_analytics_mac.sh
  streamlit run analytics/app.py

Writes (approve / emergency) go to the GCP VM over gcloud ssh — the bot stays
the source of truth; this app never needs to run on the trading VM.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analytics.vm_bridge import (  # noqa: E402
    VmConfig,
    decide_proposal_remote,
    save_capital_remote,
    set_control_remote,
    sync_snapshot,
)

DEFAULT_DATA = ROOT / "data" / "analytics_mac"
STRATEGIES = [
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
]


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
def load_trades(db_path: str) -> pd.DataFrame:
    from storage import build_trades

    path = Path(db_path)
    if not path.exists():
        return pd.DataFrame()
    trades = build_trades(db_path=path)
    return pd.DataFrame(trades) if trades else pd.DataFrame()


@st.cache_data(ttl=20)
def load_scoreboard(db_path: str) -> pd.DataFrame:
    from paper_report import summarize_trades
    from storage import build_trades

    path = Path(db_path)
    if not path.exists():
        return pd.DataFrame()
    all_trades = build_trades(db_path=path)
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


def tab_overview(dd: Path, db: Path) -> None:
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
        board = load_scoreboard(str(db))
        if not board.empty:
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
                    "pnl_after_tax",
                )
                if c in board.columns
            ]
            st.subheader("PnL board")
            st.dataframe(board[cols], use_container_width=True, hide_index=True)
            try:
                import plotly.express as px

                plot_df = board[board["strategy"] != "ALL"]
                if not plot_df.empty and "gross_pnl" in plot_df.columns:
                    fig = px.bar(
                        plot_df,
                        x="strategy",
                        y="gross_pnl",
                        title="Gross PnL by strategy",
                        color="gross_pnl",
                        color_continuous_scale=["#6b2d2d", "#c4c0b0", "#1f6b4a"],
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
                res = decide_proposal_remote(p["id"], "approved_paper", note=note)
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
                    res = decide_proposal_remote(p["id"], "approved_live", note=note)
                    st.session_state[f"live_confirm_{p['id']}"] = False
                    if res.get("ok"):
                        st.success("Marked live-approved (still locked until unlock + DRY_RUN=false).")
                    else:
                        st.error(res.get("error") or res)
            if b3.button("Reject", key=f"rj_{p['id']}"):
                res = decide_proposal_remote(p["id"], "rejected", note=note)
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
        res = set_control_remote(emergency_off=True)
        st.write(res)
    if c2.button("Clear emergency"):
        res = set_control_remote(emergency_off=False)
        st.write(res)
    if c3.button("Pause trading"):
        res = set_control_remote(trading_enabled=False)
        st.write(res)

    d1, d2, d3 = st.columns(3)
    if d1.button("Resume trading"):
        res = set_control_remote(trading_enabled=True)
        st.write(res)
    if d2.button("Unlock live (dangerous)"):
        st.session_state["unlock_arm"] = True
        st.warning("Click Confirm unlock next.")
    if d3.button("Confirm unlock") and st.session_state.get("unlock_arm"):
        res = set_control_remote(live_unlocked=True)
        st.session_state["unlock_arm"] = False
        st.write(res)
    if st.button("Lock live"):
        res = set_control_remote(live_unlocked=False)
        st.write(res)

    st.caption("After control changes: Sync again to refresh the snapshot.")


def tab_capital(dd: Path) -> None:
    st.subheader("Capital")
    cap = load_json(dd / "control" / "capital.json")
    if not cap:
        st.info("No capital.json synced.")
        return
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
        value=float(cap.get("day_loss_limit_inr") or 5000),
        step=500.0,
    )
    maxlots = st.number_input(
        "Max lots total",
        value=int(cap.get("max_lots_total") or 10),
        step=1,
    )
    strategies = list(cap.get("strategies") or [])
    if strategies:
        st.dataframe(pd.DataFrame(strategies), use_container_width=True, hide_index=True)
    if st.button("Push capital to VM", type="primary"):
        payload = {
            "total_capital_inr": total,
            "cash_reserve_pct": reserve,
            "day_loss_limit_inr": dayloss,
            "max_lots_total": int(maxlots),
            "strategies": strategies,
        }
        res = save_capital_remote(payload)
        if res.get("ok"):
            st.success("Capital saved on VM.")
        else:
            st.error(res.get("error") or res)


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


def tab_trades(db: Path) -> None:
    if not db.exists():
        st.info("Need ticks.db.")
        return
    trades = load_trades(str(db))
    if trades.empty:
        st.info("No trades.")
        return
    strat = st.multiselect(
        "Strategy",
        options=sorted(trades["strategy"].dropna().unique()),
    )
    view = trades if not strat else trades[trades["strategy"].isin(strat)]
    if st.checkbox("Today only"):
        tday = date.today().isoformat()
        view = view[
            view["entry_ts"].astype(str).str.startswith(tday)
            | view["exit_ts"].astype(str).str.startswith(tday)
        ]
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
    st.sidebar.caption("Mac UI · VM is source of truth")
    dd = Path(
        st.sidebar.text_input("Data folder", value=str(DEFAULT_DATA))
    ).expanduser()
    st.session_state["data_dir"] = str(dd)
    db = dd / "ticks.db"

    cfg = VmConfig.from_env()
    st.sidebar.text_input("VM", value=cfg.vm, key="vm_name")
    st.sidebar.text_input("Zone", value=cfg.zone, key="vm_zone")
    cfg = VmConfig(
        vm=st.session_state.get("vm_name", cfg.vm),
        zone=st.session_state.get("vm_zone", cfg.zone),
        remote_dir=cfg.remote_dir,
    )

    skip_db = st.sidebar.checkbox("Light sync (skip ticks.db)", value=False)
    if st.sidebar.button("Sync from VM", type="primary"):
        with st.spinner("Syncing…"):
            # temporarily set env for bridge
            import os

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
    st.caption(
        f"Local snapshot · approvals/emergency push to {cfg.vm} · {date.today().isoformat()}"
    )

    if not dd.exists():
        st.warning("Run **Sync from VM** in the sidebar first.")
        return

    (
        t0,
        t1,
        t2,
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
            "Capital",
            "Models",
            "Reasoning",
            "Trades",
            "Signals / Ticks",
            "Live orders",
        ]
    )
    with t0:
        tab_overview(dd, db)
    with t1:
        tab_proposals(dd)
    with t2:
        tab_control(dd)
    with t3:
        tab_capital(dd)
    with t4:
        tab_ml(dd)
    with t5:
        tab_reasoning(dd)
    with t6:
        tab_trades(db)
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
