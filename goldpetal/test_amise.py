"""AMISE — research system, not a paper book."""

from __future__ import annotations

from pathlib import Path

from amise import ENGINE_NAME, amise_desk_payload, similar_states, write_memory
from control_state import ALL_STRATEGY_NAMES, SLIM_PAPER_STRATEGIES
from live_readiness import PAPER_ONLY_BOOKS
from market_mood import classify_samples
from profit_guardian import classify_status, profit_factor, scan_guardian, score_closed_trades
from storage import init_db


def test_profit_factor_and_status() -> None:
    assert profit_factor([100.0, 80.0], [-50.0]) == 3.6
    assert classify_status(n=4, pf=2.0, recent_pf=2.0, hist_pf=2.0, dd=0, peak=0) == "THIN"
    assert (
        classify_status(n=20, pf=1.8, recent_pf=0.9, hist_pf=1.8, dd=10, peak=200)
        == "DETERIORATING"
    )
    assert classify_status(n=20, pf=1.6, recent_pf=1.5, hist_pf=1.6, dd=20, peak=400) == "HEALTHY"


def test_score_closed_trades() -> None:
    closed = [
        {"pnl_after_charges": 40, "status": "CLOSED"}
        for _ in range(12)
    ] + [
        {"pnl_after_charges": -20, "status": "CLOSED"}
        for _ in range(4)
    ]
    row = score_closed_trades(closed, strategy="S8_NET_ZIGZAG")
    assert row["n"] == 16
    assert row["profit_factor"] > 1.0
    assert row["status"] in {"HEALTHY", "WATCH"}


def test_guardian_s13_note() -> None:
    row = score_closed_trades([], strategy="S13_HHHL_DAY")
    assert "never dumps" in row["note"].lower() or "Daily swing" in row["note"]


def test_scan_guardian_empty() -> None:
    g = scan_guardian(trades=[])
    names = {b["strategy"] for b in g["books"]}
    assert "S8_NET_ZIGZAG" in names
    assert "S16_HHHL_WICK_1H" in names
    assert "S13_HHHL_DAY" in names
    assert "S19_BODY_CLOSE_1H" not in names


def test_amise_desk_payload_empty_db(tmp_path: Path) -> None:
    db = tmp_path / "ticks.db"
    init_db(db)
    d = amise_desk_payload(db=db)
    assert d["ok"] is True
    assert d["engine"] == ENGINE_NAME
    assert d["live_blocked"] is True
    assert d["enable_blocked"] is True
    assert "MARKET STATE" in d["pipeline"]
    assert "YOUR APPROVAL" in d["pipeline"]
    assert "fits" in d["manager"]
    assert "books" in d["guardian"]
    assert d["dry_run_required"] is True


def test_similar_states_quiet(tmp_path: Path) -> None:
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from storage import save_tick

    ist = ZoneInfo("Asia/Kolkata")
    db = tmp_path / "ticks.db"
    init_db(db)
    t0 = datetime(2026, 8, 18, 10, 0, tzinfo=ist)
    for i in range(80):
        px = 1540000 + (2 if i % 2 else -2)
        when = t0 + timedelta(seconds=i * 5)
        msg = {
            "last_traded_price": px,
            "total_buy_quantity": 6000,
            "total_sell_quantity": 5900,
            "volume_trade_for_the_day": 1000,
            "open_interest": 1,
            "best_5_buy_data": [],
            "best_5_sell_data": [],
        }
        save_tick(msg, "GOLDPETAL", "1", when.isoformat(timespec="seconds"), db_path=db)
    st = classify_samples(
        [(15400.0, 6000.0, 5900.0)] * 24, gate=False
    )
    out = similar_states(db, st, limit=80, stride=8, window=24)
    assert "n" in out
    assert "after_5m" in out


def test_write_memory(tmp_path: Path) -> None:
    p = tmp_path / "memory.json"
    write_memory({"engine": ENGINE_NAME, "note": "test"}, path=p)
    assert p.is_file()


def test_not_a_paper_book() -> None:
    root = Path(__file__).resolve().parent
    assert "AMISE" not in ALL_STRATEGY_NAMES
    assert ENGINE_NAME not in SLIM_PAPER_STRATEGIES
    assert "AMISE" not in PAPER_ONLY_BOOKS
    paper = (root / "station.html").read_text(encoding="utf-8").split("const PAPER_BOOKS")[1].split("];")[0]
    assert '"AMISE"' not in paper.replace("S21_AMISE", "").replace("S22_AMISE", "").replace("S23_AMISE", "").replace("S24_AMISE", "")
    assert "S21_AMISE" in paper
    station = (root / "station.html").read_text(encoding="utf-8")
    assert 'data-tab="amise"' in station
    assert "/api/amise" in station
    assert "Profit Guardian" in station
    assert "Run factory" in station
    panel = (root / "control_panel.py").read_text(encoding="utf-8")
    assert "/api/amise" in panel
    assert "amise_desk_payload" in panel
    assert "/api/amise/lab" in panel
    env = (root / ".env.example").read_text(encoding="utf-8")
    assert "amise.py" in env
    assert "ENABLE_AMISE=" not in env
    assert "ENABLE_S21=false" in env
    assert "AMISE_FAST_LAB=true" in env
    assert "ENABLE_S11=false" in env
    sh = (root / "weekly_amise.sh").read_text(encoding="utf-8")
    assert "amise.py" in sh
    assert "ENABLE" in sh
    assert "S21_AMISE" in ALL_STRATEGY_NAMES
    assert "S21_AMISE" in SLIM_PAPER_STRATEGIES


if __name__ == "__main__":
    from pathlib import Path as P
    import tempfile

    test_profit_factor_and_status()
    test_score_closed_trades()
    test_guardian_s13_note()
    test_scan_guardian_empty()
    test_write_memory(P(tempfile.mkdtemp()))
    test_not_a_paper_book()
    d = P(tempfile.mkdtemp())
    test_amise_desk_payload_empty_db(d)
    test_similar_states_quiet(P(tempfile.mkdtemp()))
    print("ALL test_amise OK")
