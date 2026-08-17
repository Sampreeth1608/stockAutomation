"""Gold Petal S14 candlestick for Streamlit (O/H/L/C on the bar + zoom).

Lives next to app.py so the desk does not depend on a newer s14_exchange_sheet.py
from a different checkout (e.g. ~/goldpetal vs ~/goldpetal-repo/goldpetal).
"""

from __future__ import annotations

from typing import Any

PLOTLY_ZOOM_CONFIG = {
    "scrollZoom": True,
    "displaylogo": False,
    "modeBarButtonsToRemove": ["lasso2d", "select2d"],
}


def _num(value: Any) -> float | int:
    x = float(value)
    if x == int(x):
        return int(x)
    return round(x, 1)


def _flag_yes(value: Any) -> bool:
    return value in {True, "Y", "y", "YES", "yes", 1, "1"}


def candle_onbar_lines(row: dict[str, Any]) -> list[str]:
    side = str(row.get("side") or "skip").strip() or "skip"
    if side.lower() != "skip":
        side = side.upper()
    flags: list[str] = []
    if _flag_yes(row.get("O=H", row.get("open_eq_high"))):
        flags.append("O=H")
    if _flag_yes(row.get("O=L", row.get("open_eq_low"))):
        flags.append("O=L")
    tail = f"{side} {' '.join(flags)}".strip()
    return [
        f"O {_num(row['open'])}",
        f"H {_num(row['high'])}",
        f"L {_num(row['low'])}",
        f"C {_num(row['close'])}",
        tail,
    ]


def candle_hover_text(row: dict[str, Any]) -> str:
    extra = [
        str(row.get("time") or ""),
        *candle_onbar_lines(row),
        str(row.get("rule") or ""),
        f"U {_num(row.get('upper') or 0)}  Lw {_num(row.get('lower') or 0)}",
        (
            f"{row.get('action') or ''}  "
            f"{row.get('pos_from') or row.get('pos_before') or ''}→"
            f"{row.get('pos_to') or row.get('pos_after') or ''}"
        ),
    ]
    return "\n".join(x for x in extra if str(x).strip())


def labeled_candlestick_figure(
    rows: list[dict[str, Any]],
    *,
    title: str = "Gold Petal",
) -> Any:
    """Candlestick with O/H/L/C on each bar. Scroll-zoom, drag-pan, rangeslider."""
    import plotly.graph_objects as go

    times = [str(r.get("time") or "") for r in rows]
    opens = [r["open"] for r in rows]
    highs = [r["high"] for r in rows]
    lows = [r["low"] for r in rows]
    closes = [r["close"] for r in rows]
    texts = ["<br>".join(candle_onbar_lines(r)) for r in rows]
    hovers = [candle_hover_text(r).replace("\n", "<br>") for r in rows]
    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=times,
            open=opens,
            high=highs,
            low=lows,
            close=closes,
            name="GOLDPETAL",
            increasing_line_color="#1f9d55",
            decreasing_line_color="#d64545",
            increasing_fillcolor="#1f9d55",
            decreasing_fillcolor="#d64545",
            hovertext=hovers,
            hoverinfo="text",
        )
    )
    yaxis_layout: dict[str, Any] = dict(title="₹ / g", fixedrange=False)
    if rows:
        fig.add_trace(
            go.Scatter(
                x=times,
                y=highs,
                mode="text",
                text=texts,
                textposition="top center",
                textfont=dict(
                    size=9,
                    family="IBM Plex Mono, ui-monospace, monospace",
                    color="#111111",
                ),
                hoverinfo="skip",
                showlegend=False,
                cliponaxis=False,
            )
        )
        lo = min(lows)
        hi = max(highs)
        span = (hi - lo) or 1.0
        yaxis_layout["range"] = [lo - 0.06 * span, hi + 0.34 * span]
    fig.update_layout(
        title=title,
        dragmode="pan",
        hovermode="closest",
        height=780,
        margin=dict(l=50, r=24, t=52, b=40),
        xaxis=dict(
            rangeslider=dict(visible=True, thickness=0.08),
            type="category",
            showspikes=True,
            title="time IST · scroll zoom · drag pan · double-click reset",
        ),
        yaxis=yaxis_layout,
        plot_bgcolor="#ffffff",
        paper_bgcolor="#ffffff",
        uirevision=title,
    )
    return fig
