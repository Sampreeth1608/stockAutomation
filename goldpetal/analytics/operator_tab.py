"""Operator desk on Streamlit 8501 — same writer as 8787, on the tunnel that opens."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

SHORT = {
    "S4_OVERNIGHT": "S4 overnight",
    "S5_MINEDGE": "S5 minedge",
    "S8_NET_ZIGZAG": "S8 zigzag",
    "S11_DISCOVERED": "S11 pack",
    "S12_HHHL30": "S12 HHHL 30m",
    "S13_HHHL_DAY": "S13 HHHL day",
    "S14_WICK30_STRICT": "S14 wick",
    "S15_WICK30_NOWICK": "S15 no-wick",
}

ROOT = Path(__file__).resolve().parents[1]


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
        feed_status,
        restart_bot,
        start_bot,
        start_feed,
        stop_bot,
        stop_feed,
    )
    from capital import capital_snapshot, load_capital, save_capital
    from control_panel import history_payload
    from control_state import set_emergency, set_live_unlocked, set_trading_enabled
    from live_readiness import apply_desk_books, apply_panel_live_env, live_readiness
    from live_readiness import panel_restart_allowed
    from panel_export import (
        TICK_CSV_FIELDS,
        TRADE_CSV_FIELDS,
        default_date_range,
        export_pack_zip,
        export_summary,
        export_ticks_csv,
        export_trades_csv,
        rows_to_tsv,
        ticks_in_range,
        trades_in_range,
    )
    from s14_exchange_sheet import sheet_zip_bytes
    from sheets_pack import SCORE_FIELDS, build_scoreboard_rows, sheets_pack_zip_bytes

    st.subheader("Operator desk")
    st.caption(
        "Open this in **Chrome on your Mac** at http://127.0.0.1:8501/ → tab **Desk**. "
        "Do not type URLs in the VM SSH window. "
        f"Code: `{ROOT}`"
    )
    _show_flash()

    if not local:
        st.warning(
            "This Streamlit is a Mac snapshot. Start/stop and live writes only work "
            "on the VM desk (8501 tunnel → tab Desk)."
        )

    live = live_readiness()
    bot = bot_status() if local else {}
    feed = feed_status() if local else {}
    cap = capital_snapshot()
    armed = bool(live.get("would_place_real_orders"))
    m1, m2, m3, m4 = st.columns(4)
    m1.metric(
        "Mode",
        "ARMED LIVE" if armed else ("LIVE env" if live.get("dry_run") is False else "PAPER"),
    )
    m2.metric("Bot", "on" if bot.get("running") else "off")
    m3.metric("Feed", str(feed.get("source") or "off"))
    m4.metric("LIVE_MAX_LOTS", live.get("live_max_lots") or 1)
    if armed:
        st.error("ARMED — next signal on a live-picked book hits Angel.")

    st.markdown("##### Stop / go")
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

    st.markdown("##### Engine + feed")
    st.caption(
        ("Bot running" if bot.get("running") else "Bot stopped")
        + " · "
        + str(feed.get("note") or feed.get("source") or "—")
        + " · type RESTART after Save strategies / Save money. Does not paper S14 by itself."
    )
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
    if f1.button(
        "Start feed only",
        disabled=(not local) or bool(bot.get("running")),
    ):
        _set_flash(start_feed())
    if f2.button(
        "Stop feed only",
        disabled=(not local) or bool(bot.get("running")) or feed.get("source") != "collector",
    ):
        _set_flash(stop_feed())

    st.markdown("##### Strategies")
    st.caption(
        "In bot = RAM after Restart. Live pick = Angel when money is LIVE + unlocked. "
        "Live pick without In bot is ignored. Does not change DRY_RUN."
    )
    books = live.get("books") or []
    enables = live.get("enables") or {}
    in_bot: list[str] = []
    live_picks: list[str] = []
    for b in books:
        name = str(b.get("strategy") or "")
        cols = st.columns([3, 1, 1, 2, 2])
        cols[0].write(SHORT.get(name, name))
        if cols[1].checkbox("In bot", value=bool(enables.get(name)), key=f"in_{name}"):
            in_bot.append(name)
        if cols[2].checkbox("Live", value=bool(b.get("live_approved")), key=f"lv_{name}"):
            live_picks.append(name)
        cols[3].write(str(b.get("ram") or "—"))
        cols[4].write(f"qty {b.get('live_qty') or 0}")
    if st.button("Save strategies", type="primary", disabled=not local):
        _set_flash(apply_desk_books(in_bot, live_picks))

    st.markdown("##### Live money")
    st.caption("Keep Paper only unless you mean Angel. LIVE_MAX_LOTS cap is 10. Paper 100 lots is not live size.")
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

    st.markdown("##### Watch")
    hist = history_payload(limit=80)
    view = st.radio(
        "Show",
        ["Trades", "Open", "Ticks", "Live orders", "Signals", "Scoreboard"],
        horizontal=True,
        key="op_watch",
    )
    if view == "Trades":
        st.dataframe(pd.DataFrame(hist.get("trades") or []), use_container_width=True, hide_index=True)
    elif view == "Open":
        st.dataframe(pd.DataFrame(hist.get("open") or []), use_container_width=True, hide_index=True)
    elif view == "Ticks":
        st.dataframe(pd.DataFrame(hist.get("ticks") or []), use_container_width=True, hide_index=True)
    elif view == "Live orders":
        st.dataframe(pd.DataFrame(hist.get("live_orders") or []), use_container_width=True, hide_index=True)
    elif view == "Signals":
        st.dataframe(pd.DataFrame(hist.get("signals") or []), use_container_width=True, hide_index=True)
    else:
        st.dataframe(pd.DataFrame(hist.get("scoreboard") or []), use_container_width=True, hide_index=True)
    st.caption(
        f"open {hist.get('total_open')} · closed {hist.get('total_closed')} · ticks {hist.get('tick_count')}"
    )

    st.markdown("##### Downloads")
    st.caption("CSV / ZIP for laptop or Google Sheets. Date range is IST inclusive. Build, then download or copy.")
    d0, d1 = default_date_range()
    cfrom, cto = st.columns(2)
    date_from = str(cfrom.date_input("From", value=pd.Timestamp(d0).date()))
    date_to = str(cto.date_input("To", value=pd.Timestamp(d1).date()))
    try:
        summ = export_summary(date_from, date_to)
        st.caption(f"{summ.get('tick_count')} ticks · {summ.get('trade_count')} trades")
    except Exception as exc:
        st.caption(str(exc))
    kind = st.selectbox(
        "File",
        [
            "Download all (ZIP)",
            "Download trades CSV",
            "Download ticks CSV",
            "Copy trades → Sheets (TSV)",
            "Copy ticks → Sheets (TSV)",
            "Download Sheets pack",
            "Copy scoreboard → Sheets (TSV)",
            "Download S14 sheet ZIP",
        ],
        key="op_dl_kind",
    )
    if st.button("Build file"):
        try:
            st.session_state.pop("op_dl_bytes", None)
            st.session_state.pop("op_dl_name", None)
            st.session_state.pop("op_dl_mime", None)
            st.session_state.pop("op_dl_text", None)
            if kind == "Download all (ZIP)":
                st.session_state["op_dl_bytes"] = export_pack_zip(date_from, date_to)
                st.session_state["op_dl_name"] = f"goldpetal_export_{date_from}_to_{date_to}.zip"
                st.session_state["op_dl_mime"] = "application/zip"
            elif kind == "Download trades CSV":
                st.session_state["op_dl_bytes"] = export_trades_csv(date_from, date_to).encode("utf-8")
                st.session_state["op_dl_name"] = f"goldpetal_trades_{date_from}_to_{date_to}.csv"
                st.session_state["op_dl_mime"] = "text/csv"
            elif kind == "Download ticks CSV":
                st.session_state["op_dl_bytes"] = export_ticks_csv(date_from, date_to).encode("utf-8")
                st.session_state["op_dl_name"] = f"goldpetal_ticks_{date_from}_to_{date_to}.csv"
                st.session_state["op_dl_mime"] = "text/csv"
            elif kind == "Copy trades → Sheets (TSV)":
                rows = trades_in_range(date_from, date_to)
                st.session_state["op_dl_text"] = rows_to_tsv(rows, TRADE_CSV_FIELDS)
            elif kind == "Copy ticks → Sheets (TSV)":
                rows = ticks_in_range(date_from, date_to)
                st.session_state["op_dl_text"] = rows_to_tsv(rows, TICK_CSV_FIELDS)
            elif kind == "Download Sheets pack":
                st.session_state["op_dl_bytes"] = sheets_pack_zip_bytes()
                st.session_state["op_dl_name"] = "goldpetal_sheets_pack.zip"
                st.session_state["op_dl_mime"] = "application/zip"
            elif kind == "Copy scoreboard → Sheets (TSV)":
                st.session_state["op_dl_text"] = rows_to_tsv(build_scoreboard_rows(), SCORE_FIELDS)
            else:
                st.session_state["op_dl_bytes"] = sheet_zip_bytes()
                st.session_state["op_dl_name"] = "goldpetal_s14_sheet.zip"
                st.session_state["op_dl_mime"] = "application/zip"
            st.success("Ready — download or copy below.")
        except Exception as exc:
            st.error(str(exc))
    if st.session_state.get("op_dl_bytes"):
        st.download_button(
            "Download",
            data=st.session_state["op_dl_bytes"],
            file_name=st.session_state.get("op_dl_name") or "goldpetal.bin",
            mime=st.session_state.get("op_dl_mime") or "application/octet-stream",
            key="op_dl_btn",
        )
    if st.session_state.get("op_dl_text"):
        st.text_area(
            "Select all and paste into Google Sheets",
            value=st.session_state["op_dl_text"],
            height=160,
            key="op_dl_tsv",
        )
