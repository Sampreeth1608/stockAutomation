"""Safe .env read/write for the Streamlit desk (whitelist only)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"

_AMISE_ENABLE_RE = re.compile(r"^ENABLE_S(\d+)$")


def env_key_allowed(key: str) -> bool:
    if key in ALLOWED_ENV_KEYS:
        return True
    m = _AMISE_ENABLE_RE.fullmatch(str(key or "").strip())
    return bool(m and int(m.group(1)) >= 21)


# Only these keys may be written from the desk (plus ENABLE_S{n} for n≥21).
ALLOWED_ENV_KEYS: frozenset[str] = frozenset(
    {
        "ENABLE_S1",
        "ENABLE_S2",
        "ENABLE_S3",
        "ENABLE_S4",
        "ENABLE_S5",
        "ENABLE_S6",
        "ENABLE_S8",
        "ENABLE_S9",
        "ENABLE_S10",
        "ENABLE_S11",
        "ENABLE_S12",
        "ENABLE_S13",
        "ENABLE_S14",
        "ENABLE_S15",
        "ENABLE_S16",
        "ENABLE_S18",
        "ENABLE_S19",
        "ENABLE_S20",
        "ENABLE_S21",
        "ENABLE_S22",
        "ENABLE_S23",
        "ENABLE_S24",
        "DRY_RUN",
        "MOOD_GATE",
        "MOOD_FLATTEN",
        "LIVE_MAX_LOTS",
        "LIVE_LOTS",
        "S11_PACK_PATH",
        "S12_BAR_MINUTES",
        "S12_MIN_RANGE",
        "S12_NO_FLIP",
        "S12_ALLOW_LONG",
        "S12_ALLOW_SHORT",
        "S12_CONFIRM_MINUTES",
        "S4_ENTRY_MINUTES_BEFORE_CLOSE",
        "S4_ALLOW_LONG",
        "S4_ALLOW_SHORT",
        "S13_MIN_RANGE",
        "S13_ENTRY_MINUTES_BEFORE_CLOSE",
        "S13_EXIT_MINUTES_AFTER_OPEN",
        "S13_ALLOW_LONG",
        "S13_ALLOW_SHORT",
        "S13_NO_FLIP",
        "S13_MIN_WICK_GAP",
        "S14_BAR_MINUTES",
        "S14_MIN_RANGE",
        "S14_CONFIRM_MINUTES",
        "S14_NOWICK_EPS",
        "S14_OPEN_HOLD_MINUTES",
        "S14_ALLOW_LONG",
        "S14_ALLOW_SHORT",
        "S15_BAR_MINUTES",
        "S15_MIN_RANGE",
        "S15_CONFIRM_MINUTES",
        "S15_NOWICK_EPS",
        "S15_ALLOW_LONG",
        "S15_ALLOW_SHORT",
        "S16_BAR_MINUTES",
        "S16_MIN_WICK_GAP",
        "S16_ALLOW_LONG",
        "S16_ALLOW_SHORT",
        "IGNORE_FEES",
        "FLATTEN_ON_BAD_REGIME",
    }
)

STRATEGY_ENABLE: dict[str, str] = {
    "S1_NETDELTA": "ENABLE_S1",
    "S2_BALANCE": "ENABLE_S2",
    "S3_ML": "ENABLE_S3",
    "S4_OVERNIGHT": "ENABLE_S4",
    "S5_MINEDGE": "ENABLE_S5",
    "S6_MIN30": "ENABLE_S6",
    "S8_NET_ZIGZAG": "ENABLE_S8",
    "S9_STATE30": "ENABLE_S9",
    "S10_LEGACY30": "ENABLE_S10",
    "S11_DISCOVERED": "ENABLE_S11",
    "S12_HHHL30": "ENABLE_S12",
    "S13_HHHL_DAY": "ENABLE_S13",
    "S14_WICK30_STRICT": "ENABLE_S14",
    "S15_WICK30_NOWICK": "ENABLE_S15",
    "S16_HHHL_WICK_1H": "ENABLE_S16",
    "S18_OHLC_VOL_HTF": "ENABLE_S18",
    "S19_BODY_CLOSE_1H": "ENABLE_S19",
    "S20_FADE_HL": "ENABLE_S20",
    "S21_AMISE": "ENABLE_S21",
    "S22_AMISE": "ENABLE_S22",
    "S23_AMISE": "ENABLE_S23",
    "S24_AMISE": "ENABLE_S24",
}

SLIM_ENABLE_DEFAULTS: dict[str, str] = {
    "ENABLE_S1": "false",
    "ENABLE_S2": "false",
    "ENABLE_S3": "false",
    "ENABLE_S4": "false",
    "ENABLE_S5": "true",
    "ENABLE_S6": "false",
    "ENABLE_S8": "true",
    "ENABLE_S9": "false",
    "ENABLE_S10": "false",
    "ENABLE_S11": "false",
    "ENABLE_S12": "false",
    "ENABLE_S13": "true",
    "ENABLE_S14": "false",
    "ENABLE_S15": "false",
    "ENABLE_S16": "true",
    "ENABLE_S18": "true",
    "ENABLE_S19": "false",
    "ENABLE_S20": "false",
    "ENABLE_S21": "false",
    "ENABLE_S22": "false",
    "ENABLE_S23": "false",
    "ENABLE_S24": "false",
}


def env_path() -> Path:
    return ENV_PATH


def read_env(path: Path | None = None) -> dict[str, str]:
    path = path or ENV_PATH
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, val = raw.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        out[key] = val
    return out


def _validate_value(key: str, value: str) -> str:
    value = str(value).strip()
    if key.startswith("ENABLE_") or key in {
        "DRY_RUN",
        "IGNORE_FEES",
        "FLATTEN_ON_BAD_REGIME",
        "MOOD_GATE",
        "MOOD_FLATTEN",
        "S12_NO_FLIP",
        "S12_ALLOW_LONG",
        "S12_ALLOW_SHORT",
        "S4_ALLOW_LONG",
        "S4_ALLOW_SHORT",
        "S13_ALLOW_LONG",
        "S13_ALLOW_SHORT",
        "S13_NO_FLIP",
        "S14_ALLOW_LONG",
        "S14_ALLOW_SHORT",
        "S15_ALLOW_LONG",
        "S15_ALLOW_SHORT",
        "S16_ALLOW_LONG",
        "S16_ALLOW_SHORT",
    }:
        low = value.lower()
        if low not in {"true", "false", "1", "0", "yes", "no", "y", "n"}:
            raise ValueError(f"{key} must be boolean-like, got {value!r}")
        return "true" if low in {"true", "1", "yes", "y"} else "false"
    if key in {
        "LIVE_MAX_LOTS",
        "LIVE_LOTS",
        "S12_BAR_MINUTES",
        "S12_CONFIRM_MINUTES",
        "S4_ENTRY_MINUTES_BEFORE_CLOSE",
        "S13_ENTRY_MINUTES_BEFORE_CLOSE",
        "S13_EXIT_MINUTES_AFTER_OPEN",
        "S14_BAR_MINUTES",
        "S14_CONFIRM_MINUTES",
        "S15_BAR_MINUTES",
        "S15_CONFIRM_MINUTES",
        "S16_BAR_MINUTES",
    }:
        n = int(float(value))
        if n < 0 or n > 10_000:
            raise ValueError(f"{key} out of range")
        return str(n)
    if key in {
        "S12_MIN_RANGE",
        "S13_MIN_RANGE",
        "S13_MIN_WICK_GAP",
        "S14_MIN_RANGE",
        "S14_NOWICK_EPS",
        "S14_OPEN_HOLD_MINUTES",
        "S15_MIN_RANGE",
        "S15_NOWICK_EPS",
        "S16_MIN_WICK_GAP",
    }:
        f = float(value)
        if f < 0 or f > 1_000_000:
            raise ValueError(f"{key} out of range")
        return str(f)
    if key == "S11_PACK_PATH":
        if value == "":
            return ""
        if ".." in value or value.startswith("~"):
            raise ValueError("S11_PACK_PATH must not contain .. or ~")
        if not re.fullmatch(r"[A-Za-z0-9_./\-]+", value):
            raise ValueError("S11_PACK_PATH has invalid characters")
        p = Path(value)
        data_root = (ROOT / "data").resolve()
        if p.is_absolute():
            try:
                p.resolve().relative_to(data_root)
            except ValueError as exc:
                raise ValueError("S11_PACK_PATH must be under data/") from exc
        elif not value.replace("\\", "/").startswith("data/"):
            raise ValueError("S11_PACK_PATH must start with data/")
        return value
    raise ValueError(f"unsupported key {key}")


def write_env_updates(
    updates: dict[str, Any],
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    """Merge whitelist updates into .env. Returns applied + skipped."""
    path = path or ENV_PATH
    applied: dict[str, str] = {}
    skipped: dict[str, str] = {}
    clean: dict[str, str] = {}
    for key, raw in updates.items():
        key = str(key).strip()
        if not env_key_allowed(key):
            skipped[key] = "not_whitelisted"
            continue
        try:
            clean[key] = _validate_value(key, str(raw))
        except ValueError as exc:
            skipped[key] = str(exc)
    if not clean:
        return {"ok": False, "error": "nothing to apply", "skipped": skipped, "applied": {}}

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in clean:
                out.append(f"{key}={clean[key]}")
                seen.add(key)
                applied[key] = clean[key]
                continue
        out.append(line)
    for key, val in clean.items():
        if key not in seen:
            out.append(f"{key}={val}")
            applied[key] = val
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return {"ok": True, "applied": applied, "skipped": skipped, "path": str(path)}


def strategy_enable_map() -> dict[str, str]:
    from amise_slots import amise_books_now, enable_key

    out = dict(STRATEGY_ENABLE)
    for name in amise_books_now():
        out[name] = enable_key(name)
    return out


def strategy_enable_snapshot(path: Path | None = None) -> dict[str, bool]:
    env = read_env(path)
    out: dict[str, bool] = {}
    for name, key in strategy_enable_map().items():
        default = SLIM_ENABLE_DEFAULTS.get(key, "false")
        raw = env.get(key, default).strip().lower()
        out[name] = raw in {"1", "true", "yes", "y"}
    return out


def apply_strategy_enables(
    enabled_names: list[str],
    *,
    known: list[str] | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Set ENABLE_* true for names in enabled_names; false for other known slim strategies."""
    mapping = strategy_enable_map()
    known = known or list(mapping.keys())
    want = {str(n).strip() for n in enabled_names}
    updates: dict[str, str] = {}
    for name in known:
        key = mapping.get(name)
        if not key:
            continue
        updates[key] = "true" if name in want else "false"
    return write_env_updates(updates, path=path)


def apply_env_patch(patch: dict[str, Any], *, path: Path | None = None) -> dict[str, Any]:
    return write_env_updates({str(k): v for k, v in (patch or {}).items()}, path=path)
