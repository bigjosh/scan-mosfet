#!/usr/bin/env python3
"""Generate arduino-scanner/wiring.png — full v1 wiring schematic.

Pure matplotlib; regenerate after wiring changes with:
    python arduino-scanner/wiring_diagram.py
"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

# Net colors
C_5V   = "#cc3333"
C_GND  = "#222222"
C_SDA  = "#1f77b4"
C_SCL1 = "#d6336c"
C_SCL2 = "#0e8f8f"
C_HIGH = "#e67e22"
C_GATE = "#b8860b"
C_A1   = "#8e44ad"  # purple, matches the bench jumper
C_LOW  = "#2ca02c"
C_LIO  = "#8c564b"
C_BODY = "#444444"  # resistor/cap bodies

LW = 1.7

fig, ax = plt.subplots(figsize=(13.6, 8.6))
ax.set_xlim(0.1, 15.4)
ax.set_ylim(0.55, 10.15)
ax.set_aspect("equal")
ax.axis("off")


def wire(pts, color, lw=LW):
    xs, ys = zip(*pts)
    ax.plot(xs, ys, color=color, lw=lw, solid_capstyle="round", zorder=2)


def dot(x, y, color):
    ax.plot([x], [y], marker="o", ms=5.5, color=color, zorder=4)


def res_h(x1, x2, y, label=None, label_xy=None):
    """Horizontal resistor zigzag between x1..x2 (leads drawn by the nets)."""
    n, amp = 6, 0.22
    xs, ys = [x1], [y]
    for k in range(1, 2 * n):
        xs.append(x1 + k * (x2 - x1) / (2 * n))
        ys.append(y + (amp if k % 2 == 1 else -amp))
    xs.append(x2); ys.append(y)
    ax.plot(xs, ys, color=C_BODY, lw=1.6, zorder=3)
    if label:
        lx, ly = label_xy if label_xy else ((x1 + x2) / 2, y + 0.28)
        ax.text(lx, ly, label, ha="center", va="bottom", fontsize=7.5, color="#333")


def cap_v(x, y_top, y_bot, label=None):
    gap, w = 0.07, 0.26
    mid = (y_top + y_bot) / 2
    ax.plot([x, x], [y_top, mid + gap], color=C_BODY, lw=1.6, zorder=3)
    ax.plot([x - w, x + w], [mid + gap] * 2, color=C_BODY, lw=2.0, zorder=3)
    ax.plot([x - w, x + w], [mid - gap] * 2, color=C_BODY, lw=2.0, zorder=3)
    ax.plot([x, x], [mid - gap, y_bot], color=C_BODY, lw=1.6, zorder=3)
    if label:
        ax.text(x - 0.36, mid, label, ha="right", va="center", fontsize=7.5, color="#333")


def gnd(x, y):
    """Ground glyph hanging below point (x, y)."""
    for i, w in enumerate((0.26, 0.17, 0.08)):
        ax.plot([x - w, x + w], [y - i * 0.09] * 2, color=C_GND, lw=1.8, zorder=3)


def vdd(x, y):
    """5 V tag: bar + label above point (x, y)."""
    ax.plot([x - 0.16, x + 0.16], [y, y], color=C_5V, lw=2.2, zorder=3)
    ax.text(x, y + 0.07, "5V", ha="center", va="bottom", fontsize=8,
            fontweight="bold", color=C_5V)


def box(x1, y1, x2, y2, fc):
    ax.add_patch(FancyBboxPatch((x1, y1), x2 - x1, y2 - y1,
                                boxstyle="round,pad=0.03",
                                fc=fc, ec="#555", lw=1.3, zorder=1))


# ---------------------------------------------------------------- Arduino Uno
box(0.6, 1.6, 3.5, 9.4, "#e8f3f3")
ax.text(2.05, 9.12, "Arduino Uno R3", ha="center", va="center",
        fontsize=10.5, fontweight="bold", color="#1a4a4a")
ax.text(2.05, 8.82, "(ATmega328P)", ha="center", va="center",
        fontsize=7, color="#1a4a4a")
ax.text(0.45, 5.5, "USB ↔ PC  (COM9 · 115200)", ha="center", va="center",
        rotation=90, fontsize=6.5, color="#777")

ROWS = {"5V": 8.8, "D4": 8.0, "D5": 7.3, "A2": 6.3, "D6": 5.7,
        "A1": 5.1, "A0": 4.5, "A3": 3.8, "D3": 3.1, "GND": 2.0}
ROLES = {"5V": "", "D4": "SDA (shared)", "D5": "SCL1 → DAC_H",
         "A2": "High sense", "D6": "SCL2 → DAC_G", "A1": "Gate sense",
         "A0": "Low sense", "A3": "LOW_IO sense", "D3": "LOW_IO drive",
         "GND": "common ground"}
for name, y in ROWS.items():
    ax.text(3.42, y, name, ha="right", va="center", fontsize=7.5,
            fontweight="bold", family="monospace", color="#1a4a4a")
    if ROLES[name]:
        ax.text(3.62, y + 0.11, ROLES[name], ha="left", va="bottom",
                fontsize=6.2, style="italic", color="#888")

# ------------------------------------------------------------- DAC modules
box(6.0, 6.9, 7.9, 8.5, "#f0ecfa")   # DAC_H
ax.text(6.85, 7.26, "MCP4725 #1\nDAC_H · 0x60", ha="center", va="bottom",
        fontsize=6.8, color="#4a3a6a", linespacing=1.3)
ax.text(6.12, 8.15, "SDA", ha="left", va="center", fontsize=7, family="monospace")
ax.text(6.12, 7.75, "SCL", ha="left", va="center", fontsize=7, family="monospace")
ax.text(7.78, 7.65, "OUT", ha="right", va="center", fontsize=7, family="monospace")
ax.text(6.6, 8.36, "VDD", ha="center", va="top", fontsize=6, family="monospace")
ax.text(6.6, 7.0, "GND", ha="center", va="bottom", fontsize=6, family="monospace")
wire([(6.6, 8.5), (6.6, 8.72)], C_5V); vdd(6.6, 8.72)
wire([(6.6, 6.9), (6.6, 6.68)], C_GND); gnd(6.6, 6.68)

box(6.0, 2.9, 7.9, 4.3, "#f0ecfa")   # DAC_G
ax.text(6.85, 3.5, "MCP4725 #2\nDAC_G · 0x60", ha="center", va="center",
        fontsize=6.8, color="#4a3a6a", linespacing=1.3)
ax.text(6.12, 3.15, "SDA", ha="left", va="center", fontsize=7, family="monospace")
ax.text(6.12, 3.95, "SCL", ha="left", va="center", fontsize=7, family="monospace")
ax.text(7.78, 3.6, "OUT", ha="right", va="center", fontsize=7, family="monospace")
ax.text(6.6, 4.26, "VDD", ha="center", va="top", fontsize=6, family="monospace")
ax.text(6.6, 3.0, "GND", ha="center", va="bottom", fontsize=6, family="monospace")
wire([(6.6, 4.3), (6.6, 4.52)], C_5V); vdd(6.6, 4.52)
wire([(6.6, 2.9), (6.6, 2.68)], C_GND); gnd(6.6, 2.68)

# --------------------------------------------------------------------- DUT
box(13.0, 4.8, 14.7, 6.4, "#fdf6e3")
ax.text(14.05, 5.78, "DUT", ha="center", va="center", fontsize=9.5,
        fontweight="bold", color="#7a5c00")
ax.text(14.05, 5.42, "N- or P-ch\nMOSFET", ha="center", va="center",
        fontsize=6, color="#7a5c00", linespacing=1.25)
ax.text(13.85, 6.28, "High", ha="center", va="top", fontsize=7,
        family="monospace", fontweight="bold")
ax.text(13.12, 5.6, "Gate", ha="left", va="center", fontsize=7,
        family="monospace", fontweight="bold")
ax.text(13.85, 4.92, "Low", ha="center", va="bottom", fontsize=7,
        family="monospace", fontweight="bold")

# ------------------------------------------------------------------- wires
# 5V + GND stubs at the Uno
wire([(3.5, ROWS["5V"]), (3.9, ROWS["5V"]), (3.9, 8.95)], C_5V); vdd(3.9, 8.95)
wire([(3.5, ROWS["GND"]), (3.9, ROWS["GND"])], C_GND); gnd(3.9, ROWS["GND"])

# I2C: shared SDA (D4) to both modules; one SCL per module
wire([(3.5, ROWS["D4"]), (5.2, ROWS["D4"]), (5.2, 3.15), (6.0, 3.15)], C_SDA)
wire([(5.2, ROWS["D4"]), (5.2, 8.15), (6.0, 8.15)], C_SDA)
dot(5.2, ROWS["D4"], C_SDA)
wire([(3.5, ROWS["D5"]), (5.45, ROWS["D5"]), (5.45, 7.75), (6.0, 7.75)], C_SCL1)
wire([(3.5, ROWS["D6"]), (5.6, ROWS["D6"]), (5.6, 3.95), (6.0, 3.95)], C_SCL2)

# High net: DAC_H OUT -> 100R -> High node -> DUT High; A2 taps the node
wire([(7.9, 7.65), (8.3, 7.65)], C_HIGH)
res_h(8.3, 9.7, 7.65, "100 Ω")
wire([(9.7, 7.65), (13.85, 7.65), (13.85, 6.4)], C_HIGH)
wire([(3.5, ROWS["A2"]), (10.6, ROWS["A2"]), (10.6, 7.65)], C_HIGH)
dot(10.6, 7.65, C_HIGH)
ax.text(11.5, 7.78, "High node", ha="center", va="bottom", fontsize=7,
        style="italic", color=C_HIGH)

# Gate net: DAC_G OUT -> 1M -> Gate node -> DUT Gate; A1 + 10nF tap the node
wire([(7.9, 3.6), (8.3, 3.6)], C_GATE)
res_h(8.3, 9.7, 3.6, "R_gate  1 MΩ")
wire([(9.7, 3.6), (12.4, 3.6), (12.4, 5.6), (13.0, 5.6)], C_GATE)
wire([(3.5, ROWS["A1"]), (10.2, ROWS["A1"]), (10.2, 3.6)], C_A1)
dot(10.2, 3.6, C_GATE)
cap_v(10.2, 3.6, 2.95, "10 nF")
gnd(10.2, 2.95)
ax.text(11.15, 3.74, "Gate node", ha="center", va="bottom", fontsize=7,
        style="italic", color=C_GATE)

# Low / LOW_IO: DUT Low -> Low node (A0) -> R_low -> LOW_IO node (A3) -> D3
wire([(13.85, 4.8), (13.85, 2.2), (6.2, 2.2)], C_LOW)
res_h(4.9, 6.2, 2.2, "R_low  1 kΩ (socketed)", label_xy=(5.9, 1.7))
wire([(3.5, ROWS["A0"]), (4.7, ROWS["A0"]), (4.7, 1.6), (8.6, 1.6), (8.6, 2.2)], C_LOW)
dot(8.6, 2.2, C_LOW)
ax.text(9.5, 2.32, "Low node", ha="center", va="bottom", fontsize=7,
        style="italic", color=C_LOW)
wire([(3.5, ROWS["D3"]), (4.0, ROWS["D3"]), (4.0, 2.2), (4.9, 2.2)], C_LIO)
wire([(3.5, ROWS["A3"]), (4.5, ROWS["A3"]), (4.5, 2.2)], C_LIO)
dot(4.5, 2.2, C_LIO)
ax.text(4.64, 2.72, "LOW_IO node", ha="left", va="center", rotation=90,
        fontsize=6, style="italic", color=C_LIO)

# ------------------------------------------------------------ title, legend
ax.text(0.6, 9.88, "Arduino MOSFET Scanner v1 — wiring",
        fontsize=13, fontweight="bold", color="#222")
ax.text(15.3, 9.92, "soft-I2C (bit-banged) · 0–5 V · see plan.md",
        fontsize=7.5, color="#888", ha="right")

x = 0.6
ax.text(x, 1.22, "Nets:", fontsize=7.5, fontweight="bold", color="#333")
x += 0.55
for label, c in [("5V", C_5V), ("GND", C_GND), ("SDA/D4", C_SDA),
                 ("SCL1/D5", C_SCL1), ("SCL2/D6", C_SCL2), ("High", C_HIGH),
                 ("Gate", C_GATE), ("A1 sense", C_A1), ("Low", C_LOW),
                 ("LOW_IO", C_LIO)]:
    ax.plot([x, x + 0.28], [1.26, 1.26], color=c, lw=3)
    ax.text(x + 0.36, 1.22, label, fontsize=7, color="#333", va="center_baseline")
    x += 0.42 + 0.115 * len(label) + 0.25

ax.text(0.6, 0.97,
        "Soft-I2C: lines are only sunk low or released (open-drain) — shared SDA, one SCL per DAC "
        "→ identical modules, no address straps (both 0x60). Swap the D5/D6 wires to swap DAC roles.",
        fontsize=7, color="#555")
ax.text(0.6, 0.72,
        "All grounds common (Uno + modules + DUT return).   Ids = (V_A0 − V_A3) / R_low     "
        "Igs = (V_cmdG − V_A1) / R_gate     Vds = V_A2 − V_A0     Vgs = V_A1 − V_A0",
        fontsize=7, color="#555")

fig.tight_layout(pad=0.4)
out = __file__.replace("wiring_diagram.py", "wiring.png")
fig.savefig(out, dpi=200, facecolor="white")
print(f"wrote {out}")
