/**
 * Plain-English candlestick rule parser and combination builder.
 * Works in the browser and in Node.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.RuleEngine = factory();
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var BAR_PREV = /^(previous|prev|last|prior|yesterday)$/i;
  var BAR_CUR = /^(current|this|today|now)$/i;

  var METRICS = [
    { id: "upperWick", label: "upper wick", names: ["upper wick", "upper shadow", "top wick", "top shadow"] },
    { id: "lowerWick", label: "lower wick", names: ["lower wick", "lower shadow", "bottom wick", "bottom shadow"] },
    { id: "bodyFill", label: "body fill", names: ["body fill", "body fraction"] },
    { id: "body", label: "body", names: ["candle body", "real body", "body size", "body"] },
    { id: "range", label: "range", names: ["true range", "range", "spread"] },
    { id: "volume", label: "volume", names: ["volume"] },
    { id: "open", label: "open", names: ["opened", "opening", "open"] },
    { id: "high", label: "high", names: ["high"] },
    { id: "low", label: "low", names: ["low"] },
    { id: "close", label: "close", names: ["closing", "close"] }
  ];

  var OPERATORS = [
    { id: "gte", symbol: ">=", english: "is at least", pattern: /\bgreater than or equal to\b|\bat least\b|>=/i },
    { id: "lte", symbol: "<=", english: "is at most", pattern: /\bless than or equal to\b|\bat most\b|<=/i },
    { id: "neq", symbol: "!=", english: "is not equal to", pattern: /\bnot equal to\b|\bis not\b|!=|<>/i },
    { id: "gt", symbol: ">", english: "is greater than", pattern: /\bgreater than\b|\bmore than\b|\bbigger than\b|\bhigher than\b|\blarger than\b|\babove\b|>/i },
    { id: "lt", symbol: "<", english: "is less than", pattern: /\bless than\b|\bsmaller than\b|\blower than\b|\bbelow\b|\bunder\b|</i },
    { id: "eq", symbol: "=", english: "is equal to", pattern: /\bequal to\b|\bequals\b|==|=/i }
  ];

  var SHORTCUTS = [
    {
      pattern: /^(a\s+)?bullish(\s+candle)?$/,
      name: "Bullish candle",
      lhs: { type: "field", bar: "current", metric: "close", label: "close" },
      op: "gt",
      rhs: { type: "field", bar: "current", metric: "open", label: "open" }
    },
    {
      pattern: /^(a\s+)?bearish(\s+candle)?$/,
      name: "Bearish candle",
      lhs: { type: "field", bar: "current", metric: "close", label: "close" },
      op: "lt",
      rhs: { type: "field", bar: "current", metric: "open", label: "open" }
    },
    {
      pattern: /^(current\s+|this\s+)?(close|closing)(\s+is)?\s+above\s+(prev|previous|last)\s+high\??$/,
      name: "Close above previous high",
      lhs: { type: "field", bar: "current", metric: "close", label: "close" },
      op: "gt",
      rhs: { type: "field", bar: "previous", metric: "high", label: "high" }
    },
    {
      pattern: /^(current\s+|this\s+)?volume(\s+is)?\s+above\s+(prev|previous|last)(\s+volume)?\??$/,
      name: "Volume above previous",
      lhs: { type: "field", bar: "current", metric: "volume", label: "volume" },
      op: "gt",
      rhs: { type: "field", bar: "previous", metric: "volume", label: "volume" }
    },
    {
      pattern: /^(current\s+|the\s+)?body\s+fills?\s+(over|more than|greater than)\s+(a\s+)?half\??$/,
      name: "Body fills over half",
      lhs: { type: "field", bar: "current", metric: "bodyFill", label: "body fill" },
      op: "gt",
      rhs: { type: "number", value: 0.5, percent: false }
    },
    {
      pattern: /^(current\s+|this\s+)?open(ed)?(\s+is)?\s+above\s+(prev|previous|last)\s+close\??$/,
      name: "Opened above previous close",
      lhs: { type: "field", bar: "current", metric: "open", label: "open" },
      op: "gt",
      rhs: { type: "field", bar: "previous", metric: "close", label: "close" }
    },
    {
      pattern: /^(current\s+|the\s+)?upper\s+wick\s+beats\s+(the\s+)?(current\s+)?lower(\s+wick)?\??$/,
      name: "Upper wick beats lower",
      lhs: { type: "field", bar: "current", metric: "upperWick", label: "upper wick" },
      op: "gt",
      rhs: { type: "field", bar: "current", metric: "lowerWick", label: "lower wick" }
    },
    {
      pattern: /^(current\s+|this\s+)?(close|closing)(\s+is)?\s+below\s+(prev|previous|last)\s+low\??$/,
      name: "Close below previous low",
      lhs: { type: "field", bar: "current", metric: "close", label: "close" },
      op: "lt",
      rhs: { type: "field", bar: "previous", metric: "low", label: "low" }
    }
  ];

  function normalizeOperators(text) {
    return String(text)
      .replace(/>=/g, " >= ")
      .replace(/<=/g, " <= ")
      .replace(/!=/g, " != ")
      .replace(/<>/g, " != ")
      .replace(/==/g, " = ")
      .replace(/(^|[^<>!=])>(?!=)/g, "$1 > ")
      .replace(/(^|[^<>!=])<(?!=)/g, "$1 < ")
      .replace(/(^|[^=<>!])=(?!=)/g, "$1 = ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function cleanSide(text) {
    return String(text)
      .replace(/^(if|is|was|the|a|an)\s+/ig, "")
      .replace(/\s+(is|was)$/ig, "")
      .replace(/[.?!,]+$/g, "")
      .trim();
  }

  function matchMetric(text) {
    var lower = text.toLowerCase();
    for (var i = 0; i < METRICS.length; i++) {
      var metric = METRICS[i];
      for (var j = 0; j < metric.names.length; j++) {
        var name = metric.names[j];
        if (lower === name || lower.indexOf(name) === 0) {
          var after = text.slice(name.length);
          if (!after.trim()) {
            return { id: metric.id, label: metric.label, matchedLength: name.length };
          }
        }
      }
    }
    return null;
  }

  function parseOperand(text) {
    var original = text;
    text = String(text || "").trim().replace(/^(the|a|an)\s+/i, "");
    if (!text) {
      return { ok: false, error: "Missing a value on one side of the comparison." };
    }

    var num = text.match(/^-?\d+(\.\d+)?%?$/);
    if (num) {
      return {
        ok: true,
        operand: {
          type: "number",
          value: parseFloat(text),
          percent: /%$/.test(text)
        }
      };
    }

    var bar = "current";
    var barMatch = text.match(/^(current|this|today|now|previous|prev|last|prior|yesterday)\s+/i);
    if (barMatch) {
      bar = BAR_PREV.test(barMatch[1]) ? "previous" : "current";
      text = text.slice(barMatch[0].length);
    }

    var metric = matchMetric(text);
    if (!metric) {
      return {
        ok: false,
        error: 'Could not understand "' + original + '". Use fields like close, open, high, low, upper wick, lower wick, body, range, or volume.'
      };
    }

    var leftover = text.slice(metric.matchedLength).trim();
    if (leftover) {
      return { ok: false, error: 'Extra text after the field: "' + leftover + '".' };
    }

    return {
      ok: true,
      operand: { type: "field", bar: bar, metric: metric.id, label: metric.label }
    };
  }

  function findOperator(text) {
    for (var i = 0; i < OPERATORS.length; i++) {
      var op = OPERATORS[i];
      var match = text.match(op.pattern);
      if (match) {
        return { op: op, index: match.index, length: match[0].length };
      }
    }
    return null;
  }

  function operatorById(id) {
    for (var i = 0; i < OPERATORS.length; i++) {
      if (OPERATORS[i].id === id) return OPERATORS[i];
    }
    return OPERATORS[3];
  }

  function parseRule(raw, index) {
    var source = String(raw == null ? "" : raw).trim();
    if (!source) {
      return { ok: false, index: index, raw: source, error: "Empty rule." };
    }

    var name = null;
    var text = source;
    var named = text.match(/^([^:]{1,40}):\s+(.+)$/);
    if (named && !/^(if|current|previous|prev|last|close|open|high|low)$/i.test(named[1].trim())) {
      name = named[1].trim();
      text = named[2];
    }

    text = normalizeOperators(text).replace(/^if\s+/i, "");
    var shortcutText = text.replace(/^(is|a|an)\s+/i, "").replace(/\s+/g, " ").trim();

    for (var s = 0; s < SHORTCUTS.length; s++) {
      if (SHORTCUTS[s].pattern.test(shortcutText)) {
        var sc = SHORTCUTS[s];
        return succeedRule(index, source, name || sc.name, sc.lhs, sc.op, sc.rhs);
      }
    }

    var found = findOperator(text);
    if (!found) {
      return {
        ok: false,
        index: index,
        raw: source,
        error: 'No comparison found in "' + source + '". Try >, <, >=, <=, or phrases like "greater than".'
      };
    }

    var lhsText = cleanSide(text.slice(0, found.index));
    var rhsText = cleanSide(text.slice(found.index + found.length));
    var lhs = parseOperand(lhsText);
    if (!lhs.ok) {
      return { ok: false, index: index, raw: source, error: "Left side: " + lhs.error };
    }
    var rhs = parseOperand(rhsText);
    if (!rhs.ok) {
      return { ok: false, index: index, raw: source, error: "Right side: " + rhs.error };
    }

    return succeedRule(index, source, name, lhs.operand, found.op.id, rhs.operand);
  }

  function succeedRule(index, raw, name, lhs, opId, rhs) {
    var rule = {
      ok: true,
      index: index,
      raw: raw,
      name: name,
      lhs: lhs,
      op: opId,
      rhs: rhs,
      id: "R" + (index + 1)
    };
    rule.canonical = formatRule(rule, false);
    rule.english = formatRule(rule, true);
    rule.label = name || rule.canonical;
    return rule;
  }

  function formatOperand(operand, english) {
    if (!operand) return "";
    if (operand.type === "number") {
      return operand.percent ? String(operand.value) + "%" : String(operand.value);
    }
    var bar = operand.bar === "previous" ? "previous" : "current";
    var field = operand.label || operand.metric;
    if (english) {
      return bar + " " + field;
    }
    return bar + " " + field;
  }

  function formatRule(rule, english) {
    var left = formatOperand(rule.lhs, english);
    var right = formatOperand(rule.rhs, english);
    if (english) {
      return capitalize(left) + " " + operatorById(rule.op).english + " " + right;
    }
    return left + " " + operatorById(rule.op).symbol + " " + right;
  }

  function capitalize(text) {
    if (!text) return text;
    return text.charAt(0).toUpperCase() + text.slice(1);
  }

  function splitOnSubsequentIf(text) {
    var indices = [];
    var re = /\bif\b/gi;
    var match;
    while ((match = re.exec(text))) {
      indices.push(match.index);
    }
    if (indices.length <= 1) return [text];
    var parts = [];
    var startAt = 0;
    if (indices[0] !== 0) {
      parts.push(text.slice(0, indices[0]));
      startAt = 0;
    }
    for (var i = startAt; i < indices.length; i++) {
      var end = i + 1 < indices.length ? indices[i + 1] : text.length;
      parts.push(text.slice(indices[i], end));
    }
    return parts;
  }

  function splitRuleText(text) {
    var source = String(text == null ? "" : text);
    var chunks = source.split(/\r?\n|;+|combining with/i);
    var rules = [];
    for (var i = 0; i < chunks.length; i++) {
      var piece = chunks[i].trim();
      if (!piece || piece.charAt(0) === "#") continue;
      var splitIfs = splitOnSubsequentIf(piece);
      for (var j = 0; j < splitIfs.length; j++) {
        var ruleText = splitIfs[j].replace(/^[-*•]\s+/, "").replace(/^\d+[.)]\s+/, "").trim();
        if (ruleText && ruleText.charAt(0) !== "#") {
          rules.push(ruleText);
        }
      }
    }
    return rules;
  }

  function parseRules(text) {
    var rawRules = splitRuleText(text);
    var parsed = [];
    for (var i = 0; i < rawRules.length; i++) {
      parsed.push(parseRule(rawRules[i], i));
    }
    return parsed;
  }

  function binomial(n, k) {
    if (k < 0 || k > n) return 0;
    k = Math.min(k, n - k);
    var c = 1;
    for (var i = 0; i < k; i++) {
      c = (c * (n - i)) / (i + 1);
    }
    return Math.round(c);
  }

  function countCombinations(n, minK, maxK) {
    var total = 0;
    for (var k = minK; k <= maxK; k++) {
      total += binomial(n, k);
    }
    return total;
  }

  function kCombinations(items, k) {
    var n = items.length;
    var out = [];
    if (k <= 0 || k > n) return out;
    var idx = [];
    for (var i = 0; i < k; i++) idx.push(i);

    while (true) {
      var combo = [];
      for (var j = 0; j < k; j++) combo.push(items[idx[j]]);
      out.push(combo);
      var t = k - 1;
      while (t >= 0 && idx[t] === n - k + t) t--;
      if (t < 0) break;
      idx[t]++;
      for (var u = t + 1; u < k; u++) idx[u] = idx[u - 1] + 1;
    }
    return out;
  }

  function generateCombinations(rules, options) {
    options = options || {};
    var valid = [];
    for (var i = 0; i < rules.length; i++) {
      if (rules[i] && rules[i].ok && rules[i].included !== false) valid.push(rules[i]);
    }

    var n = valid.length;
    var minK = options.minK == null ? 1 : Math.max(1, parseInt(options.minK, 10) || 1);
    var maxK = options.maxK == null ? n : parseInt(options.maxK, 10);
    if (isNaN(maxK) || maxK < 1) maxK = n;
    maxK = Math.min(Math.max(minK, maxK), n);
    minK = Math.min(minK, maxK);
    if (n === 0) {
      return { combinations: [], total: 0, shown: 0, minK: minK, maxK: maxK, truncated: false };
    }

    var limit = options.limit;
    if (limit == null || limit === "" || Number(limit) <= 0) {
      limit = Infinity;
    } else {
      limit = Math.max(1, parseInt(limit, 10) || Infinity);
    }

    var total = countCombinations(n, minK, maxK);
    var combinations = [];
    var seq = 1;

    for (var k = minK; k <= maxK && combinations.length < limit; k++) {
      var groups = kCombinations(valid, k);
      for (var g = 0; g < groups.length && combinations.length < limit; g++) {
        var members = groups[g];
        var combo = {
          id: "C" + seq,
          size: members.length,
          rules: members,
          indexes: members.map(function (rule) { return rule.index; }),
          ids: members.map(function (rule) { return rule.id; }),
          canonical: members.map(function (rule) { return rule.canonical; }).join(" AND "),
          english: members.map(function (rule) { return rule.english; }).join(", and ")
        };
        combinations.push(combo);
        seq += 1;
      }
    }

    return {
      combinations: combinations,
      total: total,
      shown: combinations.length,
      minK: minK,
      maxK: maxK,
      truncated: combinations.length < total,
      ruleCount: n
    };
  }

  function buildCombinationTree(combinationResult) {
    var root = {
      id: "root",
      label: "All combinations",
      children: [],
      combination: null,
      depth: 0
    };

    var combos = combinationResult.combinations || [];
    for (var i = 0; i < combos.length; i++) {
      var combo = combos[i];
      var node = root;
      for (var r = 0; r < combo.rules.length; r++) {
        var rule = combo.rules[r];
        var child = null;
        for (var c = 0; c < node.children.length; c++) {
          if (node.children[c].ruleId === rule.id) {
            child = node.children[c];
            break;
          }
        }
        if (!child) {
          child = {
            id: node.id + "/" + rule.id,
            ruleId: rule.id,
            rule: rule,
            label: rule.label,
            children: [],
            combination: null,
            depth: node.depth + 1
          };
          node.children.push(child);
        }
        if (r === combo.rules.length - 1) {
          child.combination = combo;
        }
        node = child;
      }
    }
    return root;
  }

  function metricValue(candle, metric) {
    if (!candle) return null;
    var o = Number(candle.open);
    var h = Number(candle.high);
    var l = Number(candle.low);
    var cl = Number(candle.close);
    var v = candle.volume == null ? 0 : Number(candle.volume);
    switch (metric) {
      case "open": return o;
      case "high": return h;
      case "low": return l;
      case "close": return cl;
      case "volume": return v;
      case "upperWick": return h - Math.max(o, cl);
      case "lowerWick": return Math.min(o, cl) - l;
      case "body": return Math.abs(cl - o);
      case "bodyFill": return (h - l) > 0 ? Math.abs(cl - o) / (h - l) : 0;
      case "range": return h - l;
      default: return null;
    }
  }

  function resolveOperand(operand, candles) {
    if (!operand) return null;
    if (operand.type === "number") return operand.value;
    var candle = candles[operand.bar];
    return metricValue(candle, operand.metric);
  }

  function compare(op, left, right) {
    if (left == null || right == null || isNaN(left) || isNaN(right)) return false;
    switch (op) {
      case "gt": return left > right;
      case "lt": return left < right;
      case "gte": return left >= right;
      case "lte": return left <= right;
      case "eq": return left === right;
      case "neq": return left !== right;
      default: return false;
    }
  }

  function evaluateRule(rule, candles) {
    if (!rule || !rule.ok) return false;
    return compare(rule.op, resolveOperand(rule.lhs, candles), resolveOperand(rule.rhs, candles));
  }

  function evaluateCombination(combo, candles) {
    if (!combo || !combo.rules) return false;
    for (var i = 0; i < combo.rules.length; i++) {
      if (!evaluateRule(combo.rules[i], candles)) return false;
    }
    return true;
  }

  return {
    METRICS: METRICS,
    OPERATORS: OPERATORS,
    splitRuleText: splitRuleText,
    parseRule: parseRule,
    parseRules: parseRules,
    formatRule: formatRule,
    formatOperand: formatOperand,
    generateCombinations: generateCombinations,
    countCombinations: countCombinations,
    binomial: binomial,
    buildCombinationTree: buildCombinationTree,
    metricValue: metricValue,
    evaluateRule: evaluateRule,
    evaluateCombination: evaluateCombination
  };
});
