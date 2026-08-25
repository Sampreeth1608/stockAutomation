#!/usr/bin/env python3
"""Decision-tree combinator tests. Research only — no paper, no live."""

from __future__ import annotations

import json
from pathlib import Path

from decision_tree import (
    CATALOG_BY_ID,
    DEMO_RULES,
    LAB_NAME,
    build,
    combine_and,
    demo_payload,
    mutex_pairs,
    parse_rule_line,
    rule_from_catalog,
    rules_from_payload,
    suggest_action,
    text_report,
)
from decision_tree_app import HTML_PATH, handle_request


def test_two_rules_three_and_four_leaves() -> None:
    payload = build(
        [
            {"name": "green", "when": "close > open", "polarity": "long"},
            {"name": "vol up", "when": "volume > prev", "polarity": "filter"},
        ],
        max_and=3,
    )
    assert payload["ok"] is True
    assert payload["lab"] == LAB_NAME
    assert payload["counts"]["rules"] == 2
    assert payload["counts"]["and_packs"] == 3  # A, B, A∧B
    assert payload["counts"]["leaves"] == 4
    assert payload["counts"]["pruned"] == 0
    labels = [row["label"] for row in payload["and_packs"]]
    assert labels == ["green", "vol up", "green AND vol up"]
    actions = {row["label"]: row["action"] for row in payload["and_packs"]}
    assert actions["green"] == "BUY"
    assert actions["vol up"] == "FLAT"
    assert actions["green AND vol up"] == "BUY"
    ascii_text = payload["ascii"]
    assert "green?" in ascii_text
    assert "YES" in ascii_text and "NO" in ascii_text
    assert "paper" not in ascii_text.lower() or "not" in payload["note"].lower()
    assert "Nothing is papered or live" in payload["note"]


def test_demo_seven_packs_eight_leaves() -> None:
    payload = demo_payload()
    assert payload["ok"] is True
    assert payload["counts"]["and_packs"] == 7
    assert payload["counts"]["leaves"] == 8
    names = [r["name"] for r in payload["rules"]]
    assert names == ["green candle", "volume up", "above VWAP"]
    full = [row for row in payload["and_packs"] if row["size"] == 3]
    assert len(full) == 1
    assert full[0]["action"] == "BUY"
    report = text_report(payload)
    assert "AND PACKS" in report
    assert "DECISION TREE" in report
    assert "green candle AND volume up AND above VWAP" in report


def test_mutex_skips_and_and_prunes_tree() -> None:
    payload = build(
        [
            {"name": "green", "when": "c>o", "polarity": "long", "group": "color"},
            {"name": "red", "when": "c<o", "polarity": "short", "group": "color"},
        ],
        max_and=2,
        allow_mixed=True,
    )
    assert payload["ok"] is True
    labels = [row["label"] for row in payload["and_packs"]]
    assert labels == ["green", "red"]
    assert payload["counts"]["pruned"] == 1
    assert payload["pruned"][0]["impossible"] is True
    assert "cannot both be true" in payload["pruned"][0]["reason"]
    pairs = mutex_pairs(rules_from_payload(payload["rules"]))
    assert len(pairs) == 1


def test_mixed_long_short_skipped_unless_allowed() -> None:
    rules = [
        {"name": "hh", "when": "higher high", "polarity": "long"},
        {"name": "ll", "when": "lower low", "polarity": "short"},
    ]
    blocked = build(rules, max_and=2, allow_mixed=False)
    allowed = build(rules, max_and=2, allow_mixed=True)
    assert [r["label"] for r in blocked["and_packs"]] == ["hh", "ll"]
    assert [r["label"] for r in allowed["and_packs"]] == ["hh", "ll", "hh AND ll"]
    mixed = [r for r in allowed["and_packs"] if r["size"] == 2][0]
    assert mixed["action"] == "FLAT"


def test_no_trade_forces_flat() -> None:
    payload = build(
        [
            {"name": "green", "when": "c>o", "polarity": "long"},
            {"name": "wide spread", "when": "spread too wide", "polarity": "no_trade"},
        ],
        max_and=2,
    )
    by_label = {r["label"]: r["action"] for r in payload["and_packs"]}
    assert by_label["green"] == "BUY"
    assert by_label["wide spread"] == "FLAT"
    assert by_label["green AND wide spread"] == "FLAT"


def test_max_and_caps_size() -> None:
    rules = [{"name": f"r{i}", "when": str(i), "polarity": "filter"} for i in range(4)]
    payload = build(rules, max_and=2)
    assert payload["counts"]["and_packs"] == 4 + 6  # C(4,1)+C(4,2)
    assert max(r["size"] for r in payload["and_packs"]) == 2
    assert combine_and(rules_from_payload(rules), max_and=1)
    assert all(r["size"] == 1 for r in build(rules, max_and=1)["and_packs"])


def test_catalog_and_text_parse() -> None:
    used: set[str] = set()
    bull = rule_from_catalog("bull", used)
    vol = rule_from_catalog("vol_up", used)
    assert bull.name == "green candle"
    assert bull.group == "color"
    assert vol.catalog_id == "vol_up"
    line = parse_rule_line("inside bar | high < prev high and low > prev low | filter | ", used)
    assert line is not None and line.name == "inside bar"
    payload = build(["bull", "vol_up", "px_gt_vwap"], max_and=3)
    assert payload["ok"] is True
    assert payload["counts"]["and_packs"] == 7
    assert "bull" in CATALOG_BY_ID
    demo_lines = rules_from_payload(list(DEMO_RULES))
    assert len(demo_lines) == 3


def test_empty_and_duplicate_errors() -> None:
    empty = build([])
    assert empty["ok"] is False
    assert "add at least one rule" in empty["errors"][0]
    report = text_report(empty)
    assert report.startswith("ERROR")


def test_suggest_action_votes_polarities() -> None:
    rules = rules_from_payload(
        [
            {"id": "A", "name": "a", "polarity": "long"},
            {"id": "B", "name": "b", "polarity": "filter"},
            {"id": "C", "name": "c", "polarity": "short"},
        ]
    )
    assert suggest_action(["A"], rules) == "BUY"
    assert suggest_action(["A", "B"], rules) == "BUY"
    assert suggest_action(["C"], rules) == "SHORT"
    assert suggest_action(["A", "C"], rules) == "FLAT"
    assert suggest_action(["B"], rules) == "FLAT"


def test_html_and_http_roundtrip(tmp_path: Path | None = None) -> None:
    del tmp_path
    html = HTML_PATH.read_text(encoding="utf-8")
    assert "Generate combinations" in html
    assert "Decision Tree Combinator" in html
    assert "not paper" in html.lower() or "nothing papers" in html.lower()
    status, body, ctype = handle_request("GET", "/api/catalog", b"")
    assert status == 200 and "json" in ctype
    catalog = json.loads(body)
    assert catalog["ok"] is True
    families = [row["family"] for row in catalog["families"]]
    assert "candle" in families and "vwap" in families
    status, body, _ = handle_request(
        "POST",
        "/api/build",
        json.dumps(
            {
                "rules": [
                    {"name": "green", "when": "c>o", "polarity": "long", "group": "color"},
                    {"name": "vol", "when": "v up", "polarity": "filter"},
                ],
                "max_and": 3,
            }
        ).encode("utf-8"),
    )
    assert status == 200
    payload = json.loads(body)
    assert payload["ok"] is True
    assert payload["counts"]["and_packs"] == 3
    assert payload["counts"]["leaves"] == 4
    assert "report" in payload
    missing = handle_request("GET", "/nope", b"")
    assert missing[0] == 404
    bad = handle_request("POST", "/api/build", b"not-json")
    assert bad[0] == 400


def test_not_a_trading_book() -> None:
    src = Path(__file__).read_text(encoding="utf-8")
    engine = Path(__file__).resolve().parent / "decision_tree.py"
    app = Path(__file__).resolve().parent / "decision_tree_app.py"
    text = engine.read_text(encoding="utf-8") + app.read_text(encoding="utf-8")
    assert "ENABLE_" not in text
    assert "DRY_RUN=false" not in text
    assert "placeOrder" not in text
    assert LAB_NAME == "DECISION_TREE"
    assert "test_not_a_trading_book" in src


if __name__ == "__main__":
    test_two_rules_three_and_four_leaves()
    test_demo_seven_packs_eight_leaves()
    test_mutex_skips_and_and_prunes_tree()
    test_mixed_long_short_skipped_unless_allowed()
    test_no_trade_forces_flat()
    test_max_and_caps_size()
    test_catalog_and_text_parse()
    test_empty_and_duplicate_errors()
    test_suggest_action_votes_polarities()
    test_html_and_http_roundtrip()
    test_not_a_trading_book()
    print("ALL test_decision_tree OK")
