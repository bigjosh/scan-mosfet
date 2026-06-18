// Byte-stream transports. Three backends, one interface:
//   connect() -> resolves once the link is open (board may reset via DTR)
//   onData(cb(Uint8Array)), onClose(cb)
//   write(Uint8Array), close()
//
// Android Chrome has no Web Serial -> WebUSB, speaking CDC-ACM ourselves.
// Desktop Chrome can't claim a CDC interface from the OS driver -> Web Serial.
// pickTransport() chooses; Mock (demo mode) lives in mock.js.

class BaseTransport {
  constructor() {
    this._onData = () => {};
    this._onClose = () => {};
    this._onProgress = () => {};
  }
  onData(cb) { this._onData = cb; }
  onClose(cb) { this._onClose = cb; }
  onProgress(cb) { this._onProgress = cb; }    // step-by-step status for the UI
  _progress(msg) { try { this._onProgress(msg); } catch (e) { /* ignore */ } }
}

export class WebSerialTransport extends BaseTransport {
  static available() { return 'serial' in navigator; }
  get label() { return 'Web Serial'; }

  async connect() {
    this._progress('choose the serial port...');
    this.port = await navigator.serial.requestPort();
    this._progress('opening port (115200)...');
    await this.port.open({ baudRate: 115200 });  // asserts DTR -> Uno resets
    this._progress('port open');
    this._closing = false;
    this._readLoop();
  }

  async _readLoop() {
    while (this.port.readable && !this._closing) {
      this.reader = this.port.readable.getReader();
      try {
        for (;;) {
          const { value, done } = await this.reader.read();
          if (done) break;
          if (value) this._onData(value);
        }
      } catch (e) {
        // device unplugged or stream error
      } finally {
        try { this.reader.releaseLock(); } catch (e) { /* ignore */ }
      }
      if (!this._closing) break;  // readable gone for real
    }
    if (!this._closing) this._onClose();
  }

  async write(bytes) {
    const w = this.port.writable.getWriter();
    try { await w.write(bytes); } finally { w.releaseLock(); }
  }

  async close() {
    this._closing = true;
    try { await this.reader?.cancel(); } catch (e) { /* ignore */ }
    try { await this.port?.close(); } catch (e) { /* ignore */ }
  }
}

// USB CDC-ACM over raw WebUSB (the only path on Android Chrome).
export class WebUsbCdcTransport extends BaseTransport {
  static available() { return 'usb' in navigator; }
  get label() { return 'WebUSB (CDC)'; }

  async connect() {
    this._progress('choose the USB device...');
    this.device = await navigator.usb.requestDevice({
      filters: [
        { vendorId: 0x2341 },  // Arduino
        { vendorId: 0x2a03 },  // Arduino.org
        { classCode: 0x02 },   // any CDC communications device
      ],
    });
    const d = this.device;
    await d.open();
    if (d.configuration === null) await d.selectConfiguration(1);

    // Locate CDC comm (class 2) and data (class 10) interfaces.
    this.commIf = null;
    this.dataIf = null;
    this.epIn = null;
    this.epOut = null;
    for (const iface of d.configuration.interfaces) {
      const alt = iface.alternates[0];
      if (alt.interfaceClass === 2 && this.commIf === null) this.commIf = iface.interfaceNumber;
      if (alt.interfaceClass === 10 && this.dataIf === null) {
        this.dataIf = iface.interfaceNumber;
        for (const ep of alt.endpoints) {
          if (ep.type === 'bulk' && ep.direction === 'in') this.epIn = ep.endpointNumber;
          if (ep.type === 'bulk' && ep.direction === 'out') this.epOut = ep.endpointNumber;
        }
      }
    }
    if (this.commIf === null || this.dataIf === null || this.epIn === null || this.epOut === null) {
      throw new Error('No CDC-ACM interface found - is this a 16U2-style Uno? (CH340 clones unsupported)');
    }
    const claimBoth = async () => {
      await d.claimInterface(this.commIf);
      await d.claimInterface(this.dataIf);
    };
    this._progress('claiming CDC interfaces...');
    try {
      await claimBoth();
    } catch (e1) {
      // Something else holds the interfaces (serial app / stale claim).
      // A USB reset clears foreign claims; then reconfigure and retry once.
      try {
        await d.reset();
        if (d.configuration === null) await d.selectConfiguration(1);
        await claimBoth();
      } catch (e2) {
        throw new Error(
          'Could not claim the USB interfaces - another app probably owns the ' +
          'device. Force-stop any serial terminal app, clear its Open-by-default ' +
          'in Android settings, replug, and retry. (' + (e2.message || e2) + ')');
      }
    }

    // SET_LINE_CODING: 115200 8N1
    const coding = new ArrayBuffer(7);
    const dv = new DataView(coding);
    dv.setUint32(0, 115200, true);
    dv.setUint8(4, 0);  // 1 stop bit
    dv.setUint8(5, 0);  // no parity
    dv.setUint8(6, 8);  // 8 data bits
    await d.controlTransferOut(
      { requestType: 'class', recipient: 'interface', request: 0x20, value: 0, index: this.commIf },
      coding);
    // SET_CONTROL_LINE_STATE: DTR|RTS -> Uno auto-resets, will print its banner
    await d.controlTransferOut(
      { requestType: 'class', recipient: 'interface', request: 0x22, value: 0x03, index: this.commIf });

    this._progress('port open');
    this._closing = false;
    navigator.usb.addEventListener('disconnect', (ev) => {
      if (ev.device === this.device && !this._closing) this._onClose();
    });
    this._readLoop();
  }

  async _readLoop() {
    while (!this._closing) {
      let result;
      try {
        result = await this.device.transferIn(this.epIn, 64);
      } catch (e) {
        if (!this._closing) this._onClose();
        return;
      }
      if (result.status === 'ok' && result.data && result.data.byteLength) {
        this._onData(new Uint8Array(result.data.buffer));
      } else if (result.status === 'stall') {
        await this.device.clearHalt('in', this.epIn);
      }
    }
  }

  async write(bytes) {
    await this.device.transferOut(this.epOut, bytes);
  }

  async close() {
    this._closing = true;
    try {
      await this.device.controlTransferOut(
        { requestType: 'class', recipient: 'interface', request: 0x22, value: 0x00, index: this.commIf });
    } catch (e) { /* ignore */ }
    try { await this.device.close(); } catch (e) { /* ignore */ }
  }
}

// Native bridge injected by the Android shell app (android/). Talks to the
// Android USB Host API directly, bypassing Chrome's WebUSB/Web Serial fences.
export class AndroidBridgeTransport extends BaseTransport {
  static available() { return typeof window !== 'undefined' && !!window.AndroidSerial; }
  get label() { return 'Android USB'; }

  async connect() {
    this._progress('listing USB devices...');
    const devices = JSON.parse(window.AndroidSerial.list());
    if (!devices.length) {
      throw new Error('No USB serial device found - plug the rig into the phone (OTG) and retry.');
    }
    const KNOWN = [0x2341, 0x2a03, 0x1a86, 0x0403, 0x10c4];
    const dev = devices.find((d) => KNOWN.includes(d.vid)) || devices[0];
    const hex = (n) => '0x' + (n >>> 0).toString(16).padStart(4, '0');
    this._progress(`device: ${dev.name || dev.driver || 'serial'} ${hex(dev.vid)}:${hex(dev.pid)}`);
    this._progress('opening - approve the USB permission if asked...');
    await new Promise((resolve, reject) => {
      const timer = setTimeout(
        () => reject(new Error('USB connect timed out (permission dialog still open?)')), 70000);
      window.__androidSerialEvent = (ev) => {
        if (ev.type === 'connect') {
          clearTimeout(timer);
          this._progress('port open');
          this._steady();
          resolve();
        } else if (ev.type === 'data') {
          this._onData(b64ToBytes(ev.data));  // banner bytes may race 'connect'
        } else if (ev.type === 'error' || ev.type === 'disconnect') {
          clearTimeout(timer);
          reject(new Error(ev.data || ev.type));
        }
      };
      window.AndroidSerial.connect(dev.id, 115200);
    });
  }

  _steady() {
    window.__androidSerialEvent = (ev) => {
      if (ev.type === 'data') this._onData(b64ToBytes(ev.data));
      else if (ev.type === 'disconnect') this._onClose();
      else if (ev.type === 'error') console.warn('AndroidSerial:', ev.data);
    };
  }

  async write(bytes) {
    window.AndroidSerial.write(bytesToB64(bytes));
  }

  async close() {
    try { window.AndroidSerial.close(); } catch (e) { /* ignore */ }
  }
}

function b64ToBytes(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function bytesToB64(bytes) {
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

// Prefer the native bridge (inside the shell APK), then Web Serial (desktop),
// then raw WebUSB CDC (Android browser, where Chrome still allows it).
export function pickTransport() {
  if (AndroidBridgeTransport.available()) return new AndroidBridgeTransport();
  if (WebSerialTransport.available()) return new WebSerialTransport();
  if (WebUsbCdcTransport.available()) return new WebUsbCdcTransport();
  return null;
}

// Diagnostic dump: what can this browser actually see over WebUSB?
// (Chrome's picker shows "no compatible devices" both when filters miss and
// when the OS/another app hides the device - this tells the cases apart.)
export async function usbDiagnostics() {
  const lines = [];
  lines.push(`browser: ${navigator.userAgent}`);
  lines.push(`webusb: ${'usb' in navigator}, webserial: ${'serial' in navigator}`);
  if (!('usb' in navigator)) return lines.join('\n');
  try {
    const granted = await navigator.usb.getDevices();
    lines.push(`previously-granted devices: ${granted.length}`);
    for (const d of granted) {
      lines.push(`  vid=0x${d.vendorId.toString(16).padStart(4, '0')} ` +
                 `pid=0x${d.productId.toString(16).padStart(4, '0')} ${d.productName || ''}`);
    }
  } catch (e) {
    lines.push(`getDevices failed: ${e.message}`);
  }
  lines.push('opening UNFILTERED picker (pick the Arduino if listed)...');
  try {
    const d = await navigator.usb.requestDevice({ filters: [] });
    lines.push(`picked: vid=0x${d.vendorId.toString(16).padStart(4, '0')} ` +
               `pid=0x${d.productId.toString(16).padStart(4, '0')} "${d.productName || '?'}"`);
    await d.open();
    if (d.configuration === null) await d.selectConfiguration(1);
    for (const i of d.configuration.interfaces) {
      const a = i.alternates[0];
      lines.push(`  iface ${i.interfaceNumber}: class=0x${a.interfaceClass.toString(16)} ` +
                 `sub=0x${a.interfaceSubclass.toString(16)} ` +
                 `eps=[${a.endpoints.map((e) => `${e.direction[0]}${e.endpointNumber}/${e.type}`).join(' ')}]`);
      try {
        await d.claimInterface(i.interfaceNumber);
        lines.push(`    claim iface ${i.interfaceNumber}: OK`);
        await d.releaseInterface(i.interfaceNumber);
      } catch (e) {
        lines.push(`    claim iface ${i.interfaceNumber}: ${e.name}: ${e.message}`);
      }
    }
    await d.close();
  } catch (e) {
    lines.push(`picker/open result: ${e.name}: ${e.message}`);
    lines.push('(NotFoundError here = picker was EMPTY or cancelled)');
  }
  return lines.join('\n');
}
