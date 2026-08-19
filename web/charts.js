/* charts.js — dependency-free SVG charts (offline, no CDN).
   All charts draw into a provided <svg> using its existing viewBox. */
(function (global) {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";

  // palettes read from CSS vars at draw time so the theme toggle re-skins
  // every chart on the next render (dark mode is a designed mapping in CSS).
  const cv = (n, f) => {
    const v = getComputedStyle(document.documentElement).getPropertyValue(n).trim();
    return v || f;
  };
  let PAL = {}, INK, GRID, AXIS, MUTED, ACCENT;
  function refresh() {
    PAL.home = { line: cv("--ch-home", "#2A4A3A"), band: cv("--ch-home-band", "#BCD6C6"), band2: cv("--ch-home-band2", "#DCE6DF"), dot: cv("--ch-home", "#2A4A3A") };
    PAL.relocation = { line: cv("--ch-reloc", "#722F37"), band: cv("--ch-reloc-band", "#D8B3BA"), band2: cv("--ch-reloc-band2", "#EAD9DC"), dot: cv("--ch-reloc", "#722F37") };
    PAL.gold = { line: cv("--ch-gold", "#8A6420"), band: cv("--ch-gold-band", "#E4D2A8"), band2: cv("--ch-gold-band2", "#F0E6CE"), dot: cv("--ch-gold", "#8A6420") };
    INK = cv("--ch-ink", "#6B645B"); GRID = cv("--ch-grid", "#EFEBE2");
    AXIS = cv("--ch-axis", "#D9D4CA"); MUTED = cv("--ch-muted", "#A8A29A");
    ACCENT = cv("--ch-reloc", "#722F37");
  }
  refresh();

  // empty-state strings, switchable by the app's language toggle (audit P2-4)
  let MSG = { empty: "\u65e0\u6570\u636e", sparse: "\u6837\u672c\u4e0d\u8db3" };
  function setLang(l) {
    // Tooltip labels live in `MSG` with everything else rather than behind a
    // second language variable. The first version of the hover work invented
    // a `LANG` that this file has never had -- the same shape as the
    // `fmtMoney` that rendered "fmtMoney is not defined" in a shipped panel.
    MSG = l === "zh"
      ? { empty: "\u65e0\u6570\u636e", sparse: "\u6837\u672c\u4e0d\u8db3",
          age: "\u5e74\u9f84 ", paths: "\u8def\u5f84\u6570",
          share: "\u5360\u6bd4", low: "\u4f4e\u7aef", high: "\u9ad8\u7aef",
          swing: "\u6446\u5e45", base: "\u57fa\u51c6" }
      : { empty: "No data", sparse: "Insufficient sample",
          age: "Age ", paths: "Paths", share: "Share",
          low: "Low", high: "High", swing: "Swing", base: "Base" };
  }

  // ---------- formatting ----------
  function money(x) {
    if (x == null || isNaN(x)) return "—";
    const a = Math.abs(x), s = x < 0 ? "-" : "";
    // strip trailing zeros ONLY from the decimal part (so "10.00"->"10", not "1")
    const trim = z => z.indexOf(".") >= 0 ? z.replace(/\.?0+$/, "") : z;
    if (a >= 1e6) return s + "$" + trim((a / 1e6).toFixed(a >= 1e7 ? 0 : 2)) + "M";
    if (a >= 1e3) return s + "$" + Math.round(a / 1e3) + "K";
    return s + "$" + Math.round(a).toLocaleString();
  }
  function moneyFull(x) { return x == null || isNaN(x) ? "—" : "$" + Math.round(x).toLocaleString(); }
  function pct(x, d) { return x == null || isNaN(x) ? "—" : (x * 100).toFixed(d == null ? 1 : d) + "%"; }

  // round to 2 significant figures — an Apple/Stocks axis never prints $3.02M, it prints $3.0M
  function sig2(v) {
    if (!isFinite(v) || v === 0) return v;
    const f = Math.pow(10, Math.floor(Math.log10(Math.abs(v))) - 1);
    return Math.round(v / f) * f;
  }
  function niceMax(max) {
    if (max <= 0) return 1;
    const exp = Math.floor(Math.log10(max)), base = Math.pow(10, exp), n = max / base;
    const nice = n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10;
    return nice * base;
  }

  // ---------- svg primitives ----------
  function el(tag, attrs, text) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (text != null) e.textContent = text;
    return e;
  }
  function clear(svg) {
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    if (svg._tipOff) { svg._tipOff(); svg._tipOff = null; }   // detach stale hover
  }

  // ---------- hover tooltip (singleton; desktop affordance) ----------
  let TIP = null;
  function tipEl() {
    if (!TIP) {
      TIP = document.createElement("div");
      TIP.className = "chart-tip";
      TIP.style.display = "none";
      document.body.appendChild(TIP);
    }
    return TIP;
  }
  function attachHover(svg, W, m, xToData) {
    const tip = tipEl();
    const move = evt => {
      if (evt.buttons) { tip.style.display = "none"; return; }   // don't fight drags
      const rc = svg.getBoundingClientRect();
      const xv = (evt.clientX - rc.left) * (W / rc.width);
      if (xv < m.l || xv > W - m.r) { tip.style.display = "none"; return; }
      const d = xToData(xv);
      if (!d) { tip.style.display = "none"; return; }
      tip.innerHTML = `<div class="ct-t">${d.title}</div>` +
        d.rows.map(r => `<div class="ct-r"><span class="ct-sw" style="background:${r[2] || "transparent"}"></span>${r[0]}<b>${r[1]}</b></div>`).join("");
      tip.style.display = "block";
      const tw = tip.offsetWidth, th = tip.offsetHeight;
      let x = evt.clientX + 14, y = evt.clientY + 12;
      if (x + tw > window.innerWidth - 8) x = evt.clientX - tw - 14;
      if (y + th > window.innerHeight - 8) y = evt.clientY - th - 12;
      tip.style.left = x + "px"; tip.style.top = y + "px";
    };
    const leave = () => { tip.style.display = "none"; };
    svg.addEventListener("pointermove", move);
    svg.addEventListener("pointerleave", leave);
    svg._tipOff = () => {
      svg.removeEventListener("pointermove", move);
      svg.removeEventListener("pointerleave", leave);
      tip.style.display = "none";
    };
  }
  // The y-axis sibling of `attachHover`, for charts whose rows stack
  // vertically. Tornado is one: forcing it through the x-scanner would
  // report whichever row happened to share an x-range with the pointer,
  // which is worse than no tooltip because it is confidently wrong.
  function attachHoverY(svg, H, m, yToData) {
    const tip = tipEl();
    const move = evt => {
      if (evt.buttons) { tip.style.display = "none"; return; }
      const rc = svg.getBoundingClientRect();
      const yv = (evt.clientY - rc.top) * (H / rc.height);
      if (yv < m.t || yv > H - m.b) { tip.style.display = "none"; return; }
      const d = yToData(yv);
      if (!d) { tip.style.display = "none"; return; }
      tip.innerHTML = `<div class="ct-t">${d.title}</div>` +
        d.rows.map(r => `<div class="ct-r"><span class="ct-sw" style="background:${r[2] || "transparent"}"></span>${r[0]}<b>${r[1]}</b></div>`).join("");
      tip.style.display = "block";
      const tw = tip.offsetWidth, th = tip.offsetHeight;
      let x = evt.clientX + 14, y = evt.clientY + 12;
      if (x + tw > window.innerWidth - 8) x = evt.clientX - tw - 14;
      if (y + th > window.innerHeight - 8) y = evt.clientY - th - 12;
      tip.style.left = x + "px"; tip.style.top = y + "px";
    };
    const leave = () => { tip.style.display = "none"; };
    svg.addEventListener("pointermove", move);
    svg.addEventListener("pointerleave", leave);
    svg._tipOff = () => {
      svg.removeEventListener("pointermove", move);
      svg.removeEventListener("pointerleave", leave);
      tip.style.display = "none";
    };
  }
  function vb(svg) { const p = (svg.getAttribute("viewBox") || "0 0 760 300").split(/\s+/).map(Number); return { W: p[2], H: p[3] }; }
  function txt(svg, x, y, s, o) {
    o = o || {};
    const n = el("text", {
      x, y, "text-anchor": o.anchor || "start",
      "font-family": o.mono === false ? "var(--sans)" : "var(--mono)",
      "font-size": o.size || 11, fill: o.fill || MUTED,
      "font-weight": o.weight || 400
    }, s);
    // size-specific tracking, same discipline the DOM has: tighten big numerals, loosen tiny caps
    if (o.tracking != null) n.setAttribute("letter-spacing", o.tracking);
    svg.appendChild(n);
    return n;   // returned so callers can animate the node (e.g. the gauge's % count-up)
  }

  // ---------- GAUGE (semicircle) ----------
  function gauge(svg, value, opts) {
    refresh();
    clear(svg); opts = opts || {};
    const { W } = vb(svg);
    const cx = W / 2, cy = 126, r = 88, w = 24;
    const p = Math.max(0, Math.min(1, value || 0));
    // STATE, not scenario: the gauge is the app's pass/warn/fail readout, so its arc + number
    // take the SEMANTIC tokens (a failing plan must not render in relocation-mauve — that would
    // cross-wire the one separation the palette rests on). This gauge is the one place a number
    // legitimately carries colour, because the whole element IS a state indicator.
    const col = p >= 0.9 ? cv("--good", "#6F8A5F") : p >= 0.75 ? cv("--warn", "#BD985C") : cv("--bad", "#AF746A");
    const arc = (frac) => {
      const a0 = Math.PI, a1 = Math.PI + frac * Math.PI;
      const x0 = cx + r * Math.cos(a0), y0 = cy + r * Math.sin(a0);
      const x1 = cx + r * Math.cos(a1), y1 = cy + r * Math.sin(a1);
      return `M ${x0} ${y0} A ${r} ${r} 0 ${frac > 0.5 ? 1 : 0} 1 ${x1} ${y1}`;
    };
    svg.appendChild(el("path", { d: arc(1), fill: "none", stroke: GRID, "stroke-width": w, "stroke-linecap": "round" }));
    const valArc = p > 0.001
      ? svg.appendChild(el("path", { d: arc(p), fill: "none", stroke: col, "stroke-width": w, "stroke-linecap": "round" }))
      : null;
    // ONE decimal — the same precision the verdict prints ("100.0%"): both describe three-branch
    // success, so they must agree, and the old 2nd decimal was below the run's sampling noise.
    // Tracking pulls the big numeral tight, like every other display figure. No sub-caption: the
    // hcard already labels this gauge (the removed "SOLVENT" was hardcoded English AND the wrong word).
    const pctTxt = txt(svg, cx, cy + 4, pct(p, 1), { anchor: "middle", size: 34, fill: col, weight: 700, tracking: "-0.7" });

    // Hero reveal: sweep the arc up from 0 and count the % with it, BOTH driven by one eased value
    // so the ring and the digit fill as a single body (they used to run on two different curves and
    // visibly raced). Pause-safe by construction — the gauge is drawn COMPLETE above, and we only
    // collapse-then-fill inside a rAF callback, so a paused/starved frame leaves a full, correct gauge.
    if (opts.animate && window.matchMedia && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
      requestAnimationFrame(() => {
        try {
          let len = 0;
          if (valArc) {
            len = valArc.getTotalLength();
            valArc.style.strokeDasharray = len;
            valArc.style.strokeDashoffset = len;
            valArc.getBoundingClientRect();
          }
          const dur = 900, t0 = performance.now();
          const step = now => {
            const t = Math.min(1, (now - t0) / dur);
            const e = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
            if (valArc) valArc.style.strokeDashoffset = len * (1 - e);   // same curve as the number
            pctTxt.textContent = pct(p * e, 1);
            if (t < 1) requestAnimationFrame(step);
            else { if (valArc) valArc.style.strokeDashoffset = "0"; pctTxt.textContent = pct(p, 1); }
          };
          requestAnimationFrame(step);
        } catch (e) {}
      });
    }
  }

  // ---------- FAN (percentile bands over age; log or linear) ----------
  function fan(svg, rows, opts) {
    refresh();
    clear(svg); opts = opts || {};
    rows = (rows || []).filter(r => r.p90 > 0 || !opts.log);
    if (rows.length < 2) { txt(svg, 20, 30, MSG.empty, {}); return; }
    const { W, H } = vb(svg);
    const m = { l: 62, r: 16, t: 14, b: 30 };
    const pal = PAL[opts.pal] || PAL.home;
    const log = !!opts.log;
    const ages = rows.map(r => r.age);
    const aMin = ages[0], aMax = ages[ages.length - 1];
    let lo, hi;
    if (log) {
      lo = Math.max(1, Math.min.apply(null, rows.map(r => r.p10 > 0 ? r.p10 : Infinity)));
      hi = Math.max.apply(null, rows.map(r => r.p90));
      if (!isFinite(lo)) lo = 1;                       // all p10==0 (audit P3-4)
      if (!isFinite(hi) || hi <= 0) hi = lo * 10;
      lo = Math.pow(10, Math.floor(Math.log10(lo))); hi = Math.pow(10, Math.ceil(Math.log10(hi)));
      if (hi <= lo) hi = lo * 10;
    } else {
      lo = 0; hi = niceMax(Math.max.apply(null, rows.map(r => r.p90)) * 1.02);
    }
    const px = a => m.l + (a - aMin) / Math.max(1, aMax - aMin) * (W - m.l - m.r);
    const py = v => {
      if (log) { const t = (Math.log10(Math.max(v, lo)) - Math.log10(lo)) / (Math.log10(hi) - Math.log10(lo)); return H - m.b - t * (H - m.t - m.b); }
      return H - m.b - (v / hi) * (H - m.t - m.b);
    };
    // grid + y labels
    const yvals = log ? logTicks(lo, hi) : linTicks(hi, 5);
    yvals.forEach(v => {
      const y = py(v);
      svg.appendChild(el("line", { x1: m.l, y1: y, x2: W - m.r, y2: y, stroke: GRID, "stroke-width": 1 }));
      txt(svg, m.l - 8, y + 4, money(v), { anchor: "end", size: 10, fill: MUTED });
    });
    // x labels
    const span = aMax - aMin, step = span > 40 ? 10 : span > 20 ? 5 : 2;
    for (let a = Math.ceil(aMin / step) * step; a <= aMax; a += step)
      txt(svg, px(a), H - 10, a, { anchor: "middle", size: 10, fill: MUTED });
    // bands
    const area = (loK, hiK) => {
      let d = "M " + px(rows[0].age) + " " + py(rows[0][loK]);
      for (const r of rows) d += " L " + px(r.age) + " " + py(r[loK]);
      for (let i = rows.length - 1; i >= 0; i--) d += " L " + px(rows[i].age) + " " + py(rows[i][hiK]);
      return d + " Z";
    };
    if (rows[0].p10 != null) svg.appendChild(el("path", { d: area("p10", "p90"), fill: pal.band2 }));
    if (rows[0].p25 != null) svg.appendChild(el("path", { d: area("p25", "p75"), fill: pal.band }));
    let dl = "";
    rows.forEach((r, i) => dl += (i ? " L " : "M ") + px(r.age) + " " + py(r.p50));
    // crisper, more confident median — a light halo separates it from the band so it reads as the spine
    svg.appendChild(el("path", { d: dl, fill: "none", stroke: pal.band2, "stroke-width": 5, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    const median = el("path", { d: dl, fill: "none", stroke: pal.line, "stroke-width": 2.8, "stroke-linejoin": "round" });
    svg.appendChild(median);
    // Draw-in, same pause-safe shape as the gauge: the line is already in the DOM COMPLETE, and
    // the collapse + transition + zero all happen together inside ONE rAF callback. Splitting them
    // (collapse outside, zero inside) leaves a permanently hidden median if that rAF never runs.
    if (opts.animate && window.matchMedia && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
      requestAnimationFrame(() => {
        try {
          const len = median.getTotalLength();
          median.style.strokeDasharray = len;
          median.style.strokeDashoffset = len;
          median.getBoundingClientRect();
          median.style.transition = "stroke-dashoffset .7s ease";
          median.style.strokeDashoffset = "0";
        } catch (e) {}
      });
    }
    // FIRE marker
    if (opts.fireAge != null && opts.fireAge >= aMin && opts.fireAge <= aMax) {
      const fx = px(opts.fireAge);
      svg.appendChild(el("line", { x1: fx, y1: m.t, x2: fx, y2: H - m.b, stroke: ACCENT, "stroke-width": 1.3, "stroke-dasharray": "5 4" }));
      txt(svg, fx + 4, m.t + 11, "FIRE ~" + Math.round(opts.fireAge), { size: 10, fill: ACCENT, weight: 600, tracking: "0.5" });
    }
    // cursor
    if (opts.cursorAge != null && opts.cursorAge >= aMin && opts.cursorAge <= aMax) {
      const r = rows.reduce((b, x) => Math.abs(x.age - opts.cursorAge) < Math.abs(b.age - opts.cursorAge) ? x : b, rows[0]);
      const cx = px(r.age);
      svg.appendChild(el("line", { x1: cx, y1: m.t, x2: cx, y2: H - m.b, stroke: INK, "stroke-width": 1, "stroke-dasharray": "2 3", opacity: .6 }));
      ["p10", "p50", "p90"].forEach(k => svg.appendChild(el("circle", { cx, cy: py(r[k]), r: 3, fill: pal.line })));
    }
    svg.appendChild(el("line", { x1: m.l, y1: H - m.b, x2: W - m.r, y2: H - m.b, stroke: AXIS, "stroke-width": 1 }));

    attachHover(svg, W, m, xv => {
      const a = Math.round(aMin + (xv - m.l) / (W - m.l - m.r) * (aMax - aMin));
      const r = rows.reduce((b, x) => Math.abs(x.age - a) < Math.abs(b.age - a) ? x : b, rows[0]);
      const out = [];
      if (r.p90 != null) out.push(["P90 ", money(r.p90), pal.band2]);
      if (r.p75 != null) out.push(["P75 ", money(r.p75), pal.band]);
      out.push(["P50 ", money(r.p50), pal.line]);
      if (r.p25 != null) out.push(["P25 ", money(r.p25), pal.band]);
      if (r.p10 != null) out.push(["P10 ", money(r.p10), pal.band2]);
      return { title: "age " + r.age, rows: out };
    });

    // ---- §5.3 direct manipulation: drag the FIRE line with momentum throw ----
    if (opts.onFireDrag && opts.fireAge != null) {
      const plotL = m.l, plotR = W - m.r;                       // plot pixel bounds
      const xMin = px(aMin), xMax = px(aMax);
      const dimn = plotR - plotL;
      const svgX = clientX => { const rc = svg.getBoundingClientRect(); return (clientX - rc.left) * (W / rc.width); };
      const xToAge = x => aMin + (x - plotL) / (plotR - plotL) * (aMax - aMin);
      const clampAge = a => Math.max(aMin, Math.min(aMax, Math.round(a)));
      // Apple rubber-band: resistance grows as you pull past the end
      const rubber = over => { const s = Math.sign(over), a = Math.abs(over); return s * (a * 0.55 * dimn) / (dimn + 0.55 * a); };
      const bandX = x => x < xMin ? xMin + rubber(x - xMin) : x > xMax ? xMax + rubber(x - xMax) : x;

      let fx0 = px(opts.fireAge);   // grab anchor — follows the parked target so you can re-drag from it
      const fireX0 = fx0;           // the computed FIRE line's own position (never moves within a render)
      const grip = el("rect", { x: fx0 - 12, y: m.t, width: 24, height: H - m.t - m.b,
                                fill: "transparent", style: "cursor:ew-resize;touch-action:none",
                                // §3.3 keyboard path: the grip IS the slider (add-only attributes;
                                // the label arrives pre-localised from app.js via tt())
                                tabindex: 0, role: "slider", "aria-orientation": "horizontal",
                                "aria-valuemin": aMin, "aria-valuemax": aMax,
                                "aria-valuenow": Math.round(opts.fireAge) });
      if (opts.fireLabel) grip.setAttribute("aria-label", opts.fireLabel);
      svg.appendChild(grip);

      // Gesture bookkeeping. `gestureToken` increments on every pointerdown; a release captures
      // the token it belongs to, so a settle scheduled by gesture N can never commit once gesture
      // N+1 has begun. A single shared `done` flag could not express that: it stayed true after
      // the first commit and silently swallowed the SECOND release.
      let ghost = null, ghostTxt = null, dragging = false, grabDX = 0, samples = [], spring = null;
      let gestureToken = 0, appliedToken = -1, settleTimer = null;
      const clearSettle = () => { if (settleTimer != null) { clearTimeout(settleTimer); settleTimer = null; } };
      const showGhost = x => {
        const age = clampAge(xToAge(x));
        if (!ghost) {
          ghost = el("line", { y1: m.t, y2: H - m.b, stroke: PAL.gold.line, "stroke-width": 1.8, "stroke-dasharray": "3 3", "pointer-events": "none" });
          ghostTxt = el("text", { y: m.t + 24, "font-family": "var(--mono)", "font-size": 11, fill: PAL.gold.line, "font-weight": 600, "pointer-events": "none" });
          svg.appendChild(ghost); svg.appendChild(ghostTxt);
        }
        ghost.setAttribute("x1", x); ghost.setAttribute("x2", x);
        ghostTxt.setAttribute("x", Math.min(x + 5, plotR - 34)); ghostTxt.textContent = "→ " + age;
        grip.setAttribute("aria-valuenow", age);        // AT hears every position the ghost shows
      };
      const clearGhost = () => { if (ghost) { ghost.remove(); ghostTxt.remove(); ghost = ghostTxt = null; } };

      grip.addEventListener("pointerdown", e => {
        // Tear down anything the PREVIOUS gesture left in flight before starting a new one:
        // its safety timer must not fire mid-drag, and its spring must not drive the ghost.
        clearSettle();
        if (spring) { spring.stop(); spring = null; }
        gestureToken++;
        dragging = true;
        try { grip.setPointerCapture(e.pointerId); } catch (_) {}
        grabDX = svgX(e.clientX) - fx0;                         // grab offset — line stays under the finger, no jump
        samples = [[fx0, performance.now()]];
        showGhost(fx0);
        e.preventDefault();
      });
      grip.addEventListener("pointermove", e => {
        if (!dragging) return;
        const x = bandX(svgX(e.clientX) - grabDX);              // 1:1 continuous follow (rubber-band past the ends)
        samples.push([x, performance.now()]); if (samples.length > 5) samples.shift();
        showGhost(x);
        e.preventDefault();
      });
      const release = e => {
        if (!dragging) return;
        dragging = false;
        const token = gestureToken;                             // this settle belongs to THIS gesture
        const relX = bandX(svgX(e.clientX) - grabDX);
        let v = 0;                                              // release velocity (px/s) from recent samples
        if (samples.length >= 2) {
          const a = samples[0], b = samples[samples.length - 1], dt = b[1] - a[1];
          if (dt >= 8) v = (b[0] - a[0]) / dt * 1000;           // need a real time span, else velocity is noise
          v = Math.max(-3000, Math.min(3000, v));               // sane cap so a stray tiny-dt sample can't fling it
        }
        // Momentum, then snap to the nearest integer-age tick. The raw iOS projection
        // (v/1000 * 0.998/(1-0.998) ~= v*0.5 px) is tuned for free scrolling: on this
        // picker even a slow release carries enough residual velocity to fling the target
        // several years away, which reads as drift. So momentum applies only to a
        // deliberate flick, and is capped so a throw can never overshoot by >4 years.
        const pxPerYear = (plotR - plotL) / Math.max(1, aMax - aMin);
        let proj = relX;
        if (Math.abs(v) > 400) {
          const throwPx = (v / 1000) * 0.998 / (1 - 0.998);
          proj = relX + Math.max(-4 * pxPerYear, Math.min(4 * pxPerYear, throwPx));
        }
        const projected = Math.max(xMin, Math.min(xMax, proj));
        const snapAge = clampAge(xToAge(projected)), snapX = px(snapAge);
        const apply = () => {
          // Stale-gesture guard (a new pointerdown has already bumped the token) + once-per-gesture
          // guard (spring onRest and the safety timer both call this).
          if (token !== gestureToken || appliedToken === token) return;
          appliedToken = token;
          clearSettle();
          if (spring) { spring.stop(); spring = null; }  // stop first: a live onFrame would resurrect the ghost
          if (snapAge !== Math.round(opts.fireAge)) {
            // Keep the marker parked at the age you dropped it on. The real FIRE line is a
            // computed result and never moves, so clearing here made the whole gesture look
            // like it vanished. It stays as the pending target until the next chart render.
            showGhost(snapX);
            // Move the (invisible) grab handle onto the parked target too — otherwise the only
            // draggable spot stays back at the original FIRE age and you can't continue from here.
            fx0 = snapX;
            grip.setAttribute("x", snapX - 12);
            opts.onFireDrag(snapAge);
          } else {
            clearGhost();                                // dropped back on the current age: no target
            fx0 = fireX0;                                // anchor returns to the visible FIRE line —
            grip.setAttribute("x", fireX0 - 12);         // a grip stranded at a cleared park spot
          }                                              // would make the real line ungrabbable
        };
        if (window.Motion && !window.Motion.prefersReducedMotion()) {
          spring = window.Motion.spring("THROW");
          spring.onFrame(showGhost);
          spring.set(relX);
          // 800ms, not 600: a hard THROW settles in ~0.7s, so the old fallback fired mid-flight and
          // committed while the ghost was still travelling. It is a rAF-starvation net, nothing else,
          // so a healthy onRest cancels it rather than racing it.
          settleTimer = setTimeout(apply, 800);
          spring.to(snapX, { velocity: v, onRest: () => { clearSettle(); apply(); } });
        } else { apply(); }
      };
      // A cancelled pointer (OS gesture steal, context menu, contact lost) is NOT a drop: abandon
      // the gesture silently — no commit, no onFireDrag, grip restored to its parked anchor.
      const cancel = () => {
        if (!dragging) return;
        dragging = false;
        gestureToken++;                       // invalidate anything this gesture might still settle
        clearSettle();
        if (spring) { spring.stop(); spring = null; }
        // If a previous gesture parked a pending target at fx0, the marker must survive the
        // abandoned gesture (ghost and grip/fx0 always agree); only a no-target state clears it.
        if (fx0 !== fireX0) showGhost(fx0); else clearGhost();
        grip.setAttribute("x", fx0 - 12);     // grip and fx0 stay in lockstep
      };
      grip.addEventListener("pointerup", release);
      grip.addEventListener("pointercancel", cancel);

      // §3.3 keyboard operation, mirroring the pointer contract exactly: arrows move the ghost
      // (the uncommitted preview), Enter commits through the SAME park/anchor/onFireDrag path,
      // and blur discards an unconfirmed adjustment (back to the parked state, never a commit).
      let kbAge = null;                                  // uncommitted keyboard position, or null
      const kbCommit = () => {
        const age = kbAge != null ? kbAge : clampAge(xToAge(fx0));
        kbAge = null;
        if (age !== Math.round(opts.fireAge)) {
          const x = px(age);
          showGhost(x); fx0 = x; grip.setAttribute("x", x - 12);
          opts.onFireDrag(age);
        } else {
          clearGhost(); fx0 = fireX0; grip.setAttribute("x", fireX0 - 12);
        }
      };
      grip.addEventListener("keydown", e => {
        if (dragging) return;                            // pointer owns the gesture
        const dir = e.key === "ArrowLeft" ? -1 : e.key === "ArrowRight" ? 1 : 0;
        if (dir) {
          // a keyboard adjustment supersedes any in-flight settle, like a new pointerdown does
          clearSettle();
          if (spring) { spring.stop(); spring = null; }
          gestureToken++;
          const cur = kbAge != null ? kbAge : clampAge(xToAge(fx0));
          kbAge = Math.max(aMin, Math.min(aMax, cur + dir * (e.shiftKey ? 5 : 1)));
          showGhost(px(kbAge));
          e.preventDefault();
        } else if (e.key === "Enter") {
          clearSettle();
          if (spring) { spring.stop(); spring = null; }
          gestureToken++;
          kbCommit();
          e.preventDefault();
        } else if (e.key === "Escape" && kbAge != null) {
          kbAge = null;                                  // discard the preview, restore parked state
          if (fx0 !== fireX0) showGhost(fx0); else clearGhost();
          e.preventDefault();
        }
      });
      grip.addEventListener("focus", () => {             // focus-visible reveals the ghost affordance
        if (dragging) return;
        let kb = true;
        try { kb = grip.matches(":focus-visible"); } catch (_) {}
        if (kb) showGhost(fx0);
      });
      grip.addEventListener("blur", () => {
        if (dragging) return;
        kbAge = null;                                    // leaving without Enter = no commit
        if (fx0 !== fireX0) showGhost(fx0); else clearGhost();
      });
    }

    // ---- I4 overlay: actual check-in points on top of the forecast fan ----
    (opts.overlay || []).forEach(p => {
      if (p.age < aMin || p.age > aMax || !(p.value > 0)) return;
      const x = px(p.age), y = py(p.value);
      svg.appendChild(el("circle", { cx: x, cy: y, r: 5.5, fill: ACCENT,
                                     stroke: "var(--paper, #fff)", "stroke-width": 1.5 }));
      if (p.label) txt(svg, x, y - 10, p.label,
                       { anchor: "middle", size: 9.5, fill: ACCENT, weight: 600 });
    });
  }

  function logTicks(lo, hi) {
    const out = []; for (let e = Math.log10(lo); e <= Math.log10(hi) + 1e-9; e++) out.push(Math.pow(10, e)); return out;
  }
  function linTicks(hi, n) { const out = []; for (let i = 0; i <= n; i++) out.push(hi * i / n); return out; }

  // ---------- HISTOGRAM (edges + counts; edges may be log-spaced) ----------
  function histogram(svg, hist, opts) {
    refresh();
    clear(svg); opts = opts || {};
    if (!hist || !hist.counts || !hist.counts.length) { txt(svg, 20, 30, MSG.empty, {}); return; }
    const { W, H } = vb(svg);
    const m = { l: 20, r: 16, t: 14, b: 34 };
    const col = opts.color || PAL.home.line;
    const edges = hist.edges, counts = hist.counts, n = counts.length;
    const cMax = Math.max.apply(null, counts) || 1;
    const bx = i => m.l + i / n * (W - m.l - m.r);
    const bw = (W - m.l - m.r) / n;
    const by = c => H - m.b - (c / cMax) * (H - m.t - m.b);
    const bars = [];
    counts.forEach((c, i) => {
      const h = (H - m.b) - by(c);
      const r = el("rect", { x: bx(i) + 0.5, y: by(c), width: Math.max(1, bw - 1), height: Math.max(0, h), fill: col, opacity: .85 });
      svg.appendChild(r); bars.push(r);
    });
    // §5.8 rise-in — pause-safe: bars are drawn full-height; we only collapse + cascade-rise
    // inside a rAF that confirms rAF is live, so a paused frame / reduced-motion leaves them visible.
    if (opts.animate && window.matchMedia && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
      requestAnimationFrame(() => {
        bars.forEach((r, i) => {
          r.style.transformBox = "fill-box"; r.style.transformOrigin = "center bottom";
          r.style.transform = "scaleY(0)"; r.getBoundingClientRect();
          r.style.transition = "transform .38s cubic-bezier(.32,.72,0,1) " + (i * 12) + "ms";
          r.style.transform = "scaleY(1)";
        });
      });
    }
    // x ticks: round values (decades on log edges, nice linear stops otherwise) placed by their
    // real position — not raw bin edges, which printed $3.02M / $1.07M like a script had drawn it.
    const lo = edges[0], hi = edges[edges.length - 1];
    const logsp = edges.length > 2 && Math.abs((edges[2] - edges[1]) - (edges[1] - edges[0])) > 1e-6 * edges[1];
    let xticks = [];
    if (logsp && lo > 0) {
      for (let e = Math.ceil(Math.log10(lo)); e <= Math.floor(Math.log10(hi) + 1e-9); e++) xticks.push(Math.pow(10, e));
    } else {
      xticks = linTicks(niceMax(hi), 5).filter(v => v >= lo && v <= hi);
    }
    if (xticks.length < 2) xticks = [lo, sig2(hi)];   // narrow range: just the endpoints, rounded
    xticks.forEach(v => {
      const frac = valFrac(v, edges);
      if (frac < -0.001 || frac > 1.001) return;
      txt(svg, m.l + frac * (W - m.l - m.r), H - 12, money(v), { anchor: "middle", size: 9.5, fill: MUTED });
    });
    // percentile markers — values rounded to 2 sig figs so P90 reads $3.0M, not $3.02M
    [["p10", MUTED], ["p50", ACCENT], ["p90", MUTED]].forEach(([k, cc]) => {
      if (hist[k] == null) return;
      const frac = valFrac(hist[k], edges);
      const x = m.l + frac * (W - m.l - m.r);
      svg.appendChild(el("line", { x1: x, y1: m.t, x2: x, y2: H - m.b, stroke: cc, "stroke-width": k === "p50" ? 1.6 : 1, "stroke-dasharray": k === "p50" ? "" : "3 3" }));
      txt(svg, x, m.t + 10, k.toUpperCase() + " " + money(sig2(hist[k])), { anchor: "middle", size: 9, fill: cc, weight: 600, tracking: "0.4" });
    });
    svg.appendChild(el("line", { x1: m.l, y1: H - m.b, x2: W - m.r, y2: H - m.b, stroke: AXIS, "stroke-width": 1 }));

    const totalN = counts.reduce((a, b) => a + b, 0) || 1;
    attachHover(svg, W, m, xv => {
      const i = Math.max(0, Math.min(n - 1, Math.floor((xv - m.l) / bw)));
      return { title: money(edges[i]) + " – " + money(edges[Math.min(i + 1, edges.length - 1)]),
               rows: [[" ", counts[i] + " (" + (counts[i] / totalN * 100).toFixed(1) + "%)", col]] };
    });
  }
  function valFrac(v, edges) {
    const lo = edges[0], hi = edges[edges.length - 1];
    if (v <= lo) return 0; if (v >= hi) return 1;
    // handle log-spaced edges
    const logsp = edges.length > 2 && Math.abs((edges[2] - edges[1]) - (edges[1] - edges[0])) > 1e-6 * edges[1];
    if (logsp) return (Math.log10(v) - Math.log10(lo)) / (Math.log10(hi) - Math.log10(lo));
    return (v - lo) / (hi - lo);
  }

  // ---------- AGE BARS (milestone reach ages) ----------
  function ageBars(svg, hist, opts) {
    refresh();
    clear(svg); opts = opts || {};
    if (!hist || !hist.counts || !hist.counts.length) { txt(svg, 20, 26, MSG.sparse, {}); return; }
    const { W, H } = vb(svg);
    const m = { l: 20, r: 16, t: 12, b: 28 };
    const col = opts.color || PAL.gold.line;
    const ages = hist.ages, counts = hist.counts, n = counts.length;
    const cMax = Math.max.apply(null, counts) || 1;
    const bw = (W - m.l - m.r) / n;
    const by = c => H - m.b - (c / cMax) * (H - m.t - m.b);
    const bars = [];
    counts.forEach((c, i) => {
      const r = el("rect", { x: m.l + i * bw + 0.5, y: by(c), width: Math.max(1, bw - 1.5), height: (H - m.b) - by(c), fill: col, opacity: .8 });
      svg.appendChild(r); bars.push(r);
    });
    // §5.8 rise-in, same pause-safe shape as histogram: bars drawn full first, collapse+rise
    // together inside one rAF callback.
    if (opts.animate && window.matchMedia && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
      requestAnimationFrame(() => {
        bars.forEach((r, i) => {
          r.style.transformBox = "fill-box"; r.style.transformOrigin = "center bottom";
          r.style.transform = "scaleY(0)"; r.getBoundingClientRect();
          r.style.transition = "transform .38s cubic-bezier(.32,.72,0,1) " + (i * 12) + "ms";
          r.style.transform = "scaleY(1)";
        });
      });
    }
    const step = n > 20 ? 5 : n > 10 ? 2 : 1;
    ages.forEach((a, i) => { if (a % step === 0) txt(svg, m.l + (i + 0.5) * bw, H - 10, a, { anchor: "middle", size: 9.5, fill: MUTED }); });
    [["p10", MUTED], ["p50", ACCENT], ["p90", MUTED]].forEach(([k, cc]) => {
      if (hist[k] == null) return;
      const i = hist[k] - ages[0];
      const x = m.l + (i + 0.5) * bw;
      svg.appendChild(el("line", { x1: x, y1: m.t, x2: x, y2: H - m.b, stroke: cc, "stroke-width": k === "p50" ? 1.6 : 1, "stroke-dasharray": k === "p50" ? "" : "3 3" }));
    });
    svg.appendChild(el("line", { x1: m.l, y1: H - m.b, x2: W - m.r, y2: H - m.b, stroke: AXIS, "stroke-width": 1 }));
    const total = counts.reduce((a, b) => a + b, 0) || 1;
    attachHover(svg, W, m, xv => {
      const i = Math.floor((xv - m.l) / bw);
      if (i < 0 || i >= n) return null;
      return {
        title: MSG.age + ages[i],
        rows: [
          [MSG.paths, counts[i].toLocaleString(), col],
          [MSG.share, pct(counts[i] / total, 1)],
        ],
      };
    });
  }

  // ---------- TORNADO (diverging bars from a center) ----------
  function tornado(svg, rows, center, opts) {
    refresh();
    clear(svg); opts = opts || {};
    if (!rows || !rows.length) { txt(svg, 20, 30, MSG.empty, {}); return; }
    const { W, H } = vb(svg);
    const m = { l: 150, r: 70, t: 10, b: 26 };
    rows = rows.slice().sort((a, b) => Math.abs(b.hi - b.lo) - Math.abs(a.hi - a.lo));
    const all = rows.flatMap(r => [r.lo, r.hi]).concat(center);
    let vmin = Math.min.apply(null, all), vmax = Math.max.apply(null, all);
    const pad = (vmax - vmin) * 0.05 || 1; vmin -= pad; vmax += pad;
    const bx = v => m.l + (v - vmin) / (vmax - vmin) * (W - m.l - m.r);
    const rowH = (H - m.t - m.b) / rows.length, bh = Math.min(20, rowH * 0.6);
    const cX = bx(center);
    svg.appendChild(el("line", { x1: cX, y1: m.t, x2: cX, y2: H - m.b, stroke: ACCENT, "stroke-width": 1.4, "stroke-dasharray": "4 3" }));
    txt(svg, cX, H - 10, "base " + money(center), { anchor: "middle", size: 9.5, fill: ACCENT, weight: 600 });
    const rowRects = [];
    rows.forEach((r, i) => {
      const y = m.t + i * rowH + (rowH - bh) / 2;
      const x1 = bx(Math.min(r.lo, r.hi)), x2 = bx(Math.max(r.lo, r.hi));
      // One hue at two intensities (not two SCENARIO colours — this is a sensitivity magnitude,
      // not home-vs-relocation): the below-base half is the lighter band, the above-base half the
      // stronger one. Reads as "how far this lever swings the outcome", split at the base line.
      const loR = el("rect", { x: x1, y, width: Math.max(1, cX - x1), height: bh, fill: PAL.home.band2 });
      const hiR = el("rect", { x: cX, y, width: Math.max(1, x2 - cX), height: bh, fill: PAL.home.band });
      svg.appendChild(loR); svg.appendChild(hiR); rowRects.push([loR, hiR]);
      txt(svg, m.l - 10, y + bh / 2 + 4, r.label, { anchor: "end", size: 11, fill: INK, mono: false, weight: 600 });
      txt(svg, x1 - 4, y + bh / 2 + 4, money(r.lo), { anchor: "end", size: 9, fill: MUTED });
      txt(svg, x2 + 4, y + bh / 2 + 4, money(r.hi), { anchor: "start", size: 9, fill: MUTED });
    });
    // §5.8 grow-out: each bar pair expands from the center axis (the "base case" line) toward
    // its extremes — the geometry narrates what a tornado chart means. Pause-safe as always:
    // drawn complete, collapsed + regrown inside one rAF callback.
    if (opts.animate && window.matchMedia && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
      requestAnimationFrame(() => {
        rowRects.forEach(([lo, hi], i) => {
          [[lo, "100% 50%"], [hi, "0% 50%"]].forEach(([rc, org]) => {
            rc.style.transformBox = "fill-box"; rc.style.transformOrigin = org;
            rc.style.transform = "scaleX(0)"; rc.getBoundingClientRect();
            rc.style.transition = "transform .42s cubic-bezier(.32,.72,0,1) " + (i * 30) + "ms";
            rc.style.transform = "scaleX(1)";
          });
        });
      });
    }
    // Row-based, so the y-scanner. Feeding tornado through the x-scanner
    // would report whichever row shares an x-range with the pointer -- a
    // tooltip that is confidently about the wrong lever, which is worse than
    // having none.
    attachHoverY(svg, H, m, yv => {
      const i = Math.floor((yv - m.t) / rowH);
      if (i < 0 || i >= rows.length) return null;
      const r = rows[i];
      return {
        title: r.label,
        rows: [
          [MSG.low, money(r.lo), PAL.home.band2],
          [MSG.high, money(r.hi), PAL.home.band],
          [MSG.swing, money(Math.abs(r.hi - r.lo))],
          [MSG.base, money(center), ACCENT],
        ],
      };
    });
  }

  // ---------- LINES (generic multi-series; optional dual axis + cursor) ----------
  function lines(svg, cfg) {
    refresh();
    clear(svg);
    const { W, H } = vb(svg);
    const hasR = !!cfg.yRight;
    const m = { l: 58, r: hasR ? 58 : 18, t: 16, b: 34 };
    const series = cfg.series || [];
    const xs = series.flatMap(s => s.points.map(p => p[0]));
    let xmin = cfg.xDomain ? cfg.xDomain[0] : Math.min.apply(null, xs);
    let xmax = cfg.xDomain ? cfg.xDomain[1] : Math.max.apply(null, xs);
    if (xmax === xmin) xmax = xmin + 1;
    const axisDom = (which) => {
      const pts = series.filter(s => (s.axis || "left") === which).flatMap(s => s.points.map(p => p[1]));
      if (!pts.length) return null;
      const a = cfg[which === "left" ? "yLeft" : "yRight"] || {};
      let lo = a.min != null ? a.min : Math.min.apply(null, pts);
      let hi = a.max != null ? a.max : Math.max.apply(null, pts);
      if (a.log) { lo = Math.pow(10, Math.floor(Math.log10(Math.max(lo, 1)))); hi = Math.pow(10, Math.ceil(Math.log10(hi))); }
      else { if (a.min == null) lo = Math.min(0, lo); if (a.max == null) hi = niceMax(hi * 1.05) || hi + 1; }
      return { lo, hi, log: !!a.log };
    };
    const L = axisDom("left"), R = hasR ? axisDom("right") : null;
    const px = x => m.l + (x - xmin) / (xmax - xmin) * (W - m.l - m.r);
    const pyOf = (dom) => (v) => {
      if (dom.log) { const t = (Math.log10(Math.max(v, dom.lo)) - Math.log10(dom.lo)) / (Math.log10(dom.hi) - Math.log10(dom.lo)); return H - m.b - t * (H - m.t - m.b); }
      return H - m.b - (v - dom.lo) / (dom.hi - dom.lo) * (H - m.t - m.b);
    };
    const pyL = L ? pyOf(L) : null, pyR = R ? pyOf(R) : null;
    // grid + left axis labels
    if (L) {
      const yv = L.log ? logTicks(L.lo, L.hi) : linTicks2(L.lo, L.hi, 5);
      yv.forEach(v => { const y = pyL(v); svg.appendChild(el("line", { x1: m.l, y1: y, x2: W - m.r, y2: y, stroke: GRID })); txt(svg, m.l - 8, y + 4, (cfg.yLeft && cfg.yLeft.fmt ? cfg.yLeft.fmt(v) : money(v)), { anchor: "end", size: 10 }); });
    }
    if (R) {
      const yv = linTicks2(R.lo, R.hi, 5);
      yv.forEach(v => { const y = pyR(v); txt(svg, W - m.r + 8, y + 4, (cfg.yRight && cfg.yRight.fmt ? cfg.yRight.fmt(v) : money(v)), { anchor: "start", size: 10, fill: PAL.gold.line }); });
    }
    // x labels
    const xstep = niceStep(xmax - xmin);
    for (let x = Math.ceil(xmin / xstep) * xstep; x <= xmax + 1e-9; x += xstep)
      txt(svg, px(x), H - 12, (cfg.xfmt ? cfg.xfmt(x) : x), { anchor: "middle", size: 10 });
    // markers (vertical)
    (cfg.markers || []).forEach(mk => {
      const x = px(mk.x);
      svg.appendChild(el("line", { x1: x, y1: m.t, x2: x, y2: H - m.b, stroke: mk.color || ACCENT, "stroke-width": 1.2, "stroke-dasharray": "5 4" }));
      if (mk.label) txt(svg, x + 4, m.t + 11, mk.label, { size: 9.5, fill: mk.color || ACCENT, weight: 600 });
    });
    // series
    const drawPaths = [], fadeEls = [];
    series.forEach(s => {
      const py = (s.axis === "right") ? pyR : pyL;
      if (!py) return;
      let d = "";
      s.points.forEach((p, i) => d += (i ? " L " : "M ") + px(p[0]) + " " + py(p[1]));
      const path = el("path", { d, fill: "none", stroke: s.color, "stroke-width": s.width || 2.2, "stroke-dasharray": s.dash || "" });
      svg.appendChild(path);
      // dash-offset draw-in would destroy a dashed series' pattern — those fade instead
      (s.dash ? fadeEls : drawPaths).push(path);
      if (s.dots) s.points.forEach(p => { const c = el("circle", { cx: px(p[0]), cy: py(p[1]), r: 2.6, fill: s.color }); svg.appendChild(c); fadeEls.push(c); });
    });
    // §5.8 draw-in (median-line pattern): solid series sweep left→right with a small stagger,
    // dots and dashed series fade in behind them. Everything is drawn COMPLETE first; the
    // collapse happens only inside this single rAF callback, so a starved frame changes nothing.
    if (cfg.animate && window.matchMedia && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
      requestAnimationFrame(() => {
        try {
          drawPaths.forEach((p, i) => {
            const len = p.getTotalLength();
            p.style.strokeDasharray = len; p.style.strokeDashoffset = len;
            p.getBoundingClientRect();
            p.style.transition = "stroke-dashoffset .6s cubic-bezier(.32,.72,0,1) " + (i * 90) + "ms";
            p.style.strokeDashoffset = "0";
          });
          fadeEls.forEach((n, i) => {
            n.style.opacity = "0"; n.getBoundingClientRect();
            n.style.transition = "opacity .3s ease " + (240 + i * 6) + "ms";
            n.style.opacity = "1";
          });
        } catch (e) {}
      });
    }
    // cursor
    if (cfg.cursorX != null && cfg.cursorX >= xmin && cfg.cursorX <= xmax) {
      const x = px(cfg.cursorX);
      svg.appendChild(el("line", { x1: x, y1: m.t, x2: x, y2: H - m.b, stroke: INK, "stroke-width": 1, "stroke-dasharray": "2 3", opacity: .55 }));
    }
    svg.appendChild(el("line", { x1: m.l, y1: H - m.b, x2: W - m.r, y2: H - m.b, stroke: AXIS, "stroke-width": 1 }));
    if (cfg.xLabel) txt(svg, (W) / 2, H - 0, cfg.xLabel, { anchor: "middle", size: 10, fill: MUTED, mono: false });

    attachHover(svg, W, m, xv => {
      const x = xmin + (xv - m.l) / (W - m.l - m.r) * (xmax - xmin);
      const out = [];
      series.forEach(s2 => {
        if (!s2.points.length) return;
        const p2 = s2.points.reduce((b, q) => Math.abs(q[0] - x) < Math.abs(b[0] - x) ? q : b, s2.points[0]);
        const a2 = (s2.axis === "right") ? (cfg.yRight || {}) : (cfg.yLeft || {});
        out.push([(s2.name || "") + " ", (a2.fmt ? a2.fmt(p2[1]) : money(p2[1])), s2.color]);
      });
      if (!out.length) return null;
      const near = series[0].points.reduce((b, q) => Math.abs(q[0] - x) < Math.abs(b[0] - x) ? q : b, series[0].points[0]);
      return { title: (cfg.xfmt ? cfg.xfmt(near[0]) : near[0]) + (cfg.xLabel ? " " + cfg.xLabel : ""), rows: out };
    });
  }
  function linTicks2(lo, hi, n) { const out = []; for (let i = 0; i <= n; i++) out.push(lo + (hi - lo) * i / n); return out; }
  function niceStep(span) { if (span <= 8) return 1; if (span <= 20) return 5; if (span <= 60) return 10; return 20; }

  global.Charts = { gauge, fan, histogram, ageBars, tornado, lines, money, moneyFull, pct, PAL, pal: k => PAL[k], setLang };
})(window);
