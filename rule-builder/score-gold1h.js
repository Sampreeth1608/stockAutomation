"use strict";

var fs = require("fs");
var path = require("path");
var lab = require("./candle.js");
var engine = require("./engine.js");
var charges = require("./charges.js");

var ATOMS = [
  "current high > previous high",
  "current high < previous high",
  "current close > current open",
  "current close < current open",
  "current upper wick > current lower wick",
  "current upper wick < current lower wick"
];

function inr(n) {
  if (n == null || isNaN(n)) return "—";
  var sign = n < 0 ? "-" : "";
  return sign + "₹" + Math.abs(Math.round(n)).toLocaleString("en-IN");
}

function fmt(n, d) {
  if (n == null || isNaN(n)) return "—";
  return Number(n).toFixed(d == null ? 2 : d);
}

function pad(s, n, right) {
  s = String(s);
  if (s.length >= n) return s;
  var sp = new Array(n - s.length + 1).join(" ");
  return right ? s + sp : sp + s;
}

function comboTrades(records, combo, side) {
  return lab.nextBarTrades(records, function (rec) {
    return engine.evaluateCombination(combo, rec.pair);
  }, side);
}

function fromBook(label, trades, extra) {
  var book = charges.bookPnl(trades);
  return {
    label: label,
    side: extra && extra.side,
    size: extra && extra.size,
    n: book.n,
    wins: book.wins,
    winRate: book.winRate,
    pts: book.pts,
    ptsPerTrade: book.ptsPerTrade,
    grossInr: book.grossInr,
    charges: book.charges,
    afterCharges: book.afterCharges,
    tax: book.tax,
    afterTax: book.afterTax,
    months: book.months
  };
}

function slim(row) {
  return {
    label: row.label,
    side: row.side,
    n: row.n,
    winRate: row.winRate,
    pts: row.pts != null ? row.pts : row.net,
    ptsPerTrade: row.ptsPerTrade,
    grossInr: row.grossInr,
    charges: row.charges,
    afterCharges: row.afterCharges,
    tax: row.tax,
    afterTax: row.afterTax,
    months: row.months
  };
}

function printTable(title, rows) {
  console.log("\n=== " + title + " ===");
  console.log(
    pad("combination", 52, true) +
      pad("side", 7) +
      pad("n", 6) +
      pad("win%", 8) +
      pad("pts", 10) +
      pad("pts/tr", 8) +
      pad("gross ₹", 12) +
      pad("charges", 11) +
      pad("after chg", 12) +
      pad("tax 30%", 11) +
      pad("after tax", 12)
  );
  rows.forEach(function (row) {
    console.log(
      pad(row.label, 52, true) +
        pad(row.side || "", 7) +
        pad(row.n, 6) +
        pad(row.winRate == null ? "—" : fmt(row.winRate * 100, 1), 8) +
        pad(fmt(row.pts != null ? row.pts : row.net, 1), 10) +
        pad(fmt(row.ptsPerTrade, 2), 8) +
        pad(inr(row.grossInr), 12) +
        pad(inr(row.charges), 11) +
        pad(inr(row.afterCharges), 12) +
        pad(inr(row.tax), 11) +
        pad(inr(row.afterTax), 12)
    );
  });
}

function monthLines(row) {
  var keys = Object.keys(row.months || {}).sort();
  if (!keys.length) return "";
  return keys.map(function (k) {
    var m = row.months[k];
    return k + " n=" + m.n + " pts=" + fmt(m.pts, 1) + " afterChg=" + inr(m.afterCharges);
  }).join(" | ");
}

function printMonths(row) {
  if (!row) return;
  console.log((row.side || "") + "  " + row.label + "  after-tax " + inr(row.afterTax));
  console.log("  " + monthLines(row));
}

var csvPath = path.join(__dirname, "data", "goldpetal-1h.csv");
var text = fs.readFileSync(csvPath, "utf8");
var result = lab.labFromText(text, 60);
var records = result.records;

console.log("Gold Petal Aug fut  1-hour");
console.log(result.first + " → " + result.last);
console.log(
  "bars=" + result.barCount +
    "  formed=" + records.length +
    "  same-session next-bar=" + result.baseline.n +
    "  nativeTf=" + result.nativeTf + "m"
);
console.log(
  "Buy every same-session hour: n=" + result.baseline.n +
    "  pts=" + fmt(result.baseline.net, 1) +
    "  drift=" + fmt(result.baseline.ptsPerTrade, 3) +
    " pts/bar  P(up)=" + fmt(result.baseline.pUp, 3)
);
console.log(
  "Angel: ₹20/order × 2, MCX 0.00210%, CTT 0.01% sell, SEBI 0.0001%, stamp 0.002% buy, GST 18% on brokerage+txn+SEBI"
);
console.log("Size: 100 lots (1 point = ₹100). Tax: 30% on profit after charges (losses untaxed). Overnight gaps are not next-bar trades.");

var rules = engine.parseRules(ATOMS.join("\n"));
var combos = engine.generateCombinations(rules, { minK: 1, maxK: 6, limit: 5000 }).combinations;

var nextRows = [];
nextRows.push(fromBook("BUY every bar (drift)", lab.nextBarTrades(records, function () { return true; }, "long"), { side: "long", size: 0 }));
nextRows.push(fromBook("SELL every bar", lab.nextBarTrades(records, function () { return true; }, "short"), { side: "short", size: 0 }));

["S1", "S2", "S3", "S4", "EQ"].forEach(function (st) {
  ["long", "short"].forEach(function (side) {
    nextRows.push(fromBook(
      st,
      lab.nextBarTrades(records, function (r) { return r.state === st; }, side),
      { side: side, size: 2 }
    ));
  });
});

["upper", "lower"].forEach(function (w) {
  ["long", "short"].forEach(function (side) {
    nextRows.push(fromBook(
      w + " wick dominant",
      lab.nextBarTrades(records, function (r) { return r.wickDom === w; }, side),
      { side: side, size: 1 }
    ));
  });
});

combos.forEach(function (combo) {
  ["long", "short"].forEach(function (side) {
    nextRows.push(fromBook(combo.canonical, comboTrades(records, combo, side), { side: side, size: combo.size }));
  });
});

var liveNext = nextRows.filter(function (r) { return r.n > 0; });
liveNext.sort(function (a, b) { return b.afterTax - a.afterTax; });
printTable("Next-bar (same session only) · 100 lots · Angel + 30% tax  — sorted by after-tax", liveNext);

var flipRows = [
  Object.assign({ label: "FLIP intuition · hold overnight", side: "flip" }, result.flipIntuition),
  Object.assign({ label: "FLIP reversal · hold overnight", side: "flip" }, result.flipReversal),
  Object.assign({ label: "FLIP intuition · flatten session", side: "flip" }, result.flipIntuitionSession),
  Object.assign({ label: "FLIP reversal · flatten session", side: "flip" }, result.flipReversalSession)
];
flipRows.forEach(function (row) {
  row.pts = row.pts != null ? row.pts : row.net;
});
printTable("FLIP when mapped side changes (not every hour)", flipRows);

console.log("\n=== Monthly after-charges (tax is on the full window, not per month) ===");
printMonths(liveNext.find(function (r) { return r.label === "BUY every bar (drift)"; }));
printMonths(liveNext.find(function (r) { return r.label === "S4" && r.side === "long"; }));
printMonths(liveNext.find(function (r) { return r.label === "S4" && r.side === "short"; }));
printMonths(liveNext.find(function (r) {
  return r.side === "long" &&
    r.label === "current high < previous high AND current close < current open AND current upper wick > current lower wick";
}));
printMonths(liveNext.find(function (r) {
  return r.side === "short" &&
    r.label === "current high < previous high AND current close < current open AND current upper wick > current lower wick";
}));
console.log("FLIP intuition hold overnight monthly:");
printMonths(flipRows[0]);

var jsonPath = path.join(__dirname, "data", "goldpetal-1h-scorecard.json");
fs.writeFileSync(jsonPath, JSON.stringify({
  file: "goldpetal-1h.csv",
  first: result.first,
  last: result.last,
  bars: result.barCount,
  sessionNextBars: result.baseline.n,
  lots: 100,
  taxRate: 0.30,
  nextBar: liveNext.map(slim),
  flip: flipRows.map(slim)
}, null, 2));
console.log("\nWrote " + jsonPath);
