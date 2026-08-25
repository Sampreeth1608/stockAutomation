"use strict";

var fs = require("fs");
var path = require("path");
var lab = require("./candle.js");
var engine = require("./engine.js");

function fmt(n, d) {
  if (n == null || isNaN(n)) return "—";
  return Number(n).toFixed(d == null ? 2 : d);
}

function printRow(label, row) {
  console.log(
    String(label).padEnd(32) +
      String(row.n).padStart(5) +
      String(fmt(row.pUp, 3)).padStart(8) +
      String(fmt(row.ptsPerTrade, 2)).padStart(9) +
      String(fmt(row.excess, 2)).padStart(9) +
      String(fmt(row.winRate, 3)).padStart(8)
  );
}

function runTf(text, minutes) {
  var result = lab.labFromText(text, minutes);
  console.log("\n=== " + minutes + "-min  bars=" + result.barCount +
    "  scored=" + result.baseline.n +
    "  " + result.first + " → " + result.last + " ===");
  console.log(
    "baseline drift " + fmt(result.baseline.ptsPerTrade, 3) +
      " pts/bar   P(up)=" + fmt(result.baseline.pUp, 3)
  );
  console.log("".padEnd(32) + "    n    P(up)   pts/tr   excess    win%");
  result.states.forEach(function (row) {
    printRow(row.state + " " + row.label, row);
  });
  console.log("-- wicks --");
  result.wicks.forEach(function (row) {
    printRow(row.label, row);
  });
  console.log("-- state + wick --");
  result.stateWicks.filter(function (r) { return r.n >= 8; }).forEach(function (row) {
    printRow(row.label, row);
  });
  var s4uw = engine.parseRules(
    "current high < previous high\ncurrent close < current open\ncurrent upper wick > current lower wick"
  );
  printRow("S4 + upper>lower (English)", lab.scoreRules(result.records, s4uw));
  console.log(
    "FLIP intuition (bull=long, bear=short) n=" + result.flipIntuition.n +
      " pts/tr=" + fmt(result.flipIntuition.ptsPerTrade) +
      " same-side skips=" + result.flipIntuition.skippedSame
  );
  console.log(
    "FLIP reversal (bear=long, bull=short) n=" + result.flipReversal.n +
      " pts/tr=" + fmt(result.flipReversal.ptsPerTrade) +
      " same-side skips=" + result.flipReversal.skippedSame
  );
  return result;
}

var csvPath = path.join(__dirname, "data", "gold15.csv");
var text = fs.readFileSync(csvPath, "utf8");
[15, 30, 60].forEach(function (tf) { runTf(text, tf); });
