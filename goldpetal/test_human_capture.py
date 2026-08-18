"""Human trade capture — records what you see. Does not trade."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from control_state import ALL_STRATEGY_NAMES, SLIM_PAPER_STRATEGIES
from human_capture import (
    capture_summary,
    example_public,
    is_session_open,
    ltp_at_or_after,
    record_human,
    session_status,
    settle_open,
    snapshot_market,
)
from live_readiness import PAPER_ONLY_BOOKS
from storage import init_db, save_tick

IST = ZoneInfo("Asia/Kolkata")
# Tuesday inside Gold Petal hours (Mon–Fri 09:00–23:30 IST)
OPEN = datetime(2026, 8, 18, 11, 0, tzinfo=IST)


def _tick(
    db: Path,
    *,
    when: datetime,
    ltp_paise: int,
    volume: int = 1000,
    tbq: int = 80,
    tsq: int = 40,
    oi: int = 5000,
    buy_qty: int = 10,
    sell_qty: int = 6,
) -> None:
    px = int(ltp_paise)
    msg = {
        "last_traded_price": px,
        "open_price_of_the_day": px - 100,
        "high_price_of_the_day": px + 100,
        "low_price_of_the_day": px - 200,
        "closed_price": px,
        "volume_trade_for_the_day": volume,
        "total_buy_quantity": tbq,
        "total_sell_quantity": tsq,
        "open_interest": oi,
        "best_5_buy_data": [{"price": px - 100, "quantity": buy_qty}],
        "best_5_sell_data": [{"price": px + 100, "quantity": sell_qty}],
    }
    save_tick(msg, "GOLDPETAL", "1", when.isoformat(timespec="seconds"), db_path=db)


def _tape(db: Path, n: int = 8, start: datetime | None = None) -> datetime:
    init_db(db)
    t0 = start or datetime(2026, 8, 18, 10, 0, tzinfo=IST)
    px = 1_400_000  # paise ~ ₹14000
    vol = 1000
    for i in range(n):
        _tick(
            db,
            when=t0 + timedelta(minutes=i),
            ltp_paise=px + i * 200,
            volume=vol + i * 50,
            tbq=90,
            tsq=40,
            oi=5000 + i,
        )
    return t0 + timedelta(minutes=n - 1)


def test_record_buy_does_not_trade(tmp_path: Path) -> None:
    db = tmp_path / "ticks.db"
    store = tmp_path / "examples.json"
    last = _tape(db, 12)
    snap = snapshot_market(db, tick_limit=50)
    assert snap["ltp"] is not None
    assert snap["ltp"] > 1000
    rec = record_human(
        "buy",
        confidence=4,
        note="looks genuine",
        db=db,
        path=store,
        snapshot=snap,
        now=OPEN,
    )
    assert rec["action"] == "buy"
    assert rec["places_order"] is False
    assert rec["paper"] is False
    assert rec["live"] is False
    assert rec["entry_px"] == snap["ltp"]
    assert snap["bid1"] is not None
    assert snap["ask1"] is not None
    assert snap["n_ticks_30s"] >= 1
    assert rec["vs_coded"] in {
        "you_buy_rule_also",
        "you_buy_rule_missed",
    }
    pub = example_public(rec)
    assert pub["n_bars_1m"] >= 1
    assert pub["bid1"] == snap["bid1"]


def test_no_trade_is_the_filter(tmp_path: Path) -> None:
    db = tmp_path / "ticks.db"
    store = tmp_path / "examples.json"
    _tape(db, 10)
    snap = snapshot_market(db, tick_limit=40)
    rec = record_human("no_trade", note="depth thin", db=db, path=store, snapshot=snap, now=OPEN)
    assert rec["action"] == "no_trade"
    assert rec["vs_coded"] in {
        "you_skipped_rule_would_take",
        "you_skipped_rule_quiet",
    }


def test_settle_fills_horizons(tmp_path: Path) -> None:
    db = tmp_path / "ticks.db"
    store = tmp_path / "examples.json"
    init_db(db)
    t0 = datetime(2026, 8, 18, 11, 0, tzinfo=IST)
    _tick(db, when=t0, ltp_paise=1_400_000, volume=2000)
    snap = snapshot_market(db, tick_limit=20)
    rec = record_human("buy", db=db, path=store, snapshot=snap, now=OPEN)
    _tick(db, when=t0 + timedelta(seconds=8), ltp_paise=1_400_800, volume=2100)
    _tick(db, when=t0 + timedelta(seconds=12), ltp_paise=1_401_000, volume=2200)
    n = settle_open(db, path=store, now=t0 + timedelta(seconds=15))
    assert n >= 1
    from human_capture import load_examples

    got = next(x for x in load_examples(store) if x["id"] == rec["id"])
    assert "5s" in got["outcomes"]
    assert "10s" in got["outcomes"]
    assert got["outcomes"]["5s"]["taken_pts"] > 0
    assert got["settled"] is False  # 5m not due yet
    px = ltp_at_or_after(db, (t0 + timedelta(seconds=5)).isoformat(timespec="seconds"))
    assert px is not None


def test_summary_counts_skips(tmp_path: Path) -> None:
    db = tmp_path / "ticks.db"
    store = tmp_path / "examples.json"
    _tape(db, 6)
    snap = snapshot_market(db, tick_limit=30)
    record_human("buy", db=db, path=store, snapshot=snap, now=OPEN)
    record_human("no_trade", db=db, path=store, snapshot=snap, now=OPEN)
    from human_capture import load_examples

    s = capture_summary(load_examples(store))
    assert s["n"] == 2
    assert s["n_buy"] == 1
    assert s["n_no_trade"] == 1
    vs = s["vs_coded"]
    assert vs["you_buy_rule_missed"] + vs["you_buy_rule_also"] == 1
    assert vs["you_skipped_rule_would_take"] + vs["you_skipped_rule_quiet"] == 1


def test_refuses_when_market_closed(tmp_path: Path) -> None:
    db = tmp_path / "ticks.db"
    store = tmp_path / "examples.json"
    _tape(db, 6)
    snap = snapshot_market(db, tick_limit=20)
    night = datetime(2026, 8, 18, 23, 45, tzinfo=IST)
    weekend = datetime(2026, 8, 22, 12, 0, tzinfo=IST)  # Saturday
    preopen = datetime(2026, 8, 18, 8, 59, tzinfo=IST)
    assert not is_session_open(night)
    assert not is_session_open(weekend)
    assert not is_session_open(preopen)
    assert is_session_open(OPEN)
    assert session_status(OPEN)["open"] is True
    for when in (night, weekend, preopen):
        try:
            record_human("buy", db=db, path=store, snapshot=snap, now=when)
            raise AssertionError("closed session must not record")
        except RuntimeError as exc:
            assert "market closed" in str(exc)


def test_not_a_paper_book() -> None:
    root = Path(__file__).resolve().parent
    src = (root / "human_capture.py").read_text(encoding="utf-8")
    assert "from flow_lab" not in src
    assert "import flow_lab" not in src
    panel = (root / "control_panel.py").read_text(encoding="utf-8")
    assert "from flow_lab" not in panel
    assert "HUMAN_CAPTURE" not in ALL_STRATEGY_NAMES
    assert "YOU" not in ALL_STRATEGY_NAMES
    runner = (root / "run_strategy.py").read_text(encoding="utf-8")
    assert "human_capture" not in runner
    assert "ENABLE_HUMAN" not in runner
    paper = (root / "station.html").read_text(encoding="utf-8").split("const PAPER_BOOKS")[1].split("];")[0]
    assert "HUMAN" not in paper
    assert "human_capture" not in SLIM_PAPER_STRATEGIES
    assert "HUMAN_CAPTURE" not in PAPER_ONLY_BOOKS


if __name__ == "__main__":
    import tempfile

    td = Path(tempfile.mkdtemp())
    for name in ("a", "b", "c", "d"):
        (td / name).mkdir()
    test_record_buy_does_not_trade(td / "a")
    test_no_trade_is_the_filter(td / "b")
    test_settle_fills_horizons(td / "c")
    test_summary_counts_skips(td / "d")
    (td / "e").mkdir()
    test_refuses_when_market_closed(td / "e")
    test_not_a_paper_book()
    print("ALL test_human_capture OK")
