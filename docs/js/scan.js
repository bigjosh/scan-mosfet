// Scan-cycle engine: port of scan_arduino.py's run_phase1/run_sweep_phase.
// Emits points via callbacks for live charting; honors an abort flag; always
// leaves the rig at 0 V.

import { measurePoint, vrange, effVolts } from './convert.js';

const sleep = (ms) => ms > 0 ? new Promise((r) => setTimeout(r, ms)) : Promise.resolve();

export class AbortFlag {
  constructor() { this.aborted = false; }
  abort() { this.aborted = true; }
  check() { if (this.aborted) throw new Error('aborted'); }
}

export const DEFAULT_PARAMS = {
  rlow: 1000, rgate: 1e6,
  hStart: 0, hStop: 5, hStep: 0.1,
  gStart: 0, gStop: 5, gStep: 0.25,
  avg: 32, settleMs: 10, gateSettleMs: 200,
  phases: { p1: true, p2: true, p3: true },
};

export function estimateSeconds(p) {
  const nH = vrange(p.hStart, p.hStop, p.hStep).length;
  const nG = vrange(p.gStart, p.gStop, p.gStep).length;
  const perPoint = 0.05 + p.avg / 1000 + p.settleMs / 1000;
  const sweeps = (p.phases.p2 ? 1 : 0) + (p.phases.p3 ? 1 : 0);
  return sweeps * nG * (p.gateSettleMs / 1000 + nH * perPoint) + (p.phases.p1 ? 2 : 0);
}

async function point(rig, p, vcmdG) {
  const m = await rig.meas();
  return measurePoint(m, rig.vrefIntV, p.rlow, p.rgate, vcmdG);
}

// Phase 1: forward / reverse gate leakage. Returns {fwd, rev}.
export async function runPhase1(rig, p, flag, cb = {}) {
  cb.phaseStart?.(1, 2);
  await rig.ok('LOWIO 0');
  await rig.ok('SETH 0.000');
  await rig.ok('SETG 5.000');
  await sleep(p.gateSettleMs * 2);
  flag.check();
  const fwd = await point(rig, p, 5.0);
  cb.leak?.('forward', fwd);

  await rig.ok('SETG 0.000');
  await rig.ok('SETH 5.000');
  await rig.ok('LOWIO 1');
  await sleep(p.gateSettleMs * 2);
  flag.check();
  const rev = await point(rig, p, 0.0);
  cb.leak?.('reverse', rev);

  await rig.ok('LOWIO 0');
  await rig.ok('SETH 0.000');
  cb.phaseDone?.(1);
  return { fwd, rev };
}

// Phase 2 (positive Vgs, LOW_IO=0) or 3 (negative Vgs, LOW_IO=5V).
// Returns rows: [{vdsCmd, vgsCmd, point}]; cb.point fires per measurement.
export async function runSweepPhase(rig, p, phase, flag, cb = {}) {
  const neg = phase === 3;
  const gGrid = vrange(p.gStart, p.gStop, p.gStep);
  const hGrid = vrange(p.hStart, p.hStop, p.hStep);
  const hPark = neg ? 5.0 : 0.0;  // Vds ~ 0 between gate steps
  cb.phaseStart?.(phase, gGrid.length * hGrid.length);

  // Enter without ever applying full Vds accidentally
  await rig.setVolts('H', hPark);
  await rig.ok('SETG 0.000');
  await rig.ok(neg ? 'LOWIO 1' : 'LOWIO 0');

  const rows = [];
  try {
    for (const g of gGrid) {
      const vgsCmd = neg ? g - 5.0 : g;
      await rig.setVolts('G', g);
      await sleep(p.gateSettleMs);
      cb.curveStart?.(phase, vgsCmd);
      for (const h of hGrid) {
        flag.check();
        const vdsCmd = neg ? h - 5.0 : h;
        await rig.setVolts('H', h);
        await sleep(p.settleMs);
        const pt = await point(rig, p, g);
        rows.push({ vdsCmd, vgsCmd, pt });
        cb.point?.(phase, vgsCmd, vdsCmd, pt);
      }
      await rig.setVolts('H', hPark);
    }
  } finally {
    // Leave the rig safe even on abort/error (order matters in phase 3)
    await rig.ok('LOWIO 0').catch(() => {});
    await rig.ok('SETH 0.000').catch(() => {});
    await rig.ok('SETG 0.000').catch(() => {});
  }
  cb.phaseDone?.(phase);
  return rows;
}

// Full cycle. Returns {phase1, phase2, phase3} (missing keys for unticked phases).
export async function runCycle(rig, p, flag, cb = {}) {
  await rig.ok(`AVG ${p.avg}`);
  const out = {};
  if (p.phases.p1) out.phase1 = await runPhase1(rig, p, flag, cb);
  if (p.phases.p2) out.phase2 = await runSweepPhase(rig, p, 2, flag, cb);
  if (p.phases.p3) out.phase3 = await runSweepPhase(rig, p, 3, flag, cb);
  return out;
}

export { effVolts };
