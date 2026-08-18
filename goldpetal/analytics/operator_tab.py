"""Operator desk on Streamlit 8501 — start/stop, books, live money only."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

SHORT = {
    "S4_OVERNIGHT": "S4 HHHL swing",
    "S5_MINEDGE": "S5 minedge",
    "S8_NET_ZIGZAG": "S8 zigzag",
    "S11_DISCOVERED": "S11 pack",
    "S13_HHHL_DAY": "S13 daily S16",
    "S16_HHHL_WICK_1H": "S16 HHHL+wick 1h",
}


def _flash(res: dict[str, Any]) -> None:
    if res.get("ok"):
        st.success(res.get("note") or "ok")
    else:
        st.error(res.get("error") or res.get("note") or str(res))


def _show_flash() -> None:
    msg = st.session_state.pop("op_flash", None)
    if isinstance(msg, dict):
        _flash(msg)


def _set_flash(res: dict[str, Any]) -> None:
    st.session_state["op_flash"] = res
    st.rerun()


def render_operator_desk(*, local: bool) -> None:
    from analytics.bot_ops import (
        bot_status,
        restart_bot,
        start_bot,
        start_feed,
        stop_bot,
        stop_feed,
    )
    from capital import capital_snapshot, load_capital, save_capital
    from control_state import set_emergency, set_live_unlocked, set_trading_enabled
    from live_readiness import apply_desk_books, apply_panel_live_env, desk_snapshot
    from live_readiness import panel_restart_allowed

    _show_flash()
    live = desk_snapshot()
    bot = bot_status(lite=True) if local else {}
    cap = capital_snapshot()
    armed = bool(live.get("would_place_real_orders"))
    feed = str(bot.get("feed_source") or "off")

    a, b, c, d = st.columns(4)
    a.metric("Mode", "ARMED" if armed else ("LIVE env" if live.get("dry_run") is False else "PAPER"))
    b.metric("Bot", "on" if bot.get("running") else "off")
    c.metric("Feed", feed)
    d.metric("Lots cap", live.get("live_max_lots") or 1)
    if armed:
        st.error("ARMED — live-picked books send Angel orders.")

    c1, c2, c3, c4 = st.columns(4)
    if c1.button("Emergency off", type="primary", disabled=not local):
        set_emergency(True)
        st.rerun()
    if c2.button("Clear emergency", disabled=not local):
        set_emergency(False)
        st.rerun()
    if c3.button("Trading on", disabled=not local):
        set_trading_enabled(True)
        st.rerun()
    if c4.button("Trading off", disabled=not local):
        set_trading_enabled(False)
        st.rerun()

    e1, e2, e3 = st.columns(3)
    if e1.button("Start bot", disabled=not local):
        _set_flash(start_bot())
    if e2.button("Stop bot", disabled=not local):
        _set_flash(stop_bot())
    restart_word = e3.text_input("Type RESTART", value="", key="op_restart_word")
    if e3.button("Restart bot", disabled=not local):
        ok, why = panel_restart_allowed(restart_word)
        if not ok:
            st.error(why)
        else:
            res = restart_bot()
            _set_flash(
                {
                    "ok": res.get("ok"),
                    "note": "Bot restarted — loads ENABLE_* / DRY_RUN",
                    "error": res.get("error"),
                }
            )
    f1, f2 = st.columns(2)
    if f1.button("Start feed only", disabled=(not local) or bool(bot.get("running"))):
        _set_flash(start_feed())
    if f2.button(
        "Stop feed only",
        disabled=(not local) or bool(bot.get("running")) or feed != "collector",
    ):
        _set_flash(stop_feed())

    enables = live.get("enables") or {}
    rows = []
    for b in live.get("books") or []:
        name = str(b.get("strategy") or "")
        rows.append(
            {
                "book": SHORT.get(name, name),
                "id": name,
                "in_bot": bool(enables.get(name)),
                "live": bool(b.get("live_approved")),
            }
        )
    edited = st.data_editor(
        pd.DataFrame(rows),
        column_order=["book", "in_bot", "live"],
        column_config={
            "book": st.column_config.TextColumn("Book", disabled=True),
            "id": None,
            "in_bot": st.column_config.CheckboxColumn("In bot"),
            "live": st.column_config.CheckboxColumn("Live"),
        },
        hide_index=True,
        use_container_width=True,
        key="op_books",
        disabled=not local,
    )
    if st.button("Save strategies", type="primary", disabled=not local):
        in_bot = [str(r["id"]) for r in edited.to_dict("records") if r.get("in_bot")]
        live_picks = [str(r["id"]) for r in edited.to_dict("records") if r.get("live")]
        _set_flash(apply_desk_books(in_bot, live_picks))

    paper = st.checkbox("Paper only (DRY_RUN)", value=bool(live.get("dry_run", True)))
    lots = st.number_input(
        "LIVE_MAX_LOTS (1–10)",
        min_value=1,
        max_value=10,
        value=int(live.get("live_max_lots") or 1),
    )
    live_word = st.text_input("Type LIVE to set DRY_RUN=false", value="", key="op_live_word")
    if st.button("Save money", disabled=not local):
        _set_flash(
            apply_panel_live_env(dry_run=paper, live_max_lots=int(lots), confirm=live_word)
        )
    u1, u2 = st.columns(2)
    if u1.button("Unlock live", disabled=not local):
        set_live_unlocked(True)
        st.rerun()
    if u2.button("Lock live", disabled=not local):
        set_live_unlocked(False)
        st.rerun()
    book_rs = st.number_input("Book ₹", value=float(cap.get("total_capital_inr") or 0), step=1000.0)
    dd_rs = st.number_input("Day-loss ₹", value=float(cap.get("daily_loss_limit_inr") or 0), step=100.0)
    if st.button("Save capital", disabled=not local):
        plan = load_capital()
        plan.total_capital_inr = float(book_rs)
        plan.daily_loss_limit_inr = float(dd_rs)
        save_capital(plan)
        _set_flash({"ok": True, "note": "Capital saved"})
    st.caption("Trades / downloads: sidebar Page. Restart after Save strategies or Save money.")
