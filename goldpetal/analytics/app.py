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

# Load .env before Streamlit auth / ops (ENABLE_*, DESK_PASSWORD, etc.)
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
except Exception:
    pass

from operator_desk import (  # noqa: E402
    OPERATOR_URL,
    operator_readonly_markdown,
    streamlit_control_allowed,
    streamlit_write_blocked,
)
from analytics.local_bridge import (  # noqa: E402
    decide_proposal_local,
    desk_data_dir,
    set_control_local,
)
from analytics.vm_bridge import (  # noqa: E402
    VmConfig,
    decide_proposal_remote,
    set_control_remote,
    sync_snapshot,
)
from analytics.desk_auth import (  # noqa: E402
    audit,
    auth_required,
    dangerous_requires_totp,
    desk_password_configured,
    desk_password_hint,
    verify_password,
    verify_totp,
)
from analytics.env_bridge import (  # noqa: E402
    read_env,
    strategy_enable_snapshot,
)
from analytics.bot_ops import bot_status  # noqa: E402

DEFAULT_DATA = desk_data_dir()
LOCAL_DESK = os.getenv("GP_DESK_LOCAL", "").strip().lower() in {"1", "true", "yes", "y"} or (
    DEFAULT_DATA.resolve() == (ROOT / "data").resolve()
)

# Slim paper set (S4/S5/S8/S11/S13/S16)
STRATEGIES = [
    "S4_OVERNIGHT",
    "S5_MINEDGE",
    "S8_NET_ZIGZAG",
    "S11_DISCOVERED",
    "S13_HHHL_DAY",
    "S16_HHHL_WICK_1H",
]

# One page at a time — st.tabs runs every tab on every load (that is why the desk felt late).
PAGES = [
    "Desk",
    "S14 chart",
    "Overview",
    "Proposals / ML",
    "Deploy / Ops (view)",
    "Control (stop)",
    "Live Deploy (view)",
    "Capital (view)",
    "Models",
    "Reasoning",
    "Trades",
    "Signals / Ticks",
    "Live orders",
]


def decide_proposal(pid: str, decision: str, note: str = "", *, apply_env: bool = True) -> dict:
    if LOCAL_DESK:
        try:
            return decide_proposal_local(pid, decision, note=note, apply_env=apply_env)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    return decide_proposal_remote(pid, decision, note=note)


def require_desk_login() -> bool:
    """Return True if the session may use the desk."""
    if not LOCAL_DESK:
        return True
    if not auth_required():
        return True
    if st.session_state.get("desk_authed"):
        return True
    st.title("Gold Petal desk — login")
    st.caption("SSH tunnel already limits network access. Password adds an operator gate.")
    hint = desk_password_hint()
    st.caption(
        f"Secrets file: `{hint['env_path']}` · mode={hint['mode']} · "
        f"password_len={hint['password_len']}"
    )
    if not desk_password_configured():
        st.error(
            "DESK_AUTH is on but DESK_PASSWORD / DESK_PASSWORD_HASH is not set in .env. "
            "Set one, or DESK_AUTH=false for trusted localhost-only use."
        )
        return False
    # Prefer non-form inputs: browser password managers often fill the visual
    # field without updating Streamlit form state (submit then looks "wrong").
    pw = st.text_input("Desk password", type="password", key="desk_login_pw")
    if st.button("Unlock desk", type="primary", key="desk_login_btn"):
        typed = (pw or "").strip()
        if not typed:
            st.error(
                "Password field was empty. Type the password manually "
                "(autofill often does not reach Streamlit)."
            )
            audit("desk_login", ok=False, detail={"reason": "empty"})
            return False
        if verify_password(typed):
            st.session_state["desk_authed"] = True
            audit("desk_login", ok=True, detail={"env_path": hint["env_path"]})
            st.rerun()
        audit(
            "desk_login",
            ok=False,
            detail={
                "reason": "mismatch",
                "typed_len": len(typed),
                "expect_len": hint["password_len"],
                "env_path": hint["env_path"],
            },
        )
        st.error(
            f"Wrong password (typed {len(typed)} chars, .env expects "
            f"{hint['password_len']}). Value must match DESK_PASSWORD in "
            f"`{hint['env_path']}` — type it; do not rely on autofill."
        )
    return False


def totp_ok(label: str, key: str) -> bool:
    """If TOTP is configured for dangerous ops, require a valid code in session form."""
    if not dangerous_requires_totp():
        return True
    code = st.text_input(f"OTP — {label}", type="password", key=key)
    if not code:
        st.warning("Enter authenticator OTP to confirm this action.")
        return False
    if verify_totp(code):
        return True
    st.error("Invalid OTP.")
    return False


def set_control(**kwargs):  # type: ignore[no-untyped-def]
    allowed, why = streamlit_control_allowed(kwargs)
    if not allowed:
        return streamlit_write_blocked(why)
    if LOCAL_DESK:
        try:
            return set_control_local(**kwargs)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    return set_control_remote(**kwargs)


def save_capital(payload: dict) -> dict:
    return streamlit_write_blocked("Capital")


def save_live_allocation(payload: dict) -> dict:
    return streamlit_write_blocked("Live deploy")


def save_paper_allowlist(payload: dict) -> dict:
    return streamlit_write_blocked("Paper allowlist")


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
    # Desk board is slim-only — ignore OPEN leftovers from S9/S10/etc.
    slim = set(STRATEGIES)
    slim_trades = [t for t in all_trades if t.get("strategy") in slim]
    orphan_open = sum(
        1
        for t in all_trades
        if t.get("status") == "OPEN" and t.get("strategy") not in slim
    )
    rows = [summarize_trades(slim_trades, s) for s in STRATEGIES]
    all_row = summarize_trades(slim_trades, None)
    all_row["orphan_open"] = orphan_open
    rows.append(all_row)
    return pd.DataFrame(rows)


def load_orphan_opens(db_path: str) -> pd.DataFrame:
    """OPEN trades from strategies not in the slim desk list."""
    from storage import build_trades

    path = Path(db_path)
    if not path.exists():
        return pd.DataFrame()
    slim = set(STRATEGIES)
    opens = [
        t
        for t in build_trades(db_path=path)
        if t.get("status") == "OPEN" and t.get("strategy") not in slim
    ]
    if not opens:
        return pd.DataFrame()
    cols = [
        c
        for c in ("strategy", "side", "entry_ts", "entry_price", "entry_reason", "status")
        if opens and c in opens[0]
    ]
    return pd.DataFrame(opens)[cols] if cols else pd.DataFrame(opens)


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
        html, body, [class*="css"] { font-family: ui-sans-serif, system-ui, sans-serif; }
        .block-container { padding-top: 1rem; max-width: 1200px; }
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
                open_n = int(r.get("open") or 0)
                orphan = int(r.get("orphan_open") or 0)
                st.caption(
                    f"Slim open positions: **{open_n}** "
                    f"(S4/S5/S8/S11/S13/S16 only)."
                    + (
                        f" Also {orphan} leftover OPEN from disabled strategies "
                        f"(S9/S10/…) — not active now; see expander below."
                        if orphan
                        else ""
                    )
                )
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
            orphans = load_orphan_opens(str(db))
            if not orphans.empty:
                with st.expander(
                    f"Leftover OPEN from disabled strategies ({len(orphans)})",
                    expanded=False,
                ):
                    st.caption(
                        "These are historical signal pairs that never got CLOSE after "
                        "the strategy was turned off. They are not live paper positions "
                        "in the slim runner."
                    )
                    st.dataframe(orphans, use_container_width=True, hide_index=True)
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


def tab_s14_chart(dd: Path) -> None:
    """Angel/MCX Gold Petal candles — research tab (Desk tab is the writer)."""
    from analytics.s14_chart import PLOTLY_ZOOM_CONFIG, labeled_candlestick_figure
    from s14_exchange_sheet import refresh_status, start_angel_refresh

    sheet = dd / "s14_sheet"
    st.subheader("Gold Petal exchange candles")
    st.caption(
        "O/H/L/C is printed on each candle (not a table). "
        "Scroll to zoom, drag to pan, use the rangeslider, double-click to reset. "
        "On 30m/1h zoom in until the numbers sit on the bar. "
        "Start/stop and live picks are on tab **Desk**. "
        "Open with `gcloud compute ssh … -- -N -L 8501:127.0.0.1:8501` then "
        "http://127.0.0.1:8501/ → **Desk** (this chart is the next tab)."
    )
    st.caption(f"Desk code: `{ROOT}`")
    st_status = refresh_status(sheet)
    if st_status.get("running"):
        st.warning("Angel pull running — wait ~30s and hit Rerun (top right).")
    elif st_status.get("generated_at"):
        st.caption(f"Last pull: {st_status['generated_at']}  {st_status.get('source') or ''}")
    cols = st.columns([1, 1, 2])
    with cols[0]:
        tf = st.selectbox("Timeframe", ["1d", "1h", "30m"], index=0)
    with cols[1]:
        pull = st.button("Pull live candles", type="primary")
    if pull:
        py = ROOT / "venv" / "bin" / "python"
        if not py.is_file():
            py = Path(sys.executable)
        res = start_angel_refresh(root=ROOT, python=str(py), sheet_dir=sheet)
        if res.get("started_new"):
            st.info("Pulling Angel/MCX now. Wait 30–60s, then Rerun.")
        elif res.get("running"):
            st.info("A pull is already running.")
        else:
            st.error(str(res))
    path = sheet / f"{tf}.csv"
    if not path.is_file():
        st.error(
            "No sheet yet. On the VM run:\n\n"
            "`cd ~/goldpetal && ./venv/bin/python explain_s14_candles.py "
            "--tf 30m,1h,1d --from 2026-08-02 --no-print-bars`"
        )
        return
    df = pd.read_csv(path)
    for col in ("open", "high", "low", "close", "upper", "lower"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    rows = df.where(pd.notnull(df), None).to_dict("records")
    try:
        fig = labeled_candlestick_figure(rows, title=f"Gold Petal {tf} · O/H/L/C on candle")
        st.plotly_chart(
            fig,
            use_container_width=True,
            config=PLOTLY_ZOOM_CONFIG,
            key=f"s14-chart-{tf}",
        )
    except Exception as exc:
        st.error(f"Candlestick needs plotly ({exc}).")
        return


def tab_proposals(dd: Path) -> None:
    st.subheader("Weekend proposals — ML / new & improved strategies")
    st.caption(
        "Approve → paper **auto-writes** whitelist env keys (e.g. S11_PACK_PATH). "
        f"Then Restart the bot on the Desk tab ({OPERATOR_URL}). "
        "Live still needs Unlock live + DRY_RUN=false on Desk."
    )
    raw = load_json(dd / "control" / "proposals.json")
    items = (raw or {}).get("proposals") if isinstance(raw, dict) else (raw or [])
    if not items:
        st.warning("No proposals yet. Run weekly jobs on VM.")
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
                res = decide_proposal(p["id"], "approved_paper", note=note, apply_env=True)
                if res.get("ok"):
                    audit(
                        "approve_paper",
                        ok=True,
                        detail={"id": p["id"], "env_applied": res.get("env_applied")},
                    )
                    st.success(
                        f"Approved paper: {res.get('strategy')}. "
                        f"Env auto-applied: {res.get('env_applied')}"
                    )
                    if res.get("restart_needed"):
                        st.info(f"Restart the bot on the Desk tab ({OPERATOR_URL}) to load the pack.")
                    st.cache_data.clear()
                else:
                    st.error(res.get("error") or res)
            if b2.button("Approve → live", key=f"al_{p['id']}"):
                st.warning(
                    f"Do not approve live here. After paper looks good, check Live "
                    f"on the Desk tab ({OPERATOR_URL})."
                )
            if b3.button("Reject", key=f"rj_{p['id']}"):
                res = decide_proposal(p["id"], "rejected", note=note, apply_env=False)
                if res.get("ok"):
                    st.success("Rejected.")
                    st.cache_data.clear()
                else:
                    st.error(res.get("error") or res)

    if decided:
        st.subheader("Recent decisions")
        st.dataframe(pd.DataFrame(decided), use_container_width=True, hide_index=True)


def tab_control(dd: Path) -> None:
    st.subheader("Bot control — stop only")
    st.info(
        operator_readonly_markdown()
        + " Streamlit may **stop** (emergency / pause / lock live). "
        "Clear emergency, resume, and unlock live only on the Desk tab."
    )
    state = load_json(dd / "control" / "state.json") or {}
    st.json(state)

    c1, c2, c3 = st.columns(3)
    if c1.button("EMERGENCY OFF", type="primary"):
        if totp_ok("emergency off", "totp_em_off"):
            res = set_control(emergency_off=True)
            audit("emergency_off", ok=bool(res.get("ok")), detail=res)
            st.write(res)
    if c2.button("Pause trading"):
        res = set_control(trading_enabled=False)
        st.write(res)
    if c3.button("Lock live"):
        res = set_control(live_unlocked=False)
        audit("live_lock", ok=bool(res.get("ok")), detail=res)
        st.write(res)


def tab_deploy_ops(dd: Path) -> None:
    st.subheader("Deploy / Ops — read-only")
    st.info(operator_readonly_markdown())
    st.caption(f"Change ENABLE_*, DRY_RUN, LIVE_MAX_LOTS, and Restart on the Desk tab ({OPERATOR_URL})")

    if LOCAL_DESK:
        status = bot_status()
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Bot running", "yes" if status.get("running") else "no")
        s2.metric("supervise PIDs", len(status.get("supervise") or []))
        s3.metric("run_strategy PIDs", len(status.get("run_strategy") or []))
        health = status.get("health") or {}
        s4.metric("Last health", str(health.get("ts_ist") or "—")[-8:] if health else "—")
        mismatches = status.get("mismatches") or []
        if mismatches:
            st.error("Position mismatch (DB vs RAM): " + "; ".join(mismatches))
        elif status.get("running"):
            st.caption(f"RAM positions: {health.get('positions') or '—'}")
        env = read_env()
        enables = strategy_enable_snapshot()
        e1, e2, e3 = st.columns(3)
        e1.metric("DRY_RUN", env.get("DRY_RUN", "true"))
        e2.metric("LIVE_MAX_LOTS", env.get("LIVE_MAX_LOTS", "1"))
        e3.metric("S11_PACK_PATH", env.get("S11_PACK_PATH", "") or "—")
        rows = [{"strategy": k, "ENABLE": "true" if v else "false"} for k, v in enables.items()]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        with st.expander("strategy_run.log (tail)", expanded=False):
            st.code(status.get("log_tail") or "(empty)", language="text")
    else:
        st.warning("Bot/.env snapshot is on the VM. Open the Desk tab on 8501 there.")


def tab_capital(dd: Path) -> None:
    st.subheader("Capital — read-only")
    st.info(operator_readonly_markdown())
    st.caption(f"Edit book ₹ / lots on the Desk tab ({OPERATOR_URL}).")
    cap = load_json(dd / "control" / "capital.json")
    if not cap:
        st.info("No capital.json yet.")
        return
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total ₹", f"{float(cap.get('total_capital_inr') or 0):,.0f}")
    c2.metric("Reserve %", cap.get("cash_reserve_pct") or 0)
    c3.metric("Day loss ₹", f"{float(cap.get('daily_loss_limit_inr') or cap.get('day_loss_limit_inr') or 0):,.0f}")
    c4.metric("Max lots total", cap.get("max_lots_total") or 0)
    strategies = _strategies_as_rows(cap.get("strategies"))
    if strategies:
        st.dataframe(pd.DataFrame(strategies), use_container_width=True, hide_index=True)


def tab_live_deploy(dd: Path) -> None:
    st.subheader("Live deploy — read-only")
    st.info(operator_readonly_markdown())
    st.caption(
        f"live_approved, ENABLE_*, DRY_RUN, and Restart are on the Desk tab ({OPERATOR_URL})."
    )
    state = load_json(dd / "control" / "state.json") or {}
    cap = load_json(dd / "control" / "capital.json") or {}
    force_disabled = set(state.get("force_disabled") or [])
    c1, c2, c3 = st.columns(3)
    c1.metric("Live unlocked", "yes" if state.get("live_unlocked") else "locked")
    c2.metric("Live approved", ", ".join(state.get("live_approved") or []) or "—")
    c3.metric("Trading", "ON" if state.get("trading_enabled", True) else "paused")
    if force_disabled:
        st.info("Force-disabled: " + ", ".join(sorted(force_disabled)))
    rows = []
    live_set = set(state.get("live_approved") or [])
    for row in _strategies_as_rows(cap.get("strategies")):
        name = row.get("strategy") or ""
        rows.append(
            {
                "strategy": name,
                "live_approved": name in live_set,
                "budget_inr": row.get("budget_inr"),
                "max_lots": row.get("max_lots"),
            }
        )
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    with st.expander("How live sizing works", expanded=False):
        st.markdown(
            f"""
1. On **Desk → Strategies**: In bot + Live pick, Save strategies.
2. On **Desk → Live money**: set LIVE_MAX_LOTS, keep Paper only unless you mean Angel.
3. Unlock live. Type LIVE only if you intend `DRY_RUN=false`.
4. Type RESTART on Desk → Engine. Size = min(strategy max_lots, LIVE_MAX_LOTS).

Do not also save these on the other Streamlit tabs.
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
        initial_sidebar_state="collapsed",
    )
    if not require_desk_login():
        return

    st.sidebar.title("Gold Petal")
    page = st.sidebar.selectbox("Page", PAGES, index=0, key="gp_page")
    if page != "Desk":
        inject_style()
    if LOCAL_DESK and st.session_state.get("desk_authed"):
        if st.sidebar.button("Lock desk"):
            st.session_state["desk_authed"] = False
            st.rerun()

    dd = DEFAULT_DATA
    db = dd / "ticks.db"
    lot_size = 100.0 if LOCAL_DESK else 1.0

    if page != "Desk":
        if LOCAL_DESK:
            st.sidebar.caption("VM local")
        else:
            st.sidebar.caption("Mac snapshot")
        dd = Path(
            st.sidebar.text_input("Data folder", value=str(DEFAULT_DATA))
        ).expanduser()
        st.session_state["data_dir"] = str(dd)
        db = dd / "ticks.db"
        if page in {"Overview", "Trades"}:
            lot_size = float(
                st.sidebar.number_input(
                    "Lots (fee display)",
                    min_value=1.0,
                    value=lot_size,
                    step=1.0,
                )
            )
        if LOCAL_DESK:
            if st.sidebar.button("Refresh"):
                st.cache_data.clear()
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

    if page != "Desk":
        st.title("Gold Petal research")
        st.caption(f"{'VM local' if LOCAL_DESK else 'Mac snapshot'} · {date.today().isoformat()}")

    if not dd.exists():
        st.warning("Data folder missing.")
        return

    if page == "Desk":
        from analytics.operator_tab import render_operator_desk

        render_operator_desk(local=LOCAL_DESK)
    elif page == "S14 chart":
        tab_s14_chart(dd)
    elif page == "Overview":
        tab_overview(dd, db, lot_size=lot_size)
    elif page == "Proposals / ML":
        tab_proposals(dd)
    elif page == "Deploy / Ops (view)":
        tab_deploy_ops(dd)
    elif page == "Control (stop)":
        tab_control(dd)
    elif page == "Live Deploy (view)":
        tab_live_deploy(dd)
    elif page == "Capital (view)":
        tab_capital(dd)
    elif page == "Models":
        tab_ml(dd)
    elif page == "Reasoning":
        tab_reasoning(dd)
    elif page == "Trades":
        tab_trades(db, lot_size=lot_size)
    elif page == "Signals / Ticks":
        tab_signals_ticks(db)
    else:
        tab_live_orders(dd)



if __name__ == "__main__":
    main()
