#!/usr/bin/env python3
"""Write goldpetal_all_strategies.pdf — archive of every book, including S16.

Formulas are copied from the Gold Petal source. S16 is the only live book.
This file does not change how S16 trades.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
)

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "goldpetal_all_strategies.pdf"
INK = HexColor("#1c1914")
GOLD = HexColor("#7a5314")
MUTED = HexColor("#5c5346")


BOOKS: list[dict[str, str]] = [
    {
        "id": "S16_HHHL_WICK_1H",
        "title": "S16 HHHL + wick 1h — THE ONLY LIVE BOOK",
        "hold": "Intraday. Flatten at MARKET_CLOSE (23:30 IST). Leftover at next MARKET_OPEN. Never overnight.",
        "body": (
            "Wait for the 1-hour candle to finish (first tick of the next hour). "
            "Compare that closed bar to the previous same-session closed hour. "
            "Never use yesterday’s last hour or a preopen hour as prev. "
            "A restart after the hour already finished still applies the last closed session 1h "
            "vs the hour before it (fill at that bar’s close), unless a signal was already recorded "
            "at or after that close. Fill at this bar’s close. FLIP if already the other side. "
            "No range skip. No bald-body. No open=high/low (that is S14). "
            "min_wick_gap = 0 on the live book. Mood and market regime are observe-only."
        ),
        "calc": (
            "Let c = this finished 1h, p = previous same-session finished 1h.\n"
            "green = c.C > c.O     red = c.C < c.O\n"
            "HH = c.H > p.H        LL = c.L < p.L\n"
            "upper wick U = c.H − max(c.O, c.C)\n"
            "lower wick L = min(c.O, c.C) − c.L\n"
            "\n"
            "If c.C > p.C  → HH/LL only (wicks ignored):\n"
            "    LONG  if HH and green\n"
            "    SHORT if LL and red\n"
            "    else skip\n"
            "If c.C < p.C  → wick only (HH/LL ignored), |U−L| ≥ 0:\n"
            "    LONG  if L > U\n"
            "    SHORT if U > L\n"
            "    else skip (equal wick)\n"
            "If c.C = p.C  → skip\n"
            "\n"
            "First finished session hour is stored as prev (no trade).\n"
            "Next finished session hour can BUY/SHORT vs that prev.\n"
            "Opposite signal → FLIP (CLOSE then BUY/SHORT).\n"
            "MARKET_CLOSE → flatten. Next open → leftover flatten if still open."
        ),
    },
    {
        "id": "S1_NETDELTA",
        "title": "S1 net-delta (30m pressure)",
        "hold": "Removed from live. Legacy 30-minute net-pressure.",
        "body": (
            "30-minute net-pressure on TBQ/TSQ. Signals BUY / SHORT / CLOSE / reverse. "
            "Exhaustion: large price move from entry plus netΔ stretched vs entry netΔ. "
            "Divergence: tiny price change but netΔ surges vs previous netΔ."
        ),
        "calc": (
            "NET = TBQ − TSQ over the 30m bar.\n"
            "Default exhaustion: price ≥ 2% from entry AND netΔ ≥ 3× entry netΔ.\n"
            "Default divergence: |price change| ≤ 0.1% AND netΔ ≥ 2× previous netΔ.\n"
            "Not on the live desk."
        ),
    },
    {
        "id": "S2_BALANCE",
        "title": "S2 1-minute depth balance",
        "hold": "Removed from live.",
        "body": (
            "Collect buy1..buy5 qty and sell1..sell5 qty for one minute, sum them, "
            "then trade on that minute net. Same idea as old tick-flip S2, but once per minute "
            "to cut whipsaw."
        ),
        "calc": (
            "net = sum(buy1..5 over the minute) − sum(sell1..5 over the minute)\n"
            "net > 0 → BUY / hold long\n"
            "net < 0 → SHORT / hold short\n"
            "net = 0 → CLOSE / stay flat"
        ),
    },
    {
        "id": "S3_ML",
        "title": "S3 ML model",
        "hold": "Removed from live. Research.",
        "body": (
            "BUY / SHORT / CLOSE from full-depth features (joblib model). "
            "Needs a trained model under data/models. Default buy prob 0.58, short 0.42, "
            "min hold 30s, every 5 ticks, 80-tick buffer."
        ),
        "calc": (
            "Features from export_full_ticks + ml_features.FEATURE_COLUMNS.\n"
            "P(up) ≥ 0.58 → BUY\n"
            "P(up) ≤ 0.42 → SHORT\n"
            "else HOLD / CLOSE after min hold.\n"
            "Not on the live desk."
        ),
    },
    {
        "id": "S4_OVERNIGHT",
        "title": "S4 daily HH/LL swing (old S13)",
        "hold": "Removed from live. Delivery swing. Never next-open.",
        "body": (
            "Old S13 HH+green / LL+red on the day candle, last 15 minutes (23:15–23:30 IST). "
            "Holds across days until the opposite HH/LL. FLIP. "
            "Lost the Angel/ticks daily backtest to S13 (daily S16). Desk force-off."
        ),
        "calc": (
            "Confirm only in last 15m of this day. Fill at last-15m LTP.\n"
            "LONG  = day HH and green (H > prevH and C > O)\n"
            "SHORT = day LL and red   (L < prevL and C < O)\n"
            "Stay until opposite. Monthly rollover flattens last front session."
        ),
    },
    {
        "id": "S5_MINEDGE",
        "title": "S5 minedge (fee-cover)",
        "hold": "Removed from live. Was delivery overnight.",
        "body": (
            "Trade only when expected move can cover Angel fees. "
            "Uses rolling expected-move (ATR + session range) plus strong depth imbalance. "
            "Exits on target (+expected), stop (~0.45 × expected), or flatten."
        ),
        "calc": (
            "expected_points = ATR / session-range estimate\n"
            "fee break-even ≈ 50 points (₹50 round-trip, ₹1/point, TURNOVER_MULT=1)\n"
            "required = max(MIN_EDGE_POINTS, fee_break_even × safety) when COVER_FEES=true\n"
            "Enter BUY/SHORT only if expected_points ≥ required\n"
            "AND depth imbalance ≥ ~1.35 (buy vs sell sums)\n"
            "Target = +expected from entry. Stop ≈ 0.45 × expected."
        ),
    },
    {
        "id": "S6_MIN30",
        "title": "S6 30-point floor",
        "hold": "Removed from live.",
        "body": "Same family as S5, but the expected-move floor is a flat 30 points.",
        "calc": "Enter only if expected_points ≥ 30. Same imbalance idea as S5.",
    },
    {
        "id": "S8_NET_ZIGZAG",
        "title": "S8 NET zigzag",
        "hold": "Removed from live. Was delivery overnight.",
        "body": (
            "Order-book NET zigzags. Long-only while BULL, short-only while BEAR. "
            "Entry needs IMB rising-edge or pullback-resume, plus cooldown after exit. "
            "After a stop: IMB must fall below min then re-cross."
        ),
        "calc": (
            "NET = TBQ − TSQ\n"
            "IMB% = |NET| / max(TBQ, TSQ) × 100\n"
            "Code default min_imb_pct = 10 (some notes said 14 — live code is 10)\n"
            "weaken_pct = 10    TP = 25 points    SL = 20 points\n"
            "Exit: TP / SL / supporting-qty weaken / bias flip."
        ),
    },
    {
        "id": "S9_STATE30",
        "title": "S9 30m state",
        "hold": "Removed from live. Research.",
        "body": "30-minute state machine (journal + optional bar ML). Not on the live desk.",
        "calc": "State labels from 30m bars. Paper/research only.",
    },
    {
        "id": "S10_LEGACY30",
        "title": "S10 legacy 30m zigzag",
        "hold": "Removed from live.",
        "body": "Legacy 30m always-zigzag path (MTF paper experiment).",
        "calc": "30m zigzag without S8’s rising-edge / SL-reset gates.",
    },
    {
        "id": "S11_DISCOVERED",
        "title": "S11 discovered pack",
        "hold": "Removed from live. ML-tab hypotheses.",
        "body": "Auto-discovered rules. Paper hypotheses, not proven edge. Off the hot path.",
        "calc": "Rules written by discover_strategies.py. Not live.",
    },
    {
        "id": "S12_HHHL30",
        "title": "S12 30m HH/LL",
        "hold": "Removed from live. Retired from the desk.",
        "body": (
            "30m higher-high / lower-low, last-minute confirm. "
            "Skips wick-only fakeouts: close must finish beyond the prior high/low."
        ),
        "calc": (
            "LONG  = HH and green and close finishes above prev high\n"
            "SHORT = LL and red and close finishes below prev low\n"
            "Last minute of the 30m bar confirms."
        ),
    },
    {
        "id": "S13_HHHL_DAY",
        "title": "S13 daily S16 (day candle)",
        "hold": "Removed from live. Was delivery swing. Never next-open.",
        "body": (
            "S16 close-vs-prev formula on the day candle, last 15 minutes (23:15–23:30). "
            "Holds the trend across days until the opposite S16 day signal or Gold Petal "
            "monthly rollover (flatten last front session, then trade next month). FLIP."
        ),
        "calc": (
            "Same s16_bar_decision(prev_day, this_day, min_wick_gap=0).\n"
            "Confirm only last 15m. Fill at LTP in that window.\n"
            "Rollover_DAYS=5: flatten front month on last front session."
        ),
    },
    {
        "id": "S14_WICK30_STRICT",
        "title": "S14 30m wick",
        "hold": "Removed from live. Retired.",
        "body": (
            "Same-candle open=high SHORT / open=low LONG, else raw wick. "
            "Weak nearly-equal wicks are skipped. Formula order unchanged."
        ),
        "calc": (
            "If O = H → SHORT (bald top)\n"
            "If O = L → LONG  (bald bottom)\n"
            "else U = H − max(O,C), L = min(O,C) − L\n"
            "LONG if L > U and |U−L| ≥ gap; SHORT if U > L."
        ),
    },
    {
        "id": "S15_WICK30_NOWICK",
        "title": "S15 30m bald body",
        "hold": "Removed from live. Retired.",
        "body": "Bald 30m body only, HOLD (no reverse). Fewer trades; no real edge filter.",
        "calc": "Trade only when the 30m bar has no meaningful wick. No reverse on later bars.",
    },
    {
        "id": "S17_CLOSE_HIGH_BODY",
        "title": "S17 close / high / body grid (research)",
        "hold": "Never paper. Never live.",
        "body": (
            "Research grid of close-vs-prev, high-vs-prev, and green/red. "
            "Eight cells; leftover (equals) skipped. Books: hhhl, wick, and, or."
        ),
        "calc": (
            "1 C>pC H>pH C>O  up_hh_green\n"
            "2 C>pC H>pH C<O  up_hh_red\n"
            "3 C<pC H<pH C>O  dn_lh_green\n"
            "4 C<pC H<pH C<O  dn_lh_red\n"
            "5 C<pC H>pH C>O  dn_hh_green\n"
            "6 C>pC H<pH C<O  up_lh_red\n"
            "7 C>pC H<pH C>O  up_lh_green\n"
            "8 C<pC H>pH C<O  dn_hh_red\n"
            "hhhl = S12 sides. wick = raw wick. and = both same. or = either, skip fight."
        ),
    },
    {
        "id": "S18_OHLC_VOL_HTF",
        "title": "S18 OHLC + volume + yesterday 1h",
        "hold": "Removed from live. Was delivery overnight.",
        "body": (
            "Wait for the 1h to finish. Need previous same-session 1h and yesterday’s completed day. "
            "Bar volume is the delta of Angel session-cumulative volume_trade_for_the_day. "
            "Learner may replace the pack from ticks. Base book is AND of every leg."
        ),
        "calc": (
            "Base LONG when ALL are true:\n"
            "    C > O, C > prevC, H > prevH, vol > prev vol, C > yesterday C\n"
            "Base SHORT when ALL are true:\n"
            "    C < O, C < prevC, L < prevL, vol > prev vol, C < yesterday C\n"
            "Anything equal / missing prev or day / volume not up → skip.\n"
            "FLIP at this bar’s close. Fill at close."
        ),
    },
    {
        "id": "S19_BODY_CLOSE_1H",
        "title": "S19 aligned body + close 1h",
        "hold": "Removed from live. Was delivery overnight.",
        "body": (
            "Wait for the 1h to finish. Need previous same-session 1h. "
            "LONG when green AND close up vs prev. SHORT when red AND close down. "
            "Mixed / doji / equal close: hold. FLIP only on the opposite aligned hour. "
            "100-lot + fees 1h backtest lost after charges vs S16 and S18."
        ),
        "calc": (
            "LONG  = C > O and C > prevC\n"
            "SHORT = C < O and C < prevC\n"
            "Skip mixed (green but C<prevC, red but C>prevC), doji (C=O), C=prevC, missing prev.\n"
            "Hold through skip. FLIP only on opposite aligned signal. Fill at close."
        ),
    },
    {
        "id": "S20_FADE_HL",
        "title": "S20 fade low / high 1h",
        "hold": "Removed from live. Never armed.",
        "body": (
            "Buy a bounced low, short a rejected high. "
            "Inside / outside / knife (LL that closes red) / chase (HH that closes green): hold. "
            "1h after-charges backtest lost to S16 and S18."
        ),
        "calc": (
            "LONG  = L < prevL AND not H > prevH AND C > O\n"
            "SHORT = H > prevH AND not L < prevL AND C < O\n"
            "FLIP only on the opposite fade. Fill at this bar’s close."
        ),
    },
    {
        "id": "OVERNIGHT_GAP",
        "title": "Overnight gap (close → next open)",
        "hold": "Removed from live. Was next-open exit, never EOD flatten. Mood-exempt.",
        "body": (
            "Read today’s Gold Petal tape near MARKET_CLOSE. "
            "BUY if the read says tomorrow’s open is up; SHORT if down; skip if mixed. "
            "CLOSE in the first 5 minutes after next MARKET_OPEN (09:00–09:05 IST). "
            "Not S4 daily swing."
        ),
        "calc": (
            "Need ≥ 20 ticks, valid OHLC.\n"
            "day_ret = ln(C / O)\n"
            "CLV = 2 × (C − L) / (H − L) − 1     (+1 close at high, −1 at low)\n"
            "late_ret = ln(C / late_open) over last 45 minutes\n"
            "late_imb = (TBQ − TSQ) / (TBQ + TSQ) late window\n"
            "z = 6·tanh(day_ret/0.004) + 1.2·CLV + 4·tanh(late_ret/0.002) + 0.9·tanh(imb/0.35)\n"
            "P(up) = sigmoid(z)\n"
            "BUY if P ≥ 0.58     SHORT if P ≤ 0.42     else skip\n"
            "Entry window: last 15 minutes before 23:30. Exit: 09:00–09:05 next session."
        ),
    },
    {
        "id": "FLOW_BRAIN",
        "title": "FLOW_BRAIN tick pressure",
        "hold": "Removed from live. Never armed.",
        "body": (
            "Every second: classify LTP + TBQ/TSQ change (new buy qty vs new sell qty). "
            "Trade only continuation + expansion. Flatten on decay / opposite / EOD. "
            "Desk pack also reused S5 fee-cover, S8 NET/IMB, S16/S19 1m structure, S20 fade-block."
        ),
        "calc": (
            "bull_cont = price up + buy flow up\n"
            "bear_cont = price down + sell flow up\n"
            "Expansion = price response growing AND flow imbalance growing.\n"
            "Trade continuation+expansion only. Fill at LTP. Rank after charges, tax excluded."
        ),
    },
    {
        "id": "BODY_SHRINK_FADE",
        "title": "Body-shrink fade (research, no ENABLE)",
        "hold": "Never a paper/live book. Do not rewrite S16.",
        "body": (
            "Three candles: two same-color bodies, second smaller and lower/higher, fade candle 3."
        ),
        "calc": "Research overlay only. python backtest_body_shrink_fade.py",
    },
    {
        "id": "AMISE_S21_S24",
        "title": "AMISE slots S21–S24",
        "hold": "Removed from live. Research factory.",
        "body": "Lab challengers after Approve. Off the live desk list.",
        "calc": "Factory books. ENABLE_S21..S24 default false. Not live.",
    },
]


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "cover": ParagraphStyle(
            "cover",
            parent=base["Title"],
            fontName="Times-Bold",
            fontSize=22,
            leading=26,
            textColor=GOLD,
            spaceAfter=8,
        ),
        "sub": ParagraphStyle(
            "sub",
            parent=base["Normal"],
            fontName="Times-Italic",
            fontSize=11,
            leading=14,
            textColor=MUTED,
            spaceAfter=14,
        ),
        "h": ParagraphStyle(
            "h",
            parent=base["Heading2"],
            fontName="Times-Bold",
            fontSize=13,
            leading=16,
            textColor=GOLD,
            spaceBefore=10,
            spaceAfter=4,
        ),
        "p": ParagraphStyle(
            "p",
            parent=base["Normal"],
            fontName="Times-Roman",
            fontSize=10,
            leading=13,
            textColor=INK,
            alignment=TA_JUSTIFY,
            spaceAfter=6,
        ),
        "lab": ParagraphStyle(
            "lab",
            parent=base["Normal"],
            fontName="Times-Bold",
            fontSize=9,
            leading=12,
            textColor=MUTED,
            spaceBefore=2,
            spaceAfter=2,
        ),
        "pre": ParagraphStyle(
            "pre",
            parent=base["Code"],
            fontName="Courier",
            fontSize=8.2,
            leading=11,
            textColor=INK,
            leftIndent=6,
            spaceAfter=8,
        ),
        "foot": ParagraphStyle(
            "foot",
            parent=base["Normal"],
            fontName="Times-Roman",
            fontSize=8,
            textColor=MUTED,
            alignment=TA_LEFT,
        ),
    }


def build(path: Path = OUT) -> Path:
    styles = _styles()
    story: list = []
    story.append(Paragraph("Gold Petal — every strategy, archived", styles["cover"]))
    story.append(
        Paragraph(
            "23 August 2026. Formulas as implemented in source. "
            "S16 HHHL+wick 1h is the only book that may trade live. "
            "Every other book is off live permanently. "
            "This PDF does not arm Angel and does not change the S16 formula.",
            styles["sub"],
        )
    )
    story.append(
        Paragraph(
            "Live size is still yours: tick Lots or ₹ on S16, type LIVE, Arm, then RESTART. "
            "Leftover Exit still squares an old Angel leftover without Arm. "
            "If Angel is already flat, do not Exit.",
            styles["p"],
        )
    )
    story.append(Paragraph("How to read a candle", styles["h"]))
    story.append(
        Paragraph(
            "O open, H high, L low, C close. Green body = C &gt; O. Red body = C &lt; O. "
            "Doji = C = O. Upper wick = H − max(O,C). Lower wick = min(O,C) − L. "
            "HH = this high &gt; previous high. LL = this low &lt; previous low. "
            "FLIP = close the open side, then open the opposite. "
            "Fill = this bar’s close (or LTP in a last-15m daily window). "
            "Rank and live P&amp;L are after Angel charges, tax excluded.",
            styles["p"],
        )
    )
    story.append(Paragraph("Gold Petal session", styles["h"]))
    story.append(
        Paragraph(
            "Mon–Fri 09:00–23:30 IST. S16 is the session/intraday book: flatten at 23:30, "
            "leftover at next 09:00. Monthly Gold Petal rollover uses ROLLOVER_DAYS=5.",
            styles["p"],
        )
    )

    for book in BOOKS:
        story.append(Paragraph(book["title"], styles["h"]))
        story.append(Paragraph(f"Id: {book['id']}", styles["lab"]))
        story.append(Paragraph(f"Hold / status: {book['hold']}", styles["lab"]))
        story.append(Paragraph(book["body"], styles["p"]))
        story.append(Paragraph("Calculation", styles["lab"]))
        story.append(Preformatted(book["calc"], styles["pre"]))

    story.append(PageBreak())
    story.append(Paragraph("What was removed from live", styles["h"]))
    story.append(
        Paragraph(
            "S1, S2, S3, S4, S5, S6, S8, S9, S10, S11, S12, S13, S14, S15, S17, S18, S19, "
            "S20, overnight gap, FLOW_BRAIN, AMISE S21–S24, body-shrink fade. "
            "Source files stay in the repo as this archive’s reference. "
            "They cannot be armed. The Live table lists S16 only.",
            styles["p"],
        )
    )
    story.append(Paragraph("S16 checklist (unchanged formula)", styles["h"]))
    bullets = [
        "Wait for the 1h to finish.",
        "Prev must be the previous same-session hour.",
        "Up-close → HH+green LONG / LL+red SHORT.",
        "Down-close → lower wick LONG / upper wick SHORT (gap 0).",
        "Equal close → skip.",
        "FLIP at that close. Flatten at MARKET_CLOSE.",
        "You still Arm live. The desk does not.",
    ]
    story.append(
        ListFlowable(
            [ListItem(Paragraph(b, styles["p"]), leftIndent=8) for b in bullets],
            bulletType="bullet",
        )
    )
    story.append(Spacer(1, 8 * mm))
    story.append(
        Paragraph(
            "End of archive. Keep this PDF. The live desk now trades S16 only.",
            styles["sub"],
        )
    )

    def _page(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFillColor(MUTED)
        canvas.setFont("Times-Roman", 8)
        canvas.drawString(18 * mm, 12 * mm, "Gold Petal strategy archive · S16 only live")
        canvas.drawRightString(A4[0] - 18 * mm, 12 * mm, f"{doc.page}")
        canvas.restoreState()

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=18 * mm,
        title="Gold Petal — every strategy, archived",
        author="Gold Petal",
    )
    doc.build(story, onFirstPage=_page, onLaterPages=_page)
    return path


if __name__ == "__main__":
    out = build()
    print(f"wrote {out} ({out.stat().st_size} bytes)")
