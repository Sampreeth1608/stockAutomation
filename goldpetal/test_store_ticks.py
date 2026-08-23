"""STORE_TICKS=false: S16 day calc in RAM, no tick archive."""

from __future__ import annotations

import os
from pathlib import Path


def test_store_ticks_default_off() -> None:
    os.environ.pop("STORE_TICKS", None)
    from storage import store_ticks_enabled

    assert store_ticks_enabled() is False


def test_store_ticks_true() -> None:
    os.environ["STORE_TICKS"] = "true"
    try:
        from storage import store_ticks_enabled

        assert store_ticks_enabled() is True
    finally:
        os.environ.pop("STORE_TICKS", None)


def test_write_last_quote_roundtrip(tmp_path: Path | None = None) -> None:
    from storage import read_last_quote, write_last_quote

    if tmp_path is None:
        import tempfile

        tmp_path = Path(tempfile.mkdtemp())
    path = Path(tmp_path) / "last_quote.json"
    write_last_quote(ltp=15710.5, received_at="2026-08-23T10:15:01+05:30", path=path)
    data = read_last_quote(path)
    assert data["ltp"] == 15710.5
    assert data["received_at"] == "2026-08-23T10:15:01+05:30"
    assert data["store_ticks"] is False


if __name__ == "__main__":
    test_store_ticks_default_off()
    test_store_ticks_true()
    test_write_last_quote_roundtrip()
    print("ALL test_store_ticks OK")
