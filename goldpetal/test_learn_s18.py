"""S18 learner scores packs and writes an ML proposal. Does not auto-promote."""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from learn_s18 import candidate_packs, learn
from s18_ohlc_vol_htf import BASE_PACK


def test_candidates_include_base_and_tick_extras() -> None:
    names = {p.name for p in candidate_packs()}
    assert "base" in names
    assert "drop_vol" in names
    assert "plus_net" in names
    assert "plus_ticks" in names
    assert "plus_beyond_hl" in names
    assert "plus_tbq" in names
    assert "tick_full" in names
    assert BASE_PACK.require_vol_up is True
    assert BASE_PACK.require_beyond_prev_hl is False


def test_learn_writes_latest_not_active() -> None:
    ist = ZoneInfo("Asia/Kolkata")
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        db = td / "ticks.db"
        con = sqlite3.connect(str(db))
        con.execute(
            "CREATE TABLE ticks (id INTEGER PRIMARY KEY, received_at TEXT, ltp REAL, "
            "volume REAL, bp REAL, sp REAL)"
        )
        start = datetime(2026, 8, 17, 9, 0, tzinfo=ist)
        px = 100.0
        vol = 1000.0
        for i in range(40):
            ts = start + timedelta(hours=i)
            o = px
            h = o + 6
            l = o - 2
            c = o + (5 if i % 2 == 0 else -1)
            vol += 200
            for minute, p in ((0, o), (10, h), (20, l), (50, c)):
                con.execute(
                    "INSERT INTO ticks (received_at, ltp, volume, bp, sp) VALUES (?,?,?,?,?)",
                    (
                        (ts.replace(minute=minute)).isoformat(timespec="seconds"),
                        p,
                        vol,
                        50.0 + i,
                        40.0,
                    ),
                )
            px = c
        con.commit()
        con.close()
        out = td / "learn"
        props = td / "proposals.json"
        rep = learn(
            db,
            lots=1.0,
            fees=True,
            session=False,
            train_frac=0.6,
            min_test_trades=1,
            out_dir=out,
            pack_path=out / "active.json",
            proposals_path=props,
        )
        assert "pack" in rep
        assert rep["live"] is False
        assert rep["promoted"] is False
        assert rep["metric"] == "after_charges_ex_tax"
        assert (out / "latest.json").exists()
        assert not (out / "active.json").exists()
        assert rep["pack"]["name"]
        assert "ranked" in rep
        for row in rep["ranked"]:
            assert "after_charges_inr" in row["test"]
            assert "after_tax_inr" in row["test"]
        if rep["proposed"]:
            assert (out / "proposed.json").exists()
            assert rep["proposal_id"]
            assert props.exists()
        else:
            assert not (out / "proposed.json").exists()


if __name__ == "__main__":
    test_candidates_include_base_and_tick_extras()
    test_learn_writes_latest_not_active()
    print("ALL test_learn_s18 OK")
