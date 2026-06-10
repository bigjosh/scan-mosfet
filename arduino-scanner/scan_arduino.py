#!/usr/bin/env python3
"""
Arduino MOSFET scanner tester (v1).

Drives the ArduinoMosfetScanner firmware (arduino-scanner/firmware/) over USB
serial and runs the 3-phase device test cycle from plan.md:

  phase 1 - gate leakage (forward and reverse)
  phase 2 - channel scan, positive gate voltages (LOW_IO = GND)
  phase 3 - channel scan, negative gate voltages (LOW_IO = 5 V)

Writes one CSV + one PNG per phase. The first three CSV columns match the
legacy `Vds (V), Vgs (V), Ids (uA)` schema (commanded grid values); measured
extras follow.

Sign conventions: positive Ids flows High -> Low; positive Igs flows from
DAC_G into the gate. Phase 3 reports negative Vds/Vgs (Low node is the 5 V
reference there).

Usage:
  python scan_arduino.py                  # full cycle, default steps
  python scan_arduino.py --repl           # raw firmware command passthrough
  python scan_arduino.py --cal-vdd 5.02   # bandgap cal against a DMM reading
"""

import argparse
import re
import sys
import time
from datetime import datetime

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("Error: pyserial not installed. Run: pip install pyserial")
    sys.exit(1)

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import cm, colors

# USB VIDs that look like an Uno / common clone serial chip
KNOWN_VIDS = {0x2341, 0x2A03, 0x1A86, 0x0403, 0x10C4}

CLIP_COUNTS = 1010.0  # averaged ADC counts above this are treated as clipped


def find_port() -> str:
    ports = list(serial.tools.list_ports.comports())
    candidates = [p for p in ports if p.vid in KNOWN_VIDS]
    if len(candidates) == 1:
        return candidates[0].device
    print("Could not auto-detect the Arduino. Available ports:")
    for p in ports:
        vid = f"{p.vid:04X}" if p.vid else "----"
        print(f"  {p.device}: {p.description} (VID {vid})")
    raise RuntimeError("Specify the port with --port COMx")


class Scanner:
    """Serial driver for the ArduinoMosfetScanner firmware."""

    def __init__(self, port: str, baud: int = 115200, timeout: float = 2.0):
        self.ser = serial.Serial(port, baud, timeout=timeout)
        # Opening the port toggles DTR -> the Uno resets and prints its banner.
        self.banner = self._wait_banner()
        # Port-open can leave a garbage byte in the firmware's line buffer;
        # terminate it with a bare newline and discard any ERR it produces.
        time.sleep(0.1)
        self.ser.write(b"\n")
        time.sleep(0.15)
        self.ser.reset_input_buffer()
        print(f"Connected: {self.banner}")
        self.has_h = re.search(r"DACH=0x", self.banner) is not None
        self.has_g = re.search(r"DACG=0x", self.banner) is not None
        if not (self.has_h and self.has_g):
            missing = [n for n, ok in (("DAC_H (SCL on D5)", self.has_h),
                                       ("DAC_G (SCL on D6)", self.has_g)) if not ok]
            print(f"WARNING: not detected: {', '.join(missing)}. "
                  "REPL works regardless - try PINTEST, SCAN?, RESCAN.")
        m = re.search(r"VREFINT_MV=(\d+)", self.banner)
        self.vref_int_v = int(m.group(1)) / 1000.0 if m else 1.1

    def _wait_banner(self, wait: float = 5.0) -> str:
        deadline = time.time() + wait
        while time.time() < deadline:
            line = self.ser.readline().decode(errors="replace").strip()
            if line.startswith("ArduinoMosfetScanner"):
                return line
        raise RuntimeError("No firmware banner after reset. Is the sketch uploaded?")

    def cmd(self, s: str) -> str:
        self.ser.write((s + "\n").encode())
        reply = self.ser.readline().decode(errors="replace").strip()
        if not reply:
            raise RuntimeError(f"Timeout waiting for reply to {s!r}")
        # Protocol is pure ASCII; mangle anything else (line noise, brownout
        # garbage) into '?' so printing can never crash on a cp1252 console.
        return reply.encode("ascii", "replace").decode("ascii")

    def ok(self, s: str) -> str:
        reply = self.cmd(s)
        if not reply.startswith("OK"):
            raise RuntimeError(f"Command {s!r} failed: {reply!r}")
        return reply

    def meas(self) -> dict:
        reply = self.cmd("MEAS?")
        d = {}
        for token in reply.split():
            key, _, val = token.partition("=")
            try:
                d[key] = float(val)
            except ValueError:
                pass
        if "VDD_MV" not in d or "A0_1V1" not in d:
            raise RuntimeError(f"Bad MEAS? reply: {reply!r}")
        return d

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass


def node_volts(d: dict, name: str, vref_int: float, vdd: float):
    """Pick the in-range reading for one node: 1.1 V ref if unclipped, else 5 V ref."""
    c11 = d[f"{name}_1V1"]
    c5 = d[f"{name}_5V"]
    if c11 < CLIP_COUNTS:
        return c11 / 1024.0 * vref_int, False
    return c5 / 1024.0 * vdd, c5 >= CLIP_COUNTS


def measure_point(sc: Scanner, rlow: float, rgate: float, vcmd_g: float) -> dict:
    d = sc.meas()
    vdd = d["VDD_MV"] / 1000.0
    vlow, clip0 = node_volts(d, "A0", sc.vref_int_v, vdd)
    vgate, clip1 = node_volts(d, "A1", sc.vref_int_v, vdd)
    vhigh, clip2 = node_volts(d, "A2", sc.vref_int_v, vdd)
    vlowio, clip3 = node_volts(d, "A3", sc.vref_int_v, vdd)
    return {
        "vdd": vdd,
        "vlow": vlow, "vgate": vgate, "vhigh": vhigh, "vlowio": vlowio,
        "ids_ua": (vlow - vlowio) / rlow * 1e6,
        "igs_ua": (vcmd_g - vgate) / rgate * 1e6,
        "vds": vhigh - vlow,
        "vgs": vgate - vlow,
        "flag": "clip" if (clip0 or clip1 or clip2 or clip3) else "ok",
    }


def vrange(start: float, stop: float, step: float):
    n = int(np.round((stop - start) / step)) + 1
    return list(np.linspace(start, stop, n))


# ---------------------------------------------------------------- CSV output

SWEEP_HEADER = (
    f"{'Vds (V)':>8}, {'Vgs (V)':>8}, {'Ids (uA)':>12}, {'Igs (uA)':>10}, "
    f"{'Vds_meas':>9}, {'Vgs_meas':>9}, {'Vhigh':>8}, {'Vlow':>8}, "
    f"{'Vgate':>8}, {'Vlowio':>8}, flag\n"
)


def sweep_row(vds_cmd: float, vgs_cmd: float, p: dict) -> str:
    return (
        f"{vds_cmd:8.2f}, {vgs_cmd:8.2f}, {p['ids_ua']:12.2f}, {p['igs_ua']:10.3f}, "
        f"{p['vds']:9.4f}, {p['vgs']:9.4f}, {p['vhigh']:8.4f}, {p['vlow']:8.4f}, "
        f"{p['vgate']:8.4f}, {p['vlowio']:8.4f}, {p['flag']}\n"
    )


# ------------------------------------------------------------------- phases

def run_phase1(sc: Scanner, args, prefix: str):
    """Gate leakage, forward (Vgs=+5) and reverse (Vgs=-5)."""
    print("\nPhase 1: gate leakage")
    sc.ok("LOWIO 0")
    sc.ok("SETH 0")
    sc.ok("SETG 5")
    time.sleep(args.gate_settle * 2)
    fwd = measure_point(sc, args.rlow, args.rgate, 5.0)

    sc.ok("SETG 0")
    sc.ok("SETH 5")
    sc.ok("LOWIO 1")
    time.sleep(args.gate_settle * 2)
    rev = measure_point(sc, args.rlow, args.rgate, 0.0)

    sc.ok("LOWIO 0")
    sc.ok("SETH 0")

    ceiling_ua = fwd["vdd"] / args.rgate * 1e6  # node collapses past this
    print(f"  forward Igs = {fwd['igs_ua']:+.4f} uA (Vgate sagged to {fwd['vgate']:.3f} V)")
    print(f"  reverse Igs = {rev['igs_ua']:+.4f} uA (Vgate sat at {rev['vgate']:.3f} V)")
    print(f"  (v1 measures cleanly to ~1 uA; hard ceiling ~{ceiling_ua:.1f} uA via R_gate)")

    csv_path = f"{prefix}_phase1.csv"
    with open(csv_path, "w", newline="") as f:
        f.write("mode, Igs (uA), Vgate (V), Vlow (V), Vlowio (V), Vhigh (V), flag\n")
        for mode, p in (("forward", fwd), ("reverse", rev)):
            f.write(
                f"{mode}, {p['igs_ua']:.4f}, {p['vgate']:.4f}, {p['vlow']:.4f}, "
                f"{p['vlowio']:.4f}, {p['vhigh']:.4f}, {p['flag']}\n"
            )
    print(f"  wrote {csv_path}")

    if not args.no_charts:
        fig, ax = plt.subplots(figsize=(5, 4))
        floor = 1e-3  # display floor ~= measurement resolution (a few nA)
        vals = [max(abs(fwd["igs_ua"]), floor), max(abs(rev["igs_ua"]), floor)]
        ax.bar(["forward\n(Vgs=+5V)", "reverse\n(Vgs=-5V)"], vals, color=["#4477aa", "#ee6677"])
        ax.axhline(1.0, ls="--", color="red", label="~1 uA 'leaky' ceiling")
        ax.set_yscale("log")
        ax.set_ylim(bottom=floor)
        ax.set_ylabel("|Igs| (uA)")
        ax.set_title("Phase 1: gate leakage")
        ax.legend()
        ax.grid(alpha=0.3, which="both")
        fig.tight_layout()
        fig.savefig(f"{prefix}_phase1.png", dpi=150)
        plt.close(fig)
        print(f"  wrote {prefix}_phase1.png")

    return fwd, rev


def run_sweep_phase(sc: Scanner, args, prefix: str, phase: int):
    """Phase 2 (LOW_IO=0, positive Vgs) or phase 3 (LOW_IO=5V, negative Vgs)."""
    neg = phase == 3
    g_grid = vrange(args.g_start, args.g_stop, args.g_step)
    h_grid = vrange(args.h_start, args.h_stop, args.h_step)
    h_park = 5.0 if neg else 0.0  # DAC_H value that gives Vds ~= 0 between gate steps

    label = "negative" if neg else "positive"
    print(f"\nPhase {phase}: channel scan, {label} gate voltages "
          f"({len(g_grid)} x {len(h_grid)} = {len(g_grid) * len(h_grid)} points)")

    # Enter the phase without ever applying full Vds accidentally
    sc.ok(f"SETH {h_park:.3f}")
    sc.ok("SETG 0")
    sc.ok("LOWIO 1" if neg else "LOWIO 0")

    csv_path = f"{prefix}_phase{phase}.csv"
    curves = []
    start = time.time()
    with open(csv_path, "w", newline="") as f:
        f.write(SWEEP_HEADER)
        for g in g_grid:
            vgs_cmd = g - 5.0 if neg else g
            sc.ok(f"SETG {g:.3f}")
            time.sleep(args.gate_settle)
            xs, ys = [], []
            for h in h_grid:
                vds_cmd = h - 5.0 if neg else h
                sc.ok(f"SETH {h:.3f}")
                time.sleep(args.settle)
                p = measure_point(sc, args.rlow, args.rgate, g)
                f.write(sweep_row(vds_cmd, vgs_cmd, p))
                xs.append(p["vds"])
                ys.append(p["ids_ua"])
            sc.ok(f"SETH {h_park:.3f}")
            curves.append((vgs_cmd, xs, ys))
            print(f"  Vgs_cmd={vgs_cmd:+5.2f} V: Ids {min(ys):9.2f} .. {max(ys):9.2f} uA")

    # Leave the rig safe
    sc.ok("LOWIO 0")
    sc.ok("SETH 0")
    sc.ok("SETG 0")
    print(f"  phase {phase} done in {time.time() - start:.0f} s, wrote {csv_path}")

    if not args.no_charts:
        fig, ax = plt.subplots(figsize=(9, 6))
        vgs_vals = [c[0] for c in curves]
        norm = colors.Normalize(vmin=min(vgs_vals), vmax=max(vgs_vals))
        cmap = plt.get_cmap("viridis")
        for vgs_cmd, xs, ys in curves:
            ax.plot(xs, ys, color=cmap(norm(vgs_cmd)), lw=1.2)
        sm = cm.ScalarMappable(norm=norm, cmap=cmap)
        sm.set_array([])
        fig.colorbar(sm, ax=ax, label="Vgs commanded (V)")
        ax.set_xlabel("Vds measured (V)")
        ax.set_ylabel("Ids (uA)")
        ax.set_title(f"Phase {phase}: Ids vs Vds, {label} gate voltages "
                     f"(R_low={args.rlow:.0f} ohm)")
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(f"{prefix}_phase{phase}.png", dpi=150)
        plt.close(fig)
        print(f"  wrote {prefix}_phase{phase}.png")

    return curves


# ----------------------------------------------------------------- selftest

def run_selftest(sc: Scanner, args, prefix: str):
    """No-DUT sanity check. With an empty socket there is no current path, so
    every sensed node must track its driving DAC (or rail) and the computed
    currents must be ~0 (their residuals are the rig's noise floors).
    Ratiometric by design: a bandgap-cal error scales SETx and the readback
    identically, so it cancels - this validates wiring/linearity/noise, not
    absolute scale (that needs --cal-vdd against a DMM)."""

    def set_code(cmd):
        m = re.search(r"CODE=(\d+)", sc.ok(cmd))
        return int(m.group(1)) if m else 0

    print("\nSelf-test (no DUT): sweeping both DACs, expecting tracking + zero current")
    sc.ok("LOWIO 0")
    sc.ok("SETG 0")
    sc.ok("SETH 0")
    time.sleep(0.3)

    grid = vrange(0.0, 5.0, 0.25)
    data_h, data_g = [], []
    for h in grid:
        code = set_code(f"SETH {h:.3f}")
        time.sleep(0.03)
        p = measure_point(sc, args.rlow, args.rgate, 0.0)
        p["eff"] = code / 4096.0 * p["vdd"]  # what the DAC was actually told
        data_h.append(p)
    sc.ok("SETH 0")
    for g in grid:
        code = set_code(f"SETG {g:.3f}")
        time.sleep(max(args.gate_settle, 0.1))
        p = measure_point(sc, args.rlow, args.rgate, g)
        p["eff"] = code / 4096.0 * p["vdd"]
        data_g.append(p)
    sc.ok("SETG 0")
    time.sleep(max(args.gate_settle, 0.1))
    sc.ok("LOWIO 1")
    time.sleep(0.1)
    p_hi = measure_point(sc, args.rlow, args.rgate, 0.0)
    sc.ok("LOWIO 0")

    err_h = [p["vhigh"] - p["eff"] for p in data_h]
    err_g = [p["vgate"] - p["eff"] for p in data_g]
    ids_h = [p["ids_ua"] for p in data_h]
    igs_g = [p["igs_ua"] for p in data_g]
    low0 = max(abs(p["vlow"]) for p in data_h)

    def worst(vals):
        i = max(range(len(vals)), key=lambda k: abs(vals[k]))
        return vals[i], grid[i]

    def rms(vals):
        return (sum(v * v for v in vals) / len(vals)) ** 0.5

    wh, wh_at = worst(err_h)
    wg, wg_at = worst(err_g)
    ids_w, ids_at = worst(ids_h)
    igs_w, igs_at = worst(igs_g)
    vdd = data_h[-1]["vdd"]
    dhi = p_hi["vlow"] - p_hi["vdd"]

    ok_h = abs(wh) < 0.040
    ok_g = abs(wg) < 0.060
    ok_ids = abs(ids_w) < 5.0
    ok_igs = abs(igs_w) < 0.10
    ok_low0 = low0 < 0.020
    ok_low1 = abs(dhi) < 0.060 and abs(p_hi["ids_ua"]) < 5.0

    def v(ok):
        return "PASS" if ok else "FAIL"

    print(f"  VDD (bandgap-derived)  : {vdd:.3f} V")
    print(f"  A2 vs DAC_H            : worst {wh * 1000:+7.1f} mV at {wh_at:.2f} V, "
          f"rms {rms(err_h) * 1000:5.1f} mV   [{v(ok_h)}]  (limit 40 mV)")
    print(f"  A1 vs DAC_G (thru 1 M) : worst {wg * 1000:+7.1f} mV at {wg_at:.2f} V, "
          f"rms {rms(err_g) * 1000:5.1f} mV   [{v(ok_g)}]  (limit 60 mV)")
    print(f"  Ids, no DUT            : worst {ids_w:+7.2f} uA at H={ids_at:.2f} V"
          f"                [{v(ok_ids)}]  (limit 5 uA)")
    print(f"  Igs, no DUT            : worst {igs_w:+7.3f} uA at G={igs_at:.2f} V"
          f"                [{v(ok_igs)}]  (limit 0.10 uA)")
    print(f"  Low rail, LOWIO=0      : |Vlow| max {low0 * 1000:5.1f} mV"
          f"                          [{v(ok_low0)}]  (limit 20 mV)")
    print(f"  Low rail, LOWIO=1      : Vlow {p_hi['vlow']:.3f} V vs VDD {p_hi['vdd']:.3f} V "
          f"(delta {dhi * 1000:+.1f} mV), Ids {p_hi['ids_ua']:+.2f} uA   [{v(ok_low1)}]")
    if abs(wh) > 0.5 or abs(wg) > 0.5:
        print("  HINT: a sense that sits ~1.5-2 V no matter the command is a floating pin - check that wire.")
    print("  NOTE: ratiometric test (bandgap error cancels); absolute scale needs --cal-vdd <DMM volts>.")

    csv_path = f"{prefix}_selftest.csv"
    with open(csv_path, "w", newline="") as f:
        f.write("sweep, cmd (V), eff (V), Vhigh, Vgate, Vlow, Vlowio, Ids (uA), Igs (uA), VDD, flag\n")
        for tag, dat in (("H", data_h), ("G", data_g)):
            for cmd, p in zip(grid, dat):
                f.write(f"{tag}, {cmd:.3f}, {p['eff']:.4f}, {p['vhigh']:.4f}, {p['vgate']:.4f}, "
                        f"{p['vlow']:.4f}, {p['vlowio']:.4f}, {p['ids_ua']:.3f}, {p['igs_ua']:.4f}, "
                        f"{p['vdd']:.3f}, {p['flag']}\n")
    print(f"  wrote {csv_path}")

    if not args.no_charts:
        fig, axs = plt.subplots(2, 2, figsize=(10, 7))
        for row, (dat, errs, cur, name, color, cur_name) in enumerate((
                (data_h, err_h, ids_h, "A2 (High) vs DAC_H", "#e67e22", "Ids (uA)"),
                (data_g, err_g, igs_g, "A1 (Gate) vs DAC_G thru 1 Mohm", "#8e44ad", "Igs (uA)"))):
            effs = [p["eff"] for p in dat]
            meas = [p["vhigh"] if row == 0 else p["vgate"] for p in dat]
            ax = axs[row][0]
            ax.plot([0, 5], [0, 5], "--", color="#bbbbbb", lw=1)
            ax.plot(effs, meas, "o-", ms=3, lw=1, color=color)
            ax.set_title(name, fontsize=9)
            ax.set_xlabel("commanded (V)")
            ax.set_ylabel("measured (V)")
            ax = axs[row][1]
            ax.plot(effs, [e * 1000 for e in errs], "o-", ms=3, lw=1, color=color)
            ax.set_ylabel("voltage error (mV)", color=color)
            ax.set_xlabel("commanded (V)")
            ax.set_title(f"error + {cur_name} (should be ~0)", fontsize=9)
            axb = ax.twinx()
            axb.plot(effs, cur, "s--", ms=3, lw=1, color="#2ca02c")
            axb.set_ylabel(cur_name, color="#2ca02c")
        for a in axs.flat:
            a.grid(alpha=0.3)
        fig.suptitle("Self-test, no DUT (ratiometric)")
        fig.tight_layout()
        fig.savefig(f"{prefix}_selftest.png", dpi=150)
        plt.close(fig)
        print(f"  wrote {prefix}_selftest.png")

    return [
        ("A2 tracks DAC_H", ok_h, f"worst {wh * 1000:+.1f} mV"),
        ("A1 tracks DAC_G", ok_g, f"worst {wg * 1000:+.1f} mV"),
        ("Ids zero floor", ok_ids, f"worst {ids_w:+.2f} uA"),
        ("Igs zero floor", ok_igs, f"worst {igs_w:+.3f} uA"),
        ("Low rail LOWIO=0", ok_low0, f"max {low0 * 1000:.1f} mV"),
        ("Low rail LOWIO=1", ok_low1, f"delta {dhi * 1000:+.1f} mV, Ids {p_hi['ids_ua']:+.2f} uA"),
    ]


# ------------------------------------------------------------ repl / cal

def repl(sc: Scanner):
    print("REPL: type firmware commands (IDN?, SETH 2.5, MEAS?, LOWIO 1, ...). "
          "'quit' to exit.")
    while True:
        try:
            s = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        # Protocol is pure ASCII; drop BOMs/smart-quotes from piped or pasted input
        s = "".join(ch for ch in s if 32 <= ord(ch) < 127).strip()
        if not s:
            continue
        if s.lower() in ("quit", "exit", "q"):
            break
        try:
            if s.upper().startswith("PINTEST"):
                m = re.search(r"\d+", s)
                dur = int(m.group()) if m else 20
                old_timeout = sc.ser.timeout
                sc.ser.timeout = dur + 5
                try:
                    print(f"(PINTEST running, up to ~{dur} s if a hold window was requested)")
                    print(sc.cmd(s))
                finally:
                    sc.ser.timeout = old_timeout
            else:
                print(sc.cmd(s))
        except RuntimeError as e:
            print(f"! {e}")


def cal_vdd(sc: Scanner, dmm_volts: float):
    """Rescale the stored bandgap value so firmware VDD matches a DMM reading of the 5V rail."""
    old = int(re.search(r"CALBG_MV=(\d+)", sc.cmd("CALBG?")).group(1))
    fw_mv = float(re.search(r"VDD_MV=([\d.]+)", sc.cmd("VDD?")).group(1))
    new = int(round(old * (dmm_volts * 1000.0) / fw_mv))
    print(f"Firmware thinks VDD={fw_mv:.1f} mV with CALBG={old} mV; DMM says {dmm_volts * 1000:.1f} mV")
    print(f"Writing CALBG {new}")
    reply = sc.cmd(f"CALBG {new}")
    print(f"  {reply}")
    if not reply.startswith("OK"):
        raise RuntimeError("CALBG failed")
    check = float(re.search(r"VDD_MV=([\d.]+)", sc.cmd("VDD?")).group(1))
    print(f"Firmware now reports VDD={check:.1f} mV "
          f"(error {abs(check - dmm_volts * 1000.0):.1f} mV)")
    sc.vref_int_v = new / 1000.0


# ------------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(
        description="Arduino MOSFET scanner tester (v1)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--port", default=None, help="serial port (auto-detect if omitted)")
    parser.add_argument("--rlow", type=float, default=1000.0, help="R_low burden resistor (ohms)")
    parser.add_argument("--rgate", type=float, default=1e6, help="R_gate series resistor (ohms)")
    parser.add_argument("--h-start", type=float, default=0.0, help="DAC_H sweep start (V)")
    parser.add_argument("--h-stop", type=float, default=5.0, help="DAC_H sweep stop (V)")
    parser.add_argument("--h-step", type=float, default=0.1, help="DAC_H sweep step (V)")
    parser.add_argument("--g-start", type=float, default=0.0, help="DAC_G sweep start (V)")
    parser.add_argument("--g-stop", type=float, default=5.0, help="DAC_G sweep stop (V)")
    parser.add_argument("--g-step", type=float, default=0.25, help="DAC_G sweep step (V)")
    parser.add_argument("--avg", type=int, default=32, help="ADC samples per pin per ref")
    parser.add_argument("--settle", type=float, default=0.01, help="delay after SETH (s)")
    parser.add_argument("--gate-settle", type=float, default=0.2,
                        help="delay after SETG (s); gate node is behind 1 Mohm + 10 nF")
    parser.add_argument("--phases", default="123", help="which phases to run, e.g. 23")
    parser.add_argument("--output", default=None,
                        help="output prefix (default: scan-arduino-<timestamp>)")
    parser.add_argument("--no-charts", action="store_true", help="skip PNG generation")
    parser.add_argument("--selftest", action="store_true",
                        help="no-DUT sanity check: sensed voltages must track the DACs with ~0 current")
    parser.add_argument("--repl", action="store_true", help="interactive firmware passthrough")
    parser.add_argument("--cal-vdd", type=float, default=None, metavar="VOLTS",
                        help="calibrate bandgap: DMM reading of the 5V rail, then exit")
    args = parser.parse_args()

    port = args.port or find_port()
    sc = Scanner(port)

    try:
        if args.repl:
            repl(sc)
            return
        if args.cal_vdd is not None:
            cal_vdd(sc, args.cal_vdd)
            return

        if not (sc.has_h and sc.has_g):
            print("The test cycle needs both DACs. Use --repl or --cal-vdd for now; "
                  "add/strap the second MCP4725 for sweeps.")
            return

        prefix = args.output or f"scan-arduino-{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        sc.ok(f"AVG {args.avg}")

        if args.selftest:
            run_selftest(sc, args, prefix)
            return

        n_h = len(vrange(args.h_start, args.h_stop, args.h_step))
        n_g = len(vrange(args.g_start, args.g_stop, args.g_step))
        sweep_phases = [p for p in "23" if p in args.phases]
        est = len(sweep_phases) * n_g * (args.gate_settle + n_h * (0.05 + args.settle))
        print("=" * 60)
        print("Arduino MOSFET scanner")
        print(f"  R_low={args.rlow:.0f} ohm, R_gate={args.rgate:.0f} ohm, AVG={args.avg}")
        print(f"  H: {args.h_start} to {args.h_stop} V step {args.h_step}  ({n_h} pts)")
        print(f"  G: {args.g_start} to {args.g_stop} V step {args.g_step}  ({n_g} pts)")
        print(f"  phases: {args.phases}   est. sweep time ~{est / 60:.1f} min")
        print(f"  output prefix: {prefix}")
        print("=" * 60)

        if "1" in args.phases:
            run_phase1(sc, args, prefix)
        if "2" in args.phases:
            run_sweep_phase(sc, args, prefix, 2)
        if "3" in args.phases:
            run_sweep_phase(sc, args, prefix, 3)

        print("\nDone.")

    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        # Best effort: leave the DUT unbiased
        try:
            sc.cmd("LOWIO 0")
            sc.cmd("SETH 0")
            sc.cmd("SETG 0")
        except Exception:
            pass
        sc.close()


if __name__ == "__main__":
    main()
