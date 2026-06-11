// Persistence: IndexedDB scan history + localStorage params + CSV download.

import { SWEEP_HEADER, sweepRow, PHASE1_HEADER, phase1Row } from './convert.js';

const DB_NAME = 'mosfet-scanner';
const STORE = 'scans';

function db() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      const d = req.result;
      if (!d.objectStoreNames.contains(STORE)) {
        d.createObjectStore(STORE, { keyPath: 'id' }).createIndex('when', 'when');
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function tx(d, mode, fn) {
  return new Promise((resolve, reject) => {
    const t = d.transaction(STORE, mode);
    const out = fn(t.objectStore(STORE));
    t.oncomplete = () => resolve(out && 'result' in out ? out.result : undefined);
    t.onerror = () => reject(t.error);
  });
}

// record: {id, name, when, params, banner, phase1?, phase2?, phase3?}
// sweep phases stored as {rows: [{vdsCmd, vgsCmd, pt}]}
export async function saveScan(record) {
  const d = await db();
  await tx(d, 'readwrite', (s) => s.put(record));
}

export async function listScans() {
  const d = await db();
  const recs = await new Promise((resolve, reject) => {
    const out = [];
    const req = d.transaction(STORE).objectStore(STORE).openCursor();
    req.onsuccess = () => {
      const c = req.result;
      if (c) {
        const r = c.value;
        out.push({
          id: r.id, name: r.name, when: r.when, banner: r.banner,
          phases: ['phase1', 'phase2', 'phase3'].filter((k) => r[k]),
          points: (r.phase2?.rows?.length || 0) + (r.phase3?.rows?.length || 0),
        });
        c.continue();
      } else resolve(out);
    };
    req.onerror = () => reject(req.error);
  });
  return recs.sort((a, b) => b.when - a.when);
}

export async function getScan(id) {
  const d = await db();
  return new Promise((resolve, reject) => {
    const req = d.transaction(STORE).objectStore(STORE).get(id);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export async function deleteScan(id) {
  const d = await db();
  await tx(d, 'readwrite', (s) => s.delete(id));
}

// ---- CSV export (identical columns/filenames to scan_arduino.py) ----

export function sweepCsv(phaseData) {
  let out = SWEEP_HEADER;
  for (const r of phaseData.rows) out += sweepRow(r.vdsCmd, r.vgsCmd, r.pt);
  return out;
}

export function phase1Csv(p1) {
  return PHASE1_HEADER + phase1Row('forward', p1.fwd) + phase1Row('reverse', p1.rev);
}

export function download(filename, text) {
  const blob = new Blob([text], { type: 'text/csv' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

// ---- scan params in localStorage ----

const PARAMS_KEY = 'scanParams';

export function loadParams(defaults) {
  try {
    const saved = JSON.parse(localStorage.getItem(PARAMS_KEY));
    return { ...defaults, ...saved, phases: { ...defaults.phases, ...(saved?.phases || {}) } };
  } catch (e) {
    return { ...defaults };
  }
}

export function saveParams(params) {
  localStorage.setItem(PARAMS_KEY, JSON.stringify(params));
}
