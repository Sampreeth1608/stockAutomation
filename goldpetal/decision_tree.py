#!/usr/bin/env python3
"""Decision-tree combinator — you write rules, the engine expands them.

Research only. Not a paper book. Not live. Does not ENABLE anything.

  python3 decision_tree.py --demo
  python3 decision_tree.py --catalog bull,vol_up,px_gt_vwap --max-and 3
  python3 decision_tree.py --rules "green|close > open|long|color" "vol_up|volume > prev"
  python3 decision_tree_app.py --port 8791
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any

LAB_NAME = "DECISION_TREE"
MAX_RULES_AND = 10
MAX_RULES_TREE = 8
MAX_AND_CAP = 6
DEFAULT_MAX_AND = 3
ACTIONS = ("BUY", "SHORT", "FLAT")
POLARITIES = ("long", "short", "filter", "no_trade")

# Catalog matches the research-factory atoms (labels only — this module
# does not evaluate tape). Same-group names cannot both be TRUE.
CATALOG: tuple[dict[str, str], ...] = (
    {"id": "bull", "name": "green candle", "when": "close > open", "polarity": "long", "group": "color", "family": "candle"},
    {"id": "bear", "name": "red candle", "when": "close < open", "polarity": "short", "group": "color", "family": "candle"},
    {"id": "body_strong", "name": "strong body", "when": "body / range >= 0.60", "polarity": "filter", "group": "", "family": "candle"},
    {"id": "upper_wick", "name": "upper wick", "when": "upper wick / range >= 0.40", "polarity": "short", "group": "wick", "family": "candle"},
    {"id": "lower_wick", "name": "lower wick", "when": "lower wick / range >= 0.40", "polarity": "long", "group": "wick", "family": "candle"},
    {"id": "close_high", "name": "close near high", "when": "close in the top of the range", "polarity": "long", "group": "close_loc", "family": "candle"},
    {"id": "close_low", "name": "close near low", "when": "close in the bottom of the range", "polarity": "short", "group": "close_loc", "family": "candle"},
    {"id": "hh", "name": "higher high", "when": "this high > previous high", "polarity": "long", "group": "hh_lh", "family": "ohlc"},
    {"id": "hl", "name": "higher low", "when": "this low > previous low", "polarity": "long", "group": "hl_ll", "family": "ohlc"},
    {"id": "lh", "name": "lower high", "when": "this high < previous high", "polarity": "short", "group": "hh_lh", "family": "ohlc"},
    {"id": "ll", "name": "lower low", "when": "this low < previous low", "polarity": "short", "group": "hl_ll", "family": "ohlc"},
    {"id": "hc", "name": "higher close", "when": "this close > previous close", "polarity": "long", "group": "hc_lc", "family": "ohlc"},
    {"id": "lc", "name": "lower close", "when": "this close < previous close", "polarity": "short", "group": "hc_lc", "family": "ohlc"},
    {"id": "vol_up", "name": "volume up", "when": "volume > previous bar", "polarity": "filter", "group": "vol_dir", "family": "volume"},
    {"id": "vol_dn", "name": "volume down", "when": "volume < previous bar", "polarity": "filter", "group": "vol_dir", "family": "volume"},
    {"id": "rvol_1p5", "name": "high relative volume", "when": "RVOL >= 1.5×", "polarity": "filter", "group": "", "family": "volume"},
    {"id": "px_gt_vwap", "name": "above VWAP", "when": "price > session VWAP", "polarity": "long", "group": "vwap", "family": "vwap"},
    {"id": "px_lt_vwap", "name": "below VWAP", "when": "price < session VWAP", "polarity": "short", "group": "vwap", "family": "vwap"},
    {"id": "brk20", "name": "20-bar high break", "when": "high breaks prior 20-bar high", "polarity": "long", "group": "break", "family": "structure"},
    {"id": "brk20_dn", "name": "20-bar low break", "when": "low breaks prior 20-bar low", "polarity": "short", "group": "break", "family": "structure"},
    {"id": "imb_buy", "name": "buy imbalance", "when": "TBQ/TSQ imbalance long", "polarity": "long", "group": "imb", "family": "flow"},
    {"id": "imb_sell", "name": "sell imbalance", "when": "TBQ/TSQ imbalance short", "polarity": "short", "group": "imb", "family": "flow"},
    {"id": "oi_up", "name": "OI up", "when": "open interest increasing", "polarity": "filter", "group": "oi_dir", "family": "oi"},
    {"id": "oi_dn", "name": "OI down", "when": "open interest decreasing", "polarity": "filter", "group": "oi_dir", "family": "oi"},
    {"id": "spread_wide", "name": "spread too wide", "when": "spread abnormally wide → no trade", "polarity": "no_trade", "group": "", "family": "no_trade"},
    {"id": "rvol_extreme", "name": "extreme volume", "when": "RVOL >= 4 → no trade", "polarity": "no_trade", "group": "", "family": "no_trade"},
)

CATALOG_BY_ID: dict[str, dict[str, str]] = {row["id"]: row for row in CATALOG}

DEMO_RULES = (
    "green candle|close > open|long|color",
    "volume up|volume > previous bar|filter|vol_dir",
    "above VWAP|price > session VWAP|long|vwap",
)


@dataclass
class Rule:
    id: str
    name: str
    when: str
    polarity: str = "filter"
    group: str = ""
    source: str = "custom"
    catalog_id: str = ""

    def __post_init__(self) -> None:
        self.id = _token(self.id)
        self.name = str(self.name or "").strip() or self.id
        self.when = str(self.when or "").strip()
        pol = str(self.polarity or "filter").strip().lower().replace(" ", "_")
        if pol in ("buy", "long"):
            pol = "long"
        elif pol in ("sell", "short"):
            pol = "short"
        elif pol in ("skip", "flat", "no_trade", "no-trade"):
            pol = "no_trade"
        elif pol not in POLARITIES:
            pol = "filter"
        self.polarity = pol
        self.group = str(self.group or "").strip()
        self.source = str(self.source or "custom").strip() or "custom"
        self.catalog_id = str(self.catalog_id or "").strip()

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class Combination:
    id: str
    kind: str  # and | leaf
    label: str
    sentence: str
    true_ids: list[str]
    false_ids: list[str]
    action: str
    size: int
    impossible: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TreeNode:
    id: str
    kind: str  # split | leaf
    label: str
    rule_id: str = ""
    yes: TreeNode | None = None
    no: TreeNode | None = None
    true_ids: list[str] = field(default_factory=list)
    false_ids: list[str] = field(default_factory=list)
    action: str = "FLAT"
    sentence: str = ""
    impossible: bool = False
    reason: str = ""
    combination_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "rule_id": self.rule_id,
            "yes": None if self.yes is None else self.yes.to_dict(),
            "no": None if self.no is None else self.no.to_dict(),
            "true_ids": list(self.true_ids),
            "false_ids": list(self.false_ids),
            "action": self.action,
            "sentence": self.sentence,
            "impossible": self.impossible,
            "reason": self.reason,
            "combination_id": self.combination_id,
        }


def _token(raw: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_]+", "_", str(raw or "").strip())
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "rule"


def _slug(name: str, used: set[str]) -> str:
    base = _token(name).upper()
    if not base:
        base = "R"
    if base[0].isdigit():
        base = "R_" + base
    cand = base
    n = 2
    while cand in used:
        cand = f"{base}_{n}"
        n += 1
    used.add(cand)
    return cand


def catalog_families() -> list[dict[str, Any]]:
    order = ("candle", "ohlc", "volume", "vwap", "structure", "flow", "oi", "no_trade")
    buckets: dict[str, list[dict[str, str]]] = {k: [] for k in order}
    for row in CATALOG:
        buckets.setdefault(row["family"], []).append(dict(row))
    return [{"family": fam, "rules": buckets[fam]} for fam in order if buckets.get(fam)]


def rule_from_catalog(catalog_id: str, used: set[str] | None = None) -> Rule:
    row = CATALOG_BY_ID.get(str(catalog_id).strip())
    if row is None:
        raise ValueError(f"unknown catalog rule: {catalog_id}")
    used = used if used is not None else set()
    return Rule(
        id=_slug(row["id"], used),
        name=row["name"],
        when=row["when"],
        polarity=row["polarity"],
        group=row["group"],
        source="catalog",
        catalog_id=row["id"],
    )


def parse_rule_line(line: str, used: set[str] | None = None) -> Rule | None:
    text = str(line or "").strip()
    if not text or text.startswith("#"):
        return None
    used = used if used is not None else set()
    if "|" in text:
        parts = [p.strip() for p in text.split("|")]
        name = parts[0] if parts else ""
        when = parts[1] if len(parts) > 1 else ""
        polarity = parts[2] if len(parts) > 2 else "filter"
        group = parts[3] if len(parts) > 3 else ""
        return Rule(id=_slug(name, used), name=name, when=when, polarity=polarity, group=group)
    if ":" in text:
        name, when = text.split(":", 1)
        return Rule(id=_slug(name, used), name=name.strip(), when=when.strip())
    return Rule(id=_slug(text, used), name=text, when="")


def rules_from_text(text: str) -> list[Rule]:
    used: set[str] = set()
    out: list[Rule] = []
    for line in str(text or "").splitlines():
        rule = parse_rule_line(line, used)
        if rule is not None:
            out.append(rule)
    return out


def rules_from_payload(raw: Any) -> list[Rule]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return rules_from_text(raw)
    if not isinstance(raw, list):
        raise ValueError("rules must be a list or text")
    used: set[str] = set()
    out: list[Rule] = []
    for item in raw:
        if isinstance(item, Rule):
            rid = _slug(item.id, used)
            out.append(
                Rule(
                    id=rid,
                    name=item.name,
                    when=item.when,
                    polarity=item.polarity,
                    group=item.group,
                    source=item.source,
                    catalog_id=item.catalog_id,
                )
            )
            continue
        if isinstance(item, str):
            if item.strip() in CATALOG_BY_ID and "|" not in item and ":" not in item:
                out.append(rule_from_catalog(item.strip(), used))
            else:
                parsed = parse_rule_line(item, used)
                if parsed is not None:
                    out.append(parsed)
            continue
        if not isinstance(item, dict):
            raise ValueError("each rule must be an object")
        catalog_id = str(item.get("catalog_id") or item.get("catalog") or "").strip()
        if catalog_id and not str(item.get("name") or "").strip():
            out.append(rule_from_catalog(catalog_id, used))
            continue
        name = str(item.get("name") or item.get("id") or "").strip()
        rid = str(item.get("id") or "").strip()
        out.append(
            Rule(
                id=_slug(rid or name, used),
                name=name or rid,
                when=str(item.get("when") or item.get("condition") or ""),
                polarity=str(item.get("polarity") or "filter"),
                group=str(item.get("group") or ""),
                source=str(item.get("source") or ("catalog" if catalog_id else "custom")),
                catalog_id=catalog_id,
            )
        )
    return out


def _by_id(rules: list[Rule]) -> dict[str, Rule]:
    return {r.id: r for r in rules}


def mutex_pairs(rules: list[Rule]) -> list[tuple[str, str]]:
    groups: dict[str, list[str]] = {}
    for rule in rules:
        if not rule.group:
            continue
        groups.setdefault(rule.group, []).append(rule.id)
    pairs: list[tuple[str, str]] = []
    for ids in groups.values():
        for a, b in combinations(ids, 2):
            pairs.append((a, b))
    return pairs


def _conflicts(true_ids: list[str], rules: list[Rule]) -> str:
    lookup = _by_id(rules)
    groups: dict[str, list[Rule]] = {}
    for rid in true_ids:
        rule = lookup.get(rid)
        if rule is None or not rule.group:
            continue
        groups.setdefault(rule.group, []).append(rule)
    for group, members in groups.items():
        if len(members) >= 2:
            names = " and ".join(m.name for m in members)
            return f"{names} cannot both be true ({group})"
    return ""


def _polarities(true_ids: list[str], rules: list[Rule]) -> set[str]:
    lookup = _by_id(rules)
    out: set[str] = set()
    for rid in true_ids:
        rule = lookup.get(rid)
        if rule is None:
            continue
        if rule.polarity in ("long", "short", "no_trade"):
            out.add(rule.polarity)
    return out


def mixed_side(true_ids: list[str], rules: list[Rule]) -> bool:
    pol = _polarities(true_ids, rules)
    return "long" in pol and "short" in pol


def suggest_action(true_ids: list[str], rules: list[Rule]) -> str:
    if _conflicts(true_ids, rules):
        return "FLAT"
    pol = _polarities(true_ids, rules)
    if "no_trade" in pol:
        return "FLAT"
    if pol == {"long"}:
        return "BUY"
    if pol == {"short"}:
        return "SHORT"
    return "FLAT"


def combination_label(rules: list[Rule], true_ids: list[str], false_ids: list[str]) -> str:
    lookup = _by_id(rules)
    parts: list[str] = []
    for rid in true_ids:
        name = lookup[rid].name if rid in lookup else rid
        parts.append(name)
    for rid in false_ids:
        name = lookup[rid].name if rid in lookup else rid
        parts.append(f"NOT {name}")
    return " AND ".join(parts) if parts else "(no rules true)"


def combination_sentence(rules: list[Rule], true_ids: list[str], false_ids: list[str], action: str) -> str:
    lookup = _by_id(rules)
    bits: list[str] = []
    for rid in true_ids:
        rule = lookup.get(rid)
        if rule is None:
            continue
        extra = f" ({rule.when})" if rule.when else ""
        bits.append(f"{rule.name}{extra}")
    for rid in false_ids:
        rule = lookup.get(rid)
        if rule is None:
            continue
        extra = f" ({rule.when})" if rule.when else ""
        bits.append(f"NOT {rule.name}{extra}")
    body = " AND ".join(bits) if bits else "no conditions"
    return f"If {body} → {action}"


def _and_id(true_ids: list[str]) -> str:
    return "and:" + ",".join(true_ids)


def _leaf_id(rules: list[Rule], true_ids: list[str], false_ids: list[str]) -> str:
    true_set = set(true_ids)
    parts = []
    for rule in rules:
        parts.append(f"{rule.id}={'T' if rule.id in true_set else 'F'}")
    return "leaf:" + ",".join(parts)


def combine_and(
    rules: list[Rule],
    *,
    max_and: int = DEFAULT_MAX_AND,
    allow_mixed: bool = False,
) -> list[Combination]:
    if not rules:
        return []
    kmax = min(int(max_and), len(rules), MAX_AND_CAP)
    out: list[Combination] = []
    for k in range(1, kmax + 1):
        for combo in combinations(rules, k):
            true_ids = [r.id for r in combo]
            if _conflicts(true_ids, rules):
                continue
            if not allow_mixed and mixed_side(true_ids, rules):
                continue
            action = suggest_action(true_ids, rules)
            out.append(
                Combination(
                    id=_and_id(true_ids),
                    kind="and",
                    label=combination_label(rules, true_ids, []),
                    sentence=combination_sentence(rules, true_ids, [], action),
                    true_ids=true_ids,
                    false_ids=[],
                    action=action,
                    size=k,
                )
            )
    return out


def _leaf_node(
    rules: list[Rule],
    true_ids: list[str],
    false_ids: list[str],
    *,
    node_id: str,
    impossible: str = "",
) -> TreeNode:
    action = "FLAT" if impossible else suggest_action(true_ids, rules)
    label = combination_label(rules, true_ids, false_ids)
    return TreeNode(
        id=node_id,
        kind="leaf",
        label="impossible" if impossible else label,
        true_ids=list(true_ids),
        false_ids=list(false_ids),
        action=action,
        sentence="" if impossible else combination_sentence(rules, true_ids, false_ids, action),
        impossible=bool(impossible),
        reason=impossible,
        combination_id="" if impossible else _leaf_id(rules, true_ids, false_ids),
    )


def _split_tree(
    rules: list[Rule],
    index: int,
    true_ids: list[str],
    false_ids: list[str],
    node_id: str,
) -> TreeNode:
    if index >= len(rules):
        return _leaf_node(rules, true_ids, false_ids, node_id=node_id)
    rule = rules[index]
    yes_true = true_ids + [rule.id]
    conflict = _conflicts(yes_true, rules)
    if conflict:
        yes = _leaf_node(
            rules,
            yes_true,
            false_ids,
            node_id=node_id + "Y",
            impossible=conflict,
        )
    else:
        yes = _split_tree(rules, index + 1, yes_true, false_ids, node_id + "Y")
    no = _split_tree(rules, index + 1, true_ids, false_ids + [rule.id], node_id + "N")
    return TreeNode(
        id=node_id,
        kind="split",
        label=f"{rule.name}?",
        rule_id=rule.id,
        yes=yes,
        no=no,
        true_ids=list(true_ids),
        false_ids=list(false_ids),
    )


def walk_leaves(node: TreeNode | None) -> list[TreeNode]:
    if node is None:
        return []
    if node.kind == "leaf":
        return [node]
    return walk_leaves(node.yes) + walk_leaves(node.no)


def ascii_tree(node: TreeNode | None) -> str:
    if node is None:
        return ""
    return "\n".join(_ascii_tree_simple(node, "", is_last=True, first=True))


def _ascii_child(node: TreeNode) -> str:
    if node.kind == "leaf":
        if node.impossible:
            return f"IMPOSSIBLE ({node.reason})" if node.reason else "IMPOSSIBLE"
        return f"{node.label} → {node.action}"
    return node.label


def _ascii_tree_simple(
    node: TreeNode,
    prefix: str,
    *,
    is_last: bool,
    first: bool,
) -> list[str]:
    connector = "" if first else ("└─ " if is_last else "├─ ")
    if node.kind == "leaf":
        return [prefix + connector + _ascii_child(node)]
    lines = [prefix + connector + node.label]
    next_prefix = prefix if first else prefix + ("   " if is_last else "│  ")
    kids = [("YES", node.yes), ("NO", node.no)]
    present = [(tag, child) for tag, child in kids if child is not None]
    for i, (tag, child) in enumerate(present):
        last = i == len(present) - 1
        branch = "└─ " if last else "├─ "
        follow = "   " if last else "│  "
        if child.kind == "leaf":
            lines.append(next_prefix + branch + f"{tag} → " + _ascii_child(child))
        else:
            lines.append(next_prefix + branch + f"{tag} → {child.label}")
            lines.extend(_ascii_tree_simple(child, next_prefix + follow, is_last=True, first=True)[1:])
    return lines


def build_tree(rules: list[Rule]) -> TreeNode | None:
    if not rules:
        return None
    return _split_tree(rules, 0, [], [], "T")


def counts_from_tree(root: TreeNode | None) -> dict[str, int]:
    leaves = walk_leaves(root)
    possible = [n for n in leaves if not n.impossible]
    pruned = [n for n in leaves if n.impossible]
    splits = 0

    def _walk(node: TreeNode | None) -> None:
        nonlocal splits
        if node is None:
            return
        if node.kind == "split":
            splits += 1
            _walk(node.yes)
            _walk(node.no)

    _walk(root)
    return {
        "leaves": len(possible),
        "pruned": len(pruned),
        "splits": splits,
        "all_leaves": len(leaves),
    }


def validate_rules(rules: list[Rule]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not rules:
        errors.append("add at least one rule")
        return errors, warnings
    if len(rules) > MAX_RULES_AND:
        errors.append(f"AND packs cap at {MAX_RULES_AND} rules (got {len(rules)})")
    elif len(rules) > MAX_RULES_TREE:
        warnings.append(
            f"tree view caps at {MAX_RULES_TREE} rules; AND packs still built for {len(rules)}"
        )
    seen: set[str] = set()
    for rule in rules:
        if not rule.name.strip():
            errors.append("every rule needs a name")
        if rule.id in seen:
            errors.append(f"duplicate rule id {rule.id}")
        seen.add(rule.id)
    return errors, warnings


def build(
    rules_raw: Any,
    *,
    max_and: int = DEFAULT_MAX_AND,
    allow_mixed: bool = False,
    include_tree: bool = True,
) -> dict[str, Any]:
    rules = rules_from_payload(rules_raw)
    max_and = int(max_and)
    if max_and < 1:
        max_and = 1
    if max_and > MAX_AND_CAP:
        max_and = MAX_AND_CAP
    errors, warnings = validate_rules(rules)
    if errors:
        return {
            "ok": False,
            "lab": LAB_NAME,
            "errors": errors,
            "warnings": warnings,
            "rules": [r.to_dict() for r in rules],
        }
    tree_ok = bool(include_tree) and len(rules) <= MAX_RULES_TREE
    and_packs = combine_and(rules, max_and=max_and, allow_mixed=allow_mixed)
    root = build_tree(rules) if tree_ok else None
    tree_counts = counts_from_tree(root)
    leaves = [
        Combination(
            id=node.combination_id or node.id,
            kind="leaf",
            label=node.label,
            sentence=node.sentence,
            true_ids=list(node.true_ids),
            false_ids=list(node.false_ids),
            action=node.action,
            size=len(node.true_ids) + len(node.false_ids),
            impossible=node.impossible,
            reason=node.reason,
        )
        for node in walk_leaves(root)
    ]
    return {
        "ok": True,
        "lab": LAB_NAME,
        "note": (
            "Research only. You wrote the rules. The engine listed every AND pack "
            "and every yes/no path. Nothing is papered or live."
        ),
        "warnings": warnings,
        "rules": [r.to_dict() for r in rules],
        "mutex": [{"a": a, "b": b} for a, b in mutex_pairs(rules)],
        "and_packs": [c.to_dict() for c in and_packs],
        "leaves": [c.to_dict() for c in leaves if not c.impossible],
        "pruned": [c.to_dict() for c in leaves if c.impossible],
        "tree": None if root is None else root.to_dict(),
        "ascii": ascii_tree(root),
        "counts": {
            "rules": len(rules),
            "and_packs": len(and_packs),
            "and_possible": (2 ** len(rules)) - 1,
            "max_and": max_and,
            "allow_mixed": bool(allow_mixed),
            **tree_counts,
        },
        "errors": [],
    }


def text_report(payload: dict[str, Any]) -> str:
    if not payload.get("ok"):
        errs = payload.get("errors") or ["could not build"]
        return "ERROR\n" + "\n".join(f"- {e}" for e in errs)
    lines = [
        LAB_NAME,
        payload.get("note", ""),
        "",
        "RULES",
    ]
    for rule in payload.get("rules") or []:
        extra = f"  [{rule.get('group')}]" if rule.get("group") else ""
        lines.append(
            f"  {rule.get('id'):<12} {rule.get('name')} — {rule.get('when') or '(no condition text)'} "
            f"({rule.get('polarity')}){extra}"
        )
    counts = payload.get("counts") or {}
    lines += [
        "",
        (
            f"{counts.get('rules', 0)} rules → "
            f"{counts.get('and_packs', 0)} AND packs "
            f"(up to {counts.get('max_and', 0)} at a time) · "
            f"{counts.get('leaves', 0)} tree leaves · "
            f"{counts.get('pruned', 0)} impossible"
        ),
        "",
        "AND PACKS (all selected rules must be true)",
    ]
    packs = payload.get("and_packs") or []
    if not packs:
        lines.append("  (none)")
    for row in packs:
        lines.append(f"  {row.get('action'):<5}  {row.get('label')}")
        lines.append(f"         {row.get('sentence')}")
    lines += ["", "DECISION TREE (yes / no on each rule, in order)"]
    ascii_block = str(payload.get("ascii") or "").strip()
    if ascii_block:
        lines.extend("  " + ln if ln else "" for ln in ascii_block.splitlines())
    pruned = payload.get("pruned") or []
    if pruned:
        lines += ["", "IMPOSSIBLE PATHS (mutex)"]
        for row in pruned:
            lines.append(f"  {row.get('reason') or row.get('label')}")
    lines += ["", "TREE LEAVES"]
    for row in payload.get("leaves") or []:
        lines.append(f"  {row.get('action'):<5}  {row.get('label')}")
    return "\n".join(lines).rstrip() + "\n"


def demo_payload() -> dict[str, Any]:
    return build(list(DEMO_RULES), max_and=3, allow_mixed=False, include_tree=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--demo", action="store_true", help="green + volume up + above VWAP")
    ap.add_argument("--rules", nargs="*", default=[], help="name|when|polarity|group")
    ap.add_argument("--catalog", default="", help="comma catalog ids, e.g. bull,vol_up,px_gt_vwap")
    ap.add_argument("--file", type=Path, default=None, help="one rule per line")
    ap.add_argument("--max-and", type=int, default=DEFAULT_MAX_AND)
    ap.add_argument("--allow-mixed", action="store_true", help="allow long AND short in one pack")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    raw: list[Any] = []
    if args.demo:
        raw.extend(DEMO_RULES)
    if args.catalog:
        raw.extend([p.strip() for p in str(args.catalog).split(",") if p.strip()])
    if args.file is not None:
        raw.extend(args.file.read_text(encoding="utf-8").splitlines())
    raw.extend(args.rules)
    if not raw:
        ap.print_help()
        print("\nTry: python3 decision_tree.py --demo\n", file=sys.stderr)
        return 2
    payload = build(raw, max_and=args.max_and, allow_mixed=args.allow_mixed)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        sys.stdout.write(text_report(payload))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
