(function () {
  "use strict";

  var EXAMPLE =
    "if current close > current open\n" +
    "if current high > previous high\n" +
    "if current upper wick < current lower wick";

  var PRESET_TREND =
    "bullish candle\n" +
    "current high > previous high\n" +
    "current low > previous low\n" +
    "current close > previous close";

  var PRESET_WICK =
    "current close > current open\n" +
    "current lower wick > current body\n" +
    "current upper wick < current lower wick\n" +
    "current close > previous high";

  var HARD_CAP = 5000;
  var STORAGE_KEY = "ruleBuilder.v1";

  var state = {
    rules: [],
    result: null,
    tree: null,
    view: "tree",
    selectedId: null,
    expanded: {}
  };

  var els = {};

  function $(id) {
    return document.getElementById(id);
  }

  function debounce(fn, ms) {
    var t;
    return function () {
      var args = arguments;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(null, args); }, ms);
    };
  }

  function candles() {
    return {
      current: {
        open: num("curOpen"),
        high: num("curHigh"),
        low: num("curLow"),
        close: num("curClose")
      },
      previous: {
        open: num("prevOpen"),
        high: num("prevHigh"),
        low: num("prevLow"),
        close: num("prevClose")
      }
    };
  }

  function num(id) {
    return parseFloat($(id).value);
  }

  function derivedText(candle) {
    var uw = RuleEngine.metricValue(candle, "upperWick");
    var lw = RuleEngine.metricValue(candle, "lowerWick");
    var body = RuleEngine.metricValue(candle, "body");
    var range = RuleEngine.metricValue(candle, "range");
    function f(v) { return isNaN(v) ? "—" : String(Math.round(v * 1000) / 1000); }
    return "upper wick " + f(uw) + " · lower wick " + f(lw) + " · body " + f(body) + " · range " + f(range);
  }

  function updateDerived() {
    var c = candles();
    $("curDerived").textContent = derivedText(c.current);
    $("prevDerived").textContent = derivedText(c.previous);
  }

  function parseStatus() {
    var rules = state.rules;
    var valid = rules.filter(function (r) { return r.ok; }).length;
    var invalid = rules.length - valid;
    var status = $("parseStatus");
    if (!rules.length) {
      status.className = "status-line";
      status.textContent = "No rules yet.";
      return;
    }
    if (invalid) {
      status.className = "status-line error";
      status.textContent = valid + " valid, " + invalid + " could not be read.";
    } else {
      status.className = "status-line";
      status.textContent = valid + " rule" + (valid === 1 ? "" : "s") + " understood.";
    }
  }

  function renderRuleCards() {
    var root = $("ruleCards");
    root.innerHTML = "";
    state.rules.forEach(function (rule, index) {
      var card = document.createElement("div");
      card.className = "rule-card " + (rule.ok ? "valid" : "invalid");

      var include = document.createElement("input");
      include.type = "checkbox";
      include.className = "include";
      include.checked = rule.included !== false;
      include.disabled = !rule.ok;
      include.title = "Include in combinations";
      include.addEventListener("change", function () {
        state.rules[index].included = include.checked;
        buildCombinations();
      });

      var id = document.createElement("span");
      id.className = "rule-id";
      id.textContent = rule.ok ? rule.id : "—";

      var body = document.createElement("div");
      body.className = "body";
      if (rule.ok) {
        var canonical = document.createElement("div");
        canonical.className = "canonical";
        canonical.textContent = (rule.name ? rule.name + " · " : "") + rule.canonical;
        var english = document.createElement("div");
        english.className = "english";
        english.textContent = rule.english;
        body.appendChild(canonical);
        body.appendChild(english);
      } else {
        var raw = document.createElement("div");
        raw.className = "canonical";
        raw.textContent = rule.raw || "(empty)";
        var error = document.createElement("div");
        error.className = "error";
        error.textContent = rule.error;
        body.appendChild(raw);
        body.appendChild(error);
      }

      card.appendChild(include);
      card.appendChild(id);
      card.appendChild(body);
      root.appendChild(card);
    });
  }

  function parseInput() {
    var text = $("rulesInput").value;
    var parsed = RuleEngine.parseRules(text);
    parsed.forEach(function (rule, i) {
      if (state.rules[i] && state.rules[i].raw === rule.raw) {
        rule.included = state.rules[i].included;
      }
    });
    state.rules = parsed;
    parseStatus();
    renderRuleCards();
    persist();
  }

  function comboMatches(combo) {
    if (!combo) return false;
    return RuleEngine.evaluateCombination(combo, candles());
  }

  function renderStats() {
    var stats = $("stats");
    stats.innerHTML = "";
    var result = state.result;
    if (!result) return;

    function chip(html, cls) {
      var el = document.createElement("span");
      el.className = "chip" + (cls ? " " + cls : "");
      el.innerHTML = html;
      stats.appendChild(el);
    }

    chip("Rules <strong>" + (result.ruleCount || 0) + "</strong>");
    chip("Possible <strong>" + result.total + "</strong>");
    chip("Showing <strong>" + result.shown + "</strong>");
    chip("Size <strong>" + result.minK + "–" + result.maxK + "</strong>");
    if (result.truncated) {
      chip("Capped at n — raise “show at most” to see more", "warn");
    }

    if (result.shown) {
      var hits = result.combinations.filter(comboMatches).length;
      chip("Match sample <strong>" + hits + "</strong> / " + result.shown);
    }
  }

  function selectCombo(id) {
    state.selectedId = id;
    document.querySelectorAll(".tree-row.selected, .list tr.selected").forEach(function (el) {
      el.classList.remove("selected");
    });
    document.querySelectorAll('[data-combo="' + id + '"]').forEach(function (el) {
      el.classList.add("selected");
    });
    var combo = (state.result && state.result.combinations || []).find(function (c) { return c.id === id; });
    renderDetail(combo);
  }

  function renderDetail(combo) {
    var box = $("detail");
    if (!combo) {
      box.classList.add("hidden");
      box.innerHTML = "";
      return;
    }
    var match = comboMatches(combo);
    box.classList.remove("hidden");
    box.innerHTML =
      "<h3>" + combo.id + " · " + combo.size + " rule" + (combo.size === 1 ? "" : "s") +
      (match ? " · matches sample candles" : " · does not match sample") + "</h3>" +
      "<p class=\"canonical\">" + escapeHtml(combo.canonical) + "</p>" +
      "<p>" + escapeHtml(combo.english) + "</p>" +
      "<p>Joined with <strong>AND</strong> · " + escapeHtml(combo.ids.join(" + ")) + "</p>";
  }

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function isExpanded(nodeId) {
    if (state.expanded[nodeId] === false) return false;
    return true;
  }

  function renderTreeNode(node) {
    var li = document.createElement("li");
    li.className = "tree-node";

    var row = document.createElement("button");
    row.type = "button";
    row.className = "tree-row";
    if (node.combination) {
      row.setAttribute("data-combo", node.combination.id);
      if (comboMatches(node.combination)) row.classList.add("match");
      else row.classList.add("miss");
      if (state.selectedId === node.combination.id) row.classList.add("selected");
    }

    var twist = document.createElement("span");
    twist.className = "twist";
    twist.textContent = node.children.length ? (isExpanded(node.id) ? "▾" : "▸") : "·";

    var pills = document.createElement("span");
    pills.className = "pills";
    if (node.rule) {
      var pill = document.createElement("span");
      pill.className = "pill";
      pill.textContent = node.rule.id;
      pills.appendChild(pill);
    }

    var preview = document.createElement("span");
    preview.className = "preview";
    if (node.combination) {
      preview.textContent = node.combination.canonical;
    } else if (node.rule) {
      preview.textContent = node.rule.canonical;
    } else {
      preview.textContent = node.label;
    }

    var tag = document.createElement("span");
    tag.className = "combo-tag";
    tag.textContent = node.combination ? node.combination.id : "";

    row.appendChild(twist);
    row.appendChild(pills);
    row.appendChild(preview);
    row.appendChild(tag);

    row.addEventListener("click", function (event) {
      event.stopPropagation();
      if (node.children.length && (event.target === twist || !node.combination)) {
        state.expanded[node.id] = !isExpanded(node.id);
        renderResults();
        return;
      }
      if (node.combination) selectCombo(node.combination.id);
      else if (node.children.length) {
        state.expanded[node.id] = !isExpanded(node.id);
        renderResults();
      }
    });

    li.appendChild(row);

    if (node.children.length && isExpanded(node.id)) {
      var ul = document.createElement("ul");
      node.children.forEach(function (child) {
        ul.appendChild(renderTreeNode(child));
      });
      li.appendChild(ul);
    }

    return li;
  }

  function renderTree() {
    var mount = $("treeView");
    mount.innerHTML = "";
    if (!state.tree) return;
    var ul = document.createElement("ul");
    ul.className = "tree";
    state.tree.children.forEach(function (child) {
      ul.appendChild(renderTreeNode(child));
    });
    if (!state.tree.children.length) {
      mount.innerHTML = "<div class=\"empty\"><h3>Nothing to show</h3><p>Include at least one valid rule.</p></div>";
      return;
    }
    mount.appendChild(ul);
  }

  function renderList() {
    var mount = $("listView");
    mount.innerHTML = "";
    if (!state.result || !state.result.combinations.length) return;
    var table = document.createElement("table");
    table.className = "list";
    table.innerHTML = "<thead><tr><th>ID</th><th>Size</th><th>Rules</th><th>Combination</th><th>Sample</th></tr></thead>";
    var tbody = document.createElement("tbody");
    state.result.combinations.forEach(function (combo) {
      var tr = document.createElement("tr");
      tr.setAttribute("data-combo", combo.id);
      if (state.selectedId === combo.id) tr.classList.add("selected");
      if (comboMatches(combo)) tr.classList.add("match");
      tr.innerHTML =
        "<td>" + combo.id + "</td>" +
        "<td>" + combo.size + "</td>" +
        "<td>" + escapeHtml(combo.ids.join(" + ")) + "</td>" +
        "<td>" + escapeHtml(combo.canonical) + "</td>" +
        "<td>" + (comboMatches(combo) ? "match" : "no") + "</td>";
      tr.addEventListener("click", function () { selectCombo(combo.id); });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    mount.appendChild(table);
  }

  function renderResults() {
    var has = state.result && state.result.combinations.length;
    $("emptyState").classList.toggle("hidden", !!has);
    $("treeView").classList.toggle("hidden", state.view !== "tree" || !has);
    $("listView").classList.toggle("hidden", state.view !== "list" || !has);
    $("treeViewBtn").setAttribute("aria-pressed", String(state.view === "tree"));
    $("listViewBtn").setAttribute("aria-pressed", String(state.view === "list"));
    renderStats();
    if (has) {
      if (state.view === "tree") renderTree();
      else renderList();
      if (state.selectedId) {
        var still = state.result.combinations.some(function (c) { return c.id === state.selectedId; });
        if (still) selectCombo(state.selectedId);
        else renderDetail(null);
      }
    } else {
      renderDetail(null);
    }
    updateDerived();
  }

  function buildCombinations() {
    parseInput();
    var minK = parseInt($("minK").value, 10);
    var maxRaw = $("maxK").value;
    var maxK = maxRaw === "" ? null : parseInt(maxRaw, 10);
    var limit = parseInt($("limitN").value, 10);
    state.result = RuleEngine.generateCombinations(state.rules, {
      minK: minK,
      maxK: maxK,
      limit: Math.min(HARD_CAP, isNaN(limit) ? HARD_CAP : limit)
    });
    state.tree = RuleEngine.buildCombinationTree(state.result);
    if (!state.selectedId && state.result.combinations[0]) {
      state.selectedId = state.result.combinations[0].id;
    }
    renderResults();
    persist();
  }

  function setView(view) {
    state.view = view;
    renderResults();
  }

  function setExpandedAll(value) {
    function walk(node) {
      if (!node) return;
      state.expanded[node.id] = value;
      (node.children || []).forEach(walk);
    }
    walk(state.tree);
    renderResults();
  }

  function combinationsText() {
    if (!state.result) return "";
    return state.result.combinations.map(function (combo) {
      return combo.id + " [" + combo.ids.join(" AND ") + "]: " + combo.canonical;
    }).join("\n");
  }

  function copyText() {
    var text = combinationsText();
    if (!text) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text);
      return;
    }
    var area = document.createElement("textarea");
    area.value = text;
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    document.body.removeChild(area);
  }

  function exportJson() {
    var payload = {
      rules: state.rules.filter(function (r) { return r.ok; }).map(function (r) {
        return { id: r.id, name: r.name, raw: r.raw, canonical: r.canonical, lhs: r.lhs, op: r.op, rhs: r.rhs };
      }),
      settings: {
        minK: $("minK").value,
        maxK: $("maxK").value,
        limit: $("limitN").value
      },
      combinations: (state.result && state.result.combinations || []).map(function (c) {
        return { id: c.id, size: c.size, rules: c.ids, canonical: c.canonical, english: c.english };
      })
    };
    var blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = "rule-combinations.json";
    a.click();
    URL.revokeObjectURL(url);
  }

  function persist() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        text: $("rulesInput").value,
        minK: $("minK").value,
        maxK: $("maxK").value,
        limit: $("limitN").value
      }));
    } catch (err) {}
  }

  function restore() {
    try {
      var saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
      if (!saved) return false;
      $("rulesInput").value = saved.text || EXAMPLE;
      if (saved.minK) $("minK").value = saved.minK;
      if (saved.maxK != null) $("maxK").value = saved.maxK;
      if (saved.limit) $("limitN").value = saved.limit;
      return true;
    } catch (err) {
      return false;
    }
  }

  function setRulesText(text) {
    $("rulesInput").value = text;
    state.rules = [];
    buildCombinations();
  }

  function wireHomeLink() {
    var link = $("homeLink");
    if (typeof google !== "undefined" && google.script) {
      link.href = window.location.href.split("?")[0];
    } else if (location.pathname.indexOf("rule-builder") === -1) {
      link.href = "index.html";
    }
  }

  function onReady() {
    els.input = $("rulesInput");
    wireHomeLink();
    if (!restore()) {
      $("rulesInput").value = EXAMPLE;
    }

    $("buildBtn").addEventListener("click", buildCombinations);
    $("exampleBtn").addEventListener("click", function () { setRulesText(EXAMPLE); });
    $("presetTrendBtn").addEventListener("click", function () { setRulesText(PRESET_TREND); });
    $("presetWickBtn").addEventListener("click", function () { setRulesText(PRESET_WICK); });
    $("clearBtn").addEventListener("click", function () { setRulesText(""); });
    $("treeViewBtn").addEventListener("click", function () { setView("tree"); });
    $("listViewBtn").addEventListener("click", function () { setView("list"); });
    $("expandBtn").addEventListener("click", function () { setExpandedAll(true); });
    $("collapseBtn").addEventListener("click", function () { setExpandedAll(false); });
    $("copyBtn").addEventListener("click", copyText);
    $("exportBtn").addEventListener("click", exportJson);
    $("helpBtn").addEventListener("click", function () { $("helpDialog").showModal(); });
    $("closeHelpBtn").addEventListener("click", function () { $("helpDialog").close(); });

    var liveBuildFromText = debounce(buildCombinations, 400);
    $("rulesInput").addEventListener("input", liveBuildFromText);

    var liveBuild = debounce(buildCombinations, 250);
    ["minK", "maxK", "limitN"].forEach(function (id) {
      $(id).addEventListener("change", buildCombinations);
      $(id).addEventListener("input", liveBuild);
    });

    ["curOpen", "curHigh", "curLow", "curClose", "prevOpen", "prevHigh", "prevLow", "prevClose"].forEach(function (id) {
      $(id).addEventListener("input", renderResults);
    });

    document.addEventListener("keydown", function (event) {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        event.preventDefault();
        buildCombinations();
      }
    });

    buildCombinations();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", onReady);
  } else {
    onReady();
  }
})();
