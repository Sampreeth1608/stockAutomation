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
from analytics.desk_auth import (  # noqa: E402
    audit,
    auth_required,
    dangerous_requires_totp,
    desk_password_configured,
    desk_totp_configured,
    verify_password,
    verify_totp,
)
from analytics.env_bridge import (  # noqa: E402
    apply_env_patch,
    apply_strategy_enables,
    read_env,
    strategy_enable_snapshot,
    write_env_updates,
)
from analytics.bot_ops import bot_status, restart_bot, stop_bot  # noqa: E402

DEFAULT_DATA = desk_data_dir()
LOCAL_DESK = os.getenv("GP_DESK_LOCAL", "").strip().lower() in {"1", "true", "yes", "y"} or (
    DEFAULT_DATA.resolve() == (ROOT / "data").resolve()
)

# Slim paper set (S4/S5/S8/S11/S12/S13)
STRATEGIES = [
    "S4_OVERNIGHT",
    "S5_MINEDGE",
    "S8_NET_ZIGZAG",
    "S11_DISCOVERED",
    "S12_HHHL30",
    "S13_HHHL_DAY",
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
    if not desk_password_configured():
        st.error(
            "DESK_AUTH is on but DESK_PASSWORD / DESK_PASSWORD_HASH is not set in .env. "
            "Set one, or DESK_AUTH=false for trusted localhost-only use."
        )
        return False
    with st.form("desk_login_form"):
        pw = st.text_input("Desk password", type="password")
        submitted = st.form_submit_button("Unlock desk", type="primary")
    if submitted:
        if verify_password(pw or ""):
            st.session_state["desk_authed"] = True
            audit("desk_login", ok=True)
            st.rerun()
        audit("desk_login", ok=False)
        st.error("Wrong password. Use the exact DESK_PASSWORD from ~/goldpetal/.env")
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
                open_n = int(r.get("open") or 0)
                orphan = int(r.get("orphan_open") or 0)
                st.caption(
                    f"Slim open positions: **{open_n}** "
                    f"(S4/S5/S8/S11/S12/S13 only)."
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


def tab_proposals(dd: Path) -> None:
    st.subheader("Weekend proposals — ML / new & improved strategies")
    st.caption(
        "Approve → paper **auto-writes** whitelist env keys (e.g. S11_PACK_PATH). "
        "Then use **Deploy / Ops → Restart bot**. Live still needs Unlock + DRY_RUN=false."
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
            auto_restart = st.checkbox(
                "Restart bot after approve (loads new pack)",
                value=False,
                key=f"ar_{p['id']}",
            )
            b1, b2, b3 = st.columns(3)
            if b1.button("Approve → paper", key=f"ap_{p['id']}", type="primary"):
                if p.get("safety_ok") is False:
                    st.warning("safety_ok=False — only approve if you accept the risk.")
                if auto_restart and not totp_ok("restart after approve", f"totp_ap_{p['id']}"):
                    st.stop()
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
                    if auto_restart and LOCAL_DESK:
                        rr = restart_bot()
                        st.write(rr)
                    elif res.get("restart_needed"):
                        st.info("Open **Deploy / Ops** → Restart bot to load the new pack.")
                    st.cache_data.clear()
                else:
                    st.error(res.get("error") or res)
            if b2.button("Approve → live", key=f"al_{p['id']}"):
                if not totp_ok("live approve", f"totp_al_{p['id']}"):
                    st.stop()
                ok = st.session_state.get(f"live_confirm_{p['id']}", False)
                if not ok:
                    st.session_state[f"live_confirm_{p['id']}"] = True
                    st.warning("Click again to confirm LIVE approve.")
                else:
                    res = decide_proposal(p["id"], "approved_live", note=note, apply_env=True)
                    st.session_state[f"live_confirm_{p['id']}"] = False
                    if res.get("ok"):
                        audit("approve_live", ok=True, detail={"id": p["id"]})
                        st.success(
                            "Live-approved + env applied. Still need Unlock live + DRY_RUN=false "
                            "(Deploy / Ops), then Restart."
                        )
                    else:
                        st.error(res.get("error") or res)
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
    st.subheader("Bot control")
    state = load_json(dd / "control" / "state.json") or {}
    st.json(state)

    c1, c2, c3 = st.columns(3)
    if c1.button("EMERGENCY OFF", type="primary"):
        if totp_ok("emergency off", "totp_em_off"):
            res = set_control(emergency_off=True)
            audit("emergency_off", ok=bool(res.get("ok")), detail=res)
            st.write(res)
    if c2.button("Clear emergency"):
        if totp_ok("clear emergency", "totp_em_clear"):
            res = set_control(emergency_off=False)
            audit("emergency_clear", ok=bool(res.get("ok")), detail=res)
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
        if totp_ok("unlock live", "totp_unlock"):
            res = set_control(live_unlocked=True)
            st.session_state["unlock_arm"] = False
            audit("live_unlock", ok=bool(res.get("ok")), detail=res)
            st.write(res)
    if st.button("Lock live"):
        res = set_control(live_unlocked=False)
        audit("live_lock", ok=bool(res.get("ok")), detail=res)
        st.write(res)

    st.caption("Prefer **Deploy / Ops** for DRY_RUN + restart after unlock.")


def tab_deploy_ops(dd: Path) -> None:
    st.subheader("Deploy / Ops — strategies, live arming, restart")
    if not LOCAL_DESK:
        st.warning("Full deploy/restart only works on the VM desk (GP_DESK_LOCAL=1).")
        return

    status = bot_status()
    s1, s2, s3 = st.columns(3)
    s1.metric("Bot running", "yes" if status.get("running") else "no")
    s2.metric("supervise PIDs", len(status.get("supervise") or []))
    s3.metric("run_strategy PIDs", len(status.get("run_strategy") or []))

    env = read_env()
    enables = strategy_enable_snapshot()
    dry = env.get("DRY_RUN", "true").lower() in {"1", "true", "yes", "y"}
    live_max = int(float(env.get("LIVE_MAX_LOTS", "1") or 1))
    live_lots = int(float(env.get("LIVE_LOTS", "1") or 1))
    s11_pack = env.get("S11_PACK_PATH", "")

    st.markdown("### Paper / load strategies (ENABLE_*)")
    st.caption("Checked = loaded into RAM after **Restart bot**. Unchecked strategies are not traded.")
    picked: list[str] = []
    cols = st.columns(3)
    for i, name in enumerate(STRATEGIES):
        on = cols[i % 3].checkbox(name, value=bool(enables.get(name)), key=f"en_{name}")
        if on:
            picked.append(name)

    st.markdown("### Live arming")
    dry_run = st.checkbox("DRY_RUN (paper only)", value=dry, key="ops_dry")
    live_max_in = st.number_input("LIVE_MAX_LOTS (hard ceiling)", min_value=1, value=max(1, live_max), step=1)
    live_lots_in = st.number_input("LIVE_LOTS (default size)", min_value=1, value=max(1, live_lots), step=1)
    st.text_input("S11_PACK_PATH", value=s11_pack, key="ops_s11_pack")

    restart_after = st.checkbox("Restart bot after save", value=True)
    confirm = st.checkbox("I confirm writing .env from this desk", value=False)

    b1, b2, b3, b4 = st.columns(4)
    if b1.button("Save strategy enables", type="primary", disabled=not confirm):
        if not totp_ok("save ENABLE_*", "totp_enables"):
            st.stop()
        res = apply_strategy_enables(picked, known=list(STRATEGIES))
        # Also lock paper allowlist to the same set
        save_paper_allowlist({"paper_allowlist": picked, "note": "desk deploy enables"})
        audit("save_enables", ok=bool(res.get("ok")), detail=res)
        st.write(res)
        if restart_after and res.get("ok"):
            if totp_ok("restart after enables", "totp_en_restart"):
                st.write(restart_bot())
        st.cache_data.clear()

    if b2.button("Save live env (DRY_RUN / lots / S11)", disabled=not confirm):
        if not totp_ok("save live env", "totp_live_env"):
            st.stop()
        patch = {
            "DRY_RUN": "true" if dry_run else "false",
            "LIVE_MAX_LOTS": int(live_max_in),
            "LIVE_LOTS": int(live_lots_in),
            "S11_PACK_PATH": st.session_state.get("ops_s11_pack", ""),
        }
        res = write_env_updates(patch)
        audit("save_live_env", ok=bool(res.get("ok")), detail={"applied": res.get("applied")})
        st.write(res)
        if restart_after and res.get("ok"):
            if totp_ok("restart after live env", "totp_live_restart"):
                st.write(restart_bot())
        st.cache_data.clear()

    if b3.button("Restart bot now"):
        if not totp_ok("restart bot", "totp_restart"):
            st.stop()
        res = restart_bot()
        audit("restart_bot", ok=bool(res.get("ok")), detail={"killed": res.get("killed")})
        st.write(res)
        st.cache_data.clear()

    if b4.button("Stop bot"):
        if not totp_ok("stop bot", "totp_stop"):
            st.stop()
        res = stop_bot()
        audit("stop_bot", ok=True, detail=res)
        st.write(res)

    with st.expander("strategy_run.log (tail)", expanded=False):
        st.code(status.get("log_tail") or "(empty)", language="text")

    st.markdown("### One-shot: paper set → capital → live list")
    st.caption(
        "Writes ENABLE_* + paper allowlist + live_approved + capital budgets, "
        "optionally arms DRY_RUN=false. Always confirm + OTP."
    )


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
    also_enable = st.checkbox(
        "Also write ENABLE_* for Live-checked strategies (needs Restart)",
        value=True,
        key="live_also_enable",
    )
    restart_after_live = st.checkbox(
        "Restart bot after save",
        value=False,
        key="live_restart_after",
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
        if not totp_ok("save live allocation", "totp_live_alloc"):
            st.stop()
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
            audit("live_allocation", ok=True, detail={"live_approved": live_names})
            st.success(
                f"Live approved: {', '.join(res.get('live_approved') or []) or 'none'}"
            )
            if also_enable and LOCAL_DESK and live_names:
                en = apply_strategy_enables(live_names, known=list(STRATEGIES))
                st.write({"ENABLE_applied": en})
            if restart_after_live and LOCAL_DESK:
                if totp_ok("restart after live save", "totp_live_save_restart"):
                    st.write(restart_bot())
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
    if not require_desk_login():
        return

    st.sidebar.title("Gold Petal desk")
    if LOCAL_DESK:
        st.sidebar.caption("VM local · live data/ · no Mac sync")
        if st.session_state.get("desk_authed"):
            if st.sidebar.button("Lock desk"):
                st.session_state["desk_authed"] = False
                st.rerun()
            st.sidebar.caption(
                "Auth: password"
                + (" + OTP for dangerous ops" if desk_totp_configured() else "")
            )
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
        st_status = bot_status()
        st.sidebar.metric("Bot", "RUN" if st_status.get("running") else "STOP")
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
    st.caption(f"{mode} · strategies S4/S5/S8/S11/S12/S13 · {date.today().isoformat()}")

    if not dd.exists():
        st.warning("Data folder missing.")
        return

    (
        t0,
        t1,
        t_ops,
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
            "Deploy / Ops",
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
    with t_ops:
        tab_deploy_ops(dd)
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
