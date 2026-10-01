/* Additive Status — search, filters, compare tool, label checker and quiz.
   No dependencies, no tracking, no network requests except this site's own search index. */
(function () {
  "use strict";
  const SITE = window.SITE || { base: "/", index: null, jur: [], st: {} };
  const JUR = SITE.jur || [];
  const ST = SITE.st || {};
  const ALLOWED = new Set(SITE.allowed || ["authorised", "phase_out"]);
  const NOTALLOWED = new Set(SITE.notAllowed || ["not_authorised", "prohibited", "delisted"]);
  const ICON = { ok: "✓", warn: "!", no: "✕", unknown: "?" };
  const MAX_Q = 100;
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  // animations live in motion.js; these helpers are no-ops when it is missing or motion is off
  const M = () => (window.Motion && window.Motion.on() ? window.Motion : null);
  const motionOn = () => !!M();

  // Per-visitor conveniences only; nothing is ever sent anywhere. Settings are kept in
  // localStorage only after the visitor chose "Remember my settings" (key privacy-choice).
  const KEYS = ["theme", "motion", "quiz-best", "scan-home"];
  const store = {
    choice() { try { return localStorage.getItem("privacy-choice"); } catch (e) { return null; } },
    get(k) { try { return localStorage.getItem(k) || sessionStorage.getItem(k); } catch (e) { return null; } },
    set(k, v, sessionOk) {
      try {
        if (localStorage.getItem("privacy-choice") === "yes") { localStorage.setItem(k, v); sessionStorage.removeItem(k); }
        else if (sessionOk) sessionStorage.setItem(k, v);
      } catch (e) { /* storage blocked: the site works without it */ }
    },
    decide(v) {
      try {
        localStorage.setItem("privacy-choice", v);
        if (v !== "yes") KEYS.forEach((k) => localStorage.removeItem(k));
      } catch (e) { /* ignore */ }
    },
    clearAll() { try { KEYS.concat("privacy-choice").forEach((k) => { localStorage.removeItem(k); sessionStorage.removeItem(k); }); } catch (e) { /* ignore */ } },
  };
  window.__store = store;

  // ------------------------------------------------------------ text helpers
  function norm(s) {
    return String(s || "").toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "")
      .replace(/&/g, " and ").replace(/fd\s*and\s*c/g, "fdc").replace(/\b(no|number|nr)\.?\s*(?=\d)/g, "")
      .replace(/[^a-z0-9]+/g, " ").trim();
  }
  function compact(s) { return norm(s).replace(/ /g, ""); }
  // "E 129", "e129", "129", "E-129", "INS 129" -> "e129"; "E 160a(ii)" -> "e160aii"
  function enumKey(s) {
    let c = compact(s);
    if (/^\d{3,4}[a-z]*$/.test(c)) c = "e" + c;
    if (/^ins\d/.test(c)) c = "e" + c.slice(3);
    return c;
  }
  function lev1(a, b) {
    if (a === b) return true;
    const la = a.length, lb = b.length;
    if (Math.abs(la - lb) > 1) return false;
    let i = 0, j = 0, edits = 0;
    while (i < la && j < lb) {
      if (a[i] === b[j]) { i++; j++; continue; }
      if (++edits > 1) return false;
      if (la > lb) i++; else if (lb > la) j++; else { i++; j++; }
    }
    return edits + (la - i) + (lb - j) <= 1;
  }
  function el(tag, attrs, text) {
    const e = document.createElement(tag);
    if (attrs) for (const k in attrs) if (attrs[k] != null) e.setAttribute(k, attrs[k]);
    if (text != null) e.textContent = text;
    return e;
  }
  function pageUrl(id) { return SITE.base + "additive/" + encodeURIComponent(id) + "/"; }
  function tone(s) { return (ST[s] || ST.unknown || ["", "", "unknown"])[2]; }
  function stLabel(s, short) { return (ST[s] || ST.unknown || ["No data", "No data"])[short ? 1 : 0]; }
  function isAllowed(s) { return ALLOWED.has(s); }
  function isNotAllowed(s) { return NOTALLOWED.has(s); }
  function joinWords(xs) { return xs.length <= 1 ? (xs[0] || "") : xs.slice(0, -1).join(", ") + " and " + xs[xs.length - 1]; }
  function shuffle(a) { for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; }
  function chip(s, short) {
    const t = tone(s);
    const c = el("span", { class: "st st-" + t, title: stLabel(s) });
    c.appendChild(el("i", { "aria-hidden": "true" }, ICON[t]));
    c.appendChild(document.createTextNode(stLabel(s, short)));
    return c;
  }
  function dots(statuses) {
    const wrap = el("span", { class: "dots", role: "img",
      "aria-label": statuses.map((s, k) => JUR[k].c + ": " + stLabel(s, true)).join("; ") });
    statuses.forEach((s, k) => {
      const t = tone(s);
      const d = el("span", { class: "d d-" + t, "aria-hidden": "true" });
      d.appendChild(el("b", null, JUR[k].c));
      d.appendChild(document.createTextNode(ICON[t]));
      wrap.appendChild(d);
    });
    return wrap;
  }
  function enumBadge(e) { return e ? el("span", { class: "enum" }, e) : null; }
  function summary(r) {
    const yes = [], no = [], other = [];
    r.s.forEach((s, k) => (isAllowed(s) ? yes : isNotAllowed(s) ? no : other).push(JUR[k].t));
    const parts = [];
    if (yes.length) parts.push("allowed in " + joinWords(yes));
    if (no.length) parts.push("not allowed in " + joinWords(no));
    if (other.length) parts.push("uncertain in " + joinWords(other));
    const s = (r.e ? r.n + " (" + r.e + ")" : r.n) + " is " + parts.join("; ") + ".";
    return s.charAt(0).toUpperCase() + s.slice(1);
  }

  // ------------------------------------------------------------ search index
  let indexPromise = null, INDEX = null;
  function loadIndex() {
    if (!indexPromise) {
      indexPromise = fetch(SITE.index, { credentials: "omit" }).then((r) => {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      }).then((rows) => {
        INDEX = rows.map((r) => {
          const nn = [r.n].concat(r.k || []).map(norm);
          return { r, ek: r.e ? enumKey(r.e) : "", codes: r.x || [], nn: norm(r.n), names: nn,
            padded: nn.map((x) => " " + x + " "), compacts: nn.map((x) => x.replace(/ /g, "")),
            words: nn.join(" ").split(" "), cas: (r.c || []).map(compact) };
        });
        return INDEX;
      }).catch((e) => { indexPromise = null; throw e; });
    }
    return indexPromise;
  }

  function score(item, q, qe, qc, qwords) {
    if (!q) return 0;
    if (item.ek && qe === item.ek) return 100;
    if (item.codes.indexOf(qe) >= 0) return 96;                             // INS code of a substance without an E-number
    if (/^e\d{3}/.test(qe) && item.codes.some((c) => c.indexOf(qe) === 0)) return 93;
    if (/^e\d{3}/.test(qe) && item.compacts.indexOf(qe) >= 0) return 94;
    if (item.ek && qe.length >= 4 && item.ek.indexOf(qe) === 0) return 92 - (item.ek.length - qe.length);
    if (item.cas.indexOf(qc) >= 0) return 95;
    if (q.length < 2 && !/^\d/.test(q)) return 0;
    if (item.nn === q) return 90;
    if (item.names.indexOf(q) >= 0) return 88;
    const phrase = " " + q + " ";
    if (item.padded[0].indexOf(" " + q) === 0 && item.padded[0].charAt(q.length + 1) === " ") return 82;
    if (item.padded.some((x) => x.indexOf(phrase) >= 0)) return 74;
    if (item.nn.indexOf(q) === 0) return 70;
    for (let i = 1; i < item.names.length; i++) if (item.names[i].indexOf(q) === 0) return 64;
    if (qwords.every((w) => item.words.some((x) => x.indexOf(w) === 0))) return 60;
    if (q.length >= 3 && item.names.some((n) => n.indexOf(q) >= 0)) return 45;
    const fuzzy = qwords.every((w) => w.length >= 4 && item.words.some((x) =>
      x.length >= 4 && lev1(w, x.slice(0, Math.max(w.length, Math.min(x.length, w.length + 1))))));
    return fuzzy ? 25 : 0;
  }
  function search(query, limit) {
    const q = norm(query).slice(0, MAX_Q);
    if (!q) return [];
    const qe = enumKey(query), qc = compact(query), qwords = q.split(" ").filter(Boolean);
    const out = [];
    for (const it of INDEX) {
      const s = score(it, q, qe, qc, qwords);
      if (s > 0) out.push({ s, it });
    }
    out.sort((a, b) => b.s - a.s || (a.it.r.e ? 0 : 1) - (b.it.r.e ? 0 : 1) ||
      (a.it.r.e && b.it.r.e ? a.it.r.e.localeCompare(b.it.r.e, "en", { numeric: true }) : 0) || a.it.r.n.localeCompare(b.it.r.n));
    return out.slice(0, limit || 8);
  }

  // ------------------------------------------------------------ search boxes
  function setupSearch(form) {
    const input = $("input[type=search]", form), list = $(".results", form);
    if (!input || !list) return;
    let active = -1, current = [], pending = 0;
    function close() { list.hidden = true; input.setAttribute("aria-expanded", "false"); active = -1; input.removeAttribute("aria-activedescendant"); }
    function render(results, query) {
      list.textContent = "";
      current = results; active = -1;
      if (!query.trim()) { close(); return; }
      if (!results.length) {
        list.appendChild(el("li", { class: "r-empty", role: "option", "aria-disabled": "true" },
          "No additive found for “" + query.slice(0, 60) + "”. Try an E-number or another name."));
      }
      results.forEach((res, i) => {
        const r = res.it.r;
        const li = el("li", { role: "option", id: input.id + "-opt-" + i, "aria-selected": "false" });
        const a = el("a", { href: pageUrl(r.i), tabindex: "-1" });
        const name = el("span", { class: "r-name" });
        const line = el("span");
        if (r.e) { line.appendChild(enumBadge(r.e)); line.appendChild(document.createTextNode(" ")); }
        line.appendChild(document.createTextNode(r.n));
        name.appendChild(line);
        const alt = (r.k || []).slice(0, 3).join(" · ");
        if (alt) name.appendChild(el("small", null, alt));
        a.appendChild(name);
        const d = dots(r.s); d.classList.add("r-dots");
        a.appendChild(d);
        li.appendChild(a);
        list.appendChild(li);
      });
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
      if (M()) M().cascade(list, "li", { y: 10, d: 28, max: 8 });
    }
    function highlight(i) {
      const items = $$("li[role=option]:not([aria-disabled])", list);
      if (!items.length) return;
      active = (i + items.length) % items.length;
      items.forEach((li, k) => li.setAttribute("aria-selected", k === active ? "true" : "false"));
      input.setAttribute("aria-activedescendant", items[active].id);
      items[active].scrollIntoView({ block: "nearest" });
    }
    function update() {
      const query = input.value.slice(0, MAX_Q);
      const ticket = ++pending;
      loadIndex().then(() => { if (ticket === pending) render(search(query, 8), query); })
        .catch(() => {
          list.textContent = "";
          list.appendChild(el("li", { class: "r-empty" }, "Search is not available right now. Press Enter to use the full list."));
          list.hidden = false;
        });
    }
    input.addEventListener("focus", () => { loadIndex().catch(() => {}); if (input.value.trim()) update(); });
    input.addEventListener("input", update);
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); if (list.hidden) update(); else highlight(active + 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); highlight(active - 1); }
      else if (e.key === "Escape") { close(); }
    });
    form.addEventListener("submit", (e) => {
      const query = input.value.trim().slice(0, MAX_Q);
      if (!query) { e.preventDefault(); input.focus(); return; }
      if (active >= 0 && current[active]) { e.preventDefault(); location.href = pageUrl(current[active].it.r.i); return; }
      if (INDEX) {
        const res = search(query, 2);
        // go straight to the page when there is one clear best match
        if (res.length && res[0].s >= 70 && (res.length === 1 || res[1].s <= res[0].s - 10)) { e.preventDefault(); location.href = pageUrl(res[0].it.r.i); }
      }
    });
    document.addEventListener("click", (e) => { if (!form.contains(e.target)) close(); });
  }

  // ------------------------------------------------------------ small global helpers
  function setupTheme() {
    const btn = $("[data-theme-toggle]");
    if (!btn) return;
    btn.addEventListener("click", (e) => {
      const root = document.documentElement;
      const cur = root.getAttribute("data-theme") ||
        (window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      const next = cur === "dark" ? "light" : "dark";
      const apply = () => { root.setAttribute("data-theme", next); store.set("theme", next, true); };
      if (window.Motion) window.Motion.themeSwitch(e, apply); else apply();
    });
  }
  function setupMenu() {
    const btn = $("[data-menu]"), nav = $("#main-nav");
    if (!btn || !nav) return;
    btn.addEventListener("click", () => {
      const open = !nav.classList.contains("open");
      nav.classList.toggle("open", open);
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && nav.classList.contains("open")) { nav.classList.remove("open"); btn.setAttribute("aria-expanded", "false"); btn.focus(); } });
  }
  function setupShortcut() {
    document.addEventListener("keydown", (e) => {
      if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
      const t = e.target;
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
      const input = $("form[data-search] input");
      if (input) { e.preventDefault(); input.focus(); }
    });
  }
  function setupRandom() {
    $$("[data-random]").forEach((a) => a.addEventListener("click", (e) => {
      e.preventDefault();
      loadIndex().then((idx) => {
        const here = (document.querySelector("[data-additive]") || {}).dataset;
        let pick;
        do { pick = idx[Math.floor(Math.random() * idx.length)].r; } while (idx.length > 1 && here && pick.i === here.additive);
        location.href = pageUrl(pick.i);
      }).catch(() => { location.href = a.href; });
    }));
  }
  function setupTooltips() {
    const tip = $("#tip");
    if (!tip) return;
    let owner = null;
    function show(target, x, y) {
      owner = target;
      tip.textContent = target.getAttribute("data-tip");
      tip.hidden = false;
      const r = tip.getBoundingClientRect();
      let left = x - r.width / 2, top = y - r.height - 12;
      left = Math.max(8, Math.min(left, window.innerWidth - r.width - 8));
      if (top < 8) top = y + 18;
      tip.style.left = left + "px"; tip.style.top = top + "px";
    }
    function hide() { owner = null; tip.hidden = true; }
    document.addEventListener("pointermove", (e) => {
      const t = e.target.closest && e.target.closest("[data-tip]");
      if (t && e.pointerType !== "touch") show(t, e.clientX, e.clientY); else if (owner) hide();
    });
    document.addEventListener("focusin", (e) => {
      const t = e.target.closest && e.target.closest("[data-tip]");
      if (t) { const r = t.getBoundingClientRect(); show(t, r.left + r.width / 2, r.top); } else if (owner) hide();
    });
    document.addEventListener("focusout", hide);
    window.addEventListener("scroll", () => { if (owner) hide(); }, { passive: true });
  }
  function setupShare() {
    $$("[data-share]").forEach((btn) => {
      const canShare = !!navigator.share, canCopy = !!(navigator.clipboard && navigator.clipboard.writeText);
      if (!canShare && !canCopy) { btn.hidden = true; return; }
      btn.hidden = false;
      const label = $("[data-share-label]", btn);
      if (!canShare && label) label.textContent = "Copy link";
      btn.addEventListener("click", () => {
        const data = { title: document.title, url: location.href.split("#")[0] };
        if (canShare) { navigator.share(data).catch(() => {}); return; }
        navigator.clipboard.writeText(data.url).then(() => {
          if (label) { label.textContent = "Link copied!"; setTimeout(() => { label.textContent = "Copy link"; }, 2000); }
        });
      });
    });
  }
  function setupFilters() {
    $$("[data-table-filter]").forEach((input) => {
      const table = document.getElementById(input.getAttribute("data-table-filter"));
      if (!table) return;
      const rows = Array.from(table.tBodies[0].rows).map((tr) => ({ tr, t: norm(tr.textContent) }));
      input.addEventListener("input", () => {
        const q = norm(input.value);
        rows.forEach((r) => { r.tr.hidden = !!q && r.t.indexOf(q) < 0; });
      });
    });
    $$("[data-list-filter]").forEach((input) => {
      const list = document.getElementById(input.getAttribute("data-list-filter"));
      if (!list) return;
      const items = Array.from(list.children).map((d) => ({ d, t: norm(d.textContent) }));
      input.addEventListener("input", () => {
        const q = norm(input.value);
        items.forEach((i) => { i.d.hidden = !!q && i.t.indexOf(q) < 0; });
      });
    });
  }

  // ------------------------------------------------------------ "Did you know?"
  function setupFacts() {
    const box = $("[data-facts]");
    if (!box) return;
    const facts = $$(".fact", box);
    if (facts.length < 2) return;
    box.classList.add("js");
    const nav = $(".facts-nav", box), count = $(".facts-count", box);
    nav.hidden = false;
    let order = shuffle(facts.map((_, i) => i)), pos = 0;
    const firstDiff = order.findIndex((i) => facts[i].classList.contains("k-differs"));
    if (firstDiff > 0) order.unshift(order.splice(firstDiff, 1)[0]);
    function show(dir) {
      facts.forEach((f) => f.classList.remove("on"));
      const f = facts[order[pos]];
      f.classList.add("on");
      count.textContent = (pos + 1) + " / " + facts.length;
      if (dir && M()) M().cardIn(f, dir);
    }
    $("[data-fact-next]", box).addEventListener("click", () => { pos = (pos + 1) % facts.length; show(1); });
    $("[data-fact-prev]", box).addEventListener("click", () => { pos = (pos - 1 + facts.length) % facts.length; show(-1); });
    $("[data-fact-shuffle]", box).addEventListener("click", () => { order = shuffle(order); pos = 0; show(1); });
    show();
  }

  // ------------------------------------------------------------ hero "chromatography" lanes (decorative)
  function setupLab() {
    const lab = $("[data-lab]");
    if (!lab) return;
    let items;
    try { items = JSON.parse(lab.getAttribute("data-lab")); } catch (e) { return; }
    if (!items || items.length < 2) return;
    const lanes = $$(".lane", lab), dotsEl = $$(".lab-dots i", lab), pauseBtn = $("[data-lab-pause]");
    let k = 0, hover = false, paused = false;
    function show(i) {
      k = (i + items.length) % items.length;
      const it = items[k];
      lab.style.setProperty("--dye", it.dye);
      $(".lab-e", lab).textContent = it.e || "No E-number";
      $(".lab-name", lab).textContent = it.n;
      lanes.forEach((lane, j) => {
        lane.style.setProperty("--y", it.y[j]);
        const st = $(".lane-st", lane);
        st.className = "lane-st t-" + it.t[j];
        $("i", st).textContent = ICON[it.t[j]];
        $("b", st).textContent = it.l[j];
      });
      dotsEl.forEach((d, j) => d.classList.toggle("on", j === k));
      if (M()) M().anim($(".lab-top", lab), [{ opacity: 0, translate: "0 12px" }, { opacity: 1, translate: "0 0" }], { duration: 520 });
    }
    lab.addEventListener("pointerenter", () => { hover = true; });
    lab.addEventListener("pointerleave", () => { hover = false; });
    dotsEl.forEach((d, j) => d.addEventListener("click", () => show(j)));
    if (pauseBtn) pauseBtn.addEventListener("click", () => {
      paused = !paused;
      pauseBtn.setAttribute("aria-pressed", paused ? "true" : "false");
      pauseBtn.textContent = paused ? "Play" : "Pause";
    });
    setInterval(() => {
      if (paused || hover || document.hidden || !motionOn()) return;
      show(k + 1);
    }, 4200);
  }

  // ------------------------------------------------------------ privacy choices (no cookies)
  function setupConsent() {
    const box = $("#consent");
    const status = $("[data-storage-status]");
    function describe() {
      if (!status) return;
      const c = store.choice();
      status.textContent = c === "yes" ? "You chose to remember your settings on this device."
        : c === "no" ? "You chose not to remember settings. Only that choice is kept."
        : "You have not chosen yet. Nothing is remembered until you do.";
    }
    let opener = null;
    function show(e) { if (box) { opener = e && e.currentTarget; box.hidden = false; const b = $("button", box); if (b) b.focus({ preventScroll: true }); } }
    $$("[data-consent]").forEach((b) => b.addEventListener("click", () => {
      store.decide(b.getAttribute("data-consent"));
      if (b.getAttribute("data-consent") === "yes") {
        const t = document.documentElement.getAttribute("data-theme");
        if (t) store.set("theme", t);
      }
      const inBox = box && box.contains(b);
      if (box) box.hidden = true;
      if (inBox && opener) { opener.focus({ preventScroll: true }); opener = null; }
      describe();
    }));
    $$("[data-privacy-choices]").forEach((b) => b.addEventListener("click", show));
    const clear = $("[data-storage-clear]");
    if (clear) clear.addEventListener("click", () => {
      store.clearAll();
      describe();
      if (status) status.textContent = "Deleted. Nothing from this site is stored on this device now.";
    });
    describe();
  }

  // ------------------------------------------------------------ additive page tabs
  function setupTabs() {
    const box = $("[data-tabs]");
    if (!box) return;
    const panels = $$("[data-tab]", box);
    if (panels.length < 2) return;
    const bar = el("div", { class: "tabbar" });
    const list = el("div", { class: "tablist", role: "tablist", "aria-label": "Place" });
    bar.appendChild(list);
    const tabs = panels.map((p) => {
      const j = p.getAttribute("data-tab");
      const b = el("button", { type: "button", class: "tab", role: "tab", id: "tab-" + j, "aria-controls": p.id, "aria-selected": "false", tabindex: "-1" });
      b.appendChild(el("span", { class: "tdot tdot-" + p.getAttribute("data-tab-tone"), "aria-hidden": "true" }));
      b.appendChild(document.createTextNode(p.getAttribute("data-tab-label")));
      p.setAttribute("role", "tabpanel");
      p.setAttribute("aria-labelledby", "tab-" + j);
      list.appendChild(b);
      return b;
    });
    const all = el("button", { type: "button", class: "tab tab-all", "aria-pressed": "false" }, "Show all");
    bar.appendChild(all);
    const ink = el("span", { class: "tab-ink", "aria-hidden": "true" });
    bar.appendChild(ink);
    bar.classList.add("has-ink");
    panels[0].parentNode.insertBefore(bar, panels[0]);
    let cur = -1;
    function moveInk(t) {
      if (!t) { ink.style.opacity = "0"; return; }
      ink.style.opacity = "1";
      ink.style.width = t.offsetWidth + "px"; ink.style.height = t.offsetHeight + "px";
      ink.style.transform = "translate(" + t.offsetLeft + "px," + t.offsetTop + "px)";
    }
    function select(i, focus) {
      const dir = cur < 0 ? 0 : i > cur ? 1 : -1;
      tabs.forEach((t, k) => { t.setAttribute("aria-selected", k === i ? "true" : "false"); t.tabIndex = k === i ? 0 : -1; });
      panels.forEach((p, k) => { p.hidden = k !== i; });
      all.setAttribute("aria-pressed", "false");
      moveInk(tabs[i]);
      if (dir && M()) M().anim(panels[i], [{ opacity: 0, translate: (dir * 40) + "px 0" }, { opacity: 1, translate: "0 0" }], { duration: 480 });
      cur = i;
      if (focus) tabs[i].focus();
    }
    addEventListener("resize", () => { if (all.getAttribute("aria-pressed") !== "true" && cur >= 0) moveInk(tabs[cur]); }, { passive: true });
    tabs.forEach((t, i) => {
      t.addEventListener("click", () => select(i));
      t.addEventListener("keydown", (e) => {
        if (e.key === "ArrowRight") { e.preventDefault(); select((i + 1) % tabs.length, true); }
        if (e.key === "ArrowLeft") { e.preventDefault(); select((i - 1 + tabs.length) % tabs.length, true); }
      });
    });
    all.addEventListener("click", () => {
      panels.forEach((p) => { p.hidden = false; });
      tabs.forEach((t) => t.setAttribute("aria-selected", "false"));
      all.setAttribute("aria-pressed", "true");
      moveInk(null);
      if (M()) M().cascade(box, ".jur-panel", { y: 30, d: 70 });
    });
    function fromHash() {
      const m = /^#jur-(\w+)$/.exec(location.hash);
      const i = m ? panels.findIndex((p) => p.getAttribute("data-tab") === m[1]) : -1;
      return i;
    }
    const start = fromHash();
    select(start >= 0 ? start : 0);
    $$("[data-tab-link]").forEach((a) => a.addEventListener("click", (e) => {
      const i = panels.findIndex((p) => p.getAttribute("data-tab") === a.getAttribute("data-tab-link"));
      if (i < 0) return;
      e.preventDefault();
      select(i);
      history.replaceState(null, "", "#" + panels[i].id);
      box.scrollIntoView({ behavior: motionOn() ? "smooth" : "auto", block: "start" });
    }));
  }

  // ------------------------------------------------------------ list page
  function setupList() {
    const table = document.getElementById("all"), form = document.getElementById("filters");
    if (!table || !form) return;
    const tbody = table.tBodies[0];
    const q = $("#fq"), cls = $("#f-class"), sort = $("#f-sort"), diff = $("#f-diff");
    const count = $("#f-count"), empty = $("#f-empty");
    const allowBtns = $$("[data-allow]", form), denyBtns = $$("[data-deny]", form);
    const keys = JUR.map((j) => j.k);
    const rows = Array.from(tbody.rows).map((tr) => {
      const s = $$("td[data-s]", tr).map((td) => td.getAttribute("data-s"));
      return { tr, q: norm(tr.getAttribute("data-q")), qc: compact(tr.getAttribute("data-q")),
        c: (tr.getAttribute("data-c") || "").split("|"), d: tr.getAttribute("data-d") === "1",
        o: +tr.getAttribute("data-o"), name: norm($(".nm", tr).textContent), s,
        yes: s.filter(isAllowed).length, no: s.filter(isNotAllowed).length };
    });
    let lastSort = "e";   // rows are rendered in E-number order
    function reorder(by) {
      lastSort = by;
      const sorted = rows.slice().sort((a, b) =>
        by === "name" ? a.name.localeCompare(b.name) :
        by === "allowed" ? (b.yes - a.yes) || (a.o - b.o) :
        by === "restricted" ? (b.no - a.no) || (a.o - b.o) : a.o - b.o);
      const frag = document.createDocumentFragment();
      sorted.forEach((r) => frag.appendChild(r.tr));
      tbody.appendChild(frag);
    }
    const pressed = (b) => b.getAttribute("aria-pressed") === "true";
    const press = (b, v) => b.setAttribute("aria-pressed", v ? "true" : "false");
    function apply(push) {
      const query = norm(q.value.slice(0, MAX_Q)), qe = enumKey(q.value), qc = compact(q.value);
      const words = query ? query.split(" ") : [];
      const allow = allowBtns.filter(pressed).map((b) => keys.indexOf(b.getAttribute("data-allow")));
      const deny = denyBtns.filter(pressed).map((b) => keys.indexOf(b.getAttribute("data-deny")));
      let shown = 0;
      rows.forEach((r) => {
        let ok = true;
        if (words.length) ok = r.qc.indexOf(qc) >= 0 || (qe.length > 1 && r.qc.indexOf(qe) >= 0) || words.every((w) => r.q.indexOf(w) >= 0);
        if (ok && cls.value) ok = r.c.indexOf(cls.value) >= 0;
        if (ok && pressed(diff)) ok = r.d;
        if (ok) ok = allow.every((k) => isAllowed(r.s[k])) && deny.every((k) => isNotAllowed(r.s[k]));
        r.tr.hidden = !ok;
        if (ok) shown++;
      });
      const by = sort.value;
      if (by !== lastSort) { if (M()) M().flipRows(tbody, () => reorder(by)); else reorder(by); }
      const label = shown === rows.length ? rows.length + " additives" : shown + " of " + rows.length + " additives";
      if (count.textContent !== label) { count.textContent = label; if (push !== false && M()) M().bump(count); }
      empty.hidden = shown !== 0;
      table.parentNode.hidden = shown === 0;
      if (push !== false && history.replaceState) {
        const p = new URLSearchParams();
        if (q.value.trim()) p.set("q", q.value.trim().slice(0, MAX_Q));
        const al = allowBtns.filter(pressed).map((b) => b.getAttribute("data-allow"));
        const de = denyBtns.filter(pressed).map((b) => b.getAttribute("data-deny"));
        if (al.length) p.set("allow", al.join(","));
        if (de.length) p.set("deny", de.join(","));
        if (cls.value) p.set("type", cls.value);
        if (pressed(diff)) p.set("differ", "1");
        if (sort.value !== "e") p.set("sort", sort.value);
        const qs = p.toString();
        history.replaceState(null, "", location.pathname + (qs ? "?" + qs : ""));
      }
    }
    // restore state from the URL (also accepts the older ?eu=allowed style)
    const params = new URLSearchParams(location.search);
    if (params.get("q")) q.value = params.get("q").slice(0, MAX_Q);
    const allowSet = new Set((params.get("allow") || "").split(",")), denySet = new Set((params.get("deny") || "").split(","));
    keys.forEach((k) => { if (params.get(k) === "allowed") allowSet.add(k); if (params.get(k) === "notallowed") denySet.add(k); });
    allowBtns.forEach((b) => press(b, allowSet.has(b.getAttribute("data-allow"))));
    denyBtns.forEach((b) => press(b, denySet.has(b.getAttribute("data-deny"))));
    const t = params.get("type");
    if (t && Array.from(cls.options).some((o) => o.value === t)) cls.value = t;
    const so = params.get("sort");
    if (so && Array.from(sort.options).some((o) => o.value === so)) sort.value = so;
    if (params.get("differ") === "1") press(diff, true);
    function applyAnimated() {
      const before = new Set(rows.filter((r) => !r.tr.hidden).map((r) => r.tr));
      apply();
      if (!M()) return;
      let n = 0;
      rows.forEach((r) => {
        if (r.tr.hidden || before.has(r.tr) || n > 18) return;
        const b = r.tr.getBoundingClientRect();
        if (b.top > innerHeight || b.bottom < 0) return;
        r.tr.animate([{ opacity: 0, translate: "0 18px" }, { opacity: 1, translate: "0 0" }], { duration: 480, delay: n * 28, easing: "cubic-bezier(.2,.8,.2,1)", fill: "backwards" });
        n++;
      });
    }
    q.addEventListener("input", applyAnimated);
    form.addEventListener("change", (e) => { if (e.target === sort) apply(); else applyAnimated(); });
    [diff].concat(allowBtns, denyBtns).forEach((b) => b.addEventListener("click", () => {
      press(b, !pressed(b));
      // a place cannot be both "allowed in" and "not allowed in"
      const k = b.getAttribute("data-allow") || b.getAttribute("data-deny");
      if (k && pressed(b)) {
        const other = b.hasAttribute("data-allow") ? denyBtns : allowBtns;
        other.forEach((o) => { if ((o.getAttribute("data-allow") || o.getAttribute("data-deny")) === k) press(o, false); });
      }
      applyAnimated();
    }));
    form.addEventListener("reset", () => setTimeout(() => {
      [diff].concat(allowBtns, denyBtns).forEach((b) => press(b, false));
      applyAnimated();
    }, 0));
    apply(false);
  }

  // ------------------------------------------------------------ compare tool
  function setupCompare() {
    const box = $("[data-compare]");
    if (!box) return;
    const selA = $("#cmp-a", box), selB = $("#cmp-b", box), out = $(".cmp-out", box);
    const summaryEl = $(".cmp-summary", box), cols = $(".cmp-cols", box);
    const keys = JUR.map((j) => j.k);
    const params = new URLSearchParams(location.search);
    if (keys.indexOf(params.get("a")) >= 0) selA.value = params.get("a");
    if (keys.indexOf(params.get("b")) >= 0) selB.value = params.get("b");
    function column(title, items, cls) {
      const c = el("div", { class: "cmp-col " + (cls || "") });
      const h = el("h3");
      h.appendChild(el("span", null, title));
      h.appendChild(el("span", { class: "n" }, String(items.length)));
      c.appendChild(h);
      const ul = el("ul");
      c.__n = $(".n", h); c.__count = items.length;
      const LIMIT = 40;
      function fill(n) {
        ul.textContent = "";
        items.slice(0, n).forEach((r) => {
          const li = el("li"), a = el("a", { href: pageUrl(r.i) });
          if (r.e) { a.appendChild(enumBadge(r.e)); a.appendChild(document.createTextNode(" ")); }
          a.appendChild(document.createTextNode(r.n));
          li.appendChild(a); ul.appendChild(li);
        });
      }
      fill(LIMIT);
      c.appendChild(ul);
      if (items.length > LIMIT) {
        const more = el("button", { type: "button", class: "btn btn-ghost btn-sm more" }, "Show all " + items.length);
        more.addEventListener("click", () => { fill(items.length); more.remove(); });
        c.appendChild(more);
      }
      if (!items.length) c.appendChild(el("p", { class: "muted small" }, "None."));
      return c;
    }
    function render() {
      loadIndex().then((idx) => {
        const a = keys.indexOf(selA.value), b = keys.indexOf(selB.value);
        const A = JUR[a], B = JUR[b];
        out.hidden = false;
        cols.textContent = ""; summaryEl.textContent = "";
        if (a === b) { summaryEl.textContent = "Pick two different places."; return; }
        const onlyA = [], onlyB = [], both = [];
        let unsure = 0;
        idx.forEach(({ r }) => {
          const sa = r.s[a], sb = r.s[b];
          if (isAllowed(sa) && isNotAllowed(sb)) onlyA.push(r);
          else if (isAllowed(sb) && isNotAllowed(sa)) onlyB.push(r);
          else if (isAllowed(sa) && isAllowed(sb)) both.push(r);
          else if (!(isNotAllowed(sa) && isNotAllowed(sb))) unsure++;
        });
        const p = el("p");
        p.appendChild(el("strong", null, String(both.length)));
        p.appendChild(document.createTextNode(" additives are allowed in both. "));
        p.appendChild(el("strong", null, String(onlyA.length)));
        p.appendChild(document.createTextNode(" are allowed only in " + A.t + ", and "));
        p.appendChild(el("strong", null, String(onlyB.length)));
        p.appendChild(document.createTextNode(" only in " + B.t + ". "));
        p.appendChild(el("span", { class: "muted" }, unsure + " cannot be compared because one side is “Not on the list” or has no data."));
        summaryEl.appendChild(p);
        cols.appendChild(column("Allowed in " + A.c + ", not in " + B.c, onlyA));
        cols.appendChild(column("Allowed in " + B.c + ", not in " + A.c, onlyB));
        cols.appendChild(column("Allowed in both", both));
        if (M()) {
          M().cascade(cols, ".cmp-col", { y: 30, d: 90 });
          $$(".cmp-col", cols).forEach((c) => { M().cascade(c, "li", { y: 12, d: 22, max: 14 }); if (c.__n) M().odometer(c.__n, c.__count, true); });
          $$("strong", summaryEl).forEach((s) => M().bump(s));
        }
        if (history.replaceState) history.replaceState(null, "", location.pathname + "?a=" + A.k + "&b=" + B.k);
      }).catch(() => {
        out.hidden = false;
        summaryEl.textContent = "The comparison could not be loaded. The ready-made lists below still work.";
      });
    }
    selA.addEventListener("change", render);
    selB.addEventListener("change", render);
    $("[data-swap]", box).addEventListener("click", () => { const t = selA.value; selA.value = selB.value; selB.value = t; if (M()) M().swap(selA, selB); render(); });
    render();
  }

  // ------------------------------------------------------------ label checker
  const SCAN_STOP = new Set(["water", "salt", "sugar", "sugars", "starch", "flour", "vinegar", "milk", "oil", "fat",
    "protein", "gum", "acid", "extract", "colour", "color", "colours", "colors", "flavour", "flavouring", "flavor",
    "flavoring", "yeast", "honey", "wax", "air", "gas", "sodium", "potassium", "calcium", "iron", "gold", "silver"]);
  const UNITS = "(?:g|mg|kg|µg|mcg|ml|cl|dl|l|%|kcal|kj|oz|lb|ppm)";
  function buildScanMaps(idx) {
    const codes = new Map(), names = new Map();
    idx.forEach(({ r }) => {
      if (r.e) codes.set(enumKey(r.e), r);
      (r.x || []).forEach((c) => { if (!codes.has(c)) codes.set(c, r); if (/[a-z]$/.test(c) && !codes.has(c.slice(0, -1))) codes.set(c.slice(0, -1), r); });
      (r.k || []).forEach((k) => { if (/^E\s?\d/.test(k)) { const kk = enumKey(k); if (!codes.has(kk)) codes.set(kk, r); } });
      [r.n].concat(r.k || []).forEach((n) => {
        const nn = norm(n);
        if (nn.length < 4 || /^\d/.test(nn) || /^e \d/.test(nn) || SCAN_STOP.has(nn)) return;
        // also the singular: "pectins" -> "pectin", "sodium citrates" -> "sodium citrate"
        const variants = [nn];
        if (/[^s]s$/.test(nn) && nn.length >= 6) variants.push(nn.slice(0, -1));
        variants.forEach((v) => {
          if (SCAN_STOP.has(v)) return;
          const list = names.get(v) || [];
          if (list.indexOf(r) < 0) list.push(r);
          names.set(v, list);
        });
      });
    });
    const byLength = Array.from(names.keys()).sort((a, b) => b.length - a.length);
    return { codes, names, byLength };
  }
  function findCode(codes, num, letter, roman) {
    const base = "e" + num + (letter || "").toLowerCase();
    return codes.get(base + (roman || "").toLowerCase()) || codes.get(base) || null;
  }
  function scanText(text, maps) {
    const found = new Map(), unknown = [], ambiguous = [];
    function add(r, snippet, pos) {
      const f = found.get(r.i);
      if (f) { if (f.snips.indexOf(snippet) < 0) f.snips.push(snippet); return; }
      found.set(r.i, { r, snips: [snippet], pos });
    }
    // 1. E-numbers: "E 102", "E-102", "e160a(ii)", "E 150 d"
    const eRe = /\b[Ee]\s?[-‐–]?\s?(\d{3,4})\s?([a-zA-Z])?(?![a-zA-Z])(?:\s?\(\s?(i{1,3}|iv|v|vi{1,3})\s?\))?/g;
    let m;
    const covered = [];
    while ((m = eRe.exec(text))) {
      const r = findCode(maps.codes, m[1], m[2], m[3]);
      covered.push([m.index, m.index + m[0].length]);
      if (r) add(r, m[0].trim(), m.index); else unknown.push(m[0].trim());
    }
    // 2. INS numbers: "INS 330", or bare numbers inside brackets as on Australian/NZ labels: "colour (102, 133)"
    const nRe = new RegExp("(^|[^\\w.,/])(?:INS\\s?)?(\\d{3,4})([a-z])?(?:\\s?\\((i{1,3}|iv|v|vi{1,3})\\))?(?![\\w/%]|[.,]\\d)(?!\\s?" + UNITS + "\\b)", "gi");
    while ((m = nRe.exec(text))) {
      const start = m.index + m[1].length;
      if (covered.some(([a, b]) => start >= a && start < b)) continue;
      const before = text.slice(0, start);
      const depth = (before.match(/\(/g) || []).length - (before.match(/\)/g) || []).length;
      const ins = /INS\s?$/i.test(before) || /^INS/i.test(m[0].slice(m[1].length));
      const num = +m[2];
      if (!(depth > 0 || ins) || num < 100 || num > 1599) continue;
      const r = findCode(maps.codes, m[2], m[3], m[4]);
      if (r) add(r, m[0].slice(m[1].length).trim(), start);
    }
    // 3. Names: longest first; a matched name is blanked so "citric acid" does not also count as "acid"
    let hay = " " + norm(text) + " ";
    for (const nn of maps.byLength) {
      const needle = " " + nn + " ";
      const at = hay.indexOf(needle);
      if (at < 0) continue;
      const list = maps.names.get(nn);
      if (list.length === 1) add(list[0], nn, at);
      else if (!list.some((r) => found.has(r.i))) ambiguous.push({ name: nn, rows: list });
      hay = hay.slice(0, at) + " " + "#".repeat(nn.length) + " " + hay.slice(at + needle.length);
    }
    const items = Array.from(found.values()).sort((a, b) => a.pos - b.pos);
    return { items, unknown: Array.from(new Set(unknown)), ambiguous };
  }
  function setupScan() {
    const box = $("[data-scan]");
    if (!box) return;
    const ta = $("#scan-text", box), home = $("#scan-home", box);
    const sum = $(".scan-summary", box), list = $(".scan-list", box), hint = $(".scan-hint", box);
    const keys = JUR.map((j) => j.k);
    const EXAMPLE = "Sugar, glucose syrup, water, gelling agent (pectin), acid (citric acid), acidity regulator (sodium citrates), " +
      "colours (E129, E133, titanium dioxide), thickener (1422), glazing agent (carnauba wax), flavouring.";
    const saved = store.get("scan-home");
    if (saved && keys.indexOf(saved) >= 0) home.value = saved;
    let maps = null, timer = 0;
    function run() {
      const text = ta.value.slice(0, 5000);
      if (!text.trim()) { sum.textContent = ""; list.textContent = ""; hint.hidden = true; return; }
      loadIndex().then((idx) => {
        maps = maps || buildScanMaps(idx);
        const res = scanText(text, maps);
        render(res);
      }).catch(() => { sum.textContent = "The checker could not load its data. Please try again later."; });
    }
    function render(res) {
      list.textContent = ""; sum.textContent = "";
      hint.hidden = false;
      const h = keys.indexOf(home.value);
      const items = res.items.slice();
      if (h >= 0) items.sort((a, b) => (isNotAllowed(b.r.s[h]) - isNotAllowed(a.r.s[h])) || (a.pos - b.pos));
      const p = el("p");
      if (!items.length) {
        p.appendChild(el("span", { class: "big" }, "No additives found. "));
        p.appendChild(document.createTextNode("Check the spelling, or type the E-numbers from the label."));
      } else {
        p.appendChild(el("span", { class: "big" }, items.length + (items.length === 1 ? " additive found. " : " additives found. ")));
        if (h >= 0) {
          const A = items.filter((x) => isAllowed(x.r.s[h])).length, N = items.filter((x) => isNotAllowed(x.r.s[h])).length;
          p.appendChild(document.createTextNode("In " + JUR[h].t + ": " + A + " allowed, " + N + " not allowed" +
            (items.length - A - N ? ", " + (items.length - A - N) + " uncertain." : ".")));
        } else {
          const every = items.filter((x) => x.r.s.every(isAllowed)).length;
          const some = items.filter((x) => x.r.s.some(isNotAllowed));
          p.appendChild(document.createTextNode(every + " allowed in all five places. "));
          if (some.length) p.appendChild(document.createTextNode(some.length + " not allowed in at least one: " +
            some.map((x) => x.r.e || x.r.n).join(", ") + "."));
        }
      }
      sum.appendChild(p);
      if (res.unknown.length) sum.appendChild(el("p", { class: "muted small" }, "Not in our data: " + res.unknown.join(", ") + "."));
      if (res.ambiguous.length) sum.appendChild(el("p", { class: "muted small" }, res.ambiguous.map((a) =>
        "“" + a.name + "” could be " + joinWords(a.rows.slice(0, 6).map((r) => r.e || r.n))).join("; ") + " — check the label for the E-number."));
      items.forEach((x, n) => {
        const r = x.r;
        const flag = h >= 0 && isNotAllowed(r.s[h]);
        const li = el("li", { class: "scan-item" + (flag ? " flag" : "") });
        li.style.animationDelay = motionOn() ? (n * 60) + "ms" : "0s";
        const name = el("div", { class: "si-name" });
        const a = el("a", { href: pageUrl(r.i) });
        if (r.e) a.appendChild(enumBadge(r.e));
        a.appendChild(el("span", { class: "qr-n" }, r.n));
        name.appendChild(a);
        name.appendChild(el("span", { class: "si-found" }, "Found as “" + x.snips.slice(0, 3).join("”, “") + "”" + (r.g && r.g[0] ? " · " + r.g[0] : "")));
        li.appendChild(name);
        li.appendChild(dots(r.s));
        if (h >= 0) {
          const line = el("div", { class: "si-home" });
          line.appendChild(document.createTextNode(JUR[h].s + ": "));
          line.appendChild(chip(r.s[h]));
          li.appendChild(line);
        }
        list.appendChild(li);
      });
    }
    ta.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(run, 250); });
    home.addEventListener("change", () => { store.set("scan-home", home.value); run(); });
    $("[data-scan-run]", box).addEventListener("click", run);
    const exBtn = $("[data-scan-example]", box);
    exBtn.addEventListener("click", () => {
      if (M()) { exBtn.disabled = true; M().typeInto(ta, EXAMPLE, () => { exBtn.disabled = false; run(); ta.focus(); }); }
      else { ta.value = EXAMPLE; run(); ta.focus(); }
    });
    $("[data-scan-clear]", box).addEventListener("click", () => { ta.value = ""; run(); ta.focus(); });
    if (ta.value.trim()) run();
  }

  // ------------------------------------------------------------ quiz
  function setupQuiz() {
    const box = $("[data-quiz]");
    if (!box) return;
    const ROUNDS = 10;
    const steps = { intro: $("[data-step=intro]", box), round: $("[data-step=round]", box), end: $("[data-step=end]", box) };
    const opts = $$(".quiz-opt", box);
    const q = (name) => $("[data-quiz-" + name + "]", box);
    const bestEl = q("best");
    let rounds = [], n = 0, total = 0, checked = false, mode = "mixed", log = [];
    let memBest = {}; // kept for this page visit when the visitor has not chosen to remember settings
    function best() { try { return Object.assign({}, memBest, JSON.parse(store.get("quiz-best") || "{}")); } catch (e) { return Object.assign({}, memBest); } }
    function showBest() {
      const b = best();
      const parts = [];
      if (b.mixed != null) parts.push("Mixed: " + b.mixed + "/50");
      if (b.hard != null) parts.push("Hard: " + b.hard + "/50");
      bestEl.hidden = !parts.length;
      bestEl.textContent = (store.choice() === "yes" ? "Your best on this device: " : "Your best this visit: ") + parts.join(", ");
    }
    function go(step) { Object.keys(steps).forEach((k) => { steps[k].hidden = k !== step; }); }
    function definite(r) { return r.s.every((s) => isAllowed(s) || isNotAllowed(s)); }
    function draw(idx) {
      const pool = idx.map((x) => x.r).filter(definite);
      const diff = shuffle(pool.filter((r) => r.d)), same = shuffle(pool.filter((r) => !r.d));
      let pick = mode === "hard" ? diff.slice(0, ROUNDS) : diff.slice(0, 6).concat(same.slice(0, 4));
      if (pick.length < ROUNDS) pick = pick.concat(shuffle(pool.filter((r) => pick.indexOf(r) < 0)).slice(0, ROUNDS - pick.length));
      return shuffle(pick);
    }
    function start() {
      mode = ($("input[name=quiz-mode]:checked", box) || {}).value || "mixed";
      loadIndex().then((idx) => {
        rounds = draw(idx); n = 0; total = 0; log = [];
        go("round"); round();
      }).catch(() => { bestEl.hidden = false; bestEl.textContent = "The quiz could not load its data. Please try again later."; });
    }
    function round() {
      const r = rounds[n];
      checked = false;
      q("n").textContent = String(n + 1);
      q("bar").style.width = (100 * n / ROUNDS) + "%";
      q("score").textContent = String(total);
      q("class").textContent = (r.g && r.g.length) ? r.g.join(" · ") : "Food additive";
      const e = q("e"); e.textContent = r.e || ""; e.hidden = !r.e;
      q("name").textContent = r.n;
      q("feedback").textContent = "";
      opts.forEach((o, k) => {
        o.setAttribute("aria-pressed", "false"); o.classList.remove("right", "wrong"); o.disabled = false;
        o.removeAttribute("aria-label");
        const mark = $(".qo-mark", o); mark.textContent = ""; mark.className = "qo-mark";
        $(".qo-name", o).textContent = JUR[k].s;
      });
      q("check").hidden = false; q("next").hidden = true;
      q("check").focus({ preventScroll: true });
      if (M()) { M().cardIn($(".quiz-card", box), 1); M().cascade($(".quiz-opts", box), ".quiz-opt", { y: 22, d: 60 }); }
    }
    function check() {
      if (checked) return;
      checked = true;
      const r = rounds[n];
      let pts = 0;
      opts.forEach((o, k) => {
        const guess = o.getAttribute("aria-pressed") === "true", truth = isAllowed(r.s[k]);
        const ok = guess === truth;
        if (ok) pts++;
        o.classList.add(ok ? "right" : "wrong");
        const mark = $(".qo-mark", o);
        mark.textContent = truth ? "✓" : "✕";
        mark.className = "qo-mark " + (truth ? "t-yes" : "t-no");
        $(".qo-name", o).textContent = truth ? "Allowed" : "Not allowed";
        o.setAttribute("aria-label", JUR[k].s + ": " + (truth ? "allowed" : "not allowed") + (ok ? ", you were right" : ", you were wrong"));
        o.disabled = true;
      });
      if (M()) M().flipSeq(opts, (o) => { if (o.classList.contains("wrong")) M().shake(o); else M().pop(o); });
      total += pts;
      log.push({ r, pts });
      q("score").textContent = String(total);
      if (M()) M().bump(q("score"));
      const fb = q("feedback");
      fb.textContent = "";
      const line = el("p", { class: "qf-line" }, pts + " / 5: " + (pts === 5 ? "perfect!" : pts >= 4 ? "so close." : pts >= 3 ? "not bad." : "a tricky one."));
      const detail = el("p", { class: "qf-detail" });
      detail.appendChild(document.createTextNode(summary(r) + " Green tiles are the places you got right. "));
      const more = el("a", { href: pageUrl(r.i), target: "_blank", rel: "noopener" }, "Why? See the sources");
      detail.appendChild(more);
      fb.appendChild(line); fb.appendChild(detail);
      if (pts === 5) confetti(14);
      q("check").hidden = true; q("next").hidden = false;
      q("next").textContent = n + 1 < ROUNDS ? "Next" : "See your score";
      q("next").focus({ preventScroll: true });
    }
    function next() {
      if (!checked) return;
      n++;
      if (n < ROUNDS) round(); else finish();
    }
    function finish() {
      q("bar").style.width = "100%";
      go("end");
      q("final").textContent = String(total);
      const b = best();
      const hadBest = b[mode] != null;
      const isBest = hadBest && total > b[mode];
      if (!hadBest || isBest) { b[mode] = total; memBest = Object.assign({}, b); store.set("quiz-best", JSON.stringify(b)); }
      const verdict = total >= 45 ? "Outstanding — you could write food law." : total >= 38 ? "Great score. You know your additives." :
        total >= 30 ? "Solid. The rules really do differ in surprising ways." : "The rules are tricky — that's why this site exists.";
      q("verdict").textContent = verdict + (isBest ? " New personal best!" : "");
      const ring = q("ring"), ringText = q("ring-text");
      if (ring) {
        const c = 389.6;
        ring.style.strokeDashoffset = String(c);
        requestAnimationFrame(() => requestAnimationFrame(() => { ring.style.strokeDashoffset = String(c * (1 - total / 50)); }));
      }
      if (ringText) ringText.textContent = String(total);
      if (M()) { M().odometer(q("final"), total, true); M().cascade(q("review"), ".qr", { y: 16, d: 50, max: 10 }); }
      const rev = q("review"); rev.textContent = "";
      log.forEach(({ r, pts }) => {
        const row = el("div", { class: "qr" });
        const a = el("a", { href: pageUrl(r.i) });
        if (r.e) a.appendChild(enumBadge(r.e));
        a.appendChild(el("span", { class: "qr-n" }, r.n));
        row.appendChild(a);
        row.appendChild(dots(r.s));
        row.appendChild(el("span", { class: "qr-pts" }, pts + "/5"));
        rev.appendChild(row);
      });
      if (total >= 45 || isBest) confetti(36);
      const share = q("share"), label = q("share-label");
      const canShare = !!navigator.share, canCopy = !!(navigator.clipboard && navigator.clipboard.writeText);
      share.hidden = !(canShare || canCopy);
      label.textContent = canShare ? "Share your score" : "Copy your score";
      share.onclick = () => {
        const text = "I scored " + total + "/50 on the “Where is it allowed?” food additive quiz.";
        const url = location.href.split("?")[0];
        if (canShare) navigator.share({ title: document.title, text, url }).catch(() => {});
        else navigator.clipboard.writeText(text + " " + url).then(() => { label.textContent = "Copied!"; });
      };
      showBest();
    }
    function confetti(k) {
      if (!motionOn()) return;
      const dyes = ["#ffd23f", "#ff8a3d", "#ff3d5e", "#a77bff", "#4d8bff", "#3fd39a"];
      for (let i = 0; i < k; i++) {
        const s = el("span", { class: "confetti", "aria-hidden": "true" });
        s.style.left = (Math.random() * 100) + "vw";
        s.style.background = dyes[i % dyes.length];
        s.style.setProperty("--dx", ((Math.random() - 0.5) * 30) + "vw");
        s.style.setProperty("--rot", (360 + Math.random() * 540) + "deg");
        s.style.animationDelay = (Math.random() * 0.35) + "s";
        document.body.appendChild(s);
        setTimeout(() => s.remove(), 2600);
      }
    }
    opts.forEach((o) => o.addEventListener("click", () => {
      if (checked) return;
      o.setAttribute("aria-pressed", o.getAttribute("aria-pressed") === "true" ? "false" : "true");
    }));
    q("start").addEventListener("click", start);
    q("check").addEventListener("click", check);
    q("next").addEventListener("click", next);
    q("again").addEventListener("click", () => { go("intro"); showBest(); q("start").focus(); });
    document.addEventListener("keydown", (e) => {
      if (steps.round.hidden || e.ctrlKey || e.metaKey || e.altKey) return;
      if (/^[1-5]$/.test(e.key) && !checked) { e.preventDefault(); opts[+e.key - 1].click(); }
      else if (e.key === "Enter" && !(e.target && e.target.tagName === "A")) {
        if (e.target && e.target.classList && e.target.classList.contains("quiz-opt")) { e.preventDefault(); }
        if (!checked) { e.preventDefault(); check(); } else { e.preventDefault(); next(); }
      }
    });
    showBest();
  }

  document.addEventListener("DOMContentLoaded", () => {
    $$("form[data-search]").forEach(setupSearch);
    setupTheme(); setupMenu(); setupShortcut(); setupRandom(); setupTooltips(); setupShare();
    setupFilters(); setupFacts(); setupLab(); setupConsent(); setupTabs(); setupList(); setupCompare(); setupScan(); setupQuiz();
  });
  // exported for tests
  window.__additive = { norm, enumKey, scanText, buildScanMaps };
})();
