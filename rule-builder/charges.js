/**
 * Angel One MCX Gold Petal charges + 30% tax on profit after charges.
 * Matches goldpetal/charges.py (S16 desk): ₹20/order, not per lot.
 * PAPER_LOTS=100 → 1 point = ₹100; turnover scales with lots.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.GoldCharges = factory();
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var DEFAULTS = {
    brokeragePerOrder: 20,
    mcxTxnRate: 0.0000210,
    cttSellRate: 0.0001,
    sebiRate: 0.000001,
    stampBuyRate: 0.00002,
    gstRate: 0.18,
    taxRate: 0.30,
    lots: 100,
    pointValue: 1
  };

  function round(n, d) {
    var f = Math.pow(10, d == null ? 2 : d);
    return Math.round((n + Number.EPSILON) * f) / f;
  }

  function cfgOf(extra) {
    var cfg = {};
    var k;
    for (k in DEFAULTS) cfg[k] = DEFAULTS[k];
    if (extra) {
      for (k in extra) {
        if (Object.prototype.hasOwnProperty.call(extra, k) && extra[k] != null) {
          cfg[k] = extra[k];
        }
      }
    }
    return cfg;
  }

  function isLong(side) {
    var s = String(side || "long").toLowerCase();
    return s === "long" || s === "buy";
  }

  function legCharges(isBuy, price, cfg) {
    var turnover = Math.abs(Number(price) || 0) * cfg.lots * cfg.pointValue;
    var brokerage = cfg.brokeragePerOrder;
    var txn = turnover * cfg.mcxTxnRate;
    var sebi = turnover * cfg.sebiRate;
    var stamp = isBuy ? turnover * cfg.stampBuyRate : 0;
    var ctt = isBuy ? 0 : turnover * cfg.cttSellRate;
    var gst = cfg.gstRate * (brokerage + txn + sebi);
    var total = brokerage + txn + sebi + stamp + ctt + gst;
    return {
      turnover: round(turnover, 4),
      brokerage: round(brokerage, 4),
      txn: round(txn, 6),
      sebi: round(sebi, 6),
      stamp: round(stamp, 6),
      ctt: round(ctt, 6),
      gst: round(gst, 6),
      legTotal: round(total, 4)
    };
  }

  function roundTripCharges(side, entryPrice, exitPrice, extra) {
    var cfg = cfgOf(extra);
    var openLeg;
    var closeLeg;
    if (isLong(side)) {
      openLeg = legCharges(true, entryPrice, cfg);
      closeLeg = legCharges(false, exitPrice, cfg);
    } else {
      openLeg = legCharges(false, entryPrice, cfg);
      closeLeg = legCharges(true, exitPrice, cfg);
    }
    return round(openLeg.legTotal + closeLeg.legTotal, 2);
  }

  function bookPnl(trades, extra) {
    var cfg = cfgOf(extra);
    var list = trades || [];
    var grossPts = 0;
    var charges = 0;
    var wins = 0;
    var months = {};
    var i;
    for (i = 0; i < list.length; i++) {
      var t = list[i];
      var pts = Number(t.pts) || 0;
      grossPts += pts;
      if (pts > 0) wins += 1;
      var ch = roundTripCharges(t.side, t.entry, t.exit, cfg);
      charges += ch;
      var m = t.month || "unknown";
      if (!months[m]) months[m] = { n: 0, pts: 0, charges: 0, grossInr: 0, afterCharges: 0 };
      months[m].n += 1;
      months[m].pts += pts;
      months[m].charges += ch;
    }
    charges = round(charges, 2);
    grossPts = round(grossPts, 4);
    var grossInr = round(grossPts * cfg.lots * cfg.pointValue, 2);
    var afterCharges = round(grossInr - charges, 2);
    var tax = round(Math.max(0, afterCharges) * cfg.taxRate, 2);
    var afterTax = round(afterCharges - tax, 2);
    var monthKeys = Object.keys(months).sort();
    for (i = 0; i < monthKeys.length; i++) {
      var row = months[monthKeys[i]];
      row.pts = round(row.pts, 4);
      row.charges = round(row.charges, 2);
      row.grossInr = round(row.pts * cfg.lots * cfg.pointValue, 2);
      row.afterCharges = round(row.grossInr - row.charges, 2);
    }
    return {
      n: list.length,
      wins: wins,
      winRate: list.length ? round(wins / list.length, 4) : null,
      pts: grossPts,
      ptsPerTrade: list.length ? round(grossPts / list.length, 4) : null,
      lots: cfg.lots,
      grossInr: grossInr,
      charges: charges,
      afterCharges: afterCharges,
      tax: tax,
      taxRate: cfg.taxRate,
      afterTax: afterTax,
      months: months
    };
  }

  return {
    DEFAULTS: DEFAULTS,
    cfgOf: cfgOf,
    roundTripCharges: roundTripCharges,
    bookPnl: bookPnl
  };
});
