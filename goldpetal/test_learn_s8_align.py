"""Tests for S8 learning loop (synthetic ticks)."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from learn_s8_align import (
    apply_preset_dict,
    baseline_cfg,
    diagnose_rules,
    run_align_labeled,
    score_result,
    search_space,
    summarize,
)
from strategy_s8_align import AlignS8Config, _apply_model_preset

IST = ZoneInfo("Asia/Kolkata")


def _make_db(n: int = 400) -> Path:
    td = tempfile.mkdtemp()
    db = Path(td) / "ticks.db"
    con = sqlite3.connect(db)
    con.execute(
        """
        CREATE TABLE ticks (
            id INTEGER PRIMARY KEY,
            received_at TEXT,
            ltp REAL,
            bp REAL,
            sp REAL,
            raw_json TEXT
        )
        """
    )
    t0 = datetime(2026, 8, 10, 10, 0, tzinfo=IST)
    px, tbq, tsq = 15000.0, 12000.0, 9000.0
    for i in range(n):
        # gentle bull grind with book noise
        px += 0.4 if i % 7 != 0 else -0.8
        tbq += 40 if i % 5 != 0 else -25
        tsq += 15 if i % 6 != 0 else 30
        ts = (t0 + timedelta(seconds=i * 3)).isoformat()
        raw = json.dumps(
            {"total_buy_quantity": tbq, "total_sell_quantity": tsq}
        )
        con.execute(
            "INSERT INTO ticks(received_at, ltp, bp, sp, raw_json) VALUES (?,?,?,?,?)",
            (ts, px, tbq, tsq, raw),
        )
    con.commit()
    con.close()
    return db


def test_run_align_labeled_and_diagnose():
    db = _make_db(500)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id, received_at, ltp, bp, sp, raw_json FROM ticks ORDER BY id"
    ).fetchall()
    con.close()
    cfg = baseline_cfg(bar_minutes=0, bar_ticks=20)
    res = run_align_labeled(list(rows), cfg, lots=100.0)
    assert res["n_ticks"] == 500
    sm = summarize("SYN", res)
    assert "n" in sm
    diag = diagnose_rules(res["trades"])
    assert "tips" in diag and "nudges" in diag


def test_search_and_preset_roundtrip():
    diag = {"nudges": {"book_drop_min_pct": 0.5, "min_imb_pct": 5.0}}
    cands = search_space(diag)
    assert len(cands) >= 10
    name, cfg = cands[0]
    assert isinstance(cfg, AlignS8Config)
    d = {
        **{k: getattr(cfg, k) for k in cfg.__dataclass_fields__},
        "preset_name": name,
        "meta": {"delta_inr": 1.0},
    }
    cfg2 = apply_preset_dict(d)
    assert cfg2.entry_model == cfg.entry_model
    assert cfg2.hold_model == cfg.hold_model


def test_learned_model_loads_json(tmp_path: Path | None = None):
    import os

    td = Path(tempfile.mkdtemp())
    preset = {
        "preset_name": "unit_learned",
        "entry_model": "imb_sign",
        "hold_model": "book_or_support",
        "exit_model": "break_flip",
        "entry_mode": "net_sign",
        "bar_minutes": 10,
        "bar_ticks": 50,  # should be cleared by minutes
        "min_imb_pct": 6.0,
        "book_drop_min_pct": 0.4,
        "require_rising_imb": False,
        "hold_while_book_rises": True,
        "hold_on_supported": True,
        "close_on_book_drop": True,
        "meta": {},
    }
    path = td / "learned_latest.json"
    path.write_text(json.dumps(preset), encoding="utf-8")
    os.environ["S8_LEARNED_PRESET"] = str(path)
    cfg = AlignS8Config()
    cfg = _apply_model_preset("learned", cfg)
    assert cfg.model_name == "learned"
    assert cfg.bar_minutes == 10
    assert cfg.bar_ticks == 0
    assert cfg.min_imb_pct == 6.0
    assert cfg.entry_model == "imb_sign"
    os.environ.pop("S8_LEARNED_PRESET", None)


def test_score_prefers_profit():
    a = {"n": 5, "dir_pct": 60, "sum_inr": 1000.0}
    b = {"n": 5, "dir_pct": 60, "sum_inr": 100.0}
    assert score_result(a) > score_result(b)
    assert score_result({"n": 0, "dir_pct": 0, "sum_inr": 0}) < -1e6


def test_report_real_vs_improved():
    """Synthetic ticks + fake S8 signals → report totals."""
    import os
    import subprocess
    import sys

    db = _make_db(300)
    # minimal signals table + one round trip
    con = sqlite3.connect(db)
    con.execute(
        """
        CREATE TABLE signals (
            id INTEGER PRIMARY KEY,
            time_label TEXT,
            symbol TEXT,
            strategy TEXT,
            action TEXT,
            position_after TEXT,
            reason TEXT,
            price_delta REAL,
            net REAL,
            net_delta REAL,
            dry_run INTEGER,
            cmp REAL
        )
        """
    )
    con.execute(
        "INSERT INTO signals(time_label,symbol,strategy,action,position_after,reason,dry_run,cmp) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            "2026-08-10T10:00:00+05:30",
            "G",
            "S8_NET_ZIGZAG",
            "BUY",
            "long",
            "test",
            1,
            15000.0,
        ),
    )
    con.execute(
        "INSERT INTO signals(time_label,symbol,strategy,action,position_after,reason,dry_run,cmp) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            "2026-08-10T10:10:00+05:30",
            "G",
            "S8_NET_ZIGZAG",
            "CLOSE",
            "flat",
            "tp",
            1,
            15020.0,
        ),
    )
    con.commit()
    con.close()
    out = Path(tempfile.mkdtemp())
    cmd = [
        sys.executable,
        "learn_s8_align.py",
        "report",
        "--db",
        str(db),
        "--day",
        "2026-08-10",
        "--lots",
        "100",
        "--bar-minutes",
        "0",
        "--bar-ticks",
        "20",
        "--retrain",
        "--quick",
        "--budget-min",
        "0.1",
        "--out",
        str(out),
        "--preset",
        str(out / "learned_latest.json"),
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent)
    r = subprocess.run(
        cmd,
        cwd=str(Path(__file__).resolve().parent),
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SUMMARY" in r.stdout
    assert "REAL paper" in r.stdout
    assert "IMPROVED" in r.stdout


def main() -> None:
    test_run_align_labeled_and_diagnose()
    test_search_and_preset_roundtrip()
    test_learned_model_loads_json()
    test_score_prefers_profit()
    test_report_real_vs_improved()
    print("test_learn_s8_align: OK")


if __name__ == "__main__":
    main()
