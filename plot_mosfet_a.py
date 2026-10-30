#!/usr/bin/env python3
"""
Two-panel live view of a scan_mosfet_a.py scan.

  left   Ids versus Vds, one curve per Vgs (the visualize_iv.py view)
  right  gate current Ig versus Vgs, one curve per Vds

Columns are found by name, so the CSV may carry extra columns. A scan_mosfet.py
file also loads; its gate panel is simply empty. The plot redraws whenever the
file changes, so it can run beside a scan.

Usage:
    python plot_mosfet_a.py                       # newest scan-mosfet-a-*.csv, live
    python plot_mosfet_a.py --batch NAME          # newest scan in batches/NAME/
    python plot_mosfet_a.py scan.csv --static     # one-off window
    python plot_mosfet_a.py --save out.png        # render to a file, no window
    python plot_mosfet_a.py --device-v            # x axes = device voltages
                                                  #   (setpoint minus the drop across R_sense)
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
from matplotlib.colors import ListedColormap, Normalize

# canonical key -> accepted header prefixes (lower case, after stripping)
COLUMNS = {
    "vds": ("vds (v)",),
    "vgs": ("vgs (v)",),
    "ids": ("ids (ua)",),
    "vds_dev": ("vds dev", "vds meas"),
    "vgs_dev": ("vgs dev",),
    "ig": ("ig (ua)",),
}

# The palest end of a sequential ramp is invisible on a white surface; start here.
RAMP_START = 0.35
INK = "#444444"


def load_scan(path):
    """Return {key: ndarray} for the columns present in a scan CSV (see COLUMNS)."""
    with open(path, newline="") as f:
        reader = csv.reader(f, skipinitialspace=True)
        try:
            header = [h.strip().lower() for h in next(reader)]
        except StopIteration:
            return {}
        idx = {}
        for key, prefixes in COLUMNS.items():
            for i, h in enumerate(header):
                if h.startswith(prefixes):
                    idx[key] = i
                    break
        if not all(k in idx for k in ("vds", "vgs", "ids")):
            raise ValueError(f"{path}: not a scan CSV (needs Vds, Vgs and Ids columns)")
        need = max(idx.values())
        data = {k: [] for k in idx}
        for row in reader:
            if len(row) <= need or not row[0].strip():
                continue  # partially written last line during a live scan
            try:
                vals = {k: float(row[i]) for k, i in idx.items()}
            except ValueError:
                continue
            for k, v in vals.items():
                data[k].append(v)
    return {k: np.array(v) for k, v in data.items()}


def clipped_cmap(name):
    """A sequential ramp without its pale end, as a colormap of its own."""
    base = plt.get_cmap(name)
    return ListedColormap(base(np.linspace(RAMP_START, 1.0, 256)), name=f"{name}_clipped")


def draw_family(ax, x, y, series, cmap, series_label, log_scale):
    """Plot y(x) with one curve per distinct value of `series`, colored light to dark.

    Returns (values, norm). Direct labels go on the first and last curve; the
    colorbar (added by the caller) is the legend for the rest.
    """
    vals = np.unique(np.round(series, 6))
    lo, hi = vals.min(), vals.max()
    norm = Normalize(vmin=lo, vmax=hi if hi > lo else lo + 1.0)
    for v in vals:
        m = np.isclose(series, v)
        order = np.argsort(x[m])
        xs, ys = x[m][order], y[m][order]
        if log_scale:
            ys = np.abs(ys)
        ax.plot(xs, ys, color=cmap(norm(v)), lw=1.6, marker=".", ms=4,
                label=f"{series_label} = {v:g} V")
        if v in (lo, hi) and len(xs):
            ax.annotate(f"{series_label} {v:g} V", (xs[-1], ys[-1]), xytext=(5, 0),
                        textcoords="offset points", fontsize=8, color=INK,
                        va="top" if v == lo else "bottom")
    # Room on the right for the direct labels (they would otherwise run under the colorbar)
    x0, x1 = ax.get_xlim()
    ax.set_xlim(x0, x1 + 0.14 * (x1 - x0))
    if len(vals) <= 12:
        # Highest value on top, matching the way the curves stack
        handles, labels = ax.get_legend_handles_labels()
        ax.legend(handles[::-1], labels[::-1], fontsize=8, framealpha=0.85)
    if log_scale:
        ax.set_yscale("log")
    ax.grid(True, which="both", ls="--", alpha=0.3)
    return vals, norm


class ScanPlot:
    def __init__(self, csv_file, device_v=False, log_ids=False, log_ig=False,
                 cmap_ids="Blues", cmap_ig="Oranges", photo_panel=True):
        self.csv_file = csv_file
        self.device_v = device_v
        self.log_ids = log_ids
        self.log_ig = log_ig
        self.cmap_ids = clipped_cmap(cmap_ids)
        self.cmap_ig = clipped_cmap(cmap_ig)
        self.last_mtime = None
        self.warned = set()

        self.photo_panel = photo_panel
        if photo_panel:
            self.fig = plt.figure(figsize=(18, 6.5))
            gs = self.fig.add_gridspec(1, 3, width_ratios=[1.0, 1.0, 0.55])
            self.ax_ids = self.fig.add_subplot(gs[0, 0])
            self.ax_ig = self.fig.add_subplot(gs[0, 1])
            self.ax_photo = self.fig.add_subplot(gs[0, 2])  # phone photo from photo_drop.py, if any
        else:
            self.fig, (self.ax_ids, self.ax_ig) = plt.subplots(1, 2, figsize=(15, 6.5))
            self.ax_photo = None
        self.cb_ids = None
        self.cb_ig = None

    def _warn_once(self, key, msg):
        if key not in self.warned:
            print(msg)
            self.warned.add(key)

    def _colorbar(self, which, ax, norm, cmap, label):
        sm = ScalarMappable(norm=norm, cmap=cmap)
        sm.set_array([])
        cb = getattr(self, which)
        if cb is None:
            cb = self.fig.colorbar(sm, ax=ax, pad=0.02)
            cb.set_label(label, fontsize=11)
            setattr(self, which, cb)
        else:
            cb.update_normal(sm)

    def photo_path(self):
        """First photo attached to this scan (<csv basename>.jpg next to it), or None."""
        base, _ = os.path.splitext(self.csv_file)
        for ext in (".jpg", ".jpeg", ".png"):
            if os.path.exists(base + ext):
                return base + ext
        return None

    def _draw_photo(self, photo):
        ax = self.ax_photo
        ax.clear()
        ax.axis("off")
        if photo is None:
            ax.text(0.5, 0.5, "no photo yet\n(photo_drop.py attaches one)", ha="center", va="center",
                    transform=ax.transAxes, fontsize=10, color=INK)
            return
        try:
            ax.imshow(plt.imread(photo))
        except Exception as e:
            ax.text(0.5, 0.5, f"cannot read photo\n{e}", ha="center", va="center",
                    transform=ax.transAxes, fontsize=9, color=INK)
            return
        ax.set_title(os.path.basename(photo), fontsize=10)
        note = os.path.splitext(self.csv_file)[0] + ".txt"
        if os.path.exists(note):
            try:
                with open(note, encoding="utf-8", errors="replace") as f:
                    text = f.read().strip()
            except OSError:
                text = ""
            if text:
                ax.text(0.0, -0.03, text[:400], transform=ax.transAxes, va="top", ha="left",
                        fontsize=9, color=INK, wrap=True)

    def update(self, _frame=None):
        try:
            mtime = os.path.getmtime(self.csv_file)
        except OSError:
            return
        photo = self.photo_path() if self.photo_panel else None
        state = (mtime, os.path.getmtime(photo) if photo else None)
        if state == self.last_mtime:
            return
        self.last_mtime = state

        try:
            d = load_scan(self.csv_file)
        except OSError:
            return  # file busy while the scanner writes; try again next tick
        if not d or len(d["vds"]) == 0:
            return

        # x axes: setpoints, or the device voltages when asked for and present
        x_ds, x_gs = d["vds"], d["vgs"]
        ds_label, gs_label = "Vds setpoint (V)", "Vgs commanded (V)"
        if self.device_v:
            if "vds_dev" in d:
                x_ds, ds_label = d["vds_dev"], "Vds at device (V)"
            else:
                self._warn_once("vds_dev", "No device-Vds column in this CSV; using the setpoint.")
            if "vgs_dev" in d:
                x_gs, gs_label = d["vgs_dev"], "Vgs at device (V)"
            else:
                self._warn_once("vgs_dev", "No device-Vgs column in this CSV; using the commanded Vgs.")

        # ---- left: Ids vs Vds, one curve per commanded Vgs
        ax = self.ax_ids
        ax.clear()
        vgs_vals, norm = draw_family(ax, x_ds, d["ids"] * 1e-3, d["vgs"], self.cmap_ids, "Vgs", self.log_ids)
        ax.set_xlabel(ds_label, fontsize=11)
        ax.set_ylabel("|Ids| (mA)" if self.log_ids else "Ids (mA)", fontsize=11)
        ax.set_title(f"Ids vs Vds, one curve per Vgs ({len(vgs_vals)} curves)", fontsize=12)
        self._colorbar("cb_ids", ax, norm, self.cmap_ids, "Vgs (V)")

        # ---- right: gate current vs Vgs, one curve per commanded Vds
        ax = self.ax_ig
        ax.clear()
        ig = d.get("ig")
        if ig is None or not np.isfinite(ig).any():
            ax.text(0.5, 0.5, "no gate current in this file", ha="center", va="center",
                    transform=ax.transAxes, fontsize=12, color=INK)
            ax.set_xticks([])
            ax.set_yticks([])
            ax.set_title("Gate current vs Vgs, one curve per Vds", fontsize=12)
        else:
            if np.nanmax(np.abs(ig)) < 1.0:
                scale, unit = 1e3, "nA"
            else:
                scale, unit = 1.0, "uA"
            vds_vals, norm = draw_family(ax, x_gs, ig * scale, d["vds"], self.cmap_ig, "Vds", self.log_ig)
            if not self.log_ig:
                ax.axhline(0, color="#999999", lw=0.8, ls="--")
            ax.set_xlabel(gs_label, fontsize=11)
            ax.set_ylabel(f"|Ig| ({unit})" if self.log_ig else f"Ig ({unit}), positive = into the gate",
                          fontsize=11)
            ax.set_title(f"Gate current vs Vgs, one curve per Vds ({len(vds_vals)} curves)", fontsize=12)
            self._colorbar("cb_ig", ax, norm, self.cmap_ig, "Vds (V)")

        if self.photo_panel:
            self._draw_photo(photo)
        self.fig.suptitle(f"{os.path.basename(self.csv_file)}: {len(d['vds'])} points", fontsize=13)
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

    def save(self, path):
        self.update()
        self.fig.savefig(path, dpi=130, bbox_inches="tight")
        print(f"Saved: {path}")


def newest_scan_file(search_dir="."):
    """Newest scan-mosfet-a-*.csv in the folder, else newest scan-mosfet-*.csv."""
    for pattern in ("scan-mosfet-a-*.csv", "scan-mosfet-*.csv"):
        files = [p for p in Path(search_dir).glob(pattern)
                 if not p.stem.endswith(("_results", "_filtered"))]
        if files:
            return max(files, key=lambda p: p.stat().st_mtime)
    return None


def resolve_csv(csv_file, batch):
    """Pick the CSV to plot: an explicit file, or the newest scan in the batch folder (or cwd)."""
    search_dir = os.path.join("batches", batch) if batch else "."
    if csv_file is None:
        newest = newest_scan_file(search_dir)
        if newest is None:
            print(f"Error: no scan-mosfet*.csv files found in {search_dir}")
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
        description="Live two-panel plot of a scan_mosfet_a.py scan: Ids vs Vds per Vgs, "
                    "and gate current vs Vgs per Vds",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("csv_file", nargs="?", default=None,
                        help="Scan CSV to plot (default: newest scan-mosfet-a-*.csv)")
    parser.add_argument("--batch", default=None,
                        help="Look in batches/<name>/ for the newest scan (or for a bare csv_file name)")
    parser.add_argument("--refresh", type=float, default=1.0,
                        help="Refresh interval in seconds for live mode")
    parser.add_argument("--static", action="store_true",
                        help="Draw once instead of live-updating")
    parser.add_argument("--save", metavar="PNG", default=None,
                        help="Render once to this image file and exit (no window)")
    parser.add_argument("--device-v", action="store_true",
                        help="Use the device voltages (setpoint minus the drop across R_sense) on the x axes")
    parser.add_argument("--log", action="store_true",
                        help="Plot |Ids| on a log scale")
    parser.add_argument("--log-ig", action="store_true",
                        help="Plot |Ig| on a log scale")
    parser.add_argument("--cmap-ids", default="Blues",
                        help="Sequential colormap for the Vgs curves of the Ids panel")
    parser.add_argument("--cmap-ig", default="Oranges",
                        help="Sequential colormap for the Vds curves of the gate panel")
    args = parser.parse_args()

    csv_file = resolve_csv(args.csv_file, args.batch)
    if not os.path.exists(csv_file):
        print(f"Error: file not found: {csv_file}")
        sys.exit(1)

    if args.save:
        plt.switch_backend("Agg")
    plot = ScanPlot(csv_file, args.device_v, args.log, args.log_ig, args.cmap_ids, args.cmap_ig)
    if args.save:
        plot.save(args.save)
    elif args.static:
        plot.show_static()
    else:
        plot.run_live(args.refresh)


if __name__ == "__main__":
    main()
