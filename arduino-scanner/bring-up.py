#!/usr/bin/env python3
"""Guided bring-up & calibration for a freshly assembled scanner rig.

Walks a new rig through, interactively, with PASS/FAIL at every step and
wiring hints when something fails:

  1. find the Uno, verify serial link + firmware banner
  2. firmware identity, VDD sanity, stored bandgap cal
  3. I2C harness diagnostic (PINTEST: pullups / shorts / bridges per line)
  4. DAC detection, raw write ACKs, SAVEZERO (safe cold-boot)
  5. no-DUT electrical self-test (DAC<->sense tracking, zero-current floors)
  6. jumper-wire functional tests (quantitative, using known resistors):
       High-Low  -> current loop must obey Ohm's law through 100R + R_low
       Gate-Low  -> gate-current scale through R_gate
       Gate-High -> A1 and A2 must agree on the same node
  7. optional absolute calibration against one DMM reading of the 5V pin

Usage:
    python arduino-scanner/bring-up.py [--port COMx]

Answer prompts with Enter (done/continue), s (skip a step), q (quit).
"""

import argparse
import re
import time
import types
from datetime import datetime

import serial
import serial.tools.list_ports

from scan_arduino import (KNOWN_VIDS, Scanner, cal_vdd, measure_point,
                          run_selftest)

RESULTS = []


class Abort(Exception):
    pass


def ask(msg: str) -> str:
    try:
        a = input(msg).strip().lower()
    except EOFError:
        raise Abort()
    if a == "q":
        raise Abort()
    return a


def gate(msg: str) -> bool:
    """Physical-action prompt. True = done, False = user chose to skip."""
    return ask(f"\n>> {msg}\n   [Enter = done, s = skip, q = quit] ") != "s"


def skip(name: str):
    RESULTS.append((name, "SKIP"))
    print(f"  [SKIP] {name}")


def run_step(name: str, fn):
    while True:
        print(f"\n--- {name} ---")
        try:
            ok = bool(fn())
        except Abort:
            raise
        except Exception as e:
            print(f"  error: {e}")
            ok = False
        if ok:
            print(f"  [PASS] {name}")
            RESULTS.append((name, "PASS"))
            return
        print(f"  [FAIL] {name}")
        if ask("   (r)etry / (c)ontinue anyway / (q)uit: ") == "r":
            continue
        RESULTS.append((name, "FAIL"))
        return


def set_code(sc: Scanner, cmd: str) -> int:
    m = re.search(r"CODE=(\d+)", sc.ok(cmd))
    return int(m.group(1)) if m else 0


# ------------------------------------------------------------------ steps

def pick_port(cli_port):
    if cli_port:
        return cli_port
    while True:
        ports = list(serial.tools.list_ports.comports())
        known = [p for p in ports if p.vid in KNOWN_VIDS]
        if len(known) == 1:
            print(f"  found {known[0].device}: {known[0].description}")
            return known[0].device
        if not ports:
            print("  no serial ports at all - is the Uno plugged in via USB?")
        else:
            print("  can't auto-pick a port; candidates:")
            for i, p in enumerate(ports):
                vid = f"{p.vid:04X}" if p.vid else "----"
                print(f"    {i}: {p.device}  {p.description} (VID {vid})")
            a = ask("  number to use (Enter to rescan, q to quit): ")
            if a.isdigit() and int(a) < len(ports):
                return ports[int(a)].device
        ask("  press Enter to rescan: ")


def connect(cli_port) -> Scanner:
    print("\n--- Find & link the Uno ---")
    while True:
        port = pick_port(cli_port)
        try:
            sc = Scanner(port)
            if " v1 " not in sc.banner + " ":
                print(f"  warning: unexpected firmware banner: {sc.banner}")
            RESULTS.append(("Serial link & firmware banner", "PASS"))
            return sc
        except serial.SerialException as e:
            if "denied" in str(e).lower():
                print("  the port is held by another program. Usual suspects:")
                print("    - Arduino IDE Serial Monitor tab (close the tab, it auto-reconnects)")
                print("    - another scan_arduino.py --repl window")
            else:
                print(f"  {e}")
        except RuntimeError as e:
            print(f"  {e}")
            print("  if the firmware is missing or old, flash it with:")
            print("    powershell arduino-scanner\\firmware\\build.ps1 -Upload")
        ask("  press Enter to retry, q to quit: ")
        cli_port = None  # allow re-picking after a failure


def step_fw(sc: Scanner) -> bool:
    print(f"  banner : {sc.banner}")
    r = sc.cmd("VDD?")
    m = re.search(r"VDD_MV=([\d.]+)", r)
    if not m:
        print(f"  bad VDD? reply: {r}")
        return False
    vdd = float(m.group(1)) / 1000.0
    print(f"  VDD measures {vdd:.3f} V   (stored bandgap cal: {sc.cmd('CALBG?')})")
    if not 4.3 <= vdd <= 5.5:
        print("  outside the 4.3-5.5 V a USB-powered Uno should show - check supply")
        return False
    return True


def step_pintest(sc: Scanner) -> bool:
    r = sc.cmd("PINTEST")
    print(f"  {r}")
    vals = {k: int(v) for k, v in re.findall(r"([A-Z0-9_]+)=(\d+)", r)}
    ok = True
    for line in ("SDA", "SCL1", "SCL2"):
        if not vals.get(f"{line}_EXTPU", 0):
            print(f"  {line}: no external pullup seen -> this wire is not reaching a powered "
                  f"module (unsoldered header pin? wrong breadboard row? module unpowered?)")
            ok = False
        if vals.get(f"{line}_5VSHORT", 0):
            print(f"  {line}: pin is fighting a hard 5 V source -> jumper plugged into a power pin?")
            ok = False
    if vals.get("BRIDGED", 0):
        print("  two bus lines short together -> adjacent-pin solder bridge or doubled-up row?")
        ok = False
    if ok:
        print("  all three bus lines: pullup present, no shorts, no bridges")
    return ok


def rescan(sc: Scanner) -> str:
    b = sc.cmd("RESCAN")
    sc.banner = b
    sc.has_h = "DACH=0x" in b
    sc.has_g = "DACG=0x" in b
    return b


def step_dacs(sc: Scanner) -> bool:
    print(f"  {rescan(sc)}")
    if not (sc.has_h and sc.has_g):
        missing = [n for n, present in (("DAC_H (module clocked by D5)", sc.has_h),
                                        ("DAC_G (module clocked by D6)", sc.has_g)) if not present]
        print(f"  not detected: {', '.join(missing)}")
        print("  fix the wiring (see PINTEST hints above), then retry this step")
        return False
    for cmd in ("RAWH 0", "RAWH 2048", "RAWH 0", "RAWG 0", "RAWG 2048", "RAWG 0"):
        r = sc.cmd(cmd)
        if not r.startswith("OK"):
            print(f"  {cmd} -> {r}  (DAC stopped ACKing mid-write)")
            return False
    print("  both DACs ACK raw writes (their output VOLTAGES get verified next, in the self-test)")
    r = sc.cmd("SAVEZERO")
    print(f"  SAVEZERO so a cold power-up gives 0 V instead of the factory 2.5 V: {r}")
    return r.startswith("OK")


def step_selftest(sc: Scanner, args, prefix: str) -> bool:
    ns = types.SimpleNamespace(rlow=args.rlow, rgate=args.rgate,
                               gate_settle=0.2, no_charts=False)
    checks = run_selftest(sc, ns, prefix)
    return all(ok for _, ok, _ in checks)


def test_high_low(sc: Scanner, args) -> bool:
    """Jumper High<->Low: the only path is 100R + R_low, so measured Ids must
    obey Ohm's law. Validates the entire current loop with one wire."""
    sc.ok("LOWIO 0")
    sc.ok("SETG 0")
    rtot = args.rhigh + args.rlow
    worst_pct, worst_gap = 0.0, 0.0
    last_eff, last_ids = 0.0, 0.0
    for h in (0.5, 1.0, 2.0, 3.0, 4.0, 5.0):
        code = set_code(sc, f"SETH {h:.3f}")
        time.sleep(0.05)
        p = measure_point(sc, args.rlow, args.rgate, 0.0)
        eff = code / 4096.0 * p["vdd"]
        pred = eff / rtot * 1e6
        pct = (p["ids_ua"] - pred) / pred * 100.0 if pred else 0.0
        gap = abs(p["vhigh"] - p["vlow"])
        print(f"    H={h:4.2f} V: Ids {p['ids_ua']:7.1f} uA vs Ohm's-law {pred:7.1f} uA "
              f"({pct:+5.1f} %), |A2-A0| {gap * 1000:5.1f} mV (same node)")
        worst_pct = max(worst_pct, abs(pct))
        worst_gap = max(worst_gap, gap)
        last_eff, last_ids = eff, p["ids_ua"]
    sc.ok("SETH 0")
    ok = True
    if worst_pct > 10.0:
        print(f"  current off by {worst_pct:.1f} % (limit 10 %)")
        if last_ids > 0.5:
            r_loop = last_eff / (last_ids * 1e-6)
            r_series = r_loop - args.rlow
            print(f"  back-computed loop resistance ~{r_loop / 1000:.2f} kohm "
                  f"(expected ~{rtot / 1000:.2f} kohm) -> the series element on High "
                  f"measures ~{r_series / 1000:.2f} kohm")
            print(f"  classic cause: 100 kohm in the 100 ohm spot (brown-black-YELLOW vs "
                  f"brown-black-BROWN) or a kohm DMM reading taken as ohm. If that value "
                  f"is intentional, rerun with --rhigh {r_series:.0f}")
        else:
            print("  almost no current at all: open loop - jumper seated? R_low reaching D3?")
        ok = False
    if worst_gap > 0.040:
        print(f"  A2 vs A0 disagree by {worst_gap * 1000:.0f} mV on the SAME node (limit 40 mV): "
              f"sense wiring problem")
        ok = False
    return ok


def test_gate_low(sc: Scanner, args) -> bool:
    """Jumper Gate<->Low: the gate node is clamped to ~0 V, so A1 must read ~0
    while Igs ramps as V/R_gate; the same current returns through R_low."""
    sc.ok("LOWIO 0")
    sc.ok("SETH 0")
    worst_vg = 0.0
    last = None
    for g in (1.0, 2.0, 3.0, 4.0, 5.0):
        code = set_code(sc, f"SETG {g:.3f}")
        time.sleep(0.15)
        p = measure_point(sc, args.rlow, args.rgate, g)
        eff = code / 4096.0 * p["vdd"]
        igs_eff = (eff - p["vgate"]) / args.rgate * 1e6
        print(f"    G={g:4.2f} V: Igs {igs_eff:6.3f} uA (expect {eff / args.rgate * 1e6:6.3f}), "
              f"Vgate {p['vgate'] * 1000:6.1f} mV (expect ~0)")
        worst_vg = max(worst_vg, abs(p["vgate"]))
        last = (p["ids_ua"], igs_eff)
    sc.ok("SETG 0")
    if last:
        print(f"    cross-check at G=5: same current via R_low Ids={last[0]:+.2f} uA "
              f"vs via R_gate Igs={last[1]:.2f} uA (Ids floor is ~0.3 uA, rough agreement is fine)")
    if worst_vg > 0.040:
        print(f"  gate node not clamped (worst {worst_vg * 1000:.0f} mV, limit 40): jumper seated? "
              f"A1 on the right pin?")
        return False
    return True


def test_gate_high(sc: Scanner, args) -> bool:
    """Jumper Gate<->High: A1 and A2 sit on one node and must agree."""
    sc.ok("LOWIO 0")
    sc.ok("SETG 0")
    worst = 0.0
    for h in (1.0, 2.5, 4.0):
        set_code(sc, f"SETH {h:.3f}")
        time.sleep(0.1)
        p = measure_point(sc, args.rlow, args.rgate, 0.0)
        gap = abs(p["vgate"] - p["vhigh"])
        print(f"    H={h:4.2f} V: A1 {p['vgate']:.3f} V vs A2 {p['vhigh']:.3f} V "
              f"(gap {gap * 1000:5.1f} mV)")
        worst = max(worst, gap)
    sc.ok("SETH 0")
    if worst > 0.040:
        print(f"  A1/A2 disagree by {worst * 1000:.0f} mV on one node (limit 40 mV)")
        return False
    return True


def step_cal(sc: Scanner):
    print("\n--- Absolute calibration (optional, needs a DMM) ---")
    print("  Everything so far was ratiometric (internally consistent regardless of the")
    print("  bandgap value). One DMM reading of the 5V rail pins it to absolute volts.")
    a = ask(">> DMM the Uno's 5V pin and type the reading in volts (Enter to skip, q to quit): ")
    if not a:
        skip("Absolute calibration (CALBG)")
        return
    try:
        volts = float(a)
        if not 4.0 <= volts <= 5.6:
            raise ValueError
    except ValueError:
        print("  that doesn't look like a 5V-rail reading in volts; skipping")
        skip("Absolute calibration (CALBG)")
        return
    cal_vdd(sc, volts)
    RESULTS.append(("Absolute calibration (CALBG)", "PASS"))


# ------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Guided bring-up for the Arduino MOSFET scanner")
    ap.add_argument("--port", default=None, help="serial port (auto-detect if omitted)")
    ap.add_argument("--rlow", type=float, default=1000.0, help="fitted R_low (ohms)")
    ap.add_argument("--rgate", type=float, default=1e6, help="fitted R_gate (ohms)")
    ap.add_argument("--rhigh", type=float, default=100.0, help="fitted High series R (ohms)")
    args = ap.parse_args()

    print("=" * 64)
    print("Arduino MOSFET scanner - guided bring-up")
    print("Prompts: Enter = done/continue, s = skip step, q = quit.")
    print("=" * 64)

    prefix = f"bringup-{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    sc = None
    try:
        sc = connect(args.port)
        sc.ok("AVG 32")
        run_step("Firmware identity & VDD sanity", lambda: step_fw(sc))
        run_step("I2C harness diagnostic (PINTEST)", lambda: step_pintest(sc))
        run_step("DAC detection, raw writes, SAVEZERO", lambda: step_dacs(sc))

        gate("Make sure the DUT socket is EMPTY (no MOSFET, no resistor).")
        run_step("No-DUT electrical self-test", lambda: step_selftest(sc, args, prefix))

        any_jumper = False
        if gate("Jumper wire from socket HIGH pin to socket LOW pin."):
            any_jumper = True
            run_step("Jumper High-Low: current-loop vs Ohm's law", lambda: test_high_low(sc, args))
        else:
            skip("Jumper High-Low: current-loop vs Ohm's law")
        if gate("Move the jumper: socket GATE pin to socket LOW pin."):
            any_jumper = True
            run_step("Jumper Gate-Low: gate-current scale", lambda: test_gate_low(sc, args))
        else:
            skip("Jumper Gate-Low: gate-current scale")
        if gate("Move the jumper: socket GATE pin to socket HIGH pin."):
            any_jumper = True
            run_step("Jumper Gate-High: A1/A2 agreement", lambda: test_gate_high(sc, args))
        else:
            skip("Jumper Gate-High: A1/A2 agreement")
        if any_jumper:
            gate("Remove the jumper wire (socket empty again).")

        step_cal(sc)

        print("\n" + "=" * 64)
        print("Bring-up summary")
        for name, status in RESULTS:
            print(f"  [{status:^4}] {name}")
        fails = [n for n, s in RESULTS if s == "FAIL"]
        skips = [n for n, s in RESULTS if s == "SKIP"]
        if fails:
            print("\nNot ready yet - fix the FAILs above and rerun bring-up.py.")
        elif skips:
            print(f"\nNo failures, but {len(skips)} step(s) skipped - rerun bring-up.py "
                  "anytime to pick them up.")
            print("Rig looks good. Insert a MOSFET and run:  python scan_arduino.py")
        else:
            print("\nRig proven and calibrated. Insert a MOSFET and run:  python scan_arduino.py")
    except Abort:
        print("\nAborted.")
    finally:
        if sc is not None:
            for c in ("LOWIO 0", "SETH 0", "SETG 0"):
                try:
                    sc.cmd(c)
                except Exception:
                    pass
            sc.close()


if __name__ == "__main__":
    main()
