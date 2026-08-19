"""Machine-readable strategy genome for the research factory.

A genome is entry / filter / no-trade / exit / parameters — not a live
book. The factory mutates and recombines genomes; the operator decides
whether anything reaches paper.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any

# Keep these as strings so the Lab tab can import this module without flow_lab.
EXIT_FLIP = "flip_eod"
EXIT_ATR = "atr"

LAB_NAME = "RESEARCH_FACTORY"
RESEARCH_KIND = "research"
RESEARCH_STRATEGY = "RESEARCH_FACTORY"

FORBIDDEN_ENV_PREFIXES: tuple[str, ...] = (
    "ENABLE_",
    "LIVE_",
    "S11_",
    "S18_",
    "S16_",
    "S13_",
    "S5_",
    "S8_",
)


@dataclass
class StrategyGenome:
    name: str
    genome_id: str = ""
    direction: str = "both"  # long | short | both
    timeframe: str = "1h"
    entry_long: tuple[str, ...] = ()
    entry_short: tuple[str, ...] = ()
    no_trade: tuple[str, ...] = ("spread_wide", "rvol_extreme")
    exit: str = EXIT_FLIP
    params: dict[str, float] = field(default_factory=dict)
    source: str = "compose"  # discovery | recipe | compose | mutate
    recipe: str = ""
    notes: str = ""

    def normalized(self) -> StrategyGenome:
        long_e = tuple(dict.fromkeys(str(x) for x in self.entry_long if x))
        short_e = tuple(dict.fromkeys(str(x) for x in self.entry_short if x))
        no_t = tuple(dict.fromkeys(str(x) for x in self.no_trade if x))
        exit_mode = self.exit if self.exit in {EXIT_FLIP, EXIT_ATR} else EXIT_FLIP
        direction = self.direction if self.direction in {"long", "short", "both"} else "both"
        params = {str(k): float(v) for k, v in (self.params or {}).items()}
        g = replace(
            self,
            entry_long=long_e,
            entry_short=short_e,
            no_trade=no_t,
            exit=exit_mode,
            direction=direction,
            params=params,
            recipe=str(self.recipe or ""),
            name=str(self.name or "unnamed")[:80],
        )
        return replace(g, genome_id=genome_fingerprint(g))

    def to_dict(self) -> dict[str, Any]:
        g = self.normalized()
        d = asdict(g)
        d["entry_long"] = list(g.entry_long)
        d["entry_short"] = list(g.entry_short)
        d["no_trade"] = list(g.no_trade)
        return d


def genome_from_dict(raw: dict[str, Any] | None) -> StrategyGenome:
    row = dict(raw or {})
    if "no_trade" in row:
        no_trade = tuple(row.get("no_trade") or ())
    else:
        no_trade = ("spread_wide", "rvol_extreme")
    return StrategyGenome(
        name=str(row.get("name") or "unnamed"),
        genome_id=str(row.get("genome_id") or ""),
        direction=str(row.get("direction") or "both"),
        timeframe=str(row.get("timeframe") or "1h"),
        entry_long=tuple(row.get("entry_long") or ()),
        entry_short=tuple(row.get("entry_short") or ()),
        no_trade=no_trade,
        exit=str(row.get("exit") or EXIT_FLIP),
        params={str(k): float(v) for k, v in (row.get("params") or {}).items()},
        source=str(row.get("source") or "compose"),
        recipe=str(row.get("recipe") or ""),
        notes=str(row.get("notes") or ""),
    ).normalized()


def genome_fingerprint(g: StrategyGenome) -> str:
    payload = {
        "direction": g.direction,
        "timeframe": g.timeframe,
        "entry_long": list(g.entry_long),
        "entry_short": list(g.entry_short),
        "no_trade": list(g.no_trade),
        "exit": g.exit,
        "params": {k: g.params[k] for k in sorted(g.params)},
        "recipe": g.recipe,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:10]


def research_env_patch() -> dict[str, str]:
    """Pending Lab rows may only force paper. ENABLE_S{n} is added on Approve."""
    return {"DRY_RUN": "true"}


def env_patch_is_safe(patch: dict[str, str] | None) -> bool:
    from amise_slots import is_amise_enable_key

    for key in (patch or {}):
        up = str(key).upper()
        val = str(patch[key]).strip().lower()
        if is_amise_enable_key(up):
            if val not in {"true", "1", "yes", "y"}:
                return False
            continue
        if any(up.startswith(p) for p in FORBIDDEN_ENV_PREFIXES):
            return False
        if up == "DRY_RUN" and val not in {
            "true",
            "1",
            "yes",
            "y",
        }:
            return False
    return True


def is_research_proposal(row: Any) -> bool:
    if row is None:
        return False
    if isinstance(row, dict):
        kind = str(row.get("kind") or "")
        strategy = str(row.get("strategy") or "")
    else:
        kind = str(getattr(row, "kind", "") or "")
        strategy = str(getattr(row, "strategy", "") or "")
    return kind == RESEARCH_KIND or strategy == RESEARCH_STRATEGY


def params_from_genome(g: StrategyGenome, base: Any | None = None) -> Any:
    from flow_lab import FlowParams

    p = base or FlowParams()
    raw = g.params or {}
    kwargs: dict[str, Any] = {}
    for f in fields(FlowParams):
        if f.name not in raw:
            continue
        cur = getattr(p, f.name)
        val = raw[f.name]
        if isinstance(cur, bool):
            kwargs[f.name] = bool(val)
        elif isinstance(cur, int) and not isinstance(cur, bool):
            kwargs[f.name] = int(val)
        else:
            kwargs[f.name] = float(val)
    return replace(p, **kwargs) if kwargs else p


def mutate_genome(g: StrategyGenome, *, scale: float = 0.10) -> StrategyGenome:
    """Jitter numeric thresholds. Used by robustness, not live."""
    src = g.normalized()
    params = dict(src.params)
    for key in ("rvol", "imb_th", "depth_th", "breakout_rvol", "score_th"):
        if key in params and abs(float(params[key])) > 1e-12:
            params[key] = float(params[key]) * (1.0 + float(scale))
        elif key not in params and abs(scale) > 1e-12:
            from flow_lab import FlowParams

            base = getattr(FlowParams(), key, None)
            if isinstance(base, (int, float)) and not isinstance(base, bool):
                params[key] = float(base) * (1.0 + float(scale))
    return replace(
        src,
        params=params,
        source="mutate",
        name=(src.name + f":mut{scale:+.0%}")[:80],
        genome_id="",
    ).normalized()


def combine_genomes(a: StrategyGenome, b: StrategyGenome) -> StrategyGenome:
    """AND-compose two atom genomes. Recipe wrappers are left alone."""
    left, right = a.normalized(), b.normalized()
    if left.recipe or right.recipe:
        raise ValueError("cannot combine named-recipe genomes; mutate atoms instead")
    long_e = tuple(dict.fromkeys([*left.entry_long, *right.entry_long]))
    short_e = tuple(dict.fromkeys([*left.entry_short, *right.entry_short]))
    no_t = tuple(dict.fromkeys([*left.no_trade, *right.no_trade]))
    params = dict(left.params)
    params.update(right.params)
    return StrategyGenome(
        name=f"{left.name}+{right.name}"[:80],
        direction="both",
        timeframe=left.timeframe,
        entry_long=long_e,
        entry_short=short_e,
        no_trade=no_t,
        exit=left.exit,
        params=params,
        source="compose",
        notes=f"combine {left.genome_id}+{right.genome_id}",
    ).normalized()
