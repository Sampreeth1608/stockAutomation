#!/usr/bin/env python3
"""Gold Petal Mac analytics — read-only dashboard over a synced SQLite snapshot.

Run on your Mac (not the trading VM):

  ./scripts/sync_analytics_mac.sh
  pip install -r requirements-analytics.txt
  streamlit run analytics/app.py

This is intentionally separate from the VM control panel: zero impact on bot RAM.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

# Allow importing goldpetal modules when launched from repo/goldpetal
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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


def _parse_args_data_dir() -> Path:
    # streamlit passes unknown args after --
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--data-dir" and i + 1 < len(args):
            return Path(args[i + 1]).expanduser()
    return Path(
        st.session_state.get("data_dir")
        or DEFAULT_DATA
    )


@st.cache_data(ttl=30)
def load_trades(db_path: str) -> pd.DataFrame:
    from paper_report import summarize_trades
    from storage import build_trades

    path = Path(db_path)
    if not path.exists():
        return pd.DataFrame()
    trades = build_trades(db_path=path)
    if not trades:
        return pd.DataFrame()
    return pd.DataFrame(trades)


@st.cache_data(ttl=30)
def load_scoreboard(db_path: str) -> pd.DataFrame:
    from paper_report import summarize_trades
    from storage import build_trades

    path = Path(db_path)
    if not path.exists():
        return pd.DataFrame()
    all_trades = build_trades(db_path=path)
    rows = []
    for s in STRATEGIES + [None]:
        rows.append(summarize_trades(all_trades, s))
    return pd.DataFrame(rows)


@st.cache_data(ttl=30)
def load_signals(db_path: str, limit: int = 500) -> pd.DataFrame:
    path = Path(db_path)
    if not path.exists():
        return pd.DataFrame()
    con = sqlite3.connect(path)
    try:
        df = pd.read_sql_query(
            """
            SELECT time_label, strategy, action, position_after, cmp, reason, dry_run
            FROM signals
            ORDER BY id DESC
            LIMIT ?
            """,
            con,
            params=(limit,),
        )
    except Exception:
        return pd.DataFrame()
    finally:
        con.close()
    return df


@st.cache_data(ttl=30)
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
    ltp = last[1] if last else None
    # storage stores scaled ₹; if still paise, show both
    if ltp is not None and float(ltp) > 200_000:
        ltp = float(ltp) / 100.0
    return {
        "n_ticks": int(n),
        "last_ts": last[0] if last else None,
        "last_ltp": float(ltp) if ltp is not None else None,
    }


def load_json(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def main() -> None:
    st.set_page_config(
        page_title="Gold Petal Analytics",
        page_icon="◆",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600&family=Source+Sans+3:wght@400;600&display=swap');
        html, body, [class*="css"]  {
          font-family: 'Source Sans 3', sans-serif;
        }
        h1, h2, h3 {
          font-family: 'Fraunces', serif !important;
          letter-spacing: -0.02em;
        }
        .block-container { padding-top: 1.2rem; }
        div[data-testid="stMetricValue"] { font-family: 'Fraunces', serif; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.sidebar.title("Gold Petal")
    st.sidebar.caption("Mac analytics · read-only snapshot")
    data_dir = Path(
        st.sidebar.text_input(
            "Data folder",
            value=str(DEFAULT_DATA),
            help="Filled by scripts/sync_analytics_mac.sh",
        )
    ).expanduser()
    st.session_state["data_dir"] = str(data_dir)
    db_path = data_dir / "ticks.db"

    if st.sidebar.button("Refresh cache"):
        st.cache_data.clear()
        st.rerun()

    st.sidebar.markdown(
        """
**Sync from VM (Terminal on Mac):**
```bash
cd ~/path/to/goldpetal
./scripts/sync_analytics_mac.sh
```
"""
    )

    st.title("Gold Petal research desk")
    st.caption(
        "Local SQLite snapshot — does not touch the trading VM. "
        f"Today {date.today().isoformat()}"
    )

    if not data_dir.exists():
        st.warning(
            f"No data at `{data_dir}`. Run `./scripts/sync_analytics_mac.sh` on your Mac first."
        )
        return

    # --- flags ---
    flags_path = data_dir / "env.flags"
    if flags_path.exists():
        with st.expander("VM flags (snapshot)", expanded=False):
            st.code(flags_path.read_text(encoding="utf-8"), language="bash")

    stats = load_tick_stats(str(db_path)) if db_path.exists() else {}
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Ticks in snapshot", f"{stats.get('n_ticks', 0):,}")
    c2.metric("Last LTP", f"{stats.get('last_ltp') or '—'}")
    c3.metric("Last tick", str(stats.get("last_ts") or "—")[:19])
    c4.metric("DB", "ready" if db_path.exists() else "missing")

    tab_pnl, tab_trades, tab_signals, tab_discover = st.tabs(
        ["PnL board", "Trades", "Signals", "Discovery"]
    )

    with tab_pnl:
        if not db_path.exists():
            st.info("Sync ticks.db to see PnL (or re-run sync without --skip-db).")
        else:
            board = load_scoreboard(str(db_path))
            if board.empty:
                st.info("No trades yet in this snapshot.")
            else:
                show = board.copy()
                # Prefer gross when fees ignored
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
                    if c in show.columns
                ]
                st.dataframe(show[cols], use_container_width=True, hide_index=True)
                try:
                    import plotly.express as px

                    plot_df = show[show["strategy"] != "ALL"].copy()
                    if "gross_pnl" in plot_df.columns and not plot_df.empty:
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
                            font_family="Source Sans 3",
                        )
                        st.plotly_chart(fig, use_container_width=True)
                except Exception:
                    pass

    with tab_trades:
        if not db_path.exists():
            st.info("Need ticks.db for trades.")
        else:
            trades = load_trades(str(db_path))
            if trades.empty:
                st.info("No trades.")
            else:
                strat = st.multiselect(
                    "Strategy",
                    options=sorted(trades["strategy"].dropna().unique()),
                    default=[],
                )
                view = trades if not strat else trades[trades["strategy"].isin(strat)]
                # today filter
                today_only = st.checkbox("Today only", value=False)
                if today_only and "entry_ts" in view.columns:
                    tday = date.today().isoformat()
                    view = view[
                        view["entry_ts"].astype(str).str.startswith(tday)
                        | view["exit_ts"].astype(str).str.startswith(tday)
                    ]
                prefer_cols = [
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
                st.dataframe(
                    view[prefer_cols],
                    use_container_width=True,
                    hide_index=True,
                    height=480,
                )

    with tab_signals:
        if not db_path.exists():
            st.info("Need ticks.db for signals.")
        else:
            sigs = load_signals(str(db_path))
            if sigs.empty:
                st.info("No signals.")
            else:
                st.dataframe(sigs, use_container_width=True, hide_index=True, height=480)

    with tab_discover:
        beh = load_json(data_dir / "discover" / "behavior_report.json")
        latest = load_json(data_dir / "discover" / "latest_report.json")
        props = load_json(data_dir / "control" / "proposals.json")
        if beh:
            st.subheader("Behavior report")
            st.write(beh.get("summary", ""))
            fam = beh.get("families") or []
            if fam:
                st.dataframe(pd.DataFrame(fam), use_container_width=True, hide_index=True)
            if beh.get("horizon_table"):
                st.caption("Horizon table")
                st.dataframe(
                    pd.DataFrame(beh["horizon_table"]),
                    use_container_width=True,
                    hide_index=True,
                )
        else:
            st.info("No behavior_report.json — run weekly_discover on VM, then sync.")
        if latest:
            st.subheader("Latest discovery candidates")
            cands = latest.get("candidates") or []
            if cands:
                st.dataframe(pd.DataFrame(cands), use_container_width=True, hide_index=True)
        if props:
            st.subheader("Proposals")
            items = props.get("proposals") if isinstance(props, dict) else props
            if items:
                st.dataframe(pd.DataFrame(items), use_container_width=True, hide_index=True)

    log_path = data_dir / "logs" / "strategy_run.log"
    if log_path.exists():
        with st.expander("strategy_run.log (tail)", expanded=False):
            text = log_path.read_text(encoding="utf-8", errors="replace")
            st.code("\n".join(text.splitlines()[-80:]), language="text")


if __name__ == "__main__":
    main()
