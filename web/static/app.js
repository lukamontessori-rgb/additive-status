/* Additive Status — search and table filters. No dependencies, no tracking. */
(function () {
  "use strict";
  var SITE = window.SITE || { base: "/", index: null };
  var STATUS_SHORT = { authorised: "✓", phase_out: "!", not_authorised: "✕", prohibited: "✕", delisted: "✕", not_listed: "?", unknown: "–" };
  var STATUS_TONE = { authorised: "ok", phase_out: "warn", not_authorised: "no", prohibited: "no", delisted: "no", not_listed: "unknown", unknown: "unknown" };
  var JUR = ["EU", "UK", "US", "CA"];
  var MAX_Q = 100;

  function norm(s) {
    return String(s || "").toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "")
      .replace(/&/g, " and ").replace(/fd\s*and\s*c/g, "fdc").replace(/[^a-z0-9]+/g, " ").trim();
  }
  function compact(s) { return norm(s).replace(/ /g, ""); }
  // "E 129", "e129", "129", "E-129" -> "e129"; "E 160a(ii)" -> "e160aii"
  function enumKey(s) {
    var c = compact(s);
    if (/^\d{3,4}[a-z]*$/.test(c)) c = "e" + c;
    if (/^ins\d/.test(c)) c = "e" + c.slice(3);
    return c;
  }
  function lev1(a, b) { // true if edit distance <= 1
    if (a === b) return true;
    var la = a.length, lb = b.length;
    if (Math.abs(la - lb) > 1) return false;
    var i = 0, j = 0, edits = 0;
    while (i < la && j < lb) {
      if (a[i] === b[j]) { i++; j++; continue; }
      if (++edits > 1) return false;
      if (la > lb) i++; else if (lb > la) j++; else { i++; j++; }
    }
    return edits + (la - i) + (lb - j) <= 1;
  }

  var indexPromise = null, INDEX = null;
  function loadIndex() {
    if (!indexPromise) {
      indexPromise = fetch(SITE.index, { credentials: "omit" }).then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      }).then(function (rows) {
        INDEX = rows.map(function (r) {
          var names = [r.n].concat(r.k || []);
          return { r: r, ek: r.e ? enumKey(r.e) : "", nn: norm(r.n), names: names.map(norm),
                   words: names.map(norm).join(" ").split(" "), cas: (r.c || []).map(compact) };
        });
        return INDEX;
      }).catch(function (e) { indexPromise = null; throw e; });
    }
    return indexPromise;
  }

  function score(item, q, qe, qc, qwords) {
    if (!q) return 0;
    if (item.ek && (qe === item.ek)) return 100;
    if (item.ek && qe.length >= 4 && item.ek.indexOf(qe) === 0) return 92 - (item.ek.length - qe.length);
    if (item.cas.indexOf(qc) >= 0) return 95;
    if (item.nn === q) return 90;
    if (item.names.indexOf(q) >= 0) return 88;
    if (item.nn.indexOf(q) === 0) return 80;
    for (var i = 1; i < item.names.length; i++) if (item.names[i].indexOf(q) === 0) return 72;
    // every query word is a prefix of some name word
    var all = qwords.every(function (w) { return item.words.some(function (x) { return x.indexOf(w) === 0; }); });
    if (all) return 60;
    if (q.length >= 3 && item.names.some(function (n) { return n.indexOf(q) >= 0; })) return 45;
    var fuzzy = qwords.every(function (w) {
      return w.length >= 4 && item.words.some(function (x) { return x.length >= 4 && lev1(w, x.slice(0, Math.max(w.length, Math.min(x.length, w.length + 1)))); });
    });
    return fuzzy ? 25 : 0;
  }

  function search(query, limit) {
    var q = norm(query).slice(0, MAX_Q);
    if (!q) return [];
    var qe = enumKey(query), qc = compact(query), qwords = q.split(" ").filter(Boolean);
    var out = [];
    for (var i = 0; i < INDEX.length; i++) {
      var s = score(INDEX[i], q, qe, qc, qwords);
      if (s > 0) out.push({ s: s, it: INDEX[i] });
    }
    out.sort(function (a, b) { return b.s - a.s || (a.it.r.e || "~").localeCompare(b.it.r.e || "~") || a.it.r.n.localeCompare(b.it.r.n); });
    return out.slice(0, limit || 8);
  }

  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    if (attrs) for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (text != null) e.textContent = text;
    return e;
  }
  function pageUrl(id) { return SITE.base + "additive/" + encodeURIComponent(id) + "/"; }

  function setupSearch(form) {
    var input = form.querySelector("input[type=search]");
    var list = form.querySelector(".results");
    if (!input || !list) return;
    var active = -1, current = [];

    function close() { list.hidden = true; input.setAttribute("aria-expanded", "false"); active = -1; input.removeAttribute("aria-activedescendant"); }
    function render(results, query) {
      list.textContent = "";
      current = results;
      active = -1;
      if (!query.trim()) { close(); return; }
      if (!results.length) {
        var li = el("li", { "class": "r-empty", role: "option", "aria-disabled": "true" }, "No additive found for “" + query.slice(0, 60) + "”. Try an E-number or another name.");
        list.appendChild(li);
      }
      results.forEach(function (res, i) {
        var r = res.it.r;
        var li = el("li", { role: "option", id: input.id + "-opt-" + i, "aria-selected": "false" });
        var a = el("a", { href: pageUrl(r.i), tabindex: "-1" });
        var name = el("span", { "class": "r-name" });
        if (r.e) { name.appendChild(el("span", { "class": "enum" }, r.e + " ")); }
        name.appendChild(document.createTextNode(r.n));
        var alt = (r.k || []).slice(0, 3).join(" · ");
        if (alt) name.appendChild(el("small", null, alt));
        a.appendChild(name);
        var dots = el("span", { "class": "r-dots", "aria-label": r.s.map(function (s, k) { return JUR[k] + ": " + s.replace("_", " "); }).join(", ") });
        r.s.forEach(function (s, k) { dots.appendChild(el("span", { "class": "t-" + (STATUS_TONE[s] || "unknown"), "aria-hidden": "true" }, JUR[k] + " " + (STATUS_SHORT[s] || "–"))); });
        a.appendChild(dots);
        li.appendChild(a);
        list.appendChild(li);
      });
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
    }
    function highlight(i) {
      var items = list.querySelectorAll("li[role=option]:not([aria-disabled])");
      if (!items.length) return;
      active = (i + items.length) % items.length;
      items.forEach(function (li, k) { li.setAttribute("aria-selected", k === active ? "true" : "false"); });
      input.setAttribute("aria-activedescendant", items[active].id);
      items[active].scrollIntoView({ block: "nearest" });
    }
    var pending = 0;
    function update() {
      var query = input.value.slice(0, MAX_Q);
      var ticket = ++pending;
      loadIndex().then(function () {
        if (ticket !== pending) return;
        render(search(query, 8), query);
      }).catch(function () {
        list.textContent = "";
        list.appendChild(el("li", { "class": "r-empty" }, "Search is not available right now. Press Enter to use the full list."));
        list.hidden = false;
      });
    }
    input.addEventListener("focus", function () { loadIndex().catch(function () {}); });
    input.addEventListener("input", update);
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { e.preventDefault(); if (list.hidden) update(); else highlight(active + 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); highlight(active - 1); }
      else if (e.key === "Escape") { close(); }
    });
    form.addEventListener("submit", function (e) {
      var query = input.value.trim().slice(0, MAX_Q);
      if (!query) { e.preventDefault(); input.focus(); return; }
      if (active >= 0 && current[active]) { e.preventDefault(); location.href = pageUrl(current[active].it.r.i); return; }
      if (INDEX) {
        var res = search(query, 2);
        if (res.length && res[0].s >= 88 && (res.length === 1 || res[1].s < res[0].s)) { e.preventDefault(); location.href = pageUrl(res[0].it.r.i); }
      }
    });
    document.addEventListener("click", function (e) { if (!form.contains(e.target)) close(); });
  }

  // ------------------------------------------------------------ list page filters
  function setupFilters() {
    var table = document.getElementById("all");
    var form = document.getElementById("filters");
    if (!table || !form) return;
    var rows = Array.prototype.slice.call(table.tBodies[0].rows);
    var q = document.getElementById("fq"), cls = document.getElementById("f-class");
    var jsel = Array.prototype.slice.call(form.querySelectorAll("select[data-j]"));
    var diff = document.getElementById("f-diff"), count = document.getElementById("f-count");
    var empty = document.getElementById("f-empty");
    var ALLOWED = { authorised: 1, phase_out: 1 }, NOTALLOWED = { not_authorised: 1, prohibited: 1, delisted: 1 };
    var rowData = rows.map(function (tr) {
      return { tr: tr, q: norm(tr.getAttribute("data-q")), qc: compact(tr.getAttribute("data-q")),
               c: (tr.getAttribute("data-c") || "").split("|"), d: tr.getAttribute("data-d") === "1",
               s: Array.prototype.map.call(tr.querySelectorAll("td[data-s]"), function (td) { return td.getAttribute("data-s"); }) };
    });
    function matchStatus(v, s) {
      if (!v) return true;
      if (v === "allowed") return !!ALLOWED[s];
      if (v === "notallowed") return !!NOTALLOWED[s];
      return s === v;
    }
    function apply(push) {
      var query = norm(q.value.slice(0, MAX_Q)), qe = enumKey(q.value), qc = compact(q.value);
      var words = query ? query.split(" ") : [];
      var shown = 0;
      rowData.forEach(function (r) {
        var ok = true;
        if (words.length) {
          ok = r.qc.indexOf(qc) >= 0 || (qe.length > 1 && r.qc.indexOf(qe) >= 0) ||
               words.every(function (w) { return r.q.indexOf(w) >= 0; });
        }
        if (ok && cls.value) ok = r.c.indexOf(cls.value) >= 0;
        if (ok && diff.getAttribute("aria-pressed") === "true") ok = r.d;
        if (ok) for (var k = 0; k < jsel.length; k++) if (!matchStatus(jsel[k].value, r.s[+jsel[k].getAttribute("data-j")])) { ok = false; break; }
        r.tr.hidden = !ok;
        if (ok) shown++;
      });
      count.textContent = shown + " of " + rowData.length + " shown";
      empty.hidden = shown !== 0;
      table.parentNode.hidden = shown === 0;
      if (push !== false && history.replaceState) {
        var p = new URLSearchParams();
        if (q.value.trim()) p.set("q", q.value.trim().slice(0, MAX_Q));
        jsel.forEach(function (s) { if (s.value) p.set(s.id.replace("f-", ""), s.value); });
        if (cls.value) p.set("type", cls.value);
        if (diff.getAttribute("aria-pressed") === "true") p.set("differ", "1");
        var qs = p.toString();
        history.replaceState(null, "", location.pathname + (qs ? "?" + qs : ""));
      }
    }
    // restore state from URL
    var params = new URLSearchParams(location.search);
    if (params.get("q")) q.value = params.get("q").slice(0, MAX_Q);
    jsel.forEach(function (s) {
      var v = params.get(s.id.replace("f-", ""));
      if (v && Array.prototype.some.call(s.options, function (o) { return o.value === v; })) s.value = v;
    });
    var t = params.get("type");
    if (t && Array.prototype.some.call(cls.options, function (o) { return o.value === t; })) cls.value = t;
    if (params.get("differ") === "1") diff.setAttribute("aria-pressed", "true");
    q.addEventListener("input", apply);
    form.addEventListener("change", apply);
    diff.addEventListener("click", function () {
      diff.setAttribute("aria-pressed", diff.getAttribute("aria-pressed") === "true" ? "false" : "true");
      apply();
    });
    form.addEventListener("reset", function () { setTimeout(function () { diff.setAttribute("aria-pressed", "false"); apply(); }, 0); });
    apply(false);
  }

  document.addEventListener("DOMContentLoaded", function () {
    Array.prototype.forEach.call(document.querySelectorAll("form[data-search]"), setupSearch);
    setupFilters();
  });
})();
