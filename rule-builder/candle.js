/**
 * Current-vs-previous candle lab.
 * Only this bar vs the last bar: high, open, close, wicks.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory(
      typeof require === "function" ? require("./engine.js") : root.RuleEngine,
      typeof require === "function" ? require("./charges.js") : root.GoldCharges
    );
  } else {
    root.CandleLab = factory(root.RuleEngine, root.GoldCharges);
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function (RuleEngine, Charges) {
  "use strict";

  var MONTHS = {
    jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6,
    jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12
  };

  function round(n, d) {
    var f = Math.pow(10, d == null ? 4 : d);
    return Math.round(n * f) / f;
  }

  function pad2(n) {
    return (n < 10 ? "0" : "") + n;
  }

  function parseStamp(raw) {
    var s = String(raw || "").trim().replace(/^"+|"+$/g, "");
    var m = s.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/);
    if (m) {
      return {
        day: m[1] + "-" + m[2] + "-" + m[3],
        hour: parseInt(m[4], 10),
        minute: parseInt(m[5], 10),
        second: parseInt(m[6] || "0", 10),
        text: m[1] + "-" + m[2] + "-" + m[3] + " " + m[4] + ":" + m[5] + ":" + (m[6] || "00")
      };
    }
    // TradingView / broker export: Mon Apr 20 2026 16:00:00 GMT+0530 (India Standard Time)
    var js = s.match(/^[A-Za-z]{3}\s+([A-Za-z]{3})\s+(\d{1,2})\s+(\d{4})\s+(\d{2}):(\d{2})(?::(\d{2}))?/);
    if (!js) return null;
    var month = MONTHS[js[1].toLowerCase()];
    if (!month) return null;
    var dayNum = parseInt(js[2], 10);
    var year = js[3];
    var hour = parseInt(js[4], 10);
    var minute = parseInt(js[5], 10);
    var second = parseInt(js[6] || "0", 10);
    var day = year + "-" + pad2(month) + "-" + pad2(dayNum);
    return {
      day: day,
      hour: hour,
      minute: minute,
      second: second,
      text: day + " " + pad2(hour) + ":" + pad2(minute) + ":" + pad2(second)
    };
  }

  function minsOf(stamp) {
    return stamp.hour * 60 + stamp.minute;
  }

  function hhmm(mins) {
    var h = Math.floor(mins / 60);
    var m = mins % 60;
    return (h < 10 ? "0" : "") + h + ":" + (m < 10 ? "0" : "") + m;
  }

  function looksTime(line) {
    return /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}/.test(String(line).trim());
  }

  function looksDay(line) {
    return /^\d{4}-\d{2}-\d{2}$/.test(String(line).trim());
  }

  function num(v) {
    var n = parseFloat(String(v).replace(/,/g, ""));
    return isNaN(n) ? null : n;
  }

  function splitCsvLine(line) {
    var out = [];
    var cur = "";
    var inQ = false;
    var s = String(line);
    for (var i = 0; i < s.length; i++) {
      var ch = s.charAt(i);
      if (inQ) {
        if (ch === '"') {
          if (s.charAt(i + 1) === '"') {
            cur += '"';
            i += 1;
          } else {
            inQ = false;
          }
        } else {
          cur += ch;
        }
      } else if (ch === '"') {
        inQ = true;
      } else if (ch === ",") {
        out.push(cur.trim());
        cur = "";
      } else {
        cur += ch;
      }
    }
    out.push(cur.trim());
    return out;
  }

  function parseBars(text) {
    var raw = String(text || "").replace(/^\uFEFF/, "").trim();
    if (!raw) return [];
    var lines = raw.split(/\r?\n/).map(function (l) { return l.trim(); }).filter(function (l) {
      return l && l.charAt(0) !== "#";
    });
    if (!lines.length) return [];

    var head = lines[0].toLowerCase();
    if ((head.indexOf("time") !== -1 || head.indexOf("date") !== -1) && lines[0].indexOf(",") !== -1) {
      return parseCommaCsv(lines);
    }
    if ((looksTime(lines[0]) || parseStamp(splitCsvLine(lines[0])[0])) && lines[0].indexOf(",") !== -1) {
      return parseCommaCsv(["time,open,high,low,close,volume,day"].concat(lines));
    }
    return parseColumnDump(lines);
  }

  function parseCommaCsv(lines) {
    var header = splitCsvLine(lines[0]).map(function (h) { return h.toLowerCase(); });
    var idx = {};
    header.forEach(function (h, i) { idx[h] = i; });
    var bars = [];
    for (var i = 1; i < lines.length; i++) {
      var parts = splitCsvLine(lines[i]);
      if (parts.length < 5) continue;
      var timeIx = idx.time != null ? idx.time : (idx.date != null ? idx.date : 0);
      var timeRaw = parts[timeIx];
      var stamp = parseStamp(timeRaw);
      if (!stamp) continue;
      var bar = {
        time: stamp.text,
        day: parts[idx.day] || stamp.day,
        open: num(parts[idx.open != null ? idx.open : 1]),
        high: num(parts[idx.high != null ? idx.high : 2]),
        low: num(parts[idx.low != null ? idx.low : 3]),
        close: num(parts[idx.close != null ? idx.close : 4]),
        volume: num(parts[idx.volume != null ? idx.volume : 5]) || 0
      };
      if (bar.open == null || bar.high == null || bar.low == null || bar.close == null) continue;
      bars.push(bar);
    }
    return bars;
  }

  function parseColumnDump(lines) {
    var i = 0;
    while (i < lines.length && !looksTime(lines[i])) i++;
    var bars = [];
    while (i + 5 < lines.length) {
      if (!looksTime(lines[i])) {
        i += 1;
        continue;
      }
      var stamp = parseStamp(lines[i]);
      var o = num(lines[i + 1]);
      var h = num(lines[i + 2]);
      var l = num(lines[i + 3]);
      var c = num(lines[i + 4]);
      var v = num(lines[i + 5]);
      var day = stamp ? stamp.day : "";
      var next = i + 6;
      if (next < lines.length && looksDay(lines[next])) {
        day = lines[next];
        next += 1;
      }
      if (stamp && o != null && h != null && l != null && c != null) {
        bars.push({
          time: stamp.text,
          day: day,
          open: o,
          high: h,
          low: l,
          close: c,
          volume: v || 0
        });
      }
      i = next;
    }
    return bars;
  }

  function inferBarMinutes(bars) {
    var diffs = [];
    var cap = Math.min(bars.length, 120);
    for (var i = 1; i < cap; i++) {
      var a = parseStamp(bars[i - 1].time);
      var b = parseStamp(bars[i].time);
      if (!a || !b || a.day !== b.day) continue;
      var d = minsOf(b) - minsOf(a);
      if (d > 0) diffs.push(d);
    }
    diffs.sort(function (x, y) { return x - y; });
    return diffs.length ? diffs[Math.floor(diffs.length / 2)] : 15;
  }

  function resample(bars, minutes) {
    minutes = parseInt(minutes, 10) || 15;
    var native = inferBarMinutes(bars);
    if (minutes <= native) {
      return bars.slice();
    }
    var groups = [];
    var map = {};
    for (var i = 0; i < bars.length; i++) {
      var bar = bars[i];
      var stamp = parseStamp(bar.time);
      if (!stamp) continue;
      var floored = Math.floor(minsOf(stamp) / minutes) * minutes;
      var key = stamp.day + " " + hhmm(floored);
      var g = map[key];
      if (!g) {
        g = {
          time: stamp.day + " " + hhmm(floored) + ":00",
          day: stamp.day,
          open: bar.open,
          high: bar.high,
          low: bar.low,
          close: bar.close,
          volume: bar.volume || 0
        };
        map[key] = g;
        groups.push(g);
      } else {
        g.high = Math.max(g.high, bar.high);
        g.low = Math.min(g.low, bar.low);
        g.close = bar.close;
        g.volume += bar.volume || 0;
      }
    }
    return groups;
  }

  function measure(bar) {
    var o = bar.open;
    var h = bar.high;
    var l = bar.low;
    var c = bar.close;
    return {
      upperWick: h - Math.max(o, c),
      lowerWick: Math.min(o, c) - l,
      body: Math.abs(c - o),
      range: h - l
    };
  }

  function formCandle(prev, cur) {
    var m = measure(cur);
    var hh = cur.high > prev.high;
    var lh = cur.high < prev.high;
    var eqHigh = cur.high === prev.high;
    var bull = cur.close > cur.open;
    var bear = cur.close < cur.open;
    var doji = cur.close === cur.open;
    var state = "OTHER";
    if (eqHigh) state = "EQ";
    else if (doji) state = hh ? "HH_DOJI" : "LH_DOJI";
    else if (hh && bull) state = "S1";
    else if (hh && bear) state = "S2";
    else if (lh && bull) state = "S3";
    else if (lh && bear) state = "S4";

    var wickDom = "equal";
    if (m.upperWick > m.lowerWick) wickDom = "upper";
    else if (m.lowerWick > m.upperWick) wickDom = "lower";

    var bodyFill = m.range > 0 ? m.body / m.range : 0;
    var closeAbovePrevHigh = cur.close > prev.high;
    var volAbovePrev = (cur.volume || 0) > (prev.volume || 0);
    var bodyFillsHalf = bodyFill > 0.5;
    var openedAbovePrevClose = cur.open > prev.close;
    var closeBelowPrevLow = cur.close < prev.low;

    return {
      time: cur.time,
      day: cur.day,
      open: cur.open,
      high: cur.high,
      low: cur.low,
      close: cur.close,
      volume: cur.volume || 0,
      prevOpen: prev.open,
      prevHigh: prev.high,
      prevLow: prev.low,
      prevClose: prev.close,
      prevVolume: prev.volume || 0,
      upperWick: round(m.upperWick),
      lowerWick: round(m.lowerWick),
      body: round(m.body),
      range: round(m.range),
      bodyFill: round(bodyFill, 4),
      hh: hh,
      lh: lh,
      eqHigh: eqHigh,
      bull: bull,
      bear: bear,
      doji: doji,
      state: state,
      wickDom: wickDom,
      closeAbovePrevHigh: closeAbovePrevHigh,
      volAbovePrev: volAbovePrev,
      bodyFillsHalf: bodyFillsHalf,
      openedAbovePrevClose: openedAbovePrevClose,
      closeBelowPrevLow: closeBelowPrevLow,
      closeVsPrevClose: cur.close - prev.close,
      closeVsPrevHigh: cur.close - prev.high,
      closeVsPrevLow: cur.close - prev.low,
      pair: { previous: prev, current: cur }
    };
  }

  function recordSeries(bars) {
    var out = [];
    for (var i = 1; i < bars.length; i++) {
      var rec = formCandle(bars[i - 1], bars[i]);
      rec.month = rec.time ? String(rec.time).slice(0, 7) : "";
      if (i + 1 < bars.length) {
        rec.nextClose = bars[i + 1].close;
        rec.nextPts = round(bars[i + 1].close - bars[i].close, 4);
        rec.nextUp = rec.nextPts > 0;
        rec.hasNext = true;
        rec.hasSessionNext = bars[i + 1].day === bars[i].day;
        rec.sessionNextPts = rec.hasSessionNext ? rec.nextPts : null;
      } else {
        rec.nextClose = null;
        rec.nextPts = null;
        rec.nextUp = null;
        rec.hasNext = false;
        rec.hasSessionNext = false;
        rec.sessionNextPts = null;
      }
      out.push(rec);
    }
    return out;
  }

  function scoredRecords(records) {
    return (records || []).filter(function (r) { return r.hasSessionNext; });
  }

  function nextBarTrades(records, predicate, side) {
    side = side || "long";
    var rows = scoredRecords(records);
    var trades = [];
    for (var i = 0; i < rows.length; i++) {
      var rec = rows[i];
      if (predicate && !predicate(rec)) continue;
      var pts = side === "short" ? -rec.sessionNextPts : rec.sessionNextPts;
      trades.push({
        side: side,
        entry: rec.close,
        exit: rec.nextClose,
        pts: pts,
        time: rec.time,
        month: rec.month,
        kind: "NEXT"
      });
    }
    return trades;
  }

  function attachPnl(stats, trades) {
    if (!Charges || !Charges.bookPnl) return stats;
    var book = Charges.bookPnl(trades);
    stats.n = book.n;
    stats.wins = book.wins;
    stats.winRate = book.winRate;
    stats.pUp = sidePUp(trades);
    stats.net = book.pts;
    stats.ptsPerTrade = book.ptsPerTrade;
    stats.lots = book.lots;
    stats.grossInr = book.grossInr;
    stats.charges = book.charges;
    stats.afterCharges = book.afterCharges;
    stats.tax = book.tax;
    stats.afterTax = book.afterTax;
    stats.months = book.months;
    return stats;
  }

  function sidePUp(trades) {
    if (!trades.length) return null;
    var up = 0;
    for (var i = 0; i < trades.length; i++) {
      var t = trades[i];
      var raw = t.side === "short" ? -t.pts : t.pts;
      if (raw > 0) up += 1;
    }
    return round(up / trades.length, 4);
  }

  function summarize(records, side) {
    var trades = nextBarTrades(records, null, side || "long");
    var n = trades.length;
    if (!n) {
      return attachPnl({
        n: 0,
        pUp: null,
        ptsPerTrade: null,
        net: 0,
        winRate: null,
        wins: 0,
        excess: null,
        share: 0
      }, []);
    }
    var net = 0;
    var wins = 0;
    var i;
    for (i = 0; i < trades.length; i++) {
      net += trades[i].pts;
      if (trades[i].pts > 0) wins += 1;
    }
    return attachPnl({
      n: n,
      pUp: sidePUp(trades),
      ptsPerTrade: round(net / n, 4),
      net: round(net, 4),
      winRate: round(wins / n, 4),
      wins: wins
    }, trades);
  }

  function scoreSubset(all, predicate, side) {
    side = side || "long";
    var base = summarize(all, "long");
    var trades = nextBarTrades(all, predicate, side);
    var stats = attachPnl({
      n: trades.length,
      pUp: sidePUp(trades),
      ptsPerTrade: trades.length ? round(trades.reduce(function (s, t) { return s + t.pts; }, 0) / trades.length, 4) : null,
      net: round(trades.reduce(function (s, t) { return s + t.pts; }, 0), 4),
      winRate: trades.length ? round(trades.filter(function (t) { return t.pts > 0; }).length / trades.length, 4) : null,
      wins: trades.filter(function (t) { return t.pts > 0; }).length
    }, trades);
    var drift = base.ptsPerTrade || 0;
    if (side === "short") drift = -drift;
    stats.excess = stats.n ? round(stats.ptsPerTrade - drift, 4) : null;
    stats.share = base.n ? round(trades.length / base.n, 4) : 0;
    stats.baseline = base.ptsPerTrade;
    stats.baselinePUp = base.pUp;
    stats.side = side;
    return stats;
  }

  function stateScorecard(records) {
    var names = ["S1", "S2", "S3", "S4", "EQ"];
    var labels = {
      S1: "HH + close>open",
      S2: "HH + close<open",
      S3: "LH + close>open",
      S4: "LH + close<open",
      EQ: "equal high"
    };
    return names.map(function (name) {
      var row = scoreSubset(records, function (r) { return r.state === name; });
      row.state = name;
      row.label = labels[name];
      return row;
    });
  }

  function wickScorecard(records) {
    return [
      { key: "upper", label: "upper wick > lower wick" },
      { key: "lower", label: "lower wick > upper wick" },
      { key: "equal", label: "equal wicks" }
    ].map(function (item) {
      var row = scoreSubset(records, function (r) { return r.wickDom === item.key; });
      row.wickDom = item.key;
      row.label = item.label;
      return row;
    });
  }

  function stateWickScorecard(records) {
    var states = ["S1", "S2", "S3", "S4"];
    var wicks = ["upper", "lower"];
    var out = [];
    for (var s = 0; s < states.length; s++) {
      for (var w = 0; w < wicks.length; w++) {
        (function (state, wick) {
          var row = scoreSubset(records, function (r) {
            return r.state === state && r.wickDom === wick;
          });
          row.state = state;
          row.wickDom = wick;
          row.label = state + " + " + wick + " wick dominant";
          out.push(row);
        })(states[s], wicks[w]);
      }
    }
    return out;
  }

  function scoreRules(records, rules, side) {
    var valid = (rules || []).filter(function (r) { return r && r.ok; });
    return scoreSubset(records, function (rec) {
      if (!valid.length) return false;
      for (var i = 0; i < valid.length; i++) {
        if (!RuleEngine.evaluateRule(valid[i], rec.pair)) return false;
      }
      return true;
    }, side);
  }

  function scoreCombination(records, combo, side) {
    return scoreSubset(records, function (rec) {
      return RuleEngine.evaluateCombination(combo, rec.pair);
    }, side);
  }

  function sideFromState(rec) {
    if (rec.state === "S1" || rec.state === "S3") return "long";
    if (rec.state === "S2" || rec.state === "S4") return "short";
    return null;
  }

  function scoreFlip(records, sideOf, options) {
    options = options || {};
    var flattenSession = !!options.flattenSession;
    var rows = records || [];
    var pos = "flat";
    var entry = null;
    var trades = [];
    var skippedSame = 0;
    var skippedNone = 0;
    for (var i = 0; i < rows.length; i++) {
      var rec = rows[i];
      var lastOfFile = i === rows.length - 1;
      var lastOfSession = flattenSession && !rec.hasSessionNext;
      if ((lastOfSession || lastOfFile) && pos !== "flat" && entry != null) {
        var flatPts = pos === "long" ? rec.close - entry : entry - rec.close;
        trades.push({
          side: pos,
          entry: entry,
          exit: rec.close,
          time: rec.time,
          month: rec.month,
          pts: round(flatPts, 4),
          kind: lastOfFile && !lastOfSession ? "EOF_FLAT" : "SESSION_FLAT"
        });
        pos = "flat";
        entry = null;
      }
      if (lastOfSession || lastOfFile) continue;

      var want = sideOf(rec);
      if (!want) {
        skippedNone += 1;
        continue;
      }
      if (want === pos) {
        skippedSame += 1;
        continue;
      }
      if (pos !== "flat" && entry != null) {
        var pts = pos === "long" ? rec.close - entry : entry - rec.close;
        trades.push({
          side: pos,
          entry: entry,
          exit: rec.close,
          time: rec.time,
          month: rec.month,
          pts: round(pts, 4),
          kind: "FLIP"
        });
      }
      pos = want;
      entry = rec.close;
    }
    var stats = {
      n: trades.length,
      net: round(trades.reduce(function (s, t) { return s + t.pts; }, 0), 4),
      ptsPerTrade: trades.length ? round(trades.reduce(function (s, t) { return s + t.pts; }, 0) / trades.length, 4) : null,
      winRate: trades.length ? round(trades.filter(function (t) { return t.pts > 0; }).length / trades.length, 4) : null,
      skippedSame: skippedSame,
      skippedNone: skippedNone
    };
    attachPnl(stats, trades);
    stats.pUp = stats.winRate;
    return stats;
  }

  var TREE_LEAVES = [
    {
      id: "buy_break_vol_body",
      path: "close > prev high, vol > prev, body fills half",
      action: "BUY",
      test: function (r) { return r.closeAbovePrevHigh && r.volAbovePrev && r.bodyFillsHalf; }
    },
    {
      id: "buy_break_vol_thin",
      path: "close > prev high, vol > prev, body does not fill half",
      action: "BUY",
      test: function (r) { return r.closeAbovePrevHigh && r.volAbovePrev && !r.bodyFillsHalf; }
    },
    {
      id: "hold_break_lowvol",
      path: "close > prev high, vol not above prev",
      action: "HOLD",
      test: function (r) { return r.closeAbovePrevHigh && !r.volAbovePrev; }
    },
    {
      id: "buy_gap_open",
      path: "close not above prev high, opened above prev close",
      action: "BUY",
      test: function (r) { return !r.closeAbovePrevHigh && r.openedAbovePrevClose; }
    },
    {
      id: "sell_reject_uw",
      path: "close not above prev high, opened not above prev close, vol > prev, UW > LW",
      action: "SELL",
      test: function (r) {
        return !r.closeAbovePrevHigh && !r.openedAbovePrevClose && r.volAbovePrev && r.wickDom === "upper";
      }
    },
    {
      id: "hold_vol_no_uw",
      path: "close not above prev high, opened not above prev close, vol > prev, UW does not beat LW",
      action: "HOLD",
      test: function (r) {
        return !r.closeAbovePrevHigh && !r.openedAbovePrevClose && r.volAbovePrev && r.wickDom !== "upper";
      }
    },
    {
      id: "buy_close_below_low",
      path: "close not above prev high, opened not above prev close, vol not above prev, close < prev low",
      action: "BUY",
      test: function (r) {
        return !r.closeAbovePrevHigh && !r.openedAbovePrevClose && !r.volAbovePrev && r.closeBelowPrevLow;
      }
    },
    {
      id: "hold_rest",
      path: "close not above prev high, opened not above prev close, vol not above prev, close not below prev low",
      action: "HOLD",
      test: function (r) {
        return !r.closeAbovePrevHigh && !r.openedAbovePrevClose && !r.volAbovePrev && !r.closeBelowPrevLow;
      }
    }
  ];

  function treeAction(rec) {
    if (rec.closeAbovePrevHigh) return rec.volAbovePrev ? "BUY" : "HOLD";
    if (rec.openedAbovePrevClose) return "BUY";
    if (rec.volAbovePrev) return rec.wickDom === "upper" ? "SELL" : "HOLD";
    return rec.closeBelowPrevLow ? "BUY" : "HOLD";
  }

  function sideFromTree(rec) {
    var action = treeAction(rec);
    if (action === "BUY") return "long";
    if (action === "SELL") return "short";
    return null;
  }

  function emptyBook(n) {
    return attachPnl({
      n: n || 0,
      pUp: null,
      ptsPerTrade: null,
      net: 0,
      winRate: null,
      wins: 0,
      excess: null
    }, []);
  }

  function scoreTreeLeaves(records) {
    return TREE_LEAVES.map(function (leaf) {
      var longRow = scoreSubset(records, leaf.test, "long");
      var shortRow = scoreSubset(records, leaf.test, "short");
      var actionRow = leaf.action === "BUY" ? longRow : leaf.action === "SELL" ? shortRow : emptyBook(longRow.n);
      if (leaf.action === "HOLD") actionRow.n = longRow.n;
      return {
        id: leaf.id,
        path: leaf.path,
        action: leaf.action,
        n: longRow.n,
        long: longRow,
        short: shortRow,
        acted: actionRow
      };
    });
  }

  function scoreTreeFollow(records) {
    var trades = [];
    var rows = scoredRecords(records);
    var buys = 0;
    var sells = 0;
    var holds = 0;
    for (var i = 0; i < rows.length; i++) {
      var rec = rows[i];
      var side = sideFromTree(rec);
      if (!side) {
        holds += 1;
        continue;
      }
      if (side === "long") buys += 1;
      else sells += 1;
      trades.push({
        side: side,
        entry: rec.close,
        exit: rec.nextClose,
        pts: side === "short" ? -rec.sessionNextPts : rec.sessionNextPts,
        time: rec.time,
        month: rec.month,
        kind: "TREE"
      });
    }
    var stats = attachPnl({
      n: trades.length,
      net: round(trades.reduce(function (s, t) { return s + t.pts; }, 0), 4),
      ptsPerTrade: trades.length ? round(trades.reduce(function (s, t) { return s + t.pts; }, 0) / trades.length, 4) : null,
      winRate: trades.length ? round(trades.filter(function (t) { return t.pts > 0; }).length / trades.length, 4) : null
    }, trades);
    stats.buys = buys;
    stats.sells = sells;
    stats.holds = holds;
    stats.scored = rows.length;
    return stats;
  }

  function splitBySessionDays(records, trainDays) {
    var days = [];
    var seen = {};
    (records || []).forEach(function (r) {
      if (r.day && !seen[r.day]) {
        seen[r.day] = true;
        days.push(r.day);
      }
    });
    days.sort();
    var nTrain = trainDays == null ? 64 : trainDays;
    var trainSet = {};
    days.slice(0, nTrain).forEach(function (d) { trainSet[d] = true; });
    return {
      allDays: days,
      trainDays: days.slice(0, nTrain),
      testDays: days.slice(nTrain),
      train: (records || []).filter(function (r) { return trainSet[r.day]; }),
      test: (records || []).filter(function (r) { return !trainSet[r.day]; })
    };
  }

  function labFromText(text, minutes) {
    var native = parseBars(text);
    var nativeTf = inferBarMinutes(native);
    var bars = resample(native, minutes || nativeTf || 15);
    var records = recordSeries(bars);
    var base = summarize(records);
    var reverseSide = function (r) {
      var side = sideFromState(r);
      if (side === "long") return "short";
      if (side === "short") return "long";
      return null;
    };
    return {
      nativeCount: native.length,
      nativeTf: nativeTf,
      barCount: bars.length,
      tf: minutes || nativeTf || 15,
      first: bars[0] ? bars[0].time : null,
      last: bars.length ? bars[bars.length - 1].time : null,
      baseline: base,
      records: records,
      states: stateScorecard(records),
      wicks: wickScorecard(records),
      stateWicks: stateWickScorecard(records),
      flipIntuition: scoreFlip(records, sideFromState),
      flipReversal: scoreFlip(records, reverseSide),
      flipIntuitionSession: scoreFlip(records, sideFromState, { flattenSession: true }),
      flipReversalSession: scoreFlip(records, reverseSide, { flattenSession: true }),
      treeLeaves: scoreTreeLeaves(records),
      treeFollow: scoreTreeFollow(records),
      treeFlip: scoreFlip(records, sideFromTree),
      treeFlipSession: scoreFlip(records, sideFromTree, { flattenSession: true })
    };
  }

  return {
    parseStamp: parseStamp,
    parseBars: parseBars,
    inferBarMinutes: inferBarMinutes,
    resample: resample,
    measure: measure,
    formCandle: formCandle,
    recordSeries: recordSeries,
    nextBarTrades: nextBarTrades,
    summarize: summarize,
    scoreSubset: scoreSubset,
    stateScorecard: stateScorecard,
    wickScorecard: wickScorecard,
    stateWickScorecard: stateWickScorecard,
    scoreRules: scoreRules,
    scoreCombination: scoreCombination,
    scoreFlip: scoreFlip,
    sideFromState: sideFromState,
    treeAction: treeAction,
    sideFromTree: sideFromTree,
    scoreTreeLeaves: scoreTreeLeaves,
    scoreTreeFollow: scoreTreeFollow,
    splitBySessionDays: splitBySessionDays,
    TREE_LEAVES: TREE_LEAVES,
    labFromText: labFromText
  };
});
