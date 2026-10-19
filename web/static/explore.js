/* Additive Status — the turning globe and the E-number map.
   Decoration and convenience only: every page states the same facts in text. No dependencies, no
   network requests. The globe turns by itself only while animations are on (see motion.js); dragging
   it always works. */
(function () {
  "use strict";
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const motionOn = () => !!(window.Motion && window.Motion.on());
  const RAD = Math.PI / 180, R = 150;
  const NS = "http://www.w3.org/2000/svg";

  // ------------------------------------------------------------ globe (orthographic projection)
  function setupGlobe(fig) {
    let pts;
    try { pts = JSON.parse($(".globe-data", fig).textContent); } catch (e) { return; }
    const svg = $("svg", fig), grid = $(".g-grid", svg), layer = $(".g-pts", svg);
    let focus = fig.getAttribute("data-focus") || "";
    // start over the focus place (or the Atlantic, so Europe and North America both show)
    const centre = (j) => {
      const own = pts.filter((p) => p[3] === j);
      if (!own.length) return [-28, 30];
      let x = 0, y = 0, z = 0, lat = 0;
      own.forEach((p) => { x += Math.cos(p[2] * RAD); y += Math.sin(p[2] * RAD); lat += p[1]; });
      return [Math.atan2(y, x) / RAD, Math.max(-50, Math.min(50, lat / own.length))];
    };
    let [lon0, lat0] = centre(focus);
    let target = null, dragging = false, hover = false, last = 0, visible = true;

    // a faint even dot pattern on the sphere (Fibonacci lattice) gives the ball its texture
    const dots = [];
    for (let i = 0, n = 420; i < n; i++) {
      const y = 1 - (i + 0.5) * 2 / n, r = Math.sqrt(1 - y * y), th = i * 2.399963;
      dots.push([Math.asin(y) / RAD, Math.atan2(Math.sin(th) * r, Math.cos(th) * r) / RAD]);
    }
    const dotEls = dots.map(() => { const c = document.createElementNS(NS, "circle"); c.setAttribute("r", "1.1"); c.setAttribute("class", "g-dot"); grid.appendChild(c); return c; });
    const lines = [];
    for (let k = 0; k < 12; k++) { const p = document.createElementNS(NS, "path"); p.setAttribute("class", "g-line"); grid.appendChild(p); lines.push(p); }
    const marks = pts.map((p) => {
      const g = document.createElementNS(NS, "g");
      g.setAttribute("class", "g-pt gj-" + p[3] + (p[4] ? " g-extra" : ""));
      const ring = document.createElementNS(NS, "circle"); ring.setAttribute("r", "7"); ring.setAttribute("class", "g-ring");
      const dot = document.createElementNS(NS, "circle"); dot.setAttribute("r", "3.4"); dot.setAttribute("class", "g-core");
      const t = document.createElementNS(NS, "title"); t.textContent = p[0] + (p[4] ? " (" + p[4] + ")" : "");
      g.appendChild(ring); g.appendChild(dot); g.appendChild(t); layer.appendChild(g);
      return g;
    });
    function project(lat, lon) {
      const p = lat * RAD, l = (lon - lon0) * RAD, p0 = lat0 * RAD;
      const cosc = Math.sin(p0) * Math.sin(p) + Math.cos(p0) * Math.cos(p) * Math.cos(l);
      return [R * Math.cos(p) * Math.sin(l), -R * (Math.cos(p0) * Math.sin(p) - Math.sin(p0) * Math.cos(p) * Math.cos(l)), cosc];
    }
    function path(coords) {
      let d = "", pen = false;
      coords.forEach(([la, lo]) => {
        const [x, y, z] = project(la, lo);
        if (z > 0) { d += (pen ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1); pen = true; } else pen = false;
      });
      return d;
    }
    function draw() {
      let k = 0;
      for (let lo = -180; lo < 180; lo += 30) { const c = []; for (let la = -90; la <= 90; la += 6) c.push([la, lo]); lines[k++].setAttribute("d", path(c)); }
      for (let la = -60; la <= 60; la += 30) { if (k >= lines.length) break; const c = []; for (let lo = -180; lo <= 180; lo += 6) c.push([la, lo]); lines[k++].setAttribute("d", path(c)); }
      dots.forEach((d, i) => { const [x, y, z] = project(d[0], d[1]); const el = dotEls[i]; if (z > 0.02) { el.setAttribute("cx", x.toFixed(1)); el.setAttribute("cy", y.toFixed(1)); el.style.opacity = (0.25 + z * 0.75).toFixed(2); el.style.display = ""; } else el.style.display = "none"; });
      pts.forEach((p, i) => {
        const [x, y, z] = project(p[1], p[2]), g = marks[i];
        if (z > 0) { g.setAttribute("transform", `translate(${x.toFixed(1)} ${y.toFixed(1)}) scale(${(0.55 + z * 0.45).toFixed(2)})`); g.style.display = ""; }
        else g.style.display = "none";
        g.classList.toggle("dim", !!focus && p[3] !== focus);
      });
    }
    function frame(t) {
      if (!visible) { last = 0; return; }
      const dt = last ? Math.min(64, t - last) : 16; last = t;
      if (target) {
        let d = ((target[0] - lon0 + 540) % 360) - 180;
        lon0 += d * Math.min(1, dt / 260); lat0 += (target[1] - lat0) * Math.min(1, dt / 260);
        if (Math.abs(d) < 0.2 && Math.abs(target[1] - lat0) < 0.2) target = null;
      } else if (!dragging && !hover && motionOn()) lon0 += dt * 0.006;
      draw();
      if (target || dragging || (!hover && motionOn())) requestAnimationFrame(frame); else last = 0;
    }
    const kick = () => { if (!last) requestAnimationFrame(frame); };
    draw();
    let sx = 0, sy = 0, slon = 0, slat = 0;
    svg.addEventListener("pointerdown", (e) => { dragging = true; target = null; sx = e.clientX; sy = e.clientY; slon = lon0; slat = lat0; svg.setPointerCapture(e.pointerId); fig.classList.add("dragging"); kick(); });
    svg.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      const k = 180 / (svg.getBoundingClientRect().width || 300);
      lon0 = slon - (e.clientX - sx) * k; lat0 = Math.max(-70, Math.min(70, slat + (e.clientY - sy) * k));
    });
    const end = () => { dragging = false; fig.classList.remove("dragging"); kick(); };
    svg.addEventListener("pointerup", end); svg.addEventListener("pointercancel", end);
    fig.addEventListener("pointerenter", () => { hover = true; });
    fig.addEventListener("pointerleave", () => { hover = false; kick(); });
    // hovering a place card (on the places page) turns the globe to that place
    $$("[data-globe-focus]").forEach((a) => {
      const go = () => { focus = a.getAttribute("data-globe-focus"); target = centre(focus); kick(); };
      a.addEventListener("pointerenter", go); a.addEventListener("focus", go);
      a.addEventListener("pointerleave", () => { focus = fig.getAttribute("data-focus") || ""; kick(); });
    });
    if ("IntersectionObserver" in window) new IntersectionObserver((es) => { visible = es[0].isIntersecting; if (visible) kick(); }).observe(fig);
    if (window.Motion) document.addEventListener("click", (e) => { if (e.target.closest && e.target.closest("[data-motion-toggle]")) setTimeout(kick, 50); });
    kick();
  }

  // ------------------------------------------------------------ the E-number map
  function setupMap(box) {
    const btns = $$("[data-map-place]", box), sum = $("[data-map-summary]", box), tiles = $$(".et", box);
    const names = {}; (window.SITE && SITE.jur || []).forEach((j) => { names[j.k] = j.t; });
    const initial = sum ? sum.textContent : "";
    function set(place, user) {
      if (user && motionOn()) {
        box.classList.remove("wave"); void box.offsetWidth; box.classList.add("wave");
        setTimeout(() => box.classList.remove("wave"), 1800);
      }
      box.setAttribute("data-place", place);
      btns.forEach((b) => b.setAttribute("aria-pressed", b.getAttribute("data-map-place") === place ? "true" : "false"));
      if (!sum) return;
      if (place === "all") { sum.textContent = initial; return; }
      const c = { ok: 0, warn: 0, no: 0, unknown: 0 };
      tiles.forEach((t) => { c[t.getAttribute("data-" + place)]++; });
      sum.textContent = "In " + (names[place] || place) + ": " + (c.ok + c.warn) + " allowed" + (c.warn ? " (" + c.warn + " being phased out)" : "") +
        ", " + c.no + " not allowed, " + c.unknown + " not on the list or no data.";
    }
    btns.forEach((b) => b.addEventListener("click", () => set(b.getAttribute("data-map-place"), true)));
    const p = new URLSearchParams(location.search).get("place");
    if (p && btns.some((b) => b.getAttribute("data-map-place") === p)) set(p, false);
    if (motionOn()) { box.classList.add("wave"); setTimeout(() => box.classList.remove("wave"), 1800); }
  }

  // ------------------------------------------------------------ home: the small map that fills itself in
  function setupMini(box) {
    if (!("IntersectionObserver" in window)) { box.classList.add("in"); return; }
    const io = new IntersectionObserver((es) => { if (es[0].isIntersecting) { box.classList.add("in"); io.disconnect(); } }, { rootMargin: "0px 0px -10% 0px" });
    io.observe(box);
  }

  function start() {
    $$("[data-globe]").forEach(setupGlobe);
    $$("[data-emap]").forEach(setupMap);
    $$("[data-minimap]").forEach(setupMini);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
