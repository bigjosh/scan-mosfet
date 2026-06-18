// Rig: line protocol driver on top of a transport. One command in flight at a
// time; every command gets exactly one reply line (firmware contract).

export class Rig {
  constructor(transport) {
    this.t = transport;
    this.connected = false;
    this.banner = '';
    this.hasH = false;
    this.hasG = false;
    this.vrefIntV = 1.1;
    this.onDisconnect = () => {};
    this.onProgress = () => {};   // connect-sequence status for the UI
    this._buf = '';
    this._waiters = [];   // {resolve, reject, timer}
    this._chain = Promise.resolve();
    this._dec = new TextDecoder();
    this._enc = new TextEncoder();
  }

  async open() {
    this.t.onData((bytes) => this._feed(bytes));
    this.t.onClose(() => {
      this.connected = false;
      this._failAll(new Error('device disconnected'));
      this.onDisconnect();
    });
    if (this.t.onProgress) this.t.onProgress((m) => this._progress(m));

    this._progress('opening link...');
    await this.t.connect();          // picker / permission dialog lives in here
    // Only start the banner clock now. On Android the USB permission dialog
    // runs *inside* connect(); the old code armed the timeout before connect()
    // so dialog time expired the window and the banner was reported as a
    // "timeout waiting for reply". The board is also mid-bootloader for ~1-2 s
    // after the DTR reset connect() triggers, so there is nothing to miss yet.
    this.banner = await this._handshake();
    this._progress('board ready');

    // settle, then nudge the firmware's line buffer clear of any partial command
    await new Promise((r) => setTimeout(r, 120));
    await this.t.write(this._enc.encode('\n'));
    await new Promise((r) => setTimeout(r, 150));
    this._buf = '';                  // discard the nudge reply + any extra banners
    this._parseBanner(this.banner);
    this.connected = true;
  }

  // Obtain the boot banner robustly. The board normally auto-resets on connect
  // (DTR) and prints the banner ~1-2 s later from setup(); wait for that first.
  // If it never arrives (auto-reset didn't fire, or it was missed), actively
  // poll IDN? -- the firmware answers IDN? with the same banner line. This is
  // immune both to how long the permission dialog took and to a missed reset.
  async _handshake() {
    this._progress('waiting for board banner...');
    try {
      return await this._expectLine((l) => l.startsWith('ArduinoMosfetScanner'), 3000);
    } catch (e) { /* no spontaneous banner; ask for it explicitly */ }
    for (let i = 1; i <= 5; i++) {
      this._progress(`no banner yet, polling IDN? (${i}/5)...`);
      const p = this._expectLine((l) => l.startsWith('ArduinoMosfetScanner'), 1500);
      try {
        await this.t.write(this._enc.encode('IDN?\n'));
        return await p;
      } catch (e) { /* board may still be booting; retry */ }
    }
    throw new Error('no banner from board - it never replied to IDN? (check cable/baud)');
  }

  _progress(msg) {
    try { this.onProgress(msg); } catch (e) { /* ignore */ }
  }

  _parseBanner(b) {
    this.hasH = /DACH=0x/i.test(b);
    this.hasG = /DACG=0x/i.test(b);
    const m = b.match(/VREFINT_MV=(\d+)/);
    if (m) this.vrefIntV = parseInt(m[1]) / 1000.0;
  }

  _feed(bytes) {
    this._buf += this._dec.decode(bytes);
    let i;
    while ((i = this._buf.search(/[\r\n]/)) >= 0) {
      const line = this._buf.slice(0, i).replace(/[^\x20-\x7e]/g, '').trim();
      this._buf = this._buf.slice(i + 1);
      if (line) this._dispatch(line);
    }
  }

  _dispatch(line) {
    const w = this._waiters.shift();
    if (w) {
      clearTimeout(w.timer);
      if (w.match && !w.match(line)) {
        // unexpected line while waiting (e.g. stray banner) - keep waiting
        this._waiters.unshift(w);
        return;
      }
      w.resolve(line);
    }
    // unsolicited lines (none expected in normal operation) are dropped
  }

  _expectLine(match, timeoutMs) {
    return new Promise((resolve, reject) => {
      const w = { resolve, reject, match };
      w.timer = setTimeout(() => {
        const idx = this._waiters.indexOf(w);
        if (idx >= 0) this._waiters.splice(idx, 1);
        reject(new Error('timeout waiting for reply'));
      }, timeoutMs);
      this._waiters.push(w);
    });
  }

  _failAll(err) {
    for (const w of this._waiters.splice(0)) {
      clearTimeout(w.timer);
      w.reject(err);
    }
  }

  // Serialized command -> one reply line.
  cmd(line, timeoutMs = 3000) {
    const run = async () => {
      if (!this.connected) throw new Error('not connected');
      const p = this._expectLine(null, timeoutMs);
      await this.t.write(this._enc.encode(line + '\n'));
      return p;
    };
    const p = this._chain.then(run, run);
    this._chain = p.catch(() => {});
    return p;
  }

  async ok(line, timeoutMs) {
    const r = await this.cmd(line, timeoutMs);
    if (!r.startsWith('OK')) throw new Error(`${line} -> ${r}`);
    return r;
  }

  // SETH/SETG returning the actual DAC code used.
  async setVolts(which, volts) {
    const r = await this.ok(`SET${which} ${volts.toFixed(3)}`);
    const m = r.match(/CODE=(\d+)/);
    return m ? parseInt(m[1]) : 0;
  }

  async meas() {
    const r = await this.cmd('MEAS?', 5000);
    const d = {};
    for (const tok of r.split(/\s+/)) {
      const [k, v] = tok.split('=');
      if (k && v !== undefined && !Number.isNaN(parseFloat(v))) d[k] = parseFloat(v);
    }
    if (!('VDD_MV' in d) || !('A0_1V1' in d)) throw new Error(`bad MEAS? reply: ${r}`);
    return d;
  }

  async pintest(holdSec = 0) {
    const r = await this.cmd(holdSec > 0 ? `PINTEST ${holdSec}` : 'PINTEST', (holdSec + 8) * 1000);
    const vals = {};
    for (const m of r.matchAll(/([A-Z0-9_]+)=(\d+)/g)) vals[m[1]] = parseInt(m[2]);
    return { raw: r, vals };
  }

  async rescan() {
    const b = await this.cmd('RESCAN');
    this.banner = b;
    this._parseBanner(b);
    return b;
  }

  async vddV() {
    const r = await this.cmd('VDD?');
    const m = r.match(/VDD_MV=([\d.]+)/);
    if (!m) throw new Error(`bad VDD? reply: ${r}`);
    return parseFloat(m[1]) / 1000.0;
  }

  async calbgGet() {
    const r = await this.cmd('CALBG?');
    const m = r.match(/CALBG_MV=(\d+)/);
    return m ? parseInt(m[1]) : null;
  }

  async close() {
    this.connected = false;
    try { await this.t.close(); } catch (e) { /* ignore */ }
  }
}
