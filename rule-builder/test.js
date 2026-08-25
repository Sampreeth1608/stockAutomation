"use strict";

var engine = require("./engine.js");
var failed = 0;
var passed = 0;

function assert(name, condition, detail) {
  if (condition) {
    passed += 1;
    console.log("  pass  " + name);
  } else {
    failed += 1;
    console.log("  FAIL  " + name + (detail ? " — " + detail : ""));
  }
}

function assertEqual(name, actual, expected) {
  var ok = actual === expected;
  assert(name, ok, "expected " + JSON.stringify(expected) + ", got " + JSON.stringify(actual));
}

console.log("splitRuleText");
var splitLines = engine.splitRuleText(
  "if current close > current open\nif current high > previous high\nif current upper wick < current lower wick"
);
assertEqual("splits three newline rules", splitLines.length, 3);

var blob = engine.splitRuleText(
  "if current close >current open  combining with  if current high > previous high if current upper wick < current lower wick"
);
assertEqual("splits pasted one-liner with combining with", blob.length, 3);
assertEqual("first blob rule", blob[0], "if current close >current open");
assertEqual("second blob rule", blob[1], "if current high > previous high");
assertEqual("third blob rule", blob[2], "if current upper wick < current lower wick");

assertEqual(
  "ignores comments and blanks",
  engine.splitRuleText("# ignore\n\nclose > open\n").length,
  1
);

console.log("parseRule");
var r1 = engine.parseRule("if current close >current open", 0);
assert("parses close > open", r1.ok);
assertEqual("lhs bar", r1.lhs.bar, "current");
assertEqual("lhs metric", r1.lhs.metric, "close");
assertEqual("op", r1.op, "gt");
assertEqual("rhs metric", r1.rhs.metric, "open");
assertEqual("canonical", r1.canonical, "current close > current open");

var r2 = engine.parseRule("if current high > previous high", 1);
assert("parses high > previous high", r2.ok);
assertEqual("rhs bar previous", r2.rhs.bar, "previous");
assertEqual("rhs metric high", r2.rhs.metric, "high");

var r3 = engine.parseRule("if current upper wick < current lower wick", 2);
assert("parses wick comparison", r3.ok);
assertEqual("upper wick metric", r3.lhs.metric, "upperWick");
assertEqual("lower wick metric", r3.rhs.metric, "lowerWick");
assertEqual("lt op", r3.op, "lt");

var named = engine.parseRule("Bullish body: current close > current open", 0);
assertEqual("named rule title", named.name, "Bullish body");

var words = engine.parseRule("current close greater than current open", 0);
assert("parses greater than", words.ok && words.op === "gt");

var short = engine.parseRule("close > open", 0);
assert("defaults missing bar to current", short.ok && short.lhs.bar === "current" && short.rhs.bar === "current");

var gte = engine.parseRule("current high >= previous high", 0);
assertEqual("parses >=", gte.op, "gte");

var num = engine.parseRule("current body > 2", 0);
assert("parses numeric rhs", num.ok && num.rhs.type === "number" && num.rhs.value === 2);

var bullish = engine.parseRule("bullish candle", 0);
assert("shortcut bullish", bullish.ok && bullish.lhs.metric === "close" && bullish.op === "gt");

var closeAh = engine.parseRule("close above prev high?", 0);
assert("shortcut close above prev high", closeAh.ok && closeAh.lhs.metric === "close" && closeAh.rhs.metric === "high" && closeAh.rhs.bar === "previous");

var volPrev = engine.parseRule("volume above prev", 0);
assert("shortcut volume above prev", volPrev.ok && volPrev.lhs.metric === "volume" && volPrev.rhs.bar === "previous");

var half = engine.parseRule("body fills over half", 0);
assert("shortcut body fills over half", half.ok && half.lhs.metric === "bodyFill" && half.rhs.value === 0.5);

var opened = engine.parseRule("opened above prev close", 0);
assert("shortcut opened above prev close", opened.ok && opened.lhs.metric === "open" && opened.rhs.metric === "close");

var beats = engine.parseRule("upper wick beats lower", 0);
assert("shortcut upper wick beats lower", beats.ok && beats.lhs.metric === "upperWick" && beats.rhs.metric === "lowerWick");

var belowLow = engine.parseRule("close below prev low", 0);
assert("shortcut close below prev low", belowLow.ok && belowLow.rhs.metric === "low" && belowLow.op === "lt");

var bad = engine.parseRule("hello world", 0);
assert("rejects nonsense", !bad.ok && /comparison/i.test(bad.error));

console.log("combinations");
var parsed = engine.parseRules(
  "if current close > current open\nif current high > previous high\nif current upper wick < current lower wick"
);
assertEqual("three valid rules", parsed.filter(function (r) { return r.ok; }).length, 3);

var all = engine.generateCombinations(parsed, { minK: 1, maxK: 3 });
assertEqual("C(3,1)+C(3,2)+C(3,3) = 7", all.total, 7);
assertEqual("shows all 7", all.shown, 7);
assertEqual("first is singleton R1", all.combinations[0].ids.join(","), "R1");
assertEqual("last is all three", all.combinations[6].ids.join(","), "R1,R2,R3");
assert(
  "pair english joins with and",
  /and/i.test(all.combinations[3].english)
);

var pairs = engine.generateCombinations(parsed, { minK: 2, maxK: 2 });
assertEqual("only pairs", pairs.total, 3);
assertEqual("pair size", pairs.combinations[0].size, 2);

var limited = engine.generateCombinations(parsed, { minK: 1, maxK: 3, limit: 2 });
assertEqual("honors n limit", limited.shown, 2);
assert("marks truncated", limited.truncated === true);
assertEqual("still reports full total", limited.total, 7);

var tree = engine.buildCombinationTree(all);
assertEqual("tree has 3 top-level branches", tree.children.length, 3);
assertEqual("R1 branch first child is R2", tree.children[0].children[0].ruleId, "R2");
assertEqual("R1+R2+R3 exists", tree.children[0].children[0].children[0].ruleId, "R3");
assert("leaf holds combination", !!tree.children[0].children[0].children[0].combination);

console.log("evaluate");
var candles = {
  current: { open: 100, high: 110, low: 90, close: 108, volume: 1000 },
  previous: { open: 99, high: 105, low: 95, close: 101, volume: 800 }
};
assert("sample matches bullish", engine.evaluateRule(parsed[0], candles));
assert("sample matches higher high", engine.evaluateRule(parsed[1], candles));
assert("sample matches wick rule", engine.evaluateRule(parsed[2], candles));
assert("full combo matches", engine.evaluateCombination(all.combinations[6], candles));

var miss = {
  current: { open: 108, high: 110, low: 100, close: 101, volume: 1000 },
  previous: { open: 99, high: 112, low: 95, close: 101, volume: 800 }
};
assert("bearish sample fails bullish rule", !engine.evaluateRule(parsed[0], miss));

console.log("\n" + passed + " passed, " + failed + " failed");
if (failed) process.exit(1);
