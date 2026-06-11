// App controller: connect/UI/scan/history/bring-up wiring.

import { pickTransport, WebSerialTransport, WebUsbCdcTransport } from './transport.js';
import { MockTransport } from './mock.js';
import { Rig } from './protocol.js';
import { DEFAULT_PARAMS, AbortFlag, runCycle, estimateSeconds } from './scan.js';
import { MirrorChart, LeakBars } from './chart.js';
import { buildSteps } from './bringup.js';
import { timestamp, sanitizeName } from './convert.js';
import * as store from './store.js';

const $ = (id) => document.getElementById(id);
const R_HIGH = 100;  // fitted series resistor on High (bring-up checks use it)

let rig = null;
let scanning = false;
let abortFlag = null;
let wakeLock = null;

// ---------------------------------------------------------------- charts

const charts = {
  cv: new MirrorChart($('chart-cv')),
  p1: new LeakBars($('chart-p1')),
};
const hvCharts = {
  cv: new MirrorChart($('hv-chart-cv')),
  p1: new LeakBars($('hv-chart-p1')),
};

function showChart(which) {
  for (const k of ['cv', 'p1']) {
    $(`wrap-${k}`).hidden = k !== which;
    document.querySelector(`#chart-tabs [data-chart="${k}"]`).classList.toggle('active', k === which);
  }
  charts[which].requestDraw?.();
}
$('chart-tabs').addEventListener('click', (e) => {
  const b = e.target.closest('[data-chart]');
  if (b) showChart(b.dataset.chart);
});

// ---------------------------------------------------------------- screens

function showScreen(name) {
  for (const s of ['scan', 'bringup', 'history']) {
    $(`screen-${s}`).hidden = s !== name;
    document.querySelector(`#nav [data-screen="${s}"]`).classList.toggle('active', s === name);
  }
  if (name === 'history') renderHistoryList();
}
$('nav').addEventListener('click', (e) => {
  const b = e.target.closest('[data-screen]');
  if (b) showScreen(b.dataset.screen);
});

// ---------------------------------------------------------------- params

const NUM_FIELDS = ['hStart', 'hStop', 'hStep', 'gStart', 'gStop', 'gStep',
  'avg', 'settleMs', 'gateSettleMs', 'rlow', 'rgate'];

function paramsToForm(p) {
  for (const f of NUM_FIELDS) $(`p-${f}`).value = p[f];
  $('p-p1').checked = p.phases.p1;
  $('p-p2').checked = p.phases.p2;
  $('p-p3').checked = p.phases.p3;
}

function paramsFromForm() {
  const p = { phases: {} };
  for (const f of NUM_FIELDS) {
    const v = parseFloat($(`p-${f}`).value);
    p[f] = Number.isFinite(v) ? v : DEFAULT_PARAMS[f];
  }
  p.hStep = Math.max(0.01, p.hStep);
  p.gStep = Math.max(0.01, p.gStep);
  p.avg = Math.min(200, Math.max(1, Math.round(p.avg)));
  p.phases.p1 = $('p-p1').checked;
  p.phases.p2 = $('p-p2').checked;
  p.phases.p3 = $('p-p3').checked;
  return p;
}

function updateEstimate() {
  const p = paramsFromForm();
  const s = estimateSeconds(p);
  $('scan-est').textContent = s > 0 ? `~${(s / 60).toFixed(1)} min` : '';
  store.saveParams(p);
}
paramsToForm(store.loadParams(DEFAULT_PARAMS));
updateEstimate();
document.querySelector('.params').addEventListener('input', updateEstimate);

// ---------------------------------------------------------------- connect

function setConnChip(text, cls) {
  const chip = $('conn-chip');
  chip.textContent = text;
  chip.className = `chip ${cls}`;
}

function setConnectedUi(connected) {
  $('btn-scan').disabled = !connected || scanning;
  $('btn-bringup').disabled = !connected;
  $('btn-connect').textContent = connected ? 'Disconnect' : 'Connect';
}

async function connectWith(transport) {
  try {
    setConnChip('connecting...', 'off');
    const r = new Rig(transport);
    await r.open();
    rig = r;
    rig.onDisconnect = () => {
      setConnChip('disconnected', 'off');
      setConnectedUi(false);
      rig = null;
      if (abortFlag) abortFlag.abort();
    };
    const dacs = `${rig.hasH ? 'H' : '-'}${rig.hasG ? 'G' : '-'}`;
    const demo = transport instanceof MockTransport;
    setConnChip(`${demo ? 'DEMO' : transport.label} · DAC ${dacs} · bg ${Math.round(rig.vrefIntV * 1000)} mV`,
      demo ? 'demo' : 'on');
    setConnectedUi(true);
    if (!rig.hasH || !rig.hasG) {
      $('scan-status').textContent =
        'Warning: firmware did not find both DACs - run Bring-up / check wiring.';
    }
  } catch (e) {
    rig = null;
    setConnectedUi(false);
    if (e && (e.name === 'NotFoundError' || /No port selected|cancelled/i.test(String(e)))) {
      setConnChip('disconnected', 'off');  // user closed the picker
    } else {
      setConnChip('connect failed', 'off');
      $('scan-status').textContent = `Connect failed: ${e.message || e}`;
    }
  }
}

$('btn-connect').addEventListener('click', async () => {
  if (rig) {
    const r = rig;
    rig = null;
    r.onDisconnect = () => {};
    await r.close();
    setConnChip('disconnected', 'off');
    setConnectedUi(false);
    return;
  }
  const t = pickTransport();
  if (!t) {
    $('scan-status').textContent =
      'This browser has neither Web Serial nor WebUSB. Use Chrome (Android or desktop).';
    return;
  }
  await connectWith(t);
});

$('btn-demo').addEventListener('click', async () => {
  if (rig) { await rig.close(); rig = null; }
  await connectWith(new MockTransport());
});

// ---------------------------------------------------------------- scanning

async function acquireWakeLock() {
  try { wakeLock = await navigator.wakeLock?.request('screen'); } catch (e) { /* fine */ }
}
function releaseWakeLock() {
  try { wakeLock?.release(); } catch (e) { /* fine */ }
  wakeLock = null;
}
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && scanning) acquireWakeLock();
});

function countPoints(p) {
  const nH = Math.round((p.hStop - p.hStart) / p.hStep) + 1;
  const nG = Math.round((p.gStop - p.gStart) / p.gStep) + 1;
  return ((p.phases.p2 ? 1 : 0) + (p.phases.p3 ? 1 : 0)) * nH * nG + (p.phases.p1 ? 2 : 0);
}

$('btn-scan').addEventListener('click', async () => {
  if (!rig || scanning) return;
  const p = paramsFromForm();
  if (!p.phases.p1 && !p.phases.p2 && !p.phases.p3) {
    $('scan-status').textContent = 'Pick at least one phase.';
    return;
  }
  scanning = true;
  abortFlag = new AbortFlag();
  $('btn-scan').disabled = true;
  $('btn-abort').hidden = false;
  await acquireWakeLock();

  const name = sanitizeName($('p-name').value);
  const base = `${name}-${timestamp()}`;
  const record = {
    id: base, name, fileBase: base, when: Date.now(),
    params: p, banner: rig.banner, vrefIntMv: Math.round(rig.vrefIntV * 1000),
  };
  const total = countPoints(p);
  let done = 0;
  const bar = $('progress-bar');
  bar.style.width = '0%';
  $('leak-summary').textContent = '';
  charts.p1.set(0, 0);
  charts.cv.reset(p.gStart - 5, p.gStop);  // color span covers both halves

  const cb = {
    phaseStart(phase) {
      $('scan-status').textContent = `Running phase ${phase}...`;
      showChart(phase === 1 ? 'p1' : 'cv');
    },
    leak(mode, pt) {
      record.phase1 = record.phase1 || {};
      record.phase1[mode === 'forward' ? 'fwd' : 'rev'] = pt;
      const p1 = record.phase1;
      if (p1.fwd && p1.rev) {
        charts.p1.set(p1.fwd.igsUa, p1.rev.igsUa);
        $('leak-summary').textContent =
          `forward Igs ${p1.fwd.igsUa.toFixed(3)} uA (Vgate ${p1.fwd.vgate.toFixed(3)} V) · ` +
          `reverse Igs ${p1.rev.igsUa.toFixed(3)} uA · clean if well under ~1 uA`;
      }
      done++; bar.style.width = `${(100 * done) / total}%`;
    },
    curveStart(phase, vgs) {
      charts.cv.startCurve(phase === 2 ? 'top' : 'bottom', vgs);
    },
    point(phase, vgsCmd, vdsCmd, pt) {
      const key = `phase${phase}`;
      record[key] = record[key] || { rows: [] };
      record[key].rows.push({ vdsCmd, vgsCmd, pt });
      charts.cv.addPoint(phase === 2 ? 'top' : 'bottom', Math.abs(pt.vds), Math.abs(pt.idsUa));
      done++;
      if (done % 3 === 0 || done === total) bar.style.width = `${(100 * done) / total}%`;
    },
  };

  let outcome = '';
  try {
    await runCycle(rig, p, abortFlag, cb);
    outcome = `Done: ${base}`;
  } catch (e) {
    outcome = abortFlag.aborted ? `Aborted (rig zeroed): ${base}` : `Error: ${e.message || e}`;
  } finally {
    scanning = false;
    $('btn-abort').hidden = true;
    $('btn-scan').disabled = !rig;
    releaseWakeLock();
  }
  if (record.phase1 || record.phase2 || record.phase3) {
    try {
      await store.saveScan(record);
      outcome += ' · saved to History';
    } catch (e) {
      outcome += ` · SAVE FAILED: ${e.message || e}`;
    }
  }
  $('scan-status').textContent = outcome;
});

$('btn-abort').addEventListener('click', () => {
  abortFlag?.abort();
  $('scan-status').textContent = 'Aborting...';
});

// ---------------------------------------------------------------- history

function rowsToCurves(rows) {
  // magnitudes for the mirror chart: x = |Vds_meas|, y = |Ids|
  const byVgs = new Map();
  for (const r of rows) {
    if (!byVgs.has(r.vgsCmd)) byVgs.set(r.vgsCmd, { vgs: r.vgsCmd, xs: [], ys: [] });
    const c = byVgs.get(r.vgsCmd);
    c.xs.push(Math.abs(r.pt.vds));
    c.ys.push(Math.abs(r.pt.idsUa));
  }
  return [...byVgs.values()];
}

async function renderHistoryList() {
  const list = $('history-list');
  const scans = await store.listScans();
  $('history-view').hidden = true;
  $('history-list-panel').hidden = false;
  if (!scans.length) {
    list.innerHTML = '<p class="dim">No scans yet - run one from the Scan tab.</p>';
    return;
  }
  list.innerHTML = '';
  for (const s of scans) {
    const row = document.createElement('div');
    row.className = 'scanrow';
    const phases = s.phases.map((x) => x.replace('phase', 'P')).join(' ');
    row.innerHTML =
      `<div class="meta"><div class="name">${s.id}</div>` +
      `<div class="sub">${new Date(s.when).toLocaleString()} · ${phases} · ${s.points} pts</div></div>`;
    const view = document.createElement('button');
    view.textContent = 'View';
    view.onclick = () => renderHistoryView(s.id);
    const del = document.createElement('button');
    del.className = 'ghost';
    del.textContent = 'Delete';
    del.onclick = async () => {
      if (confirm(`Delete ${s.id}?`)) { await store.deleteScan(s.id); renderHistoryList(); }
    };
    row.append(view, del);
    list.append(row);
  }
}

async function renderHistoryView(id) {
  const rec = await store.getScan(id);
  if (!rec) return;
  $('history-list-panel').hidden = true;
  $('history-view').hidden = false;
  $('hv-title').textContent = rec.id;

  const dl = $('hv-downloads');
  dl.innerHTML = '';
  const addDl = (label, filename, text) => {
    const b = document.createElement('button');
    b.textContent = label;
    b.onclick = () => store.download(filename, text);
    dl.append(b);
  };
  if (rec.phase1) addDl('CSV phase 1', `${rec.fileBase}_phase1.csv`, store.phase1Csv(rec.phase1));
  if (rec.phase2) addDl('CSV phase 2', `${rec.fileBase}_phase2.csv`, store.sweepCsv(rec.phase2));
  if (rec.phase3) addDl('CSV phase 3', `${rec.fileBase}_phase3.csv`, store.sweepCsv(rec.phase3));

  $('hv-wrap-p1').hidden = !rec.phase1;
  if (rec.phase1) {
    hvCharts.p1.set(rec.phase1.fwd.igsUa, rec.phase1.rev.igsUa);
    $('hv-leak').textContent =
      `forward Igs ${rec.phase1.fwd.igsUa.toFixed(3)} uA · reverse ${rec.phase1.rev.igsUa.toFixed(3)} uA`;
  }
  const prm = rec.params || DEFAULT_PARAMS;
  const hasSweeps = !!(rec.phase2 || rec.phase3);
  $('hv-wrap-cv').hidden = !hasSweeps;
  if (hasSweeps) {
    hvCharts.cv.setData(
      rec.phase2 ? rowsToCurves(rec.phase2.rows) : [],
      rec.phase3 ? rowsToCurves(rec.phase3.rows) : [],
      prm.gStart - 5, prm.gStop);
  }
}

$('btn-hv-back').addEventListener('click', () => renderHistoryList());

// ---------------------------------------------------------------- bring-up

function awaitClick(parent, labels) {
  return new Promise((resolve) => {
    const row = document.createElement('div');
    row.className = 'runrow';
    for (const l of labels) {
      const b = document.createElement('button');
      b.textContent = l;
      if (l === 'Done' || l === 'Run' || l === 'Retry') b.className = 'primary';
      b.onclick = () => { row.remove(); resolve(l); };
      row.append(b);
    }
    parent.append(row);
  });
}

$('btn-bringup').addEventListener('click', async () => {
  if (!rig) return;
  const btn = $('btn-bringup');
  btn.disabled = true;
  const ol = $('bringup-steps');
  ol.innerHTML = '';
  $('bringup-summary').textContent = '';
  const p = { ...paramsFromForm(), rhigh: R_HIGH };
  const steps = buildSteps(rig, p);
  const results = [];
  // In demo mode, put the right thing "in the socket" for each step
  const mockBench = { selftest: 'none', jumpHL: 'jumpHL', jumpGL: 'jumpGL', jumpGH: 'jumpGH' };

  for (const step of steps) {
    if (rig?.t instanceof MockTransport) rig.t.bench = mockBench[step.id] || 'none';
    const li = document.createElement('li');
    li.innerHTML = `<div class="head"><span class="badge">...</span><span>${step.name}</span></div>`;
    ol.append(li);
    const badge = li.querySelector('.badge');
    const setBadge = (txt, cls) => { badge.textContent = txt; badge.className = `badge ${cls || ''}`; };

    let inputValue = null;
    if (step.action) {
      const box = document.createElement('div');
      box.className = 'action';
      box.textContent = step.action;
      li.append(box);
      let inputEl = null;
      if (step.input) {
        const lab = document.createElement('label');
        lab.className = 'row';
        lab.style.marginTop = '8px';
        lab.textContent = step.input.label;
        inputEl = document.createElement('input');
        inputEl.type = 'text';
        inputEl.inputMode = 'decimal';
        inputEl.placeholder = step.input.placeholder || '';
        lab.append(inputEl);
        box.append(lab);
      }
      const choice = await awaitClick(box, [step.input ? 'Run' : 'Done', 'Skip']);
      if (choice === 'Skip') {
        setBadge('SKIP', 'skip');
        results.push([step.name, 'SKIP']);
        continue;
      }
      inputValue = inputEl ? inputEl.value : null;
    }

    const pre = document.createElement('pre');
    li.append(pre);
    const log = (line) => { pre.textContent += line + '\n'; pre.scrollTop = pre.scrollHeight; };

    let status = '';
    for (;;) {
      setBadge('RUN', 'run');
      let ok = false;
      try {
        ok = await step.run(log, inputValue);
      } catch (e) {
        log(`error: ${e.message || e}`);
      }
      if (ok) { setBadge('PASS', 'pass'); status = 'PASS'; break; }
      setBadge('FAIL', 'fail');
      const choice = await awaitClick(li, ['Retry', 'Continue']);
      if (choice === 'Retry') { pre.textContent = ''; continue; }
      status = 'FAIL';
      break;
    }
    results.push([step.name, status]);
    if (!rig) break;  // disconnected mid-wizard
  }

  // leave the rig safe
  if (rig?.t instanceof MockTransport) rig.t.bench = 'fet';
  try {
    await rig?.cmd('LOWIO 0'); await rig?.cmd('SETH 0'); await rig?.cmd('SETG 0');
  } catch (e) { /* ignore */ }

  const fails = results.filter(([, s]) => s === 'FAIL').length;
  const skips = results.filter(([, s]) => s === 'SKIP').length;
  $('bringup-summary').textContent = fails
    ? `Not ready: ${fails} step(s) failed - fix and rerun.`
    : skips
      ? `No failures, ${skips} step(s) skipped. Rig looks good.`
      : 'Rig proven and calibrated.';
  btn.disabled = !rig;
});

// ---------------------------------------------------------------- boot

if ('serviceWorker' in navigator && location.protocol === 'https:') {
  navigator.serviceWorker.register('sw.js').catch(() => {});
}
if (new URLSearchParams(location.search).has('demo')) {
  connectWith(new MockTransport());
}
setConnectedUi(false);
window.__appBooted = true;  // boot-guard in index.html checks this
