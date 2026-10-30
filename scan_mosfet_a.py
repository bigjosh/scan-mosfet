#!/usr/bin/env python3
"""
MOSFET Characterization Scanner, Arduino-sense variant.

Same sweep as scan_mosfet.py (Siglent SPD3303C: CH1 drives Vds, CH2 drives Vgs)
but the two currents are measured by two different instruments:

  Ids  An Arduino Uno running the arduino-scanner firmware reads the voltage
       across a sense resistor between the DUT source and ground on A0
       (MEAS? command: 1.1 V bandgap reference for resolution, 5 V rail for range).
  Ig   The Joulescope JS110 sits in the CH2 line and reports the gate current
       and the CH2 voltage.

Wiring (photo and details in README.md, section "scan_mosfet_a.py"):

  CH1+ ----------------------------------------------------> drain
  CH2+ --> JS IN+    JS OUT+ --> [battery, + toward the JS, optional] --> gate
  JS IN-/OUT-, CH1-, CH2-, Uno GND -------------------------> common ground
  source --> Uno A0 --> R_sense --> ground

The script never touches the supply's ON/OFF state or current limits.
Set CH1's current limit so that I_limit x R_sense stays well below 5 V:
A0 is a 5 V pin and the sense resistor is its only protection.
"""

import argparse
import math
import re
import time
from typing import Optional, Tuple

from scan_mosfet import (PowerSupply, JoulescopeMeter, create_voltage_range, resolve_output,
                         print_photo_hint, SUPPLY_MAX_VOLTAGE)

# USB vendor IDs of a genuine Uno and the usual clone serial bridges
KNOWN_VIDS = {0x2341, 0x2A03, 0x1A86, 0x0403, 0x10C4}
# Averaged ADC counts at or above this are treated as clipped (10-bit ADC, max 1023)
CLIP_COUNTS = 1010.0
ADC_PIN_MAX_V = 5.0


class ArduinoAmmeter:
    """Ids from the voltage across R_sense, read on A0 by the arduino-scanner firmware.

    The firmware's MEAS? reads every analog pin under both ADC references and
    reports averaged counts plus the measured 5 V rail. The 1.1 V bandgap
    reading is used while it is in range (about 1 mV per count), the 5 V-rail
    reading above that (about 5 mV per count). Only A0 is used here.
    """

    def __init__(self, port: Optional[str], r_sense: float, avg: int = 32,
                 baud: int = 115200, timeout: float = 3.0):
        try:
            import serial
            import serial.tools.list_ports
        except ImportError:
            raise RuntimeError("pyserial not installed. Run: pip install pyserial")
        self._serial = serial
        self.r_sense = r_sense
        if port is None:
            port = self._find_port()
        # Windows sometimes aborts the first open right after another process released the
        # port or the board re-enumerated (error 995); a short retry covers that.
        for attempt in range(3):
            try:
                self.ser = serial.Serial(port, baud, timeout=timeout)
                break
            except serial.SerialException as e:
                if attempt == 2:
                    raise RuntimeError(f"Cannot open {port}: {e}. Is another scan or a serial monitor "
                                       f"holding it? Unplugging and replugging the Uno clears a stuck port.")
                print(f"Opening {port} failed ({e}); retrying in 1.5 s...")
                time.sleep(1.5)
        # Opening the port toggles DTR: the Uno resets and prints its banner.
        self.banner = self._wait_banner()
        # Port-open can leave a stray byte in the firmware's line buffer;
        # terminate it and discard whatever that produces.
        time.sleep(0.1)
        self.ser.write(b"\n")
        time.sleep(0.15)
        self.ser.reset_input_buffer()
        m = re.search(r"VREFINT_MV=(\d+)", self.banner)
        self.vref_int = int(m.group(1)) / 1000.0 if m else 1.1
        reply = self.cmd(f"AVG {int(avg)}")
        if not reply.startswith("OK"):
            print(f"Warning: 'AVG {int(avg)}' answered {reply!r}; using the firmware default")
        print(f"Connected to Arduino on {port}: {self.banner}")

    def _find_port(self) -> str:
        ports = list(self._serial.tools.list_ports.comports())
        candidates = [p for p in ports if p.vid in KNOWN_VIDS]
        if len(candidates) == 1:
            return candidates[0].device
        print("Could not auto-detect the Arduino. Available ports:")
        for p in ports:
            vid = f"{p.vid:04X}" if p.vid else "----"
            print(f"  {p.device}: {p.description} (VID {vid})")
        raise RuntimeError("Specify the port with --arduino-port COMx")

    def _wait_banner(self, wait: float = 5.0) -> str:
        deadline = time.time() + wait
        while time.time() < deadline:
            line = self.ser.readline().decode(errors="replace").strip()
            if line.startswith("ArduinoMosfetScanner"):
                return line
        raise RuntimeError("No firmware banner after reset. Is arduino-scanner/firmware uploaded?")

    def cmd(self, s: str) -> str:
        self.ser.write((s + "\n").encode())
        reply = self.ser.readline().decode(errors="replace").strip()
        if not reply:
            raise RuntimeError(f"Timeout waiting for reply to {s!r}")
        # Protocol is pure ASCII; mangle anything else so printing never fails.
        return reply.encode("ascii", "replace").decode("ascii")

    def resolution(self) -> str:
        """Human-readable Ids resolution and range for the fitted sense resistor."""
        lsb11 = self.vref_int / 1024 / self.r_sense
        lsb5 = ADC_PIN_MAX_V / 1024 / self.r_sense
        return (f"{lsb11 * 1e6:.1f} uA/count up to {self.vref_int / self.r_sense * 1e3:.2f} mA (1.1 V ref), "
                f"{lsb5 * 1e6:.0f} uA/count up to {ADC_PIN_MAX_V / self.r_sense * 1e3:.1f} mA (5 V ref)")

    def measure(self) -> Tuple[float, float, bool]:
        """Return (Ids in A, source-node voltage in V, clipped) from one MEAS?."""
        reply = self.cmd("MEAS?")
        d = {}
        for token in reply.split():
            key, _, val = token.partition("=")
            try:
                d[key] = float(val)
            except ValueError:
                pass
        if "VDD_MV" not in d or "A0_1V1" not in d or "A0_5V" not in d:
            raise RuntimeError(f"Bad MEAS? reply: {reply!r}")
        c11, c5 = d["A0_1V1"], d["A0_5V"]
        if c11 < CLIP_COUNTS:
            v_src = c11 / 1024.0 * self.vref_int
            clipped = False
        else:
            v_src = c5 / 1024.0 * (d["VDD_MV"] / 1000.0)
            clipped = c5 >= CLIP_COUNTS
        return v_src / self.r_sense, v_src, clipped

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass


def perform_scan_a(
    ps: PowerSupply,
    ard: ArduinoAmmeter,
    js: Optional[JoulescopeMeter],
    vds_start: float,
    vds_stop: float,
    vds_step: float,
    vgs_start: float,
    vgs_stop: Optional[float],
    vgs_step: float,
    vgs_offset: float,
    setup_delay: float,
    reset_delay: float,
    num_samples: int,
    sample_delay: float,
    verbose: bool,
    csv_filename: Optional[str] = None,
):
    """Sweep Vgs (outer) and Vds (inner); Ids from the Arduino, Ig from the Joulescope.

    Vgs values are the voltages at the gate; CH2 is set to Vgs - vgs_offset.
    Every row also records the measured source-node voltage and the device
    voltages derived from it (setpoint minus the drop across R_sense).
    """
    vds_values = create_voltage_range(vds_start, vds_stop, vds_step)
    vgs_stop_val = vds_stop if vgs_stop is None else vgs_stop
    vgs_values = create_voltage_range(vgs_start, vgs_stop_val, vgs_step)

    if csv_filename:
        with open(csv_filename, 'w', newline='') as f:
            # First three columns match scan_mosfet.py so existing tools read this file.
            f.write(f"{'Vds (V)':>8}, {'Vgs (V)':>8}, {'Ids (uA)':>12}, {'Vds dev (V)':>11}, "
                    f"{'Vgs dev (V)':>11}, {'Vsrc (V)':>8}, {'Ig (uA)':>10}, {'Vjs (V)':>8}, flag\n")
        print(f"Writing incremental results to: {csv_filename}")

    total = len(vgs_values) * len(vds_values)
    count = 0
    print(f"\nStarting scan with {len(vgs_values)} Vgs points (Outer Loop)")
    print(f"Sweeping {len(vds_values)} Vds points per Vgs (Inner Loop)")
    print(f"Total measurements: {total}")
    print("-" * 60)
    start_time = time.time()

    for vgs in vgs_values:
        # Rest both channels at 0 V before each new Vgs step
        ps.set_voltage(1, 0)
        ps.set_voltage(2, 0)
        time.sleep(reset_delay)

        ch2 = max(0.0, vgs - vgs_offset)  # max() only guards against -0.000 from float noise
        ps.set_voltage(2, ch2)

        for vds in vds_values:
            ps.set_voltage(1, vds)
            time.sleep(setup_delay)

            ids, vsrc, clipped = ard.measure()
            if js is not None:
                ig, vjs = js.measure(num_samples, sample_delay)
            else:
                ig, vjs = math.nan, math.nan
            vds_dev = vds - vsrc
            vgs_dev = vgs - vsrc
            flag = "clip" if clipped else "ok"
            count += 1

            if verbose:
                progress = count / total * 100
                ch2_note = f" (CH2 {ch2:5.2f}V)" if vgs_offset else ""
                clip_note = "  ** A0 CLIPPED: check CH1 current limit **" if clipped else ""
                print(f"[{progress:5.1f}%] Vgs={vgs:6.2f}V{ch2_note}, Vds={vds:6.2f}V, "
                      f"Ids={ids * 1000:8.3f}mA (Vsrc {vsrc:5.3f}V), "
                      f"Ig={ig * 1e6:9.4f}uA (CH2 meas {vjs:6.2f}V){clip_note}")

            if csv_filename:
                with open(csv_filename, 'a', newline='') as f:
                    f.write(f"{vds:8.2f}, {vgs:8.2f}, {ids * 1e6:12.2f}, {vds_dev:11.3f}, "
                            f"{vgs_dev:11.3f}, {vsrc:8.4f}, {ig * 1e6:10.4f}, {vjs:8.3f}, {flag}\n")

    elapsed = time.time() - start_time
    print("-" * 60)
    print(f"Scan complete! Total time: {elapsed:.1f} seconds")


def main():
    parser = argparse.ArgumentParser(
        description='MOSFET Characterization Scanner: Ids via Arduino sense resistor, '
                    'gate current via Joulescope in the CH2 line',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    # Voltage sweep parameters (same as scan_mosfet.py)
    parser.add_argument('--vds-start', type=float, default=0.0, help='Starting Vds voltage (V)')
    parser.add_argument('--vds-stop', type=float, default=15.0, help='Stopping Vds voltage (V)')
    parser.add_argument('--vds-step', type=float, default=0.1, help='Vds voltage step (V)')
    parser.add_argument('--vgs-start', type=float, default=0.0, help='Starting Vgs voltage (V), at the gate')
    parser.add_argument('--vgs-stop', type=float, default=None,
                        help='Stopping Vgs voltage (V), at the gate. If not specified, same as --vds-stop.')
    parser.add_argument('--vgs-step', type=float, default=0.1, help='Vgs voltage step (V)')
    parser.add_argument('--vgs-offset', type=float, default=0.0,
                        help='Voltage of an external bias between the Joulescope and the gate, e.g. a battery, '
                             'so that Vgs = CH2 + offset. The --vgs-* values are the voltages at the gate; '
                             'CH2 is set to Vgs - offset. A battery that makes Vgs=-3V when CH2=0V is '
                             '--vgs-offset -3')
    parser.add_argument('--ch2-max', type=float, default=SUPPLY_MAX_VOLTAGE,
                        help='Refuse a sweep whose CH2 setpoint would exceed this. The Joulescope sits at '
                             'CH2 potential: 20 is its absolute maximum, 15 its recommended maximum.')

    # Timing parameters
    parser.add_argument('--setup-delay', type=float, default=1.0,
                        help='Settling delay after each Vds change (seconds)')
    parser.add_argument('--reset-delay', type=float, default=2.0,
                        help='Delay after resetting to 0V between Vgs steps (seconds)')
    parser.add_argument('--num-samples', type=int, default=10,
                        help='Number of Joulescope gate-current samples to average')
    parser.add_argument('--sample-delay', type=float, default=0.01,
                        help='Delay between consecutive Joulescope samples (seconds)')

    # Arduino parameters
    parser.add_argument('--rsense', type=float, required=True,
                        help='Sense resistor between the DUT source and ground (ohms)')
    parser.add_argument('--arduino-port', type=str, default=None,
                        help='Serial port of the Uno (auto-detect if not specified)')
    parser.add_argument('--arduino-avg', type=int, default=32,
                        help='ADC samples averaged per reading by the firmware (1-200)')

    # Output parameters
    parser.add_argument('--output', type=str, default=None,
                        help='Output CSV filename (default: scan-mosfet-a-<timestamp>.csv)')
    parser.add_argument('--batch', type=str, default=None,
                        help='Group this scan with others: the CSV goes in batches/<name>/ (created if needed)')
    parser.add_argument('--quiet', action='store_true', help='Disable progress output')

    # Hardware parameters
    parser.add_argument('--visa-resource', type=str, default=None,
                        help='VISA resource name for power supply (auto-detect if not specified)')
    parser.add_argument('--no-joulescope', action='store_true',
                        help='Skip the Joulescope: Ids only, Ig and Vjs are written as nan')

    args = parser.parse_args()

    if args.rsense <= 0:
        parser.error("--rsense must be a positive resistance in ohms")

    args.output = resolve_output(args.output, args.batch, "scan-mosfet-a")

    # Every Vgs point is produced as CH2 = Vgs - offset. Check the whole sweep is
    # reachable by the supply and within the CH2 ceiling before connecting to anything.
    vgs_stop_val = args.vds_stop if args.vgs_stop is None else args.vgs_stop
    ch2_lo = min(args.vgs_start, vgs_stop_val) - args.vgs_offset
    ch2_hi = max(args.vgs_start, vgs_stop_val) - args.vgs_offset
    ch2_limit = min(SUPPLY_MAX_VOLTAGE, args.ch2_max)
    if ch2_lo < 0 or ch2_hi > ch2_limit:
        parser.error(
            f"Vgs {args.vgs_start}V to {vgs_stop_val}V with --vgs-offset {args.vgs_offset} "
            f"needs CH2 from {ch2_lo:.2f}V to {ch2_hi:.2f}V, but CH2 is limited to "
            f"0V to {ch2_limit:g}V (--ch2-max {args.ch2_max:g}, supply max {SUPPLY_MAX_VOLTAGE:g}V)"
        )

    a0_limit_ma = ADC_PIN_MAX_V / args.rsense * 1e3

    print("=" * 60)
    print("MOSFET Characterization Scanner (Arduino Ids + Joulescope Ig)")
    print("=" * 60)
    print(f"Vds range: {args.vds_start}V to {args.vds_stop}V in {args.vds_step}V steps")
    print(f"Vgs range: {args.vgs_start}V to {vgs_stop_val}V in {args.vgs_step}V steps")
    if args.vgs_offset:
        print(f"Vgs offset: {args.vgs_offset:+g}V external bias on the gate, so CH2 is set to "
              f"{ch2_lo:g}V to {ch2_hi:g}V (Vgs - offset)")
    print(f"CH2 ceiling: {ch2_limit:g}V")
    print(f"R_sense: {args.rsense:g} ohm. A0 reaches 5 V at {a0_limit_ma:.0f} mA: set CH1's current "
          f"limit below that (10 mA puts {0.01 * args.rsense:.2f} V on A0)")
    print(f"Setup delay: {args.setup_delay * 1000}ms, reset delay: {args.reset_delay * 1000}ms")
    if not args.no_joulescope:
        print(f"Joulescope samples per point: {args.num_samples} at {args.sample_delay * 1000}ms")
    print(f"Output file: {args.output}")
    print("=" * 60)
    print_photo_hint(args.output)
    print("\nNOTE: Configure power supply ON/OFF and current limits manually")

    print("\nInitializing hardware...")
    ps = None
    ard = None
    js = None

    try:
        ps = PowerSupply(args.visa_resource)
        print("Setting initial voltages to 0V...")
        ps.set_voltage(1, 0)
        ps.set_voltage(2, 0)

        ard = ArduinoAmmeter(args.arduino_port, args.rsense, args.arduino_avg)
        print(f"Ids resolution: {ard.resolution()}")

        if not args.no_joulescope:
            js = JoulescopeMeter(mock_mode=False)
            js.start_streaming()
            try:
                # Gate current lives in the nA to uA ranges; make sure autoranging is on.
                js.js.parameter_set('i_range', 'auto')
            except Exception as e:
                print(f"Note: could not set Joulescope current range to auto ({e})")

        print("Waiting for system to stabilize...")
        time.sleep(1.0)

        perform_scan_a(
            ps=ps, ard=ard, js=js,
            vds_start=args.vds_start, vds_stop=args.vds_stop, vds_step=args.vds_step,
            vgs_start=args.vgs_start, vgs_stop=args.vgs_stop, vgs_step=args.vgs_step,
            vgs_offset=args.vgs_offset,
            setup_delay=args.setup_delay, reset_delay=args.reset_delay,
            num_samples=args.num_samples, sample_delay=args.sample_delay,
            verbose=not args.quiet,
            csv_filename=args.output,
        )
        print("\n✓ Scan completed successfully!")

    except KeyboardInterrupt:
        print("\n\n! Scan interrupted by user")

    except Exception as e:
        print(f"\n\n✗ Error during scan: {e}")
        import traceback
        traceback.print_exc()

    finally:
        print("\nShutting down hardware...")
        if ps is not None:
            try:
                ps.close()
                print("✓ Power supply connection closed")
            except Exception as e:
                print(f"✗ Error closing power supply: {e}")
        if ard is not None:
            ard.close()
            print("✓ Arduino port closed")
        if js is not None:
            try:
                js.stop_streaming()
                print("✓ Joulescope stopped")
            except Exception as e:
                print(f"✗ Error closing Joulescope: {e}")


if __name__ == "__main__":
    main()
