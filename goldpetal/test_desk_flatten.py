"""Per-book desk Exit — queue a flatten; runner CLOSEs that strategy only."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from types import SimpleNamespace

from desk_flatten import (
    apply_pending_flattens,
    flatten_ram,
    known_flatten_names,
    request_flatten,
    take_flatten_requests,
)

IST = ZoneInfo("Asia/Kolkata")


def test_unknown_book_rejected(tmp_path: Path) -> None:
    res = request_flatten("NOT_A_BOOK", path=tmp_path / "q.json")
    assert res["ok"] is False
    assert res["queued"] is False


def test_you_manual_not_via_exit(tmp_path: Path) -> None:
    res = request_flatten("YOU_MANUAL", path=tmp_path / "q.json")
    assert res["ok"] is False
    assert "You tab" in res["error"]


def test_queue_take_finish(tmp_path: Path) -> None:
    path = tmp_path / "flatten_queue.json"
    now = datetime(2026, 8, 20, 12, 0, tzinfo=IST)
    res = request_flatten("S5_MINEDGE", path=path, now=now)
    assert res["ok"] is True
    assert res["queued"] is True
    assert res["request"]["strategy"] == "S5_MINEDGE"
    again = request_flatten("S5_MINEDGE", path=path, now=now)
    assert again["already_queued"] is True
    assert again["queued"] is False
    jobs = take_flatten_requests(path=path, now=now)
    assert len(jobs) == 1
    assert jobs[0]["status"] == "in_flight"
    assert take_flatten_requests(path=path, now=now) == []


def test_two_books_independent(tmp_path: Path) -> None:
    path = tmp_path / "flatten_two.json"
    now = datetime(2026, 8, 20, 12, 0, tzinfo=IST)
    assert request_flatten("S5_MINEDGE", path=path, now=now)["queued"]
    assert request_flatten("S8_NET_ZIGZAG", path=path, now=now)["queued"]
    jobs = take_flatten_requests(path=path, now=now)
    names = {j["strategy"] for j in jobs}
    assert names == {"S5_MINEDGE", "S8_NET_ZIGZAG"}


def test_flatten_ram_long() -> None:
    obj = SimpleNamespace(position="long", entry_price=90000.0, last_skip=None)
    info = flatten_ram(obj, px=90100.0)
    assert info["already_flat"] is False
    assert info["was_side"] == "long"
    assert obj.position == "flat"
    assert obj.entry_price is None
    assert obj.last_skip == "desk_exit"


def test_flatten_ram_already_flat() -> None:
    obj = SimpleNamespace(position="flat", entry_price=None)
    info = flatten_ram(obj)
    assert info["already_flat"] is True
    assert obj.position == "flat"


def test_flatten_ram_uses_flatten_method() -> None:
    calls: list[tuple] = []

    class Book:
        position = "short"
        entry_price = 1.0

        def _flatten(self, px: float, why: str) -> None:
            calls.append((px, why))
            self.position = "flat"
            self.entry_price = None

    obj = Book()
    info = flatten_ram(obj, px=88.0, why="desk Exit button")
    assert info["was_side"] == "short"
    assert calls == [(88.0, "desk Exit button")]
    assert obj.position == "flat"


def test_apply_records_close(tmp_path: Path) -> None:
    path = tmp_path / "flatten_close.json"
    now = datetime(2026, 8, 20, 12, 0, tzinfo=IST)
    request_flatten("S16_HHHL_WICK_1H", path=path, now=now)
    obj = SimpleNamespace(position="long", entry_price=10.0, last_skip=None)
    recorded: list[dict] = []

    def record_close(**kwargs):
        recorded.append(kwargs)
        return {"ok": True, "skipped": True}

    rows = apply_pending_flattens(
        {"S16_HHHL_WICK_1H": obj},
        record_close,
        now=now,
        cmp=11.0,
        path=path,
    )
    assert len(rows) == 1
    assert rows[0]["status"] == "done"
    assert obj.position == "flat"
    assert recorded[0]["action"] == "CLOSE"
    assert recorded[0]["strategy"] == "S16_HHHL_WICK_1H"
    assert "desk Exit button" in recorded[0]["reason"]


def test_apply_already_flat_skips_record(tmp_path: Path) -> None:
    path = tmp_path / "flatten_flat.json"
    now = datetime(2026, 8, 20, 12, 0, tzinfo=IST)
    request_flatten("S8_NET_ZIGZAG", path=path, now=now)
    obj = SimpleNamespace(position="flat")
    recorded: list = []
    apply_pending_flattens(
        {"S8_NET_ZIGZAG": obj},
        lambda **k: recorded.append(k),
        now=now,
        cmp=1.0,
        path=path,
    )
    assert recorded == []


def test_apply_missing_ram(tmp_path: Path) -> None:
    path = tmp_path / "flatten_missing.json"
    now = datetime(2026, 8, 20, 12, 0, tzinfo=IST)
    request_flatten("S13_HHHL_DAY", path=path, now=now)
    rows = apply_pending_flattens({}, lambda **k: None, now=now, cmp=1.0, path=path)
    assert rows[0]["status"] == "error"
    assert "RAM" in rows[0]["error"]


def test_stale_pending_is_error(tmp_path: Path) -> None:
    path = tmp_path / "flatten_stale.json"
    t0 = datetime(2026, 8, 20, 10, 0, tzinfo=IST)
    request_flatten("S5_MINEDGE", path=path, now=t0)
    later = t0 + timedelta(seconds=901)
    jobs = take_flatten_requests(path=path, now=later)
    assert jobs == []


def test_known_includes_desk_books() -> None:
    names = known_flatten_names()
    assert "S5_MINEDGE" in names
    assert "S8_NET_ZIGZAG" in names
    assert "S13_HHHL_DAY" in names
    assert "S16_HHHL_WICK_1H" in names
    assert "S18_OHLC_VOL_HTF" in names
    assert "YOU_MANUAL" not in names


if __name__ == "__main__":
    import tempfile

    td = tempfile.TemporaryDirectory()
    p = Path(td.name)
    test_unknown_book_rejected(p)
    test_you_manual_not_via_exit(p)
    test_queue_take_finish(p)
    test_two_books_independent(p)
    test_flatten_ram_long()
    test_flatten_ram_already_flat()
    test_flatten_ram_uses_flatten_method()
    test_apply_records_close(p)
    test_apply_already_flat_skips_record(p)
    test_apply_missing_ram(p)
    test_stale_pending_is_error(p)
    test_known_includes_desk_books()
    td.cleanup()
    print("ALL test_desk_flatten OK")
