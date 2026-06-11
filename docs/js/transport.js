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
  }
  onData(cb) { this._onData = cb; }
  onClose(cb) { this._onClose = cb; }
}

export class WebSerialTransport extends BaseTransport {
  static available() { return 'serial' in navigator; }
  get label() { return 'Web Serial'; }

  async connect() {
    this.port = await navigator.serial.requestPort();
    await this.port.open({ baudRate: 115200 });  // asserts DTR -> Uno resets
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
    this.device = await navigator.usb.requestDevice({
      filters: [
        { vendorId: 0x2341 },  // Arduino
        { vendorId: 0x2a03 },  // Arduino.org
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
    await d.claimInterface(this.commIf);
    await d.claimInterface(this.dataIf);

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

// Prefer Web Serial (desktop); fall back to WebUSB CDC (Android).
export function pickTransport() {
  if (WebSerialTransport.available()) return new WebSerialTransport();
  if (WebUsbCdcTransport.available()) return new WebUsbCdcTransport();
  return null;
}
