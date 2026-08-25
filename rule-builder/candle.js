/**
 * Current-vs-previous candle lab.
 * Only this bar vs the last bar: high, open, close, wicks.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory(typeof require === "function" ? require("./engine.js") : root.RuleEngine);
  } else {
    root.CandleLab = factory(root.RuleEngine);
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function (RuleEngine) {
  "use strict";

  function round(n, d) {
    var f = Math.pow(10, d == null ? 4 : d);
    return Math.round(n * f) / f;
  }

  function parseStamp(raw) {
    var m = String(raw).trim().match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/);
    if (!m) return null;
    return {
      day: m[1] + "-" + m[2] + "-" + m[3],
      hour: parseInt(m[4], 10),
      minute: parseInt(m[5], 10),
      second: parseInt(m[6] || "0", 10),
      text: m[1] + "-" + m[2] + "-" + m[3] + " " + m[4] + ":" + m[5] + ":" + (m[6] || "00")
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
    return String(line).split(",").map(function (p) { return p.trim(); });
  }

  function parseBars(text) {
    var raw = String(text || "").replace(/^\uFEFF/, "").trim();
    if (!raw) return [];
    var lines = raw.split(/\r?\n/).map(function (l) { return l.trim(); }).filter(function (l) {
      return l && l.charAt(0) !== "#";
    });
    if (!lines.length) return [];

    if (lines[0].toLowerCase().indexOf("time") !== -1 && lines[0].indexOf(",") !== -1) {
      return parseCommaCsv(lines);
    }
    if (looksTime(lines[0]) && lines[0].indexOf(",") !== -1) {
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
      var timeRaw = parts[idx.time != null ? idx.time : 0];
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

  function resample(bars, minutes) {
    minutes = parseInt(minutes, 10) || 15;
    if (minutes <= 15) {
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
      upperWick: round(m.upperWick),
      lowerWick: round(m.lowerWick),
      body: round(m.body),
      range: round(m.range),
      hh: hh,
      lh: lh,
      eqHigh: eqHigh,
      bull: bull,
      bear: bear,
      doji: doji,
      state: state,
      wickDom: wickDom,
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
      if (i + 1 < bars.length) {
        rec.nextClose = bars[i + 1].close;
        rec.nextPts = round(bars[i + 1].close - bars[i].close, 4);
        rec.nextUp = rec.nextPts > 0;
        rec.hasNext = true;
      } else {
        rec.nextClose = null;
        rec.nextPts = null;
        rec.nextUp = null;
        rec.hasNext = false;
      }
      out.push(rec);
    }
    return out;
  }

  function scoredRecords(records) {
    return records.filter(function (r) { return r.hasNext; });
  }

  function summarize(records) {
    var rows = scoredRecords(records);
    var n = rows.length;
    if (!n) {
      return { n: 0, pUp: null, ptsPerTrade: null, net: 0, winRate: null, wins: 0 };
    }
    var net = 0;
    var wins = 0;
    for (var i = 0; i < rows.length; i++) {
      net += rows[i].nextPts;
      if (rows[i].nextPts > 0) wins += 1;
    }
    return {
      n: n,
      pUp: round(wins / n, 4),
      ptsPerTrade: round(net / n, 4),
      net: round(net, 4),
      winRate: round(wins / n, 4),
      wins: wins
    };
  }

  function scoreSubset(all, predicate) {
    var base = summarize(all);
    var sub = scoredRecords(all).filter(predicate);
    var stats = summarize(sub);
    stats.excess = stats.n ? round(stats.ptsPerTrade - (base.ptsPerTrade || 0), 4) : null;
    stats.share = base.n ? round(sub.length / base.n, 4) : 0;
    stats.baseline = base.ptsPerTrade;
    stats.baselinePUp = base.pUp;
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

  function scoreRules(records, rules) {
    var valid = (rules || []).filter(function (r) { return r && r.ok; });
    return scoreSubset(records, function (rec) {
      if (!valid.length) return false;
      for (var i = 0; i < valid.length; i++) {
        if (!RuleEngine.evaluateRule(valid[i], rec.pair)) return false;
      }
      return true;
    });
  }

  function scoreCombination(records, combo) {
    return scoreSubset(records, function (rec) {
      return RuleEngine.evaluateCombination(combo, rec.pair);
    });
  }

  function sideFromState(rec) {
    if (rec.state === "S1" || rec.state === "S3") return "long";
    if (rec.state === "S2" || rec.state === "S4") return "short";
    return null;
  }

  function scoreFlip(records, sideOf) {
    var rows = scoredRecords(records);
    var pos = "flat";
    var entry = null;
    var trades = [];
    var skippedSame = 0;
    var skippedNone = 0;
    for (var i = 0; i < rows.length; i++) {
      var rec = rows[i];
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
          pts: round(pts, 4),
          kind: "FLIP"
        });
      }
      pos = want;
      entry = rec.close;
    }
    var net = 0;
    var wins = 0;
    for (var t = 0; t < trades.length; t++) {
      net += trades[t].pts;
      if (trades[t].pts > 0) wins += 1;
    }
    return {
      n: trades.length,
      net: round(net, 4),
      ptsPerTrade: trades.length ? round(net / trades.length, 4) : null,
      winRate: trades.length ? round(wins / trades.length, 4) : null,
      skippedSame: skippedSame,
      skippedNone: skippedNone,
      trades: trades
    };
  }

  function labFromText(text, minutes) {
    var native = parseBars(text);
    var bars = resample(native, minutes || 15);
    var records = recordSeries(bars);
    var base = summarize(records);
    return {
      nativeCount: native.length,
      barCount: bars.length,
      tf: minutes || 15,
      first: bars[0] ? bars[0].time : null,
      last: bars.length ? bars[bars.length - 1].time : null,
      baseline: base,
      records: records,
      states: stateScorecard(records),
      wicks: wickScorecard(records),
      stateWicks: stateWickScorecard(records),
      flipIntuition: scoreFlip(records, sideFromState),
      flipReversal: scoreFlip(records, function (r) {
        var side = sideFromState(r);
        if (side === "long") return "short";
        if (side === "short") return "long";
        return null;
      })
    };
  }

  return {
    parseBars: parseBars,
    resample: resample,
    measure: measure,
    formCandle: formCandle,
    recordSeries: recordSeries,
    summarize: summarize,
    scoreSubset: scoreSubset,
    stateScorecard: stateScorecard,
    wickScorecard: wickScorecard,
    stateWickScorecard: stateWickScorecard,
    scoreRules: scoreRules,
    scoreCombination: scoreCombination,
    scoreFlip: scoreFlip,
    sideFromState: sideFromState,
    labFromText: labFromText
  };
});
