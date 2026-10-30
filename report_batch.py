#!/usr/bin/env python3
"""
Build a shareable HTML report for a batch of scans.

    python report_batch.py --batch 2026-10-30-new-tubes            # batches/<name>/index.html + <name>.html
    python report_batch.py --batch 2026-10-30-new-tubes --publish  # also copy into docs/batches/<name>/ (GitHub Pages)

Each scan in the batch gets, in time order, its two-panel plot (Ids vs Vds per
Vgs, gate current vs Vgs per Vds), its phone photos and note, a few headline
numbers, and links to the raw files.

  index.html   links to the CSVs, notes and photos sitting next to it
  <name>.html  embeds all of them: one file you can send to anyone
"""

import argparse
import base64
import html
import io
import re
import shutil
from datetime import datetime
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from plot_mosfet_a import ScanPlot, load_scan

PHOTO_EXTS = (".jpg", ".jpeg", ".png")
MIME = {".csv": "text/csv", ".txt": "text/plain", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
REPO_URL = "https://github.com/bigjosh/scan-mosfet"
PAGES_URL = "https://bigjosh.github.io/scan-mosfet/batches/"

CSS = """
 body{font-family:system-ui,-apple-system,sans-serif;max-width:1240px;margin:24px auto;padding:0 16px;color:#222;line-height:1.45}
 header p{color:#666} a{color:#1f5fbf}
 table.summary{border-collapse:collapse;width:100%;font-size:14px;margin:18px 0}
 table.summary th,table.summary td{padding:6px 8px;border-bottom:1px solid #ddd;text-align:left;white-space:nowrap}
 table.summary td.note{white-space:normal;color:#555}
 section.scan{margin-top:44px;border-top:2px solid #eee;padding-top:14px}
 section.scan h2{font-size:20px;margin:0 0 6px}
 .badges span{display:inline-block;background:#eef3ff;border-radius:6px;padding:2px 9px;margin:2px 6px 2px 0;font-size:13px}
 .badges span.warn{background:#fde8e8;color:#9b1c1c}
 .plot svg{max-width:100%;height:auto;display:block;margin:12px 0}
 .photos{display:flex;gap:14px;flex-wrap:wrap;margin:10px 0}
 .photos figure{margin:0;font-size:12px;color:#666}
 .photos img{max-width:360px;max-height:360px;border-radius:6px;display:block}
 pre.note{background:#f6f8fa;padding:8px 12px;white-space:pre-wrap;border-radius:6px;font-size:14px}
 p.files{font-size:14px;color:#444} p.empty{color:#9b1c1c}
 footer{margin:48px 0 24px;color:#888;font-size:13px}
"""


def scan_time(base, path):
    """Timestamp from the filename (scan-mosfet-a-YYYYMMDD_HHMMSS), else the file's mtime."""
    m = re.search(r"(\d{8}_\d{6})", base)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y%m%d_%H%M%S")
        except ValueError:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime)


def find_scans(folder: Path):
    """Scans in the folder, oldest first, each with its photos and note."""
    scans = []
    for p in folder.glob("scan-*.csv"):
        if p.stem.endswith(("_results", "_filtered")):
            continue
        pat = re.compile(re.escape(p.stem) + r"(-\d+)?")
        photos = sorted(q for q in folder.iterdir()
                        if q.suffix.lower() in PHOTO_EXTS and pat.fullmatch(q.stem))
        note = folder / f"{p.stem}.txt"
        scans.append({"csv": p, "base": p.stem, "time": scan_time(p.stem, p),
                      "photos": photos, "note": note if note.exists() else None})
    scans.sort(key=lambda s: s["time"])
    return scans


def stats(d, csv_path: Path):
    """Headline numbers for one scan."""
    vds, vgs, ids = d["vds"], d["vgs"], d["ids"] / 1000.0  # mA
    s = {"points": len(vds), "vgs_lo": vgs.min(), "vgs_hi": vgs.max(),
         "vds_lo": vds.min(), "vds_hi": vds.max()}
    k = int(np.argmax(ids))
    s["ids_max"], s["ids_max_at"] = ids[k], (vgs[k], vds[k])
    top = np.isclose(vds, vds.max())
    lo, hi = top & np.isclose(vgs, vgs.min()), top & np.isclose(vgs, vgs.max())
    s["ids_off"] = float(ids[lo][0]) if lo.any() else None
    s["ids_on"] = float(ids[hi][0]) if hi.any() else None
    s["ratio"] = (s["ids_on"] / s["ids_off"]) if s["ids_off"] and s["ids_on"] and s["ids_off"] > 0 else None
    ig = d.get("ig")
    s["ig_max_na"] = float(np.nanmax(np.abs(ig))) * 1000 if ig is not None and np.isfinite(ig).any() else None
    with open(csv_path, encoding="utf-8", errors="replace") as f:
        s["clips"] = sum(1 for line in f if line.rstrip().endswith("clip"))
    return s


def render_svg(csv_path: Path, base: str) -> str:
    """The two-panel plot as an inline <svg> element."""
    plt.rcParams["svg.hashsalt"] = base      # unique element ids per figure: several SVGs share one page
    plt.rcParams["svg.fonttype"] = "none"    # keep text as text (smaller, selectable)
    plot = ScanPlot(str(csv_path), photo_panel=False)
    plot.update()
    buf = io.BytesIO()
    plot.fig.savefig(buf, format="svg", bbox_inches="tight")
    plt.close(plot.fig)
    svg = buf.getvalue().decode("utf-8")
    return svg[svg.find("<svg"):]


def ref(path: Path, embed: bool) -> str:
    """href/src for a file next to the report, or a data: URL when embedding."""
    if not embed:
        return path.name
    mime = MIME.get(path.suffix.lower(), "application/octet-stream")
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def fmt_range(lo, hi):
    return f"{lo:g} to {hi:g} V"


def build_html(batch: str, scans, embed: bool) -> str:
    e = html.escape
    rows, sections = [], []
    for i, s in enumerate(scans, 1):
        base, t = s["base"], s["time"].strftime("%H:%M:%S")
        try:
            d = load_scan(s["csv"])
        except Exception:
            d = {}
        note_text = s["note"].read_text(encoding="utf-8", errors="replace").strip() if s["note"] else ""
        first_note = note_text.splitlines()[0][:80] if note_text else ""
        files = [f'<a href="{e(ref(s["csv"], embed))}" download="{e(s["csv"].name)}">{e(s["csv"].name)}</a>']
        if s["note"]:
            files.append(f'<a href="{e(ref(s["note"], embed))}" download="{e(s["note"].name)}">{e(s["note"].name)}</a>')
        for p in s["photos"]:
            files.append(f'<a href="{e(ref(p, embed))}" download="{e(p.name)}">{e(p.name)}</a>')
        photos_html = "".join(
            f'<figure><img src="{e(ref(p, embed))}" alt="{e(p.name)}" loading="lazy">'
            f'<figcaption>{e(p.name)}</figcaption></figure>' for p in s["photos"])
        note_html = f'<pre class="note">{e(note_text)}</pre>' if note_text else ""

        if not d or len(d["vds"]) == 0:
            rows.append(f'<tr><td>{i}</td><td>{t}</td><td><a href="#{e(base)}">{e(base)}</a></td>'
                        f'<td>0</td><td colspan="5">no data</td><td>{len(s["photos"])}</td>'
                        f'<td class="note">{e(first_note)}</td></tr>')
            sections.append(f'<section class="scan" id="{e(base)}"><h2>#{i} · {t} · {e(base)}</h2>'
                            f'<p class="empty">No data points in this file.</p>'
                            f'<div class="photos">{photos_html}</div>{note_html}'
                            f'<p class="files">Files: {" · ".join(files)}</p></section>')
            continue

        st = stats(d, s["csv"])
        svg = render_svg(s["csv"], base)
        ig_txt = f"{st['ig_max_na']:.1f} nA" if st["ig_max_na"] is not None else "n/a"
        ratio_txt = f"{st['ratio']:.1f}:1" if st["ratio"] else "n/a"
        rows.append(
            f'<tr><td>{i}</td><td>{t}</td><td><a href="#{e(base)}">{e(base)}</a></td>'
            f'<td>{st["points"]}</td><td>{fmt_range(st["vgs_lo"], st["vgs_hi"])}</td>'
            f'<td>{fmt_range(st["vds_lo"], st["vds_hi"])}</td><td>{st["ids_max"]:.3f} mA</td>'
            f'<td>{ratio_txt}</td><td>{ig_txt}</td><td>{len(s["photos"])}</td>'
            f'<td class="note">{e(first_note)}</td></tr>')
        badges = [
            f"{st['points']} points",
            f"Vgs {fmt_range(st['vgs_lo'], st['vgs_hi'])}",
            f"Vds {fmt_range(st['vds_lo'], st['vds_hi'])}",
            f"max Ids {st['ids_max']:.3f} mA at Vgs {st['ids_max_at'][0]:g} V, Vds {st['ids_max_at'][1]:g} V",
        ]
        if st["ids_off"] is not None:
            badges.append(f"Ids at Vgs {st['vgs_lo']:g} V, Vds {st['vds_hi']:g} V: {st['ids_off']:.3f} mA")
        if st["ratio"]:
            badges.append(f"on/off at Vds {st['vds_hi']:g} V: {ratio_txt}")
        badges.append(f"max |Ig| {ig_txt}")
        if st["clips"]:
            badges.append(f'<span class="warn">{st["clips"]} clipped A0 readings</span>')
        badge_html = "".join(b if b.startswith("<span") else f"<span>{e(b)}</span>" for b in badges)
        sections.append(
            f'<section class="scan" id="{e(base)}"><h2>#{i} · {t} · {e(base)}</h2>'
            f'<div class="badges">{badge_html}</div>'
            f'<div class="plot">{svg}</div>'
            f'<div class="photos">{photos_html}</div>{note_html}'
            f'<p class="files">Files: {" · ".join(files)}</p></section>')

    kind = "self-contained: plots, photos and data are embedded" if embed else "data files sit next to this page"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Batch {e(batch)}</title>
<style>{CSS}</style></head>
<body>
<header><h1>Batch {e(batch)}</h1>
<p>{len(scans)} scans · generated {datetime.now():%Y-%m-%d %H:%M} · {kind} · <a href="{REPO_URL}">scan-mosfet</a></p></header>
<table class="summary"><thead><tr><th>#</th><th>Time</th><th>Scan</th><th>Points</th><th>Vgs</th><th>Vds</th>
<th>Max Ids</th><th>On/off</th><th>Max |Ig|</th><th>Photos</th><th>Note</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>
{"".join(sections)}
<footer>Generated by report_batch.py. Left panel: Ids vs Vds, one curve per Vgs. Right panel: gate current vs Vgs, one curve per Vds, positive = into the gate.</footer>
</body></html>
"""


def write_batches_index(root: Path):
    """docs/batches/index.html: one line per published batch, newest first."""
    e = html.escape
    items = []
    for d in sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True):
        n = len(list(d.glob("scan-*.csv")))
        items.append(f'<li><a href="{e(d.name)}/">{e(d.name)}</a> · {n} scans · '
                     f'<a href="{e(d.name)}/{e(d.name)}.html" download>single-file copy</a></li>')
    (root / "index.html").write_text(
        f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>Scan batches</title>"
        f"<style>{CSS}</style></head><body><header><h1>Scan batches</h1>"
        f"<p><a href=\"{REPO_URL}\">scan-mosfet</a></p></header><ul>{''.join(items)}</ul></body></html>\n",
        encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Build an HTML report for a batch of scans",
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--batch", required=True, help="Batch name (folder under batches/)")
    parser.add_argument("--publish", action="store_true",
                        help=f"Also copy the report and its data files into docs/batches/<name>/ (served at {PAGES_URL})")
    args = parser.parse_args()

    folder = Path("batches") / args.batch
    if not folder.is_dir():
        raise SystemExit(f"No such batch folder: {folder}")
    scans = find_scans(folder)
    if not scans:
        raise SystemExit(f"No scan-*.csv files in {folder}")
    print(f"{len(scans)} scans in {folder}")

    index_html = build_html(args.batch, scans, embed=False)
    single_html = build_html(args.batch, scans, embed=True)
    index_path = folder / "index.html"
    single_path = folder / f"{args.batch}.html"
    index_path.write_text(index_html, encoding="utf-8")
    single_path.write_text(single_html, encoding="utf-8")
    print(f"wrote {index_path} ({index_path.stat().st_size // 1024} KB, links to the files beside it)")
    print(f"wrote {single_path} ({single_path.stat().st_size // 1024} KB, self-contained)")

    if args.publish:
        dest = Path("docs") / "batches" / args.batch
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(index_path, dest / "index.html")
        shutil.copy2(single_path, dest / single_path.name)
        n = 0
        for s in scans:
            for p in [s["csv"], s["note"], *s["photos"]]:
                if p:
                    shutil.copy2(p, dest / p.name)
                    n += 1
        write_batches_index(dest.parent)
        print(f"published to {dest} ({n} data files). After commit and push: {PAGES_URL}{args.batch}/")


if __name__ == "__main__":
    main()
