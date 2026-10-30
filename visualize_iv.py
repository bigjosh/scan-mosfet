#!/usr/bin/env python3
"""
Live 2D view of a MOSFET scan: Ids versus Vds, one colored curve per Vgs.

Reads the long-format CSV written by scan_mosfet.py
(Vds setpoint, Vgs, Ids in uA, and optionally Vds measured by the Joulescope)
and redraws whenever the file changes, so it can run alongside a scan.

Usage:
    python visualize_iv.py                      # newest scan-mosfet-*.csv, live
    python visualize_iv.py scan.csv --static    # one-off plot
    python visualize_iv.py --measured-vds       # x axis = Joulescope Vds
    python visualize_iv.py --log                # |Ids| on a log axis
    python visualize_iv.py --batch NAME         # newest scan in batches/NAME/
"""

import argparse
import csv
import os
import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize


def load_scan(path, use_measured_vds):
    """Return (vds, vgs, ids_mA, used_measured) arrays from a long-format scan CSV."""
    vds, vgs, ids = [], [], []
    with open(path, newline="") as f:
        reader = csv.reader(f, skipinitialspace=True)
        try:
            header = next(reader)
        except StopIteration:
            return np.array([]), np.array([]), np.array([]), False

        unit = header[2].lower() if len(header) > 2 else "ua"
        # Convert current to milliamps.
        scale = 1e-3 if "ua" in unit else (1.0 if "ma" in unit else 1e3)

        has_measured = len(header) > 3 and any(k in header[3].lower() for k in ("meas", "dev"))
        used_measured = use_measured_vds and has_measured

        for row in reader:
            if len(row) < 3 or not row[0].strip():
                continue
            try:
                x = float(row[3]) if used_measured else float(row[0])
                vds.append(x)
                vgs.append(float(row[1]))
                ids.append(float(row[2]) * scale)
            except (ValueError, IndexError):
                continue  # partially written last line during a live scan

    return np.array(vds), np.array(vgs), np.array(ids), used_measured


class IVViewer:
    def __init__(self, csv_file, use_measured_vds=False, log_scale=False, cmap="viridis"):
        self.csv_file = csv_file
        self.use_measured_vds = use_measured_vds
        self.log_scale = log_scale
        self.cmap = plt.get_cmap(cmap)
        self.last_mtime = None
        self.warned_no_measured = False

        self.fig, self.ax = plt.subplots(figsize=(11, 7))
        self.colorbar = None

    def update(self, _frame=None):
        try:
            mtime = os.path.getmtime(self.csv_file)
        except OSError:
            return
        if mtime == self.last_mtime:
            return
        self.last_mtime = mtime

        try:
            vds, vgs, ids, used_measured = load_scan(self.csv_file, self.use_measured_vds)
        except OSError:
            return  # file busy while the scanner writes; try again next tick
        if len(vds) == 0:
            return

        if self.use_measured_vds and not used_measured and not self.warned_no_measured:
            print("This CSV has no measured Vds column; plotting against the Vds setpoint.")
            self.warned_no_measured = True

        vgs_vals = np.unique(np.round(vgs, 6))
        lo, hi = vgs_vals.min(), vgs_vals.max()
        norm = Normalize(vmin=lo, vmax=hi if hi > lo else lo + 1.0)

        ax = self.ax
        ax.clear()
        for g in vgs_vals:
            m = np.isclose(vgs, g)
            order = np.argsort(vds[m])
            y = ids[m][order]
            if self.log_scale:
                y = np.abs(y)
            ax.plot(vds[m][order], y, color=self.cmap(norm(g)), lw=1.5,
                    marker=".", ms=4, label=f"Vgs = {g:.2f} V")

        x_label = "Vds measured by Joulescope (V)" if used_measured else "Vds setpoint (V)"
        ax.set_xlabel(x_label, fontsize=12)
        ax.set_ylabel("|Ids| (mA)" if self.log_scale else "Ids (mA)", fontsize=12)
        if self.log_scale:
            ax.set_yscale("log")
        ax.grid(True, which="both", ls="--", alpha=0.35)
        ax.set_title(f"Ids vs Vds per Vgs: {os.path.basename(self.csv_file)}\n"
                     f"{len(vgs_vals)} Vgs curves, {len(vds)} points", fontsize=13)

        # A legend is readable for a handful of curves; the colorbar covers the rest.
        if len(vgs_vals) <= 12:
            # Highest Vgs on top, matching the way the curves stack
            handles, labels = ax.get_legend_handles_labels()
            ax.legend(handles[::-1], labels[::-1], loc="upper left", fontsize=9, framealpha=0.85)

        sm = ScalarMappable(norm=norm, cmap=self.cmap)
        sm.set_array([])
        if self.colorbar is None:
            self.colorbar = self.fig.colorbar(sm, ax=ax, pad=0.02)
            self.colorbar.set_label("Vgs (V)", fontsize=12)
        else:
            self.colorbar.update_normal(sm)

        self.fig.canvas.draw_idle()

    def run_live(self, refresh):
        print(f"Monitoring: {self.csv_file}")
        print("Close the plot window to exit.")
        self.update()
        # Keep a reference so the animation is not garbage-collected.
        self._anim = FuncAnimation(self.fig, self.update, interval=int(refresh * 1000),
                                   cache_frame_data=False)
        plt.show()

    def show_static(self):
        self.update()
        plt.show()


def newest_scan_file(search_dir="."):
    files = [p for p in Path(search_dir).glob("scan-mosfet-*.csv")
             if not p.stem.endswith(("_results", "_filtered"))]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def resolve_csv(csv_file, batch):
    """Pick the CSV to plot: an explicit file, or the newest scan in the batch folder (or cwd)."""
    search_dir = os.path.join("batches", batch) if batch else "."
    if csv_file is None:
        newest = newest_scan_file(search_dir)
        if newest is None:
            print(f"Error: no scan-mosfet-*.csv files found in {search_dir}")
            sys.exit(1)
        print(f"Auto-detected file: {newest}")
        return str(newest)
    # A bare filename with --batch is looked up inside the batch folder
    in_batch = os.path.join(search_dir, csv_file)
    if batch and not os.path.exists(csv_file) and os.path.exists(in_batch):
        return in_batch
    return str(csv_file)


def main():
    parser = argparse.ArgumentParser(
        description="Live 2D plot of Ids vs Vds with one colored curve per Vgs",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("csv_file", nargs="?", default=None,
                        help="Scan CSV to plot (default: newest scan-mosfet-*.csv)")
    parser.add_argument("--refresh", type=float, default=1.0,
                        help="Refresh interval in seconds for live mode")
    parser.add_argument("--static", action="store_true",
                        help="Draw once instead of live-updating")
    parser.add_argument("--measured-vds", action="store_true",
                        help="Use the Joulescope-measured Vds for the x axis instead of the setpoint")
    parser.add_argument("--log", action="store_true",
                        help="Plot |Ids| on a log scale (shows off-state leakage)")
    parser.add_argument("--cmap", default="viridis",
                        help="Matplotlib colormap used to color the Vgs curves")
    parser.add_argument("--batch", default=None,
                        help="Look in batches/<name>/ for the newest scan (or for a bare csv_file name)")
    args = parser.parse_args()

    csv_file = resolve_csv(args.csv_file, args.batch)
    if not os.path.exists(csv_file):
        print(f"Error: file not found: {csv_file}")
        sys.exit(1)

    viewer = IVViewer(csv_file, args.measured_vds, args.log, args.cmap)
    if args.static:
        viewer.show_static()
    else:
        viewer.run_live(args.refresh)


if __name__ == "__main__":
    main()
