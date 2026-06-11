// Hand-rolled canvas charts: FamilyChart (Ids-vs-Vds line families colored by
// Vgs, live-appendable) and LeakBars (phase-1 log bars). No dependencies.

const VIRIDIS = [
  [0.267, 0.005, 0.329], [0.283, 0.141, 0.458], [0.254, 0.265, 0.530],
  [0.207, 0.372, 0.553], [0.164, 0.471, 0.558], [0.128, 0.567, 0.551],
  [0.135, 0.659, 0.518], [0.267, 0.749, 0.441], [0.478, 0.821, 0.318],
  [0.741, 0.873, 0.150], [0.993, 0.906, 0.144],
];

export function viridis(t) {
  t = Math.max(0, Math.min(1, t));
  const x = t * (VIRIDIS.length - 1);
  const i = Math.min(VIRIDIS.length - 2, Math.floor(x));
  const f = x - i;
  const c = VIRIDIS[i].map((v, k) => v + (VIRIDIS[i + 1][k] - v) * f);
  return `rgb(${c.map((v) => Math.round(v * 255)).join(',')})`;
}

// 1-2-5 "nice" ticks covering [lo, hi].
function ticks(lo, hi, target = 6) {
  if (!(hi > lo)) { hi = lo + 1; }
  const span = hi - lo;
  const step0 = Math.pow(10, Math.floor(Math.log10(span / target)));
  let step = step0;
  for (const m of [1, 2, 5, 10]) { if (span / (step0 * m) <= target) { step = step0 * m; break; } }
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) {
    out.push(Math.abs(v) < step * 1e-6 ? 0 : v);
  }
  return out;
}

function fmtTick(v) {
  const a = Math.abs(v);
  if (a >= 1000) return v.toFixed(0);
  if (a >= 10 || a === 0) return String(Math.round(v * 100) / 100);
  if (a >= 0.01) return String(Math.round(v * 1000) / 1000);
  return v.toExponential(0);
}

class CanvasBase {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this._dirty = false;
    this._ro = new ResizeObserver(() => this._resize());
    this._ro.observe(canvas.parentElement || canvas);
    this._resize();
  }
  _resize() {
    const parent = this.canvas.parentElement;
    const w = Math.max(280, parent ? parent.clientWidth : this.canvas.clientWidth);
    const h = Math.max(220, Math.min(440, Math.round(w * 0.62)));
    const dpr = window.devicePixelRatio || 1;
    this.w = w; this.h = h;
    this.canvas.style.width = w + 'px';
    this.canvas.style.height = h + 'px';
    this.canvas.width = Math.round(w * dpr);
    this.canvas.height = Math.round(h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.requestDraw();
  }
  requestDraw() {
    if (this._dirty) return;
    this._dirty = true;
    requestAnimationFrame(() => { this._dirty = false; this.draw(); });
  }
  destroy() { this._ro.disconnect(); }
}

export class FamilyChart extends CanvasBase {
  constructor(canvas, { xLabel = 'Vds (V)', yLabel = 'Ids (uA)', colorLabel = 'Vgs (V)' } = {}) {
    super(canvas);
    this.xLabel = xLabel; this.yLabel = yLabel; this.colorLabel = colorLabel;
    this.reset(0, 5);
  }

  reset(cMin, cMax, title = '') {
    this.cMin = cMin; this.cMax = cMax; this.title = title;
    this.curves = [];   // {label, color, xs:[], ys:[]}
    this.requestDraw();
  }

  startCurve(vgs) {
    const t = this.cMax > this.cMin ? (vgs - this.cMin) / (this.cMax - this.cMin) : 0.5;
    this.curves.push({ label: vgs, color: viridis(t), xs: [], ys: [] });
  }

  addPoint(x, y) {
    const c = this.curves[this.curves.length - 1];
    if (!c) return;
    c.xs.push(x); c.ys.push(y);
    this.requestDraw();
  }

  setData(curveList, cMin, cMax, title = '') {
    this.cMin = cMin; this.cMax = cMax; this.title = title;
    this.curves = curveList.map((c) => ({
      label: c.vgs, xs: c.xs, ys: c.ys,
      color: viridis(cMax > cMin ? (c.vgs - cMin) / (cMax - cMin) : 0.5),
    }));
    this.requestDraw();
  }

  _extent() {
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (const c of this.curves) {
      for (let i = 0; i < c.xs.length; i++) {
        if (c.xs[i] < x0) x0 = c.xs[i];
        if (c.xs[i] > x1) x1 = c.xs[i];
        if (c.ys[i] < y0) y0 = c.ys[i];
        if (c.ys[i] > y1) y1 = c.ys[i];
      }
    }
    if (!isFinite(x0)) { x0 = 0; x1 = 5; y0 = 0; y1 = 1; }
    if (x1 - x0 < 1e-9) { x0 -= 0.5; x1 += 0.5; }
    if (y1 - y0 < 1e-9) { y0 -= 1; y1 += 1; }
    const yp = (y1 - y0) * 0.06;
    return { x0, x1, y0: y0 - yp, y1: y1 + yp };
  }

  draw() {
    const { ctx, w, h } = this;
    const css = getComputedStyle(this.canvas);
    const fg = css.getPropertyValue('--chart-fg').trim() || '#ccc';
    const grid = css.getPropertyValue('--chart-grid').trim() || '#333';
    const bg = css.getPropertyValue('--chart-bg').trim() || '#16181d';
    const ml = 58, mr = 54, mt = this.title ? 26 : 12, mb = 40;
    const pw = w - ml - mr, ph = h - mt - mb;
    const e = this._extent();
    const X = (x) => ml + ((x - e.x0) / (e.x1 - e.x0)) * pw;
    const Y = (y) => mt + ph - ((y - e.y0) / (e.y1 - e.y0)) * ph;

    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, w, h);
    ctx.font = '11px system-ui, sans-serif';

    // grid + ticks
    ctx.strokeStyle = grid; ctx.fillStyle = fg; ctx.lineWidth = 1;
    for (const tx of ticks(e.x0, e.x1)) {
      const px = X(tx);
      ctx.beginPath(); ctx.moveTo(px, mt); ctx.lineTo(px, mt + ph); ctx.stroke();
      ctx.textAlign = 'center'; ctx.textBaseline = 'top';
      ctx.fillText(fmtTick(tx), px, mt + ph + 4);
    }
    for (const ty of ticks(e.y0, e.y1)) {
      const py = Y(ty);
      ctx.beginPath(); ctx.moveTo(ml, py); ctx.lineTo(ml + pw, py); ctx.stroke();
      ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
      ctx.fillText(fmtTick(ty), ml - 6, py);
    }
    // zero lines
    ctx.strokeStyle = fg; ctx.globalAlpha = 0.35;
    if (e.x0 < 0 && e.x1 > 0) { ctx.beginPath(); ctx.moveTo(X(0), mt); ctx.lineTo(X(0), mt + ph); ctx.stroke(); }
    if (e.y0 < 0 && e.y1 > 0) { ctx.beginPath(); ctx.moveTo(ml, Y(0)); ctx.lineTo(ml + pw, Y(0)); ctx.stroke(); }
    ctx.globalAlpha = 1;

    // curves
    ctx.lineWidth = 1.5; ctx.lineJoin = 'round';
    for (const c of this.curves) {
      if (!c.xs.length) continue;
      ctx.strokeStyle = c.color;
      ctx.beginPath();
      ctx.moveTo(X(c.xs[0]), Y(c.ys[0]));
      for (let i = 1; i < c.xs.length; i++) ctx.lineTo(X(c.xs[i]), Y(c.ys[i]));
      ctx.stroke();
    }

    // labels
    ctx.fillStyle = fg; ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
    ctx.fillText(this.xLabel, ml + pw / 2, h - 4);
    ctx.save();
    ctx.translate(12, mt + ph / 2); ctx.rotate(-Math.PI / 2);
    ctx.fillText(this.yLabel, 0, 0);
    ctx.restore();
    if (this.title) {
      ctx.textBaseline = 'top'; ctx.font = 'bold 12px system-ui, sans-serif';
      ctx.fillText(this.title, ml + pw / 2, 6);
      ctx.font = '11px system-ui, sans-serif';
    }

    // colorbar
    const cbx = w - mr + 16, cbw = 10;
    for (let i = 0; i < ph; i++) {
      ctx.fillStyle = viridis(1 - i / ph);
      ctx.fillRect(cbx, mt + i, cbw, 1.5);
    }
    ctx.fillStyle = fg; ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
    ctx.fillText(fmtTick(this.cMax), cbx + cbw + 3, mt + 5);
    ctx.fillText(fmtTick(this.cMin), cbx + cbw + 3, mt + ph - 5);
    ctx.save();
    ctx.translate(cbx + cbw + 16, mt + ph / 2); ctx.rotate(-Math.PI / 2);
    ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
    ctx.fillText(this.colorLabel, 0, 0);
    ctx.restore();
  }
}

// Mirror chart: the +Vgs family (phase 2) above and the -Vgs family (phase 3)
// below a shared x-axis seam at gate = 0. Both halves plot positive
// magnitudes: x = |V(High->Low)| with the same range/steps, y = |Ids|.
// Each half autoscales independently (channel vs body-diode currents differ).
export class MirrorChart extends CanvasBase {
  constructor(canvas, { xLabel = '|V(High-Low)| measured (V)', yLabel = '|Ids| (uA)',
                        colorLabel = 'Vgs commanded (V)' } = {}) {
    super(canvas);
    this.xLabel = xLabel; this.yLabel = yLabel; this.colorLabel = colorLabel;
    this.reset(-5, 5);
  }

  reset(cMin, cMax) {
    this.cMin = cMin; this.cMax = cMax;
    this.top = [];     // {vgs, color, xs, ys} - phase 2, drawn upward
    this.bottom = [];  // phase 3, drawn downward
    this.requestDraw();
  }

  _color(vgs) {
    return viridis(this.cMax > this.cMin ? (vgs - this.cMin) / (this.cMax - this.cMin) : 0.5);
  }

  startCurve(side, vgs) {
    this[side].push({ vgs, color: this._color(vgs), xs: [], ys: [] });
  }

  addPoint(side, x, y) {
    const list = this[side];
    const c = list[list.length - 1];
    if (!c) return;
    c.xs.push(x); c.ys.push(y);
    this.requestDraw();
  }

  setData(top, bottom, cMin, cMax) {
    this.cMin = cMin; this.cMax = cMax;
    this.top = top.map((c) => ({ ...c, color: this._color(c.vgs) }));
    this.bottom = bottom.map((c) => ({ ...c, color: this._color(c.vgs) }));
    this.requestDraw();
  }

  _resize() {
    // mirror charts want more height than the default aspect
    const parent = this.canvas.parentElement;
    const w = Math.max(280, parent ? parent.clientWidth : this.canvas.clientWidth);
    const h = Math.max(320, Math.min(560, Math.round(w * 0.8)));
    const dpr = window.devicePixelRatio || 1;
    this.w = w; this.h = h;
    this.canvas.style.width = w + 'px';
    this.canvas.style.height = h + 'px';
    this.canvas.width = Math.round(w * dpr);
    this.canvas.height = Math.round(h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.requestDraw();
  }

  draw() {
    const { ctx, w, h } = this;
    const css = getComputedStyle(this.canvas);
    const fg = css.getPropertyValue('--chart-fg').trim() || '#ccc';
    const grid = css.getPropertyValue('--chart-grid').trim() || '#333';
    const bg = css.getPropertyValue('--chart-bg').trim() || '#16181d';
    const ml = 58, mr = 54, mt = 12, mb = 40;
    const pw = w - ml - mr, ph = h - mt - mb;
    const cy = mt + ph / 2;  // the gate = 0 seam

    let xMax = 0, topMax = 0, botMax = 0;
    for (const c of this.top) for (let i = 0; i < c.xs.length; i++) {
      if (c.xs[i] > xMax) xMax = c.xs[i];
      if (c.ys[i] > topMax) topMax = c.ys[i];
    }
    for (const c of this.bottom) for (let i = 0; i < c.xs.length; i++) {
      if (c.xs[i] > xMax) xMax = c.xs[i];
      if (c.ys[i] > botMax) botMax = c.ys[i];
    }
    if (xMax < 1e-9) xMax = 5;
    if (topMax < 1e-9) topMax = 1;
    if (botMax < 1e-9) botMax = 1;
    topMax *= 1.06; botMax *= 1.06;
    const X = (x) => ml + (x / xMax) * pw;
    const Yt = (y) => cy - (y / topMax) * (ph / 2);
    const Yb = (y) => cy + (y / botMax) * (ph / 2);

    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, w, h);
    ctx.font = '11px system-ui, sans-serif';

    // x grid + ticks (full height)
    ctx.strokeStyle = grid; ctx.fillStyle = fg; ctx.lineWidth = 1;
    for (const tx of ticks(0, xMax)) {
      const px = X(tx);
      ctx.beginPath(); ctx.moveTo(px, mt); ctx.lineTo(px, mt + ph); ctx.stroke();
      ctx.textAlign = 'center'; ctx.textBaseline = 'top';
      ctx.fillText(fmtTick(tx), px, mt + ph + 4);
    }
    // y grid + ticks, per half (labels positive both ways)
    for (const ty of ticks(0, topMax, 4)) {
      if (ty <= 0) continue;
      const py = Yt(ty);
      ctx.beginPath(); ctx.moveTo(ml, py); ctx.lineTo(ml + pw, py); ctx.stroke();
      ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
      ctx.fillText(fmtTick(ty), ml - 6, py);
    }
    for (const ty of ticks(0, botMax, 4)) {
      if (ty <= 0) continue;
      const py = Yb(ty);
      ctx.beginPath(); ctx.moveTo(ml, py); ctx.lineTo(ml + pw, py); ctx.stroke();
      ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
      ctx.fillText(fmtTick(ty), ml - 6, py);
    }

    // the gate-0 seam
    ctx.strokeStyle = fg; ctx.globalAlpha = 0.6; ctx.lineWidth = 1.4;
    ctx.beginPath(); ctx.moveTo(ml, cy); ctx.lineTo(ml + pw, cy); ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.fillStyle = fg; ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
    ctx.fillText('0', ml - 6, cy);

    // curves
    ctx.lineWidth = 1.5; ctx.lineJoin = 'round';
    for (const [list, Y] of [[this.top, Yt], [this.bottom, Yb]]) {
      for (const c of list) {
        if (!c.xs.length) continue;
        ctx.strokeStyle = c.color;
        ctx.beginPath();
        ctx.moveTo(X(c.xs[0]), Y(c.ys[0]));
        for (let i = 1; i < c.xs.length; i++) ctx.lineTo(X(c.xs[i]), Y(c.ys[i]));
        ctx.stroke();
      }
    }

    // half labels + axis labels
    ctx.fillStyle = fg; ctx.globalAlpha = 0.8;
    ctx.textAlign = 'left'; ctx.textBaseline = 'top';
    ctx.fillText('Vgs > 0', ml + 8, mt + 4);
    ctx.textBaseline = 'bottom';
    ctx.fillText('Vgs < 0', ml + 8, mt + ph - 4);
    ctx.globalAlpha = 1;
    ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
    ctx.fillText(this.xLabel, ml + pw / 2, h - 4);
    ctx.save();
    ctx.translate(12, cy); ctx.rotate(-Math.PI / 2);
    ctx.fillText(this.yLabel, 0, 0);
    ctx.restore();

    // colorbar over the full Vgs span
    const cbx = w - mr + 16, cbw = 10;
    for (let i = 0; i < ph; i++) {
      ctx.fillStyle = viridis(1 - i / ph);
      ctx.fillRect(cbx, mt + i, cbw, 1.5);
    }
    ctx.fillStyle = fg; ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
    ctx.fillText(fmtTick(this.cMax), cbx + cbw + 3, mt + 5);
    ctx.fillText(fmtTick(this.cMin), cbx + cbw + 3, mt + ph - 5);
    ctx.save();
    ctx.translate(cbx + cbw + 16, cy); ctx.rotate(-Math.PI / 2);
    ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
    ctx.fillText(this.colorLabel, 0, 0);
    ctx.restore();
  }
}

// Phase-1 leakage bars on a log scale with the ~1 uA ceiling marked.
export class LeakBars extends CanvasBase {
  constructor(canvas) {
    super(canvas);
    this.data = null;
  }
  set(fwdUa, revUa, limitUa = 1.0) {
    this.data = { fwd: Math.abs(fwdUa), rev: Math.abs(revUa), limit: limitUa };
    this.requestDraw();
  }
  draw() {
    const { ctx, w, h } = this;
    const css = getComputedStyle(this.canvas);
    const fg = css.getPropertyValue('--chart-fg').trim() || '#ccc';
    const grid = css.getPropertyValue('--chart-grid').trim() || '#333';
    const bg = css.getPropertyValue('--chart-bg').trim() || '#16181d';
    ctx.fillStyle = bg; ctx.fillRect(0, 0, w, h);
    if (!this.data) return;
    const ml = 58, mr = 16, mt = 16, mb = 44;
    const pw = w - ml - mr, ph = h - mt - mb;
    const lo = 1e-3, hi = 10;
    const Y = (v) => mt + ph - ((Math.log10(Math.max(lo, v)) - Math.log10(lo)) /
      (Math.log10(hi) - Math.log10(lo))) * ph;
    ctx.font = '11px system-ui, sans-serif';
    ctx.strokeStyle = grid; ctx.fillStyle = fg;
    for (const dec of [1e-3, 1e-2, 1e-1, 1, 10]) {
      const py = Y(dec);
      ctx.beginPath(); ctx.moveTo(ml, py); ctx.lineTo(ml + pw, py); ctx.stroke();
      ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
      ctx.fillText(dec >= 1 ? String(dec) : dec.toExponential(0), ml - 6, py);
    }
    const bars = [
      { label: 'forward (Vgs=+5)', v: this.data.fwd, color: '#4a7fd4' },
      { label: 'reverse (Vgs=-5)', v: this.data.rev, color: '#d46a6a' },
    ];
    const bw = pw / 7;
    bars.forEach((b, i) => {
      const cx = ml + pw * (0.28 + 0.44 * i);
      ctx.fillStyle = b.color;
      ctx.fillRect(cx - bw / 2, Y(b.v), bw, mt + ph - Y(b.v));
      ctx.fillStyle = fg;
      ctx.textAlign = 'center'; ctx.textBaseline = 'top';
      ctx.fillText(b.label, cx, mt + ph + 6);
      ctx.textBaseline = 'bottom';
      ctx.fillText(`${b.v.toFixed(3)} uA`, cx, Y(b.v) - 2);
    });
    // ceiling line
    ctx.strokeStyle = '#e05555'; ctx.setLineDash([5, 4]);
    ctx.beginPath(); ctx.moveTo(ml, Y(this.data.limit)); ctx.lineTo(ml + pw, Y(this.data.limit)); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#e05555'; ctx.textAlign = 'left'; ctx.textBaseline = 'bottom';
    ctx.fillText('~1 uA "leaky" ceiling', ml + 4, Y(this.data.limit) - 2);
    ctx.fillStyle = fg;
    ctx.save();
    ctx.translate(12, mt + ph / 2); ctx.rotate(-Math.PI / 2);
    ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
    ctx.fillText('|Igs| (uA)', 0, 0);
    ctx.restore();
  }
}
