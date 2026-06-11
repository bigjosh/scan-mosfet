// Measurement math — direct port of scan_arduino.py (node_volts / measure_point).
// All physics lives here; the firmware only reports averaged ADC counts.

export const CLIP_COUNTS = 1010.0;

// Pick the in-range reading for one node: 1.1 V ref if unclipped, else 5 V ref.
export function nodeVolts(meas, name, vrefIntV, vddV) {
  const c11 = meas[`${name}_1V1`];
  const c5 = meas[`${name}_5V`];
  if (c11 < CLIP_COUNTS) return { v: (c11 / 1024.0) * vrefIntV, clip: false };
  return { v: (c5 / 1024.0) * vddV, clip: c5 >= CLIP_COUNTS };
}

// Convert one MEAS? reply into node voltages + currents.
// rlow/rgate in ohms, vcmdG = commanded gate volts (for Igs).
export function measurePoint(meas, vrefIntV, rlow, rgate, vcmdG) {
  const vdd = meas.VDD_MV / 1000.0;
  const a0 = nodeVolts(meas, 'A0', vrefIntV, vdd);
  const a1 = nodeVolts(meas, 'A1', vrefIntV, vdd);
  const a2 = nodeVolts(meas, 'A2', vrefIntV, vdd);
  const a3 = nodeVolts(meas, 'A3', vrefIntV, vdd);
  return {
    vdd,
    vlow: a0.v, vgate: a1.v, vhigh: a2.v, vlowio: a3.v,
    idsUa: ((a0.v - a3.v) / rlow) * 1e6,
    igsUa: ((vcmdG - a1.v) / rgate) * 1e6,
    vds: a2.v - a0.v,
    vgs: a1.v - a0.v,
    flag: (a0.clip || a1.clip || a2.clip || a3.clip) ? 'clip' : 'ok',
  };
}

// What the DAC was actually told, from the CODE= in a SETx reply.
export function effVolts(code, vddV) {
  return (code / 4096.0) * vddV;
}

// Inclusive voltage grid, mirrors create_voltage_range / vrange.
export function vrange(start, stop, step) {
  const n = Math.round((stop - start) / step) + 1;
  const out = [];
  for (let i = 0; i < n; i++) out.push(start + ((stop - start) * i) / (n - 1 || 1));
  return out;
}

// ---- CSV builders (same columns as scan_arduino.py) ----

const pad = (s, w) => String(s).padStart(w);
const f = (x, w, d) => pad(x.toFixed(d), w);

export const SWEEP_HEADER =
  `${pad('Vds (V)', 8)}, ${pad('Vgs (V)', 8)}, ${pad('Ids (uA)', 12)}, ${pad('Igs (uA)', 10)}, ` +
  `${pad('Vds_meas', 9)}, ${pad('Vgs_meas', 9)}, ${pad('Vhigh', 8)}, ${pad('Vlow', 8)}, ` +
  `${pad('Vgate', 8)}, ${pad('Vlowio', 8)}, flag\n`;

export function sweepRow(vdsCmd, vgsCmd, p) {
  return (
    `${f(vdsCmd, 8, 2)}, ${f(vgsCmd, 8, 2)}, ${f(p.idsUa, 12, 2)}, ${f(p.igsUa, 10, 3)}, ` +
    `${f(p.vds, 9, 4)}, ${f(p.vgs, 9, 4)}, ${f(p.vhigh, 8, 4)}, ${f(p.vlow, 8, 4)}, ` +
    `${f(p.vgate, 8, 4)}, ${f(p.vlowio, 8, 4)}, ${p.flag}\n`
  );
}

export const PHASE1_HEADER = 'mode, Igs (uA), Vgate (V), Vlow (V), Vlowio (V), Vhigh (V), flag\n';

export function phase1Row(mode, p) {
  return `${mode}, ${p.igsUa.toFixed(4)}, ${p.vgate.toFixed(4)}, ${p.vlow.toFixed(4)}, ` +
         `${p.vlowio.toFixed(4)}, ${p.vhigh.toFixed(4)}, ${p.flag}\n`;
}

export function timestamp(d = new Date()) {
  const p2 = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p2(d.getMonth() + 1)}${p2(d.getDate())}_` +
         `${p2(d.getHours())}${p2(d.getMinutes())}${p2(d.getSeconds())}`;
}

export function sanitizeName(name) {
  const s = (name || 'scan').trim().replace(/[^a-zA-Z0-9._-]+/g, '-').replace(/^-+|-+$/g, '');
  return s || 'scan';
}
