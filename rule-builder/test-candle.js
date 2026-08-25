"use strict";

var fs = require("fs");
var path = require("path");
var lab = require("./candle.js");
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
  assert(name, actual === expected, "expected " + JSON.stringify(expected) + ", got " + JSON.stringify(actual));
}

function bar(time, o, h, l, c, v) {
  return { time: time, day: time.slice(0, 10), open: o, high: h, low: l, close: c, volume: v || 0 };
}

console.log("wicks + states");
var prev = bar("2026-08-01 10:00:00", 100, 110, 95, 108);
var s1 = lab.formCandle(prev, bar("2026-08-01 10:15:00", 108, 120, 107, 118));
assertEqual("S1 HH+bull", s1.state, "S1");
assertEqual("upper wick", s1.upperWick, 2);
assertEqual("lower wick", s1.lowerWick, 1);

var s2 = lab.formCandle(prev, bar("2026-08-01 10:15:00", 108, 120, 100, 104));
assertEqual("S2 HH+bear", s2.state, "S2");
assert(s2.upperWick > s2.lowerWick, "S2 upper wick dominates");

var s4 = lab.formCandle(prev, bar("2026-08-01 10:15:00", 108, 109, 90, 100));
assertEqual("S4 LH+bear", s4.state, "S4");
assert(s4.lowerWick > s4.upperWick, "S4 lower wick dominates");

var s3 = lab.formCandle(prev, bar("2026-08-01 10:15:00", 100, 109, 99, 107));
assertEqual("S3 LH+bull", s3.state, "S3");

var eq = lab.formCandle(prev, bar("2026-08-01 10:15:00", 108, 110, 100, 109));
assertEqual("equal high excluded from S1-S4", eq.state, "EQ");

console.log("csv + resample");
var csv = [
  "time,open,high,low,close,volume,day",
  "2026-08-01 10:00:00,100,105,99,104,10,2026-08-01",
  "2026-08-01 10:15:00,104,110,103,109,20,2026-08-01",
  "2026-08-01 10:30:00,109,111,108,108,5,2026-08-01",
  "2026-08-01 10:45:00,108,109,100,101,8,2026-08-01"
].join("\n");
var parsed = lab.parseBars(csv);
assertEqual("parsed 4 15m bars", parsed.length, 4);
var h30 = lab.resample(parsed, 30);
assertEqual("two 30m bars", h30.length, 2);
assertEqual("30m first open", h30[0].open, 100);
assertEqual("30m first high", h30[0].high, 110);
assertEqual("30m first low", h30[0].low, 99);
assertEqual("30m first close", h30[0].close, 109);
assertEqual("30m volume sum", h30[0].volume, 30);
assertEqual("30m second close", h30[1].close, 101);

var dump = [
  "time", "open", "high", "low", "close", "volume", "day",
  "2026-07-27 22:15:00", "14524.0", "14527.0", "14501.0", "14515.0", "4599.0", "2026-07-27",
  "2026-07-27 22:30:00", "14515.0", "14522.0", "14495.0", "14497.0", "1594.0", "2026-07-27"
].join("\n");
var dumped = lab.parseBars(dump);
assertEqual("column dump parses", dumped.length, 2);
assertEqual("dump close", dumped[0].close, 14515);

console.log("next-bar score + drift");
var series = [
  bar("2026-08-01 10:00:00", 100, 110, 95, 108),
  bar("2026-08-01 10:15:00", 108, 120, 107, 118),
  bar("2026-08-01 10:30:00", 118, 119, 110, 112),
  bar("2026-08-01 10:45:00", 112, 130, 111, 128),
  bar("2026-08-01 11:00:00", 128, 129, 120, 121)
];
var recs = lab.recordSeries(series);
assertEqual("records exclude first bar only", recs.length, 4);
assertEqual("first formed is S1", recs[0].state, "S1");
assertEqual("first next pts", recs[0].nextPts, -6);
assert(recs[3].hasNext === false, "last formed has no next");

var s1score = lab.scoreSubset(recs, function (r) { return r.state === "S1"; });
assert(s1score.n >= 1, "S1 has samples");
assert(s1score.excess != null, "excess vs all-bars baseline is set");

console.log("english rules on formed candles");
var bearish = engine.parseRules("current close < current open\ncurrent high < previous high");
var s4hits = lab.scoreRules(recs, bearish);
assert(s4hits.n >= 1, "S4 english rules match LH+bear bars");

console.log("flip only when side changes");
var flip = lab.scoreFlip(recs, lab.sideFromState);
assert(flip.skippedSame >= 0, "same-side hours are skipped, not new trades");
assert(flip.n <= recs.filter(function (r) { return r.hasNext; }).length, "flip count <= bars");

console.log("gold15 sample");
var goldText = fs.readFileSync(path.join(__dirname, "data", "gold15.csv"), "utf8");
var gold15 = lab.labFromText(goldText, 15);
assertEqual("499 native 15m bars", gold15.nativeCount, 499);
assert(gold15.states[0].n > 0 && gold15.states[3].n > 0, "S1 and S4 both occur");
var s4en = engine.parseRules("current high < previous high\ncurrent close < current open");
var s4score = lab.scoreRules(gold15.records, s4en);
assertEqual("English S4 matches labelled S4 count", s4score.n, gold15.states[3].n);
var gold30 = lab.labFromText(goldText, 30);
assert(gold30.barCount < gold15.barCount, "30m has fewer bars than 15m");
assert(gold30.baseline.ptsPerTrade > 0, "this Gold15 window has positive drift");

console.log("\n" + passed + " passed, " + failed + " failed");
if (failed) process.exit(1);
