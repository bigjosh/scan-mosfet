// Bring-up wizard logic: port of bring-up.py (same steps, same limits).
// Each step: {id, name, action?, input?, run(log[, value]) -> bool}.
// UI/flow (retry/skip/continue) lives in app.js.

import { measurePoint, vrange, effVolts } from './convert.js';

const sleep = (ms) => ms > 0 ? new Promise((r) => setTimeout(r, ms)) : Promise.resolve();

async function point(rig, p, vcmdG) {
  return measurePoint(await rig.meas(), rig.vrefIntV, p.rlow, p.rgate, vcmdG);
}

export function buildSteps(rig, p) {
  return [
    {
      id: 'fw', name: 'Firmware identity & VDD sanity',
      async run(log) {
        log(`banner: ${rig.banner}`);
        const vdd = await rig.vddV();
        const cal = await rig.calbgGet();
        log(`VDD measures ${vdd.toFixed(3)} V (stored bandgap cal: ${cal} mV)`);
        if (vdd < 4.3 || vdd > 5.5) {
          log('outside the 4.3-5.5 V a USB-powered Uno should show - check supply');
          return false;
        }
        return true;
      },
    },
    {
      id: 'pintest', name: 'I2C harness diagnostic (PINTEST)',
      async run(log) {
        const { raw, vals } = await rig.pintest();
        log(raw);
        let ok = true;
        for (const line of ['SDA', 'SCL1', 'SCL2']) {
          if (!vals[`${line}_EXTPU`]) {
            log(`${line}: no external pullup seen -> wire not reaching a powered module ` +
                `(unsoldered header pin? wrong row? module unpowered?)`);
            ok = false;
          }
          if (vals[`${line}_5VSHORT`]) {
            log(`${line}: pin is fighting a hard 5 V source -> plugged into a power pin?`);
            ok = false;
          }
        }
        if (vals.BRIDGED) { log('two bus lines short together (solder bridge / doubled row?)'); ok = false; }
        if (ok) log('all three bus lines: pullup present, no shorts, no bridges');
        return ok;
      },
    },
    {
      id: 'dacs', name: 'DAC detection, raw writes, SAVEZERO',
      async run(log) {
        log(await rig.rescan());
        if (!rig.hasH || !rig.hasG) {
          const missing = [!rig.hasH && 'DAC_H (clocked by D5)', !rig.hasG && 'DAC_G (clocked by D6)']
            .filter(Boolean).join(', ');
          log(`not detected: ${missing} - fix wiring (see PINTEST hints), then Retry`);
          return false;
        }
        for (const c of ['RAWH 0', 'RAWH 2048', 'RAWH 0', 'RAWG 0', 'RAWG 2048', 'RAWG 0']) {
          const r = await rig.cmd(c);
          if (!r.startsWith('OK')) { log(`${c} -> ${r} (DAC stopped ACKing mid-write)`); return false; }
        }
        log('both DACs ACK raw writes (voltages verified in the self-test step)');
        const r = await rig.cmd('SAVEZERO');
        log(`SAVEZERO (cold power-up = 0 V instead of factory 2.5 V): ${r}`);
        return r.startsWith('OK');
      },
    },
    {
      id: 'selftest', name: 'No-DUT electrical self-test',
      action: 'Make sure the DUT socket is EMPTY (no MOSFET, no resistor).',
      async run(log) {
        await rig.ok('LOWIO 0'); await rig.ok('SETG 0.000'); await rig.ok('SETH 0.000');
        await sleep(300);
        const grid = vrange(0, 5, 0.25);
        const dataH = [], dataG = [];
        for (const h of grid) {
          const code = await rig.setVolts('H', h);
          await sleep(30);
          const pt = await point(rig, p, 0.0);
          pt.eff = effVolts(code, pt.vdd);
          dataH.push(pt);
        }
        await rig.ok('SETH 0.000');
        for (const g of grid) {
          const code = await rig.setVolts('G', g);
          await sleep(Math.max(p.gateSettleMs, 100));
          const pt = await point(rig, p, g);
          pt.eff = effVolts(code, pt.vdd);
          dataG.push(pt);
        }
        await rig.ok('SETG 0.000');
        await sleep(Math.max(p.gateSettleMs, 100));
        await rig.ok('LOWIO 1');
        await sleep(100);
        const pHi = await point(rig, p, 0.0);
        await rig.ok('LOWIO 0');

        const worst = (vals) => vals.reduce((a, b) => Math.abs(b) > Math.abs(a) ? b : a, 0);
        const errH = dataH.map((d) => d.vhigh - d.eff);
        const errG = dataG.map((d) => d.vgate - d.eff);
        const wh = worst(errH), wg = worst(errG);
        const idsW = worst(dataH.map((d) => d.idsUa));
        const igsW = worst(dataG.map((d) => d.igsUa));
        const low0 = Math.max(...dataH.map((d) => Math.abs(d.vlow)));
        const dhi = pHi.vlow - pHi.vdd;

        const checks = [
          ['A2 tracks DAC_H', Math.abs(wh) < 0.040, `worst ${(wh * 1000).toFixed(1)} mV (limit 40)`],
          ['A1 tracks DAC_G thru 1 M', Math.abs(wg) < 0.060, `worst ${(wg * 1000).toFixed(1)} mV (limit 60)`],
          ['Ids zero floor', Math.abs(idsW) < 5.0, `worst ${idsW.toFixed(2)} uA (limit 5)`],
          ['Igs zero floor', Math.abs(igsW) < 0.10, `worst ${igsW.toFixed(3)} uA (limit 0.10)`],
          ['Low rail LOWIO=0', low0 < 0.020, `max ${(low0 * 1000).toFixed(1)} mV (limit 20)`],
          ['Low rail LOWIO=1', Math.abs(dhi) < 0.060 && Math.abs(pHi.idsUa) < 5.0,
            `delta ${(dhi * 1000).toFixed(1)} mV, Ids ${pHi.idsUa.toFixed(2)} uA`],
        ];
        let ok = true;
        for (const [name, pass, detail] of checks) {
          log(`${pass ? 'PASS' : 'FAIL'}  ${name}: ${detail}`);
          ok = ok && pass;
        }
        if (Math.abs(wh) > 0.5 || Math.abs(wg) > 0.5) {
          log('HINT: a sense pinned at 0 V regardless of command often means that node is cut ' +
              'off from its DAC (swapped SCL wires? open resistor?).');
        }
        log('note: ratiometric test - absolute scale comes from the calibration step');
        return ok;
      },
    },
    {
      id: 'jumpHL', name: 'Jumper High-Low: current loop vs Ohm’s law',
      action: 'Jumper wire from socket HIGH pin to socket LOW pin.',
      async run(log) {
        await rig.ok('LOWIO 0'); await rig.ok('SETG 0.000');
        const rtot = p.rhigh + p.rlow;
        let worstPct = 0, worstGap = 0, lastEff = 0, lastIds = 0;
        for (const h of [0.5, 1, 2, 3, 4, 5]) {
          const code = await rig.setVolts('H', h);
          await sleep(50);
          const pt = await point(rig, p, 0.0);
          const eff = effVolts(code, pt.vdd);
          const pred = (eff / rtot) * 1e6;
          const pct = pred ? ((pt.idsUa - pred) / pred) * 100 : 0;
          const gap = Math.abs(pt.vhigh - pt.vlow);
          log(`H=${h.toFixed(2)} V: Ids ${pt.idsUa.toFixed(1)} uA vs Ohm ${pred.toFixed(1)} uA ` +
              `(${pct >= 0 ? '+' : ''}${pct.toFixed(1)} %), |A2-A0| ${(gap * 1000).toFixed(1)} mV`);
          worstPct = Math.max(worstPct, Math.abs(pct));
          worstGap = Math.max(worstGap, gap);
          lastEff = eff; lastIds = pt.idsUa;
        }
        await rig.ok('SETH 0.000');
        let ok = true;
        if (worstPct > 10) {
          log(`current off by ${worstPct.toFixed(1)} % (limit 10 %)`);
          if (lastIds > 0.5) {
            const rLoop = lastEff / (lastIds * 1e-6);
            log(`back-computed loop ~${(rLoop / 1000).toFixed(2)} kohm (expected ` +
                `${(rtot / 1000).toFixed(2)}) -> series element on High ~` +
                `${((rLoop - p.rlow) / 1000).toFixed(2)} kohm. Classic cause: 100 kohm in the ` +
                `100 ohm spot (brown-black-YELLOW vs BROWN).`);
          } else {
            log('almost no current: open loop - jumper seated? R_low reaching D3?');
          }
          ok = false;
        }
        if (worstGap > 0.040) {
          log(`A2 vs A0 disagree ${(worstGap * 1000).toFixed(0)} mV on the SAME node (limit 40)`);
          ok = false;
        }
        return ok;
      },
    },
    {
      id: 'jumpGL', name: 'Jumper Gate-Low: gate-current scale',
      action: 'Move the jumper: socket GATE pin to socket LOW pin.',
      async run(log) {
        await rig.ok('LOWIO 0'); await rig.ok('SETH 0.000');
        let worstVg = 0, last = null;
        for (const g of [1, 2, 3, 4, 5]) {
          const code = await rig.setVolts('G', g);
          await sleep(150);
          const pt = await point(rig, p, g);
          const eff = effVolts(code, pt.vdd);
          const igsEff = ((eff - pt.vgate) / p.rgate) * 1e6;
          log(`G=${g.toFixed(2)} V: Igs ${igsEff.toFixed(3)} uA (expect ` +
              `${((eff / p.rgate) * 1e6).toFixed(3)}), Vgate ${(pt.vgate * 1000).toFixed(1)} mV (~0)`);
          worstVg = Math.max(worstVg, Math.abs(pt.vgate));
          last = { ids: pt.idsUa, igs: igsEff };
        }
        await rig.ok('SETG 0.000');
        if (last) log(`cross-check at G=5: Ids via R_low ${last.ids.toFixed(2)} uA vs Igs ` +
                      `${last.igs.toFixed(2)} uA (same current; Ids floor ~0.3 uA)`);
        if (worstVg > 0.040) {
          log(`gate node not clamped (worst ${(worstVg * 1000).toFixed(0)} mV, limit 40): ` +
              'jumper seated? A1 on the right pin?');
          return false;
        }
        return true;
      },
    },
    {
      id: 'jumpGH', name: 'Jumper Gate-High: A1/A2 sense agreement',
      action: 'Move the jumper: socket GATE pin to socket HIGH pin.',
      async run(log) {
        await rig.ok('LOWIO 0'); await rig.ok('SETG 0.000');
        let worst = 0;
        for (const h of [1, 2.5, 4]) {
          await rig.setVolts('H', h);
          await sleep(100);
          const pt = await point(rig, p, 0.0);
          const gap = Math.abs(pt.vgate - pt.vhigh);
          log(`H=${h.toFixed(2)} V: A1 ${pt.vgate.toFixed(3)} V vs A2 ${pt.vhigh.toFixed(3)} V ` +
              `(gap ${(gap * 1000).toFixed(1)} mV)`);
          worst = Math.max(worst, gap);
        }
        await rig.ok('SETH 0.000');
        if (worst > 0.040) { log(`A1/A2 disagree ${(worst * 1000).toFixed(0)} mV (limit 40)`); return false; }
        return true;
      },
    },
    {
      id: 'cal', name: 'Absolute calibration (DMM)',
      action: 'Remove the jumper. Then measure the Uno 5V pin with a DMM and enter the reading below.',
      input: { label: 'DMM reading of the 5V pin (volts)', placeholder: 'e.g. 4.93' },
      async run(log, value) {
        const volts = parseFloat(value);
        if (!(volts >= 4.0 && volts <= 5.6)) {
          log('that does not look like a 5V-rail reading in volts');
          return false;
        }
        const old = await rig.calbgGet();
        const fwV = await rig.vddV();
        const next = Math.round(old * (volts / fwV));
        log(`firmware thinks VDD=${fwV.toFixed(3)} V with CALBG=${old} mV; DMM says ${volts.toFixed(3)} V`);
        const r = await rig.cmd(`CALBG ${next}`);
        log(`CALBG ${next} -> ${r}`);
        if (!r.startsWith('OK')) return false;
        rig.vrefIntV = next / 1000.0;
        const check = await rig.vddV();
        log(`firmware now reports VDD=${check.toFixed(3)} V ` +
            `(error ${(Math.abs(check - volts) * 1000).toFixed(0)} mV)`);
        return Math.abs(check - volts) < 0.05;
      },
    },
  ];
}
