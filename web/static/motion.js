/* Additive Status — motion.
   Scroll scenes, reveals, cursor effects and page transitions. Everything here is decoration:
   every page works and reads the same without it. No dependencies, no network requests.
   Motion runs only when the html element has class "mo" (set in <head> unless the visitor's
   system asks for reduced motion or the visitor pressed "Pause animations"). */
(function () {
  "use strict";
  window.__motion = true;
  const root = document.documentElement;
  const RM = window.matchMedia ? matchMedia("(prefers-reduced-motion: reduce)") : { matches: false, addEventListener() {} };
  const FINE = window.matchMedia ? matchMedia("(hover: hover) and (pointer: fine)") : { matches: false };
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
  const EASE = "cubic-bezier(.2,.8,.2,1)";
  const SPRING = "linear(0, .008 1.1%, .031 2.2%, .129 4.8%, .257 7.2%, .671 14.2%, .789 16.8%, .881 19.3%, .957 22.1%, 1.01 24.9%, " +
    "1.044 27.9%, 1.064 31.3%, 1.07 34.4%, 1.064 38.2%, 1.033 46.4%, 1.012 52.2%, .996 61.4%, .998 79.1%, 1)";
  const docTop = (el) => el.getBoundingClientRect().top + scrollY;
  // older browsers without linear() easing get a plain curve instead of an error
  const SPRING_OK = !!(window.CSS && CSS.supports && CSS.supports("transition-timing-function", SPRING));
  const springy = (e) => (e === SPRING && !SPRING_OK ? EASE : e);

  // ------------------------------------------------------------ on / off
  function storedOff() { try { return (localStorage.getItem("motion") || sessionStorage.getItem("motion")) === "off"; } catch (e) { return false; } }
  function on() { return root.classList.contains("mo"); }
  const listeners = [];
  function sync() {
    const was = on();
    root.classList.toggle("mo", !RM.matches && !storedOff());
    if (was !== on()) {
      if (!on()) finishAll();
      listeners.forEach((f) => f(on()));
      remeasure();
    }
    $$("[data-motion-toggle]").forEach((b) => {
      const paused = !on();
      b.setAttribute("aria-pressed", paused ? "true" : "false");
      const l = $(".nm-label", b); if (l) l.textContent = paused ? "Play animations" : "Pause animations";
      b.title = paused ? "Play animations" : "Pause animations";
      b.classList.toggle("paused", paused);
    });
  }
  function finishAll() {
    if (!document.getAnimations) return;
    document.getAnimations().forEach((a) => {
      try { const it = a.effect && a.effect.getTiming().iterations; if (it !== Infinity && !(a instanceof CSSAnimation)) a.finish(); } catch (e) { /* ignore */ }
    });
  }
  function anim(el, frames, opts) {
    if (!el || !el.animate || !on()) return null;
    const o = Object.assign({ duration: 700, easing: EASE, fill: "backwards" }, opts);
    o.easing = springy(o.easing);
    try { return el.animate(frames, o); } catch (e) { return null; }
  }

  // ------------------------------------------------------------ one loop for every scroll-linked effect
  const effects = new Set(), active = new Set();
  let raf = 0, lastY = scrollY, lastT = 0, vel = 0;
  const vio = "IntersectionObserver" in window ? new IntersectionObserver((es) => {
    es.forEach((e) => { const fx = e.target.__fx; if (!fx) return; if (e.isIntersecting) active.add(fx); else active.delete(fx); });
    kick();
  }, { rootMargin: "30% 0px 30% 0px" }) : null;
  function register(el, fx) {
    if (!el) return;
    fx.el = el; el.__fx = fx; effects.add(fx);
    if (fx.measure) fx.measure();
    if (vio) vio.observe(el); else active.add(fx);
  }
  function kick() { if (!raf) raf = requestAnimationFrame(tick); }
  function tick(t) {
    raf = 0;
    const dt = Math.min(64, t - (lastT || t - 16.7)); lastT = t;
    const y = scrollY, v = (y - lastY) / dt; lastY = y;
    vel += (v - vel) * 0.22;
    const st = { y, vh: innerHeight, vw: innerWidth, vel, dt, t };
    let again = Math.abs(vel) > 0.003;
    chrome(st);
    active.forEach((fx) => { try { if (fx.update(st)) again = true; } catch (e) { /* one effect failing must not stop the rest */ } });
    if (again) kick(); else { vel = 0; lastT = 0; }
  }
  function remeasure() { effects.forEach((fx) => { try { fx.measure && fx.measure(); } catch (e) { /* ignore */ } }); kick(); }
  let rto = 0, lastW = innerWidth;
  addEventListener("resize", () => {
    clearTimeout(rto);
    rto = setTimeout(() => { if (innerWidth !== lastW || !FINE.matches) { lastW = innerWidth; remeasure(); } else kick(); }, 150);
  }, { passive: true });
  addEventListener("scroll", kick, { passive: true });

  // progress bar, condensed header and back-to-top: always on (they are cheap and informative)
  let bar, toTop, maxScroll = 1, upward = false;
  function chrome(st) {
    maxScroll = Math.max(1, document.documentElement.scrollHeight - st.vh);
    const p = clamp(st.y / maxScroll, 0, 1);
    if (bar) bar.style.setProperty("--p", p.toFixed(4));
    document.body.classList.toggle("scrolled", st.y > 8);
    if (toTop) {
      // wide screens: it sits in the empty margin, so it can stay. Narrow screens: only while scrolling
      // back up or at the very end, so it never sits on top of text you are reading.
      const roomy = st.vw >= 1340;
      if (st.vel < -0.05) upward = true; else if (st.vel > 0.05) upward = false;
      const atEnd = st.y > maxScroll - 40;
      toTop.classList.toggle("show", st.y > st.vh * 0.9 && (roomy || upward || atEnd));
      toTop.style.setProperty("--p", p.toFixed(4));
    }
  }

  // ------------------------------------------------------------ reveals (entrances when things scroll into view)
  const STAGGER = [".card-grid", ".type-list", ".legal-cards", ".dl-grid", ".pair-grid", ".count-strip", ".who-grid",
    ".pipeline", ".check-list", ".gloss", ".toc", ".defs", ".lic", ".legend-strip", ".foot-grid", ".try", ".a-tags",
    ".prose > ul:not(.check-list)", ".prose > ol", ".sb-legend", ".heat-key", ".quiz-rules"];
  const SOLO = [".panel", ".note", ".cta-quiz", ".tbl-wrap:not(.heat-wrap)", ".overview", ".answer", "details.uses", ".jur-details",
    ".prose > p", ".prose > dl", ".prose > .tbl-wrap", ".cmp-tool", ".empty", ".statement", ".foot-legal", ".checked"];
  const rio = "IntersectionObserver" in window ? new IntersectionObserver((es) => es.forEach((e) => {
    if (!e.isIntersecting) return;
    rio.unobserve(e.target); reveal(e.target);
  }), { rootMargin: "0px 0px -6% 0px" }) : null;
  const inView = (el) => { const r = el.getBoundingClientRect(); return r.top < innerHeight && r.bottom > 0; };
  function watch(el, kind, visible) {
    if (!rio || !el || el.__rv) return;
    el.__rv = kind;
    if (kind !== "section" && (visible === undefined ? inView(el) : visible)) { el.__rv = null; return; }   // already on screen: never hide what was painted
    if (kind === "stagger") el.setAttribute("data-stagger", "");
    if (kind === "solo") el.setAttribute("data-solo", "");
    if (kind === "split") el.classList.add("split-h");
    rio.observe(el);
  }
  function reveal(el) {
    const kind = el.__rv;
    el.classList.add("in");
    if (!on()) return;
    if (kind === "section" || kind === "solo") {
      anim(el, [{ opacity: 0, translate: "0 48px", scale: kind === "solo" ? .97 : 1 }, { opacity: 1, translate: "0 0", scale: 1 }], { duration: 950 });
    } else if (kind === "stagger") {
      Array.from(el.children).forEach((c, i) => anim(c, [{ opacity: 0, translate: "0 30px", scale: .92 }, { opacity: 1, translate: "0 0", scale: 1 }],
        { duration: 800, delay: Math.min(i, 14) * 55, easing: SPRING }));
    }
    $$("[data-count]", el).forEach((n) => countTo(n, +n.getAttribute("data-count")));
    if (el.matches("[data-count]")) countTo(el, +el.getAttribute("data-count"));
  }
  // split a plain-text heading into words (real text kept in a visually hidden copy)
  function splitWords(el) {
    if (el.__split || el.children.length || !el.textContent.trim()) return false;
    const text = el.textContent.trim();
    const sr = document.createElement("span"); sr.className = "sr"; sr.textContent = text;
    const vis = document.createElement("span"); vis.className = "split"; vis.setAttribute("aria-hidden", "true");
    text.split(/\s+/).forEach((w, i) => {
      if (i) vis.appendChild(document.createTextNode(" "));
      const o = document.createElement("span"); o.className = "w";
      const n = document.createElement("span"); n.className = "wi"; n.textContent = w; n.style.setProperty("--i", i);
      o.appendChild(n); vis.appendChild(o);
    });
    el.textContent = ""; el.appendChild(sr); el.appendChild(vis); el.__split = true;
    return true;
  }
  function setupReveals() {
    $$(".reveal").forEach((el) => watch(el, "section"));
    // read every position in one pass, then change the DOM
    const stag = [], solo = [], heads = [];
    STAGGER.forEach((sel) => $$(sel).forEach((el) => stag.push(el)));
    SOLO.forEach((sel) => $$(sel).forEach((el) => solo.push(el)));
    $$("main h2:not(.sr):not(.story-h), .foot-mark").forEach((h) => heads.push(h));
    const vis = new Map();
    stag.concat(solo, heads).forEach((el) => vis.set(el, inView(el)));
    stag.forEach((el) => watch(el, "stagger", vis.get(el)));
    solo.forEach((el) => { if (!el.closest("[data-stagger], .reveal")) watch(el, "solo", vis.get(el)); });
    heads.forEach((h) => {
      if (vis.get(h)) return;
      if (h.classList.contains("foot-mark") || splitWords(h)) watch(h, "split", false);
    });
    $$(".sbars, .heat, .vcols, .timeline").forEach((el) => { if (rio) { el.__rv = "chart"; rio.observe(el); } else el.classList.add("in"); });
    $$("[data-count]").forEach((n) => { if (!n.closest(".reveal, [data-stagger], [data-solo]") && rio) { n.__rv = "count"; rio.observe(n); } });
  }

  // ------------------------------------------------------------ numbers that roll like an odometer
  function countTo(el, n) {
    if (!el || isNaN(n) || el.__counted) return;
    el.__counted = true;
    odometer(el, n, true);
  }
  function odometer(el, n, fromZero) {
    const text = String(Math.max(0, Math.round(n)));
    if (!on()) { setPlain(el, text); return; }
    let vis = $(":scope > .odo", el), sr = $(":scope > .sr", el);
    if (!vis || vis.children.length !== text.length) {
      el.textContent = "";
      sr = document.createElement("span"); sr.className = "sr";
      vis = document.createElement("span"); vis.className = "odo"; vis.setAttribute("aria-hidden", "true");
      for (let i = 0; i < text.length; i++) {
        const c = document.createElement("span"); c.className = "odo-c";
        const s = document.createElement("span"); s.className = "odo-s"; s.textContent = "0 1 2 3 4 5 6 7 8 9";
        s.style.setProperty("--d", fromZero ? 0 : text[i]);
        s.style.transitionDelay = (i * 90) + "ms";
        c.appendChild(s); vis.appendChild(c);
      }
      el.appendChild(sr); el.appendChild(vis);
      void vis.offsetWidth;
    }
    sr.textContent = text;
    Array.from(vis.children).forEach((c, i) => c.firstChild.style.setProperty("--d", text[i]));
  }
  function setPlain(el, text) { if (el.textContent !== text || el.children.length) el.textContent = text; }

  // ------------------------------------------------------------ home: hero
  function setupHero() {
    const hero = $("[data-hero]");
    if (!hero) return;
    const text = $(".hero-text", hero), art = $(".hero-art", hero), bg = $(".hero-bg", hero);
    let h = 1, wide = false;
    register(hero, {
      measure() { h = hero.offsetHeight || 1; wide = innerWidth >= 900; },
      update(st) {
        if (!on() || !wide) { [text, art, bg].forEach((e) => e && (e.style.transform = "", e.style.opacity = "")); return false; }
        const p = clamp(st.y / h, 0, 1);
        if (text) { text.style.transform = `translate3d(0, ${(st.y * 0.2).toFixed(1)}px, 0)`; text.style.opacity = String(clamp(1 - (p - 0.25) * 1.6, 0, 1)); }
        if (art) art.style.transform = `translate3d(0, ${(st.y * 0.08).toFixed(1)}px, 0) rotate(${(-p * 5).toFixed(2)}deg) scale(${(1 - p * 0.1).toFixed(3)})`;
        if (bg) bg.style.transform = `translate3d(0, ${(st.y * 0.32).toFixed(1)}px, 0)`;
        return false;
      },
    });
    if (FINE.matches) {
      let qx = 0, qy = 0, rq = 0;
      hero.addEventListener("pointermove", (e) => {
        if (!on()) return;
        const r = hero.getBoundingClientRect();
        qx = ((e.clientX - r.left) / r.width - 0.5) * 2; qy = ((e.clientY - r.top) / r.height - 0.5) * 2;
        if (!rq) rq = requestAnimationFrame(() => { rq = 0; hero.style.setProperty("--px", qx.toFixed(3)); hero.style.setProperty("--py", qy.toFixed(3)); });
      });
      hero.addEventListener("pointerleave", () => { hero.style.setProperty("--px", 0); hero.style.setProperty("--py", 0); });
    }
    // the search box cycles through example searches while it is empty and not focused
    const input = $("#q-home");
    if (input) {
      const ideas = ["Try E171, Red 40 or aspartame", "Try titanium dioxide", "Try E 129", "Try potassium bromate", "Try sucralose", "Try E 621"];
      let k = 0;
      setInterval(() => {
        if (!on() || document.hidden || document.activeElement === input || input.value) return;
        k = (k + 1) % ideas.length;
        input.classList.add("ph-swap");
        setTimeout(() => { input.placeholder = ideas[k]; input.classList.remove("ph-swap"); }, 260);
      }, 3200);
    }
  }

  // ------------------------------------------------------------ home: moving band (speed follows your scrolling)
  function setupBand() {
    const band = $(".band");
    if (!band) return;
    const btn = $("[data-band-pause]", band);
    const rows = $$("[data-marquee]", band).map((row) => {
      const track = $(".band-track", row), list = $(".band-list", track);
      const dir = +row.getAttribute("data-marquee") || -1;
      return { row, track, list, dir, x: 0, w: 1, s: 0, cloned: false };
    });
    function cloneOnce() {
      rows.forEach((r) => {
        if (r.cloned) return;
        r.cloned = true;
        for (let i = 0; i < 3; i++) { const c = r.list.cloneNode(true); c.setAttribute("aria-hidden", "true"); r.track.appendChild(c); }
      });
    }
    let paused = false, hover = false;
    band.addEventListener("pointerenter", () => { hover = true; });
    band.addEventListener("pointerleave", () => { hover = false; kick(); });
    if (btn) btn.addEventListener("click", () => {
      paused = !paused;
      btn.setAttribute("aria-pressed", paused ? "true" : "false");
      $("span", btn).textContent = paused ? "Play the band" : "Pause the band";
      band.classList.toggle("paused", paused);
      kick();
    });
    register(band, {
      measure() { if (rows[0].cloned) rows.forEach((r) => { r.w = r.list.offsetWidth || 1; if (r.dir > 0 && r.x === 0) r.x = -r.w; }); },
      update(st) {
        if (!on() || paused) { rows.forEach((r) => { r.row.style.transform = ""; }); return false; }
        if (!rows[0].cloned) { cloneOnce(); rows.forEach((r) => { r.w = r.list.offsetWidth || 1; if (r.dir > 0) r.x = -r.w; }); }
        const up = st.vel < -0.03;
        rows.forEach((r) => {
          const base = r.row.classList.contains("band-big") ? 0.05 : 0.065;
          const target = base * (hover ? 0.18 : 1) * (1 + Math.min(Math.abs(st.vel) * 3.2, 6));
          r.s += (target - r.s) * 0.08;
          r.x += r.s * st.dt * r.dir * (up ? -1 : 1);
          if (r.x <= -r.w) r.x += r.w;
          if (r.x > 0) r.x -= r.w;
          r.track.style.transform = `translate3d(${r.x.toFixed(2)}px,0,0)`;
          r.row.style.transform = `skewX(${clamp(-st.vel * 5, -9, 9).toFixed(2)}deg)`;
        });
        return true;
      },
    });
  }

  // ------------------------------------------------------------ home: the 405-dot scroll story
  function setupStory() {
    const sec = $("[data-story]");
    if (!sec || !$("#story-data") || innerHeight < 480) return;
    // the section is laid out (tall, pinned) at once so the page does not jump; the dots come later
    sec.classList.add("live");
    sec.style.setProperty("--n", $$(".story-text", sec).length);
    if (!("IntersectionObserver" in window)) { buildStory(sec); return; }
    const near = new IntersectionObserver((es) => {
      if (es.some((e) => e.isIntersecting)) { near.disconnect(); buildStory(sec); }
    }, { rootMargin: "120% 0px 120% 0px" });
    near.observe(sec);
  }
  function buildStory(sec) {
    const dataEl = $("#story-data");
    let data;
    try { data = JSON.parse(dataEl.textContent); } catch (e) { return; }
    const stage = $(".story-stage", sec), box = $(".story-dots", sec), viz = $(".story-viz", sec);
    const labels = $$(".story-labels > span", sec), texts = $$(".story-text", sec), nav = $$("[data-story-go]", sec);
    const rows = data.rows, steps = data.steps, N = steps.length;
    const frag = document.createDocumentFragment();
    const dots = rows.map((r, i) => {
      const d = document.createElement("i");
      d.dataset.i = i;
      d.style.setProperty("--dd", Math.round(Math.random() * 320) + "ms");
      d.style.setProperty("--hue", Math.round(250 + (i / rows.length) * 200));
      frag.appendChild(d);
      return d;
    });
    box.appendChild(frag);
    let step = -1, W = 0, H = 0, size = 8, gap = 2, cg = 16, secTop = 0, range = 1, stick = 80;
    const group = (r, s) => (s <= N - 2 ? r[3][s - 1] : r[3][5]);
    const toneCls = (r, s) => (s === 0 ? "t0" : s <= N - 2 ? "t-" + r[2][s - 1] : "s-" + r[3][5]);
    function fit() {
      W = box.clientWidth; H = box.clientHeight;
      cg = Math.max(12, Math.round(W * 0.05));
      viz.style.setProperty("--cg", cg + "px");
      const maxPile = Math.max(1, ...steps.map((p) => Math.max(0, ...p.map((x) => x[2]))));
      for (let s = 16; s >= 3; s--) {
        const g = s >= 10 ? 3 : s >= 6 ? 2 : 1;
        const perG = Math.floor((W + g) / (s + g));
        if (perG < 1 || Math.ceil(rows.length / perG) * (s + g) > H) continue;
        const cw = (W - cg * 2) / 3, per = Math.floor((cw + g) / (s + g));
        if (per < 1 || Math.ceil(maxPile / per) * (s + g) > H) continue;
        size = s; gap = g; return;
      }
      size = 3; gap = 1;
    }
    function layout(s) {
      const piles = steps[s], pos = new Array(rows.length), u = size + gap;
      if (!piles.length) {
        const per = Math.max(1, Math.floor((W + gap) / u)), nr = Math.ceil(rows.length / per);
        const ox = (W - (per * u - gap)) / 2, oy = (H - (nr * u - gap)) / 2;
        rows.forEach((r, i) => { pos[i] = [ox + (i % per) * u, oy + Math.floor(i / per) * u]; });
        return pos;
      }
      const cols = piles.length, cw = (W - cg * (cols - 1)) / cols, per = Math.max(1, Math.floor((cw + gap) / u));
      const pw = per * u - gap, at = {};
      piles.forEach((p, k) => { at[p[0]] = { k, n: 0 }; });
      rows.forEach((r, i) => {
        const c = at[group(r, s)] || at[piles[piles.length - 1][0]], n = c.n++;
        pos[i] = [c.k * (cw + cg) + (cw - pw) / 2 + (n % per) * u, H - size - Math.floor(n / per) * u];
      });
      return pos;
    }
    const pileCls = { a: "sw-a", n: "sw-n", u: "sw-u", d: "sw-d", r: "sw-r" };
    function apply(s, force) {
      if (s === step && !force) return;
      const first = step < 0;
      step = s;
      const pos = layout(s);
      dots.forEach((d, i) => {
        d.style.transform = `translate(${pos[i][0].toFixed(1)}px,${pos[i][1].toFixed(1)}px)`;
        d.className = toneCls(rows[i], s);
      });
      texts.forEach((t, k) => t.classList.toggle("on", k === s));
      nav.forEach((b, k) => { if (k === s) b.setAttribute("aria-current", "step"); else b.removeAttribute("aria-current"); });
      const piles = steps[s];
      viz.classList.toggle("grid-step", !piles.length);
      labels.forEach((l, k) => {
        const p = piles[k];
        if (!p) { l.hidden = true; return; }
        l.hidden = false;
        if (!l.__built) {
          l.innerHTML = '<b class="sl-n"></b><span class="sl-t"><i class="sl-sw"></i><span></span></span>';
          l.__built = true;
        }
        $(".sl-sw", l).className = "sl-sw " + pileCls[p[0]];
        $(".sl-t > span", l).textContent = p[1];
        const n = $(".sl-n", l);
        if (first || !on()) setPlain(n, String(p[2])); else odometer(n, p[2]);
        if (!first) anim(l, [{ opacity: 0.2, translate: "0 10px" }, { opacity: 1, translate: "0 0" }], { duration: 500, delay: k * 70 });
      });
      if (!first) { const t = texts[s]; anim(t, [{ opacity: 0, translate: "0 24px", filter: "blur(6px)" }, { opacity: 1, translate: "0 0", filter: "blur(0)" }], { duration: 650 }); }
    }
    function measure() {
      const head = $(".top");
      stick = (head ? head.offsetHeight : 70) + 10;
      sec.style.setProperty("--stick", stick + "px");
      secTop = docTop(sec);
      range = Math.max(1, sec.offsetHeight - stage.offsetHeight);
      fit();
      box.style.setProperty("--s", size + "px");
      apply(step < 0 ? 0 : step, true);
    }
    register(sec, {
      measure,
      update(st) {
        const p = clamp((st.y + stick - secTop) / range, 0, 0.9999);
        apply(Math.floor(p * N));
        stage.style.setProperty("--sp", p.toFixed(4));
        return false;
      },
    });
    nav.forEach((b, k) => b.addEventListener("click", () => {
      const y = secTop - stick + ((k + 0.5) / N) * range;
      scrollTo({ top: y, behavior: on() ? "smooth" : "auto" });
    }));
    // hover a dot to see which additive it is; click it to open the page
    const tip = document.createElement("div");
    tip.className = "story-tip"; tip.hidden = true;
    viz.appendChild(tip);
    const word = { o: "allowed", w: "allowed, phasing out", n: "not allowed", u: "unclear" };
    box.addEventListener("pointermove", (e) => {
      const d = e.target.closest("i");
      if (!d) { tip.hidden = true; return; }
      const r = rows[+d.dataset.i];
      const vr = viz.getBoundingClientRect(), dr = d.getBoundingClientRect();
      let extra = "";
      if (step > 0 && step <= N - 2) extra = " · " + word[r[2][step - 1]];
      tip.textContent = r[1] + extra;
      tip.hidden = false;
      const tw = tip.offsetWidth;
      tip.style.left = clamp(dr.left - vr.left + dr.width / 2 - tw / 2, 0, vr.width - tw) + "px";
      tip.style.top = (dr.top - vr.top - tip.offsetHeight - 8) + "px";
    });
    box.addEventListener("pointerleave", () => { tip.hidden = true; });
    box.addEventListener("click", (e) => {
      const d = e.target.closest("i");
      if (d && e.pointerType !== "touch") location.href = (window.SITE ? SITE.base : "/") + "additive/" + encodeURIComponent(rows[+d.dataset.i][0]) + "/";
    });
  }

  // ------------------------------------------------------------ home: words light up as you scroll
  function setupScrub() {
    $$("[data-scrub]").forEach((p) => {
      const words = p.textContent.trim().split(/\s+/);
      p.textContent = "";
      const spans = words.map((w, i) => {
        const s = document.createElement("span"); s.className = "scrub-w"; s.textContent = w;
        p.appendChild(s); if (i < words.length - 1) p.appendChild(document.createTextNode(" "));
        return s;
      });
      let lit = -1, t0 = 0, h = 1;
      register(p, {
        measure() { t0 = docTop(p); h = p.offsetHeight || 1; lit = -1; },
        update(st) {
          const start = t0 - st.vh * 0.8, end = t0 + h - st.vh * 0.4;
          const k = on() ? Math.round(clamp((st.y - start) / (end - start), 0, 1) * spans.length) : spans.length;
          if (k !== lit) { spans.forEach((s, i) => s.classList.toggle("lit", i < k)); lit = k; }
          return false;
        },
      });
    });
  }

  // ------------------------------------------------------------ home: sideways section, pinned while you scroll down
  function setupSideways() {
    const sec = $("[data-hs]");
    if (!sec) return;
    const pin = $(".hs-pin", sec), view = $(".hs-view", sec), track = $(".hs-track", sec), bar = $(".hs-progress span", sec);
    let pinned = false, secTop = 0, dist = 0, stick = 80;
    register(sec, {
      measure() {
        const want = on() && innerWidth >= 960 && innerHeight >= 620;
        sec.classList.toggle("pinned", want);
        track.style.transform = "";
        sec.style.height = "";
        pinned = want;
        if (!pinned) return;
        const head = $(".top");
        stick = (head ? head.offsetHeight : 70) + 10;
        sec.style.setProperty("--stick", stick + "px");
        dist = Math.max(0, track.scrollWidth - view.clientWidth);
        sec.style.height = (pin.offsetHeight + dist) + "px";
        secTop = docTop(sec);
      },
      update(st) {
        if (!pinned) return false;
        const p = clamp((st.y + stick - secTop) / Math.max(1, dist), 0, 1);
        track.style.transform = `translate3d(${(-p * dist).toFixed(1)}px,0,0)`;
        if (bar) bar.style.transform = `scaleX(${p.toFixed(4)})`;
        $$(".why-big", track).forEach((b, i) => { b.style.transform = `translate3d(${((p * 4 - i) * -30).toFixed(1)}px,0,0)`; });
        return false;
      },
    });
    // keyboard users: bring the focused card into view
    sec.addEventListener("focusin", (e) => {
      if (!pinned) return;
      const card = e.target.closest(".why-card");
      if (!card) return;
      const x = clamp((card.offsetLeft - (view.clientWidth - card.offsetWidth) / 2) / Math.max(1, dist), 0, 1);
      scrollTo({ top: secTop - stick + x * dist, behavior: "auto" });
    });
  }

  // ------------------------------------------------------------ home: cards that stack as you scroll
  function setupStack() {
    const grid = $("[data-stack]");
    if (!grid) return;
    const cards = $$(".tool", grid);
    cards.forEach((c, i) => c.style.setProperty("--k", i));
    let stacking = false;
    register(grid, {
      measure() {
        stacking = on() && innerHeight >= 560 && innerWidth >= 640;
        grid.classList.toggle("stacking", stacking);
        if (!stacking) cards.forEach((c) => { ["--sc", "--dim", "--fade"].forEach((v) => c.style.removeProperty(v)); });
        const head = $(".top");
        grid.style.setProperty("--stick", ((head ? head.offsetHeight : 70) + 18) + "px");
      },
      update() {
        if (!stacking) return false;
        const rs = cards.map((c) => c.getBoundingClientRect());
        cards.forEach((c, i) => {
          const next = rs[i + 1];
          const cover = next ? clamp((rs[i].bottom - next.top) / rs[i].height, 0, 1) : 0;
          c.style.setProperty("--sc", (1 - cover * 0.07).toFixed(4));
          c.style.setProperty("--dim", Math.min(0.75, cover * 1.6).toFixed(3));
          c.style.setProperty("--fade", clamp(1 - cover * 3.2, 0, 1).toFixed(3));
        });
        return false;
      },
    });
  }

  // ------------------------------------------------------------ timelines draw themselves as you scroll
  function setupTimelines() {
    $$(".timeline").forEach((tl) => {
      const items = Array.from(tl.children);
      let t0 = 0, h = 1, tops = [];
      register(tl, {
        measure() { t0 = docTop(tl); h = tl.offsetHeight || 1; tops = items.map((li) => li.offsetTop); },
        update(st) {
          const line = on() ? clamp((st.y + st.vh * 0.78 - t0) / h, 0, 1) : 1;
          tl.style.setProperty("--tl", line.toFixed(4));
          items.forEach((li, i) => li.classList.toggle("lit", tops[i] + 10 <= line * h + 1));
          return false;
        },
      });
    });
  }

  // ------------------------------------------------------------ cursor ring, magnetic buttons, tilt, ripples
  function setupPointer() {
    if (FINE.matches) {
      const ring = document.createElement("div");
      ring.className = "cursor"; ring.setAttribute("aria-hidden", "true");
      document.body.appendChild(ring);
      let x = -100, y = -100, tx = -100, ty = -100, rq = 0, last = 0, seen = false;
      const HOT = "a, button, select, summary, label, [data-tilt], .story-dots i, [role=option]";
      addEventListener("pointermove", (e) => {
        if (e.pointerType !== "mouse" || !on()) { ring.classList.remove("on"); return; }
        tx = e.clientX; ty = e.clientY;
        if (!seen) { x = tx; y = ty; seen = true; }
        const t = e.target;
        ring.classList.add("on");
        ring.classList.toggle("hot", !!(t.closest && t.closest(HOT)));
        ring.classList.toggle("text", !!(t.closest && t.closest("input[type=search], input[type=text], textarea")));
        if (!rq) rq = requestAnimationFrame(step);
      }, { passive: true });
      document.addEventListener("mouseleave", () => { ring.classList.remove("on"); seen = false; });
      addEventListener("pointerdown", () => ring.classList.add("down"));
      addEventListener("pointerup", () => ring.classList.remove("down"));
      function step(t) {
        const dt = Math.min(50, t - (last || t - 16.7)); last = t;
        const k = 1 - Math.pow(0.74, dt / 16.7);
        x += (tx - x) * k; y += (ty - y) * k;
        ring.style.transform = `translate3d(${x.toFixed(1)}px,${y.toFixed(1)}px,0)`;
        rq = Math.abs(tx - x) + Math.abs(ty - y) > 0.3 ? requestAnimationFrame(step) : (last = 0, 0);
      }
      // magnetic buttons: they lean toward the pointer (the hit area stays where it is)
      const MAG = ".btn, .icon-btn, .pill, .tog, .tab, .to-top, .band-pause, .story-nav button, .nav-motion";
      let mag = null;
      document.addEventListener("pointermove", (e) => {
        if (e.pointerType !== "mouse") return;
        const el = on() && e.target.closest ? e.target.closest(MAG) : null;
        if (mag && mag !== el) { mag.style.translate = ""; mag = null; }
        if (!el) return;
        mag = el;
        const r = el.getBoundingClientRect();
        const dx = (e.clientX - r.left) / r.width - 0.5, dy = (e.clientY - r.top) / r.height - 0.5;
        el.style.translate = `${(dx * Math.min(12, r.width * 0.16)).toFixed(1)}px ${(dy * Math.min(8, r.height * 0.22)).toFixed(1)}px`;
      }, { passive: true });
      // 3D tilt with a light that follows the pointer
      const TILT = "[data-tilt], .acard, .legal-card, .dl, .pair, .v-tile a, .tool, .why-card, .stat";
      let tilt = null;
      document.addEventListener("pointermove", (e) => {
        if (e.pointerType !== "mouse") return;
        let el = on() && e.target.closest ? e.target.closest(TILT) : null;
        if (el && el.closest(".stacking")) el = null;
        if (tilt && tilt !== el) { untilt(tilt); tilt = null; }
        if (!el) return;
        tilt = el;
        const r = el.getBoundingClientRect();
        const px = (e.clientX - r.left) / r.width, py = (e.clientY - r.top) / r.height;
        const max = el.classList.contains("lab") ? 7 : r.width > 420 ? 5 : 10;
        el.classList.add("tilting");
        el.style.setProperty("--rx", ((0.5 - py) * max).toFixed(2) + "deg");
        el.style.setProperty("--ry", ((px - 0.5) * max).toFixed(2) + "deg");
        el.style.setProperty("--gx", (px * 100).toFixed(1) + "%");
        el.style.setProperty("--gy", (py * 100).toFixed(1) + "%");
      }, { passive: true });
      function untilt(el) { el.classList.remove("tilting"); ["--rx", "--ry", "--gx", "--gy"].forEach((p) => el.style.removeProperty(p)); }
      document.addEventListener("mouseleave", () => { if (tilt) { untilt(tilt); tilt = null; } if (mag) { mag.style.translate = ""; mag = null; } });
    }
    // ripples: every button and card answers a press with a wave from where you touched it
    const RIP = ".btn, .icon-btn, .pill, .tog, .tab, .quiz-opt, .acard, .tool, .stat, .type-list a, .legal-card, .dl, .pair, " +
      ".v-tile a, .toc a, .nav a, .story-nav button, .band-pause, .to-top, .heat td > a, .nav-motion, .results a, .quiz-mode label";
    function ripple(el, x, y) {
      if (!on() || !el.animate) return;
      const r = el.getBoundingClientRect();
      let layer = el.querySelector(":scope > .rip-layer");
      if (!layer) { layer = document.createElement("span"); layer.className = "rip-layer"; layer.setAttribute("aria-hidden", "true"); el.appendChild(layer); }
      const s = Math.hypot(r.width, r.height) * 2.1, dot = document.createElement("span");
      dot.className = "rip";
      dot.style.cssText = `left:${x - r.left}px;top:${y - r.top}px;width:${s}px;height:${s}px`;
      layer.appendChild(dot);
      const a = dot.animate([{ transform: "translate(-50%,-50%) scale(0)", opacity: 0.32 }, { transform: "translate(-50%,-50%) scale(1)", opacity: 0 }],
        { duration: 700, easing: EASE });
      a.onfinish = a.oncancel = () => dot.remove();
    }
    document.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      const el = e.target.closest && e.target.closest(RIP);
      if (el) ripple(el, e.clientX, e.clientY);
    });
    document.addEventListener("click", (e) => {
      const el = e.target.closest && e.target.closest(RIP);
      if (!el) return;
      if (e.detail === 0) { const r = el.getBoundingClientRect(); ripple(el, r.left + r.width / 2, r.top + r.height / 2); }
      if (el.matches(".tog, .tab, .quiz-opt, .story-nav button, .tab-all")) pop(el);
    });
    // a select that changes glows for a moment
    document.addEventListener("change", (e) => {
      const t = e.target;
      if (t && t.matches && t.matches("select, input[type=radio]")) {
        const box = t.matches("select") ? t : t.closest("label");
        if (box) anim(box, [{ boxShadow: "0 0 0 0 color-mix(in srgb, var(--brand) 55%, transparent)" }, { boxShadow: "0 0 0 10px transparent" }], { duration: 650, easing: "ease-out", fill: "none" });
      }
    });
  }
  function pop(el) { anim(el, [{ scale: 1 }, { scale: 0.88 }, { scale: 1.07 }, { scale: 1 }], { duration: 480, easing: "ease-out", fill: "none" }); }

  // ------------------------------------------------------------ page transitions and the shared E-number badge
  function setupTransitions() {
    let named = null;
    document.addEventListener("click", (e) => {
      const a = e.target.closest && e.target.closest("a[href]");
      if (!a || e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || a.target === "_blank") return;
      if (!/\/additive\/[^/]+\/?$/.test(a.pathname)) return;
      const badge = $(".enum", a);
      if (!badge || !on()) return;
      const own = $(".a-title .enum.xl");
      if (own && own !== badge) own.style.viewTransitionName = "none";
      if (named && named !== badge) named.style.viewTransitionName = "";
      badge.style.viewTransitionName = "hero-enum";
      named = badge;
    }, true);
    addEventListener("pageswap", (e) => { if (e.viewTransition && !on()) e.viewTransition.skipTransition(); });
    addEventListener("pageshow", () => {
      if (named) { named.style.viewTransitionName = ""; named = null; }
      const own = $(".a-title .enum.xl"); if (own) own.style.viewTransitionName = "";
    });
  }
  function themeSwitch(e, apply) {
    if (!on() || !document.startViewTransition) { apply(); return; }
    const btn = e && e.currentTarget, r = btn ? btn.getBoundingClientRect() : { left: innerWidth - 40, top: 30, width: 0, height: 0 };
    const x = e && e.detail ? e.clientX : r.left + r.width / 2, y = e && e.detail ? e.clientY : r.top + r.height / 2;
    const end = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
    root.classList.add("vt-theme");
    let vt;
    try { vt = document.startViewTransition(apply); } catch (err) { root.classList.remove("vt-theme"); apply(); return; }
    vt.ready.then(() => root.animate({ clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${end}px at ${x}px ${y}px)`] },
      { duration: 700, easing: "cubic-bezier(.65,0,.25,1)", pseudoElement: "::view-transition-new(root)" })).catch(() => {});
    vt.finished.finally(() => root.classList.remove("vt-theme"));
  }

  // ------------------------------------------------------------ small helpers other scripts call
  function cascade(container, sel, o) {
    if (!container || !on()) return;
    const opt = Object.assign({ y: 18, d: 40, max: 16 }, o || {});
    $$(sel, container).slice(0, opt.max).forEach((el, i) => anim(el, [{ opacity: 0, translate: `0 ${opt.y}px`, scale: .97 }, { opacity: 1, translate: "0 0", scale: 1 }],
      { duration: 560, delay: i * opt.d, easing: SPRING }));
  }
  function cardIn(el, dir) {
    anim(el, [{ opacity: 0, transform: `perspective(1000px) rotateY(${(dir || 1) * -55}deg) translateX(${(dir || 1) * 50}px) scale(.94)` },
      { opacity: 1, transform: "none" }], { duration: 720 });
  }
  function flipSeq(els, after) {
    els.forEach((el, i) => {
      const a = anim(el, [{ transform: "perspective(600px) rotateY(90deg)", opacity: .4 }, { transform: "none", opacity: 1 }], { duration: 460, delay: i * 110 });
      if (a && after) a.onfinish = () => after(el, i);
      else if (!a && after) after(el, i);
    });
  }
  function shake(el) { anim(el, [{ translate: "0 0" }, { translate: "-7px 0" }, { translate: "6px 0" }, { translate: "-4px 0" }, { translate: "0 0" }], { duration: 420, easing: "ease-out", fill: "none" }); }
  function swap(a, b) {
    if (!on() || !a || !b) return;
    const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
    anim(a, [{ translate: `${rb.left - ra.left}px ${rb.top - ra.top}px` }, { translate: "0 0" }], { duration: 620, easing: SPRING });
    anim(b, [{ translate: `${ra.left - rb.left}px ${ra.top - rb.top}px` }, { translate: "0 0" }], { duration: 620, easing: SPRING });
  }
  function typeInto(input, text, done) {
    if (!on()) { input.value = text; done && done(); return; }
    const words = text.split(/(\s+)/);
    let i = 0;
    input.value = "";
    input.classList.add("typing");
    (function next() {
      if (i >= words.length) { input.classList.remove("typing"); done && done(); return; }
      input.value += words[i++];
      input.scrollTop = input.scrollHeight;
      setTimeout(next, words[i - 1].trim() ? 34 : 0);
    })();
  }
  function bump(el) { anim(el, [{ scale: 1 }, { scale: 1.12 }, { scale: 1 }], { duration: 420, easing: "ease-out", fill: "none" }); }
  // list page: rows glide to their new places when the order changes (FLIP), new rows rise in
  function flipRows(tbody, mutate) {
    if (!on() || !tbody) { mutate(); return; }
    const vh = innerHeight, before = new Map();
    Array.from(tbody.rows).forEach((tr) => {
      if (tr.hidden) return;
      const r = tr.getBoundingClientRect();
      if (r.bottom > -40 && r.top < vh + 40) before.set(tr, r.top);
    });
    mutate();
    let n = 0;
    Array.from(tbody.rows).forEach((tr) => {
      if (tr.hidden || n > 40) return;
      const r = tr.getBoundingClientRect();
      if (r.bottom < 0 || r.top > vh) return;
      const b = before.get(tr);
      if (b != null) { if (Math.abs(b - r.top) > 1) tr.animate([{ translate: `0 ${b - r.top}px` }, { translate: "0 0" }], { duration: 650, easing: EASE }); }
      else tr.animate([{ opacity: 0, translate: "0 24px" }, { opacity: 1, translate: "0 0" }], { duration: 500, delay: (n % 16) * 25, easing: EASE, fill: "backwards" });
      n++;
    });
  }

  window.Motion = { on, cascade, cardIn, flipSeq, shake, swap, typeInto, odometer, bump, pop, flipRows, themeSwitch, anim, kick, remeasure };

  // ------------------------------------------------------------ start
  function start() {
    bar = $(".progress span");
    toTop = $("[data-to-top]");
    sync();
    if (RM.addEventListener) RM.addEventListener("change", sync);
    $$("[data-motion-toggle]").forEach((b) => b.addEventListener("click", () => {
      const next = on() ? "off" : "on";
      if (window.__store) window.__store.set("motion", next, true);
      else { try { sessionStorage.setItem("motion", next); } catch (e) { /* ignore */ } }
      sync();
    }));
    if (toTop) toTop.addEventListener("click", (e) => {
      e.preventDefault();
      scrollTo({ top: 0, behavior: on() ? "smooth" : "auto" });
      const brand = $(".top .brand");
      if (brand) setTimeout(() => brand.focus({ preventScroll: true }), on() ? 700 : 0);
    });
    setupReveals();
    setupHero(); setupStory(); setupPointer(); setupTransitions();
    const idle = window.requestIdleCallback || ((f) => setTimeout(f, 120));
    idle(() => { setupBand(); setupScrub(); setupSideways(); setupStack(); setupTimelines(); kick(); }, { timeout: 600 });
    listeners.push(() => $$(".reveal, [data-stagger], [data-solo], .split-h, .sbars, .heat, .vcols, .timeline").forEach((el) => el.classList.add("in")));
    let rq = 0;
    const later = () => { cancelAnimationFrame(rq); rq = requestAnimationFrame(remeasure); };
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(later);
    addEventListener("load", later);
    kick();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
