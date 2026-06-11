// MockTransport: simulates the firmware + a synthetic N-channel MOSFET so the
// whole app runs with no hardware ("demo mode") and stays testable in CI/dev.
// Mirrors firmware replies byte-for-byte (banner, MEAS?, PINTEST, ...).

const VDD_TRUE = 4.93;       // actual rail volts in the simulated rig
const VREF_TRUE = 1.085;     // actual bandgap volts
const R_HIGH = 100, R_LOW = 1000, R_GATE = 1e6;
const RON_SINK = 25, RON_SRC = 40;   // GPIO output resistance
const VTH = 1.55, KFET = 0.004, LAMBDA = 0.02;  // synthetic DUT
const ADC_LEAK_A = 25e-9;    // A1 pin leakage through R_gate

function fetCurrent(vgs, vds) {
  // Quadratic model + body diode, current defined High -> Low.
  let i = 0;
  if (vds >= 0) {
    const vov = vgs - VTH;
    if (vov > 0) {
      i = vds < vov ? KFET * (vov - vds / 2) * vds : (KFET / 2) * vov * vov * (1 + LAMBDA * vds);
    }
    i += vds * 1e-9;  // tiny off-leakage
  } else {
    // third quadrant: body diode plus (if gate on) reverse channel
    const vd = -vds;
    i = -1e-12 * (Math.exp(vd / 0.052) - 1);
    if (i < -0.1) i = -0.1;
    const vov = vgs - VTH;  // source/drain roles swap, crude but fine for demo
    if (vov > 0) i -= KFET * vov * vd * 0.8;
  }
  return i;
}

export class MockTransport {
  get label() { return 'Demo (simulated rig)'; }

  constructor() {
    this._onData = () => {};
    this._onClose = () => {};
    this.codeH = 0;
    this.codeG = 0;
    this.lowio = '0';
    this.avg = 32;
    this.vrefIntMv = 1085;
    this._rx = '';
    // what's "in the socket": fet | none | jumpHL | jumpGL | jumpGH
    // (the app sets this during demo bring-up so every wizard step is realistic)
    this.bench = 'fet';
  }

  onData(cb) { this._onData = cb; }
  onClose(cb) { this._onClose = cb; }

  async connect() {
    setTimeout(() => this._send('ArduinoMosfetScanner v1 DACH=0x60 DACG=0x60 VREFINT_MV=' +
      this.vrefIntMv + '\r\n'), 250);
  }

  _send(text) {
    this._onData(new TextEncoder().encode(text));
  }

  async write(bytes) {
    this._rx += new TextDecoder().decode(bytes);
    let i;
    while ((i = this._rx.search(/[\r\n]/)) >= 0) {
      const line = this._rx.slice(0, i).trim();
      this._rx = this._rx.slice(i + 1);
      if (line) this._handle(line.toUpperCase());
    }
  }

  async close() {}

  _noise(lsb = 0.4) { return (Math.random() - 0.5) * 2 * lsb; }

  _banner() {
    return `ArduinoMosfetScanner v1 DACH=0x60 DACG=0x60 VREFINT_MV=${this.vrefIntMv}`;
  }

  _vddReportedMv() {
    return (VDD_TRUE * 1000) * (this.vrefIntMv / (VREF_TRUE * 1000)) + this._noise(1.5);
  }

  // Solve the bench for node voltages given what's in the socket.
  _solve() {
    const effH = (this.codeH / 4096) * VDD_TRUE;
    const effG = (this.codeG / 4096) * VDD_TRUE;
    const leakGate = effG - ADC_LEAK_A * R_GATE;  // leak sag, like the real rig
    const pinV = (i) => this.lowio === '1' ? VDD_TRUE + i * RON_SRC : i * RON_SINK;
    if (this.lowio === 'Z') {
      return { vhigh: effH, vlow: effH, vlowio: effH, vgate: leakGate };
    }

    if (this.bench === 'none') {  // empty socket: no current anywhere
      const pin = pinV(0);
      return { vhigh: effH, vlow: pin, vlowio: pin, vgate: leakGate };
    }
    if (this.bench === 'jumpHL') {  // High pin shorted to Low node
      const rtot = R_HIGH + R_LOW + (this.lowio === '1' ? RON_SRC : RON_SINK);
      const i = (effH - (this.lowio === '1' ? VDD_TRUE : 0)) / rtot;
      const node = effH - i * R_HIGH;
      return { vhigh: node, vlow: node, vlowio: pinV(i), vgate: leakGate };
    }
    if (this.bench === 'jumpGL') {  // Gate node shorted to Low node
      // gate current returns through R_low; solve linear loop
      const ron = this.lowio === '1' ? RON_SRC : RON_SINK;
      const vsrc = this.lowio === '1' ? VDD_TRUE : 0;
      const i = (effG - vsrc) / (R_GATE + R_LOW + ron);
      const node = vsrc + i * (R_LOW + ron);
      return { vhigh: effH, vlow: node, vlowio: pinV(i), vgate: node };
    }
    if (this.bench === 'jumpGH') {  // Gate node shorted to High pin
      const i = (effH - effG) / (R_HIGH + R_GATE);
      const node = effH - i * R_HIGH;
      const pin = pinV(0);
      return { vhigh: node, vlow: pin, vlowio: pin, vgate: node };
    }

    // 'fet': bisection on channel current I (High->Low)
    const resid = (i) => {
      const vlow = pinV(i) + i * R_LOW;
      const vhigh = effH - i * R_HIGH;
      return fetCurrent(leakGate - vlow, vhigh - vlow) - i;
    };
    let lo = -0.006, hi = 0.006;
    for (let k = 0; k < 60; k++) {
      const mid = (lo + hi) / 2;
      if (resid(lo) * resid(mid) <= 0) hi = mid; else lo = mid;
    }
    const i = (lo + hi) / 2;
    return {
      vhigh: effH - i * R_HIGH,
      vlow: pinV(i) + i * R_LOW,
      vlowio: pinV(i),
      vgate: leakGate,
    };
  }

  _counts(v) {
    const c11 = Math.max(0, Math.min(1023, (v / VREF_TRUE) * 1024 + this._noise()));
    const c5 = Math.max(0, Math.min(1023, (v / VDD_TRUE) * 1024 + this._noise()));
    return { c11, c5 };
  }

  async _handle(cmd) {
    const [op, arg] = cmd.split(/\s+/);
    const reply = (s) => this._send(s + '\r\n');
    switch (op) {
      case 'IDN?': return reply(this._banner());
      case 'SETH': case 'SETG': {
        let v = Math.max(0, Math.min(5, parseFloat(arg) || 0));
        const vddMv = this._vddReportedMv();
        let code = Math.round((v * 1000 * 4096) / vddMv);
        code = Math.min(4095, Math.max(0, code));
        if (op === 'SETH') this.codeH = code; else this.codeG = code;
        return reply(`OK CODE=${code} VDD_MV=${vddMv.toFixed(1)}`);
      }
      case 'RAWH': this.codeH = Math.min(4095, parseInt(arg) || 0); return reply('OK');
      case 'RAWG': this.codeG = Math.min(4095, parseInt(arg) || 0); return reply('OK');
      case 'LOWIO':
        if (!'01Z'.includes(arg)) return reply('ERR arg 0|1|Z');
        this.lowio = arg; return reply('OK');
      case 'AVG': this.avg = Math.min(200, Math.max(1, parseInt(arg) || 32)); return reply('OK');
      case 'CALBG':
        this.vrefIntMv = parseInt(arg);
        return reply(`OK CALBG_MV=${this.vrefIntMv} VDD_MV=${this._vddReportedMv().toFixed(1)}`);
      case 'CALBG?': return reply(`CALBG_MV=${this.vrefIntMv}`);
      case 'VDD?': return reply(`VDD_MV=${this._vddReportedMv().toFixed(1)}`);
      case 'SAVEZERO': return reply('OK');
      case 'RESCAN': return reply(this._banner());
      case 'SCAN?': return reply('B1(SDA4/SCL5)=0x60 B2(SDA4/SCL6)=0x60');
      case 'PINTEST':
        return reply('OK PINTEST SDA_IDLE=1 SDA_EXTPU=1 SDA_5VSHORT=0 SCL1_IDLE=1 SCL1_EXTPU=1 ' +
          'SCL1_5VSHORT=0 SCL2_IDLE=1 SCL2_EXTPU=1 SCL2_5VSHORT=0 BRIDGED=0 ' + this._banner());
      case 'MEAS?': {
        // realistic-ish pacing, but never fight background-tab timer throttling
        if (!document.hidden) await new Promise((r) => setTimeout(r, 15 + this.avg));
        const n = this._solve();
        const a0 = this._counts(n.vlow), a1 = this._counts(n.vgate);
        const a2 = this._counts(n.vhigh), a3 = this._counts(n.vlowio);
        const fx = (c) => c.toFixed(2);
        return reply(
          `VDD_MV=${this._vddReportedMv().toFixed(1)} ` +
          `A0_1V1=${fx(a0.c11)} A1_1V1=${fx(a1.c11)} A2_1V1=${fx(a2.c11)} A3_1V1=${fx(a3.c11)} ` +
          `A0_5V=${fx(a0.c5)} A1_5V=${fx(a1.c5)} A2_5V=${fx(a2.c5)} A3_5V=${fx(a3.c5)}`);
      }
      default: return reply(`ERR unknown cmd '${op}'`);
    }
  }
}
