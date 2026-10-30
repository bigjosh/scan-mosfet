#!/usr/bin/env python3
"""
Attach phone photos to scans over the LAN.

Runs a small web server on this PC and prints its URL as a QR code. Scanning
it on the phone opens a page that takes a photo and saves it next to the
chosen scan's CSV as <basename>.jpg (then -2.jpg, ...), plus an optional note
appended to <basename>.txt. The scan scripts print the same URL with
?name=<basename> so their own file comes up preselected.

    python photo_drop.py --batch 2026-10-30-new-tubes
    python photo_drop.py                      # scans in the current folder

Only writes into the chosen folder, and only under names of CSV files found there.
"""

import argparse
import html
import io
import os
import re
import socket
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DEFAULT_PORT = 8765
MAX_UPLOAD_BYTES = 40 * 1024 * 1024
PHOTO_EXTS = (".jpg", ".jpeg", ".png", ".heic")


def lan_ip():
    """This machine's address on the LAN (no packets are sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        s.close()


def print_qr(url):
    """Print a QR code for the URL in the terminal (needs the qrcode package)."""
    try:
        import qrcode
    except ImportError:
        print("(pip install qrcode to get a scannable QR code here)")
        return
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    try:
        qr.print_ascii(invert=True)
    except UnicodeEncodeError:
        # Plain-ASCII fallback for consoles that cannot show block characters
        for row in qr.get_matrix():
            print("".join("##" if cell else "  " for cell in row))


def list_scans(folder: Path):
    """Scan CSVs in the folder, newest first, each with the photos already attached to it."""
    photo_stems = [q.stem for q in folder.iterdir() if q.suffix.lower() in PHOTO_EXTS]
    scans = []
    for p in folder.glob("scan-*.csv"):
        if p.stem.endswith(("_results", "_filtered")):
            continue
        pattern = re.compile(re.escape(p.stem) + r"(-\d+)?")
        photos = sorted(s for s in photo_stems if pattern.fullmatch(s))
        scans.append({"base": p.stem, "mtime": p.stat().st_mtime, "photos": photos})
    scans.sort(key=lambda s: s["mtime"], reverse=True)
    return scans


def next_free(folder: Path, base: str, ext: str) -> Path:
    """<base><ext>, or <base>-2<ext>, -3... counting photos of any type already attached."""
    taken = {q.stem for q in folder.iterdir() if q.suffix.lower() in PHOTO_EXTS}
    if base not in taken:
        return folder / f"{base}{ext}"
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return folder / f"{base}-{n}{ext}"


def save_photo(folder: Path, base: str, filename: str, data: bytes, max_px: int) -> Path:
    """Save as a rotation-corrected, downscaled JPEG; keep the raw bytes if it will not decode."""
    try:
        from PIL import Image, ImageOps
        im = Image.open(io.BytesIO(data))
        im = ImageOps.exif_transpose(im)
        im.thumbnail((max_px, max_px))
        im = im.convert("RGB")
        out = next_free(folder, base, ".jpg")
        im.save(out, "JPEG", quality=85, optimize=True)
        return out
    except Exception as e:
        ext = os.path.splitext(filename)[1].lower() or ".bin"
        out = next_free(folder, base, ext)
        out.write_bytes(data)
        print(f"  stored as received, could not decode it: {e}")
        return out


def parse_multipart(content_type, body):
    """Minimal multipart/form-data parser: (fields, files), files as name -> (filename, bytes)."""
    m = re.search(r'boundary="?([^";]+)"?', content_type or "")
    if not m:
        raise ValueError("expected multipart/form-data")
    boundary = b"--" + m.group(1).encode()
    fields, files = {}, {}
    for chunk in body.split(boundary)[1:]:
        if chunk.startswith(b"--"):
            break  # closing boundary
        head, sep, data = chunk.partition(b"\r\n\r\n")
        if not sep:
            continue
        if data.endswith(b"\r\n"):
            data = data[:-2]
        headers = head.decode("utf-8", "replace")
        name = re.search(r'\bname="([^"]*)"', headers)
        if not name:
            continue
        fname = re.search(r'filename="([^"]*)"', headers)
        if fname:
            files[name.group(1)] = (fname.group(1), data)
        else:
            fields[name.group(1)] = data.decode("utf-8", "replace")
    return fields, files


PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scan photo</title>
<style>
 body{font-family:system-ui,sans-serif;margin:16px;max-width:560px;color:#222}
 label{display:block;margin-top:14px;color:#555;font-size:14px}
 select,textarea,input,button{font-size:18px;width:100%;box-sizing:border-box;margin-top:6px}
 button{padding:14px;background:#1f6feb;color:#fff;border:0;border-radius:8px;margin-top:18px}
 .hint{color:#666;font-size:14px;margin-top:18px}
</style></head><body>
<h2>Attach a photo</h2>
<form method="post" action="/upload" enctype="multipart/form-data">
<label>Scan</label><select name="name">{options}</select>
<label>Photo</label><input type="file" name="photo" accept="image/*" capture="environment">
<label>Note</label><textarea name="note" rows="2" placeholder="optional; a note without a photo is fine too"></textarea>
<button type="submit">Upload</button>
</form>
<p class="hint">Saved into {folder} as &lt;scan&gt;.jpg; notes go to &lt;scan&gt;.txt</p>
</body></html>"""

DONE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Saved</title>
<style>body{font-family:system-ui,sans-serif;margin:16px;max-width:560px;color:#222} a{font-size:20px;display:block;margin-top:18px}</style>
</head><body>
<p style="color:#1a7f37;font-size:22px">Saved {name}</p>{note}
<a href="/?name={base}">Another photo of this scan</a>
<a href="/">Back to the scan list</a>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    folder = Path(".")
    max_px = 1600

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path != "/":
            self._send(404, "not found", "text/plain")
            return
        scans = list_scans(self.folder)
        if not scans:
            self._send(200, f"<p>No scan-*.csv files in {html.escape(str(self.folder))} yet. "
                            f"Start a scan, then reload.</p>")
            return
        wanted = parse_qs(url.query).get("name", [None])[0]
        if wanted not in [s["base"] for s in scans]:
            # newest scan without a photo, else the newest scan
            wanted = next((s["base"] for s in scans if not s["photos"]), scans[0]["base"])
        options = []
        for s in scans:
            sel = " selected" if s["base"] == wanted else ""
            tag = f" (photo x{len(s['photos'])})" if s["photos"] else ""
            options.append(f'<option value="{html.escape(s["base"])}"{sel}>'
                           f'{html.escape(s["base"] + tag)}</option>')
        page = PAGE.replace("{options}", "".join(options)).replace("{folder}", html.escape(str(self.folder)))
        self._send(200, page)

    def do_POST(self):
        if urlparse(self.path).path != "/upload":
            self._send(404, "not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_UPLOAD_BYTES:
            self._send(413, "upload empty or too large", "text/plain")
            return
        body = self.rfile.read(length)
        try:
            fields, files = parse_multipart(self.headers.get("Content-Type"), body)
        except ValueError as e:
            self._send(400, str(e), "text/plain")
            return
        base = fields.get("name", "")
        if base not in [s["base"] for s in list_scans(self.folder)]:
            self._send(400, "unknown scan", "text/plain")
            return
        note = fields.get("note", "").strip()
        photo = files.get("photo")
        stamp = datetime.now()
        saved = None
        if photo and photo[1]:
            saved = save_photo(self.folder, base, photo[0], photo[1], self.max_px)
            print(f"{stamp:%H:%M:%S}  {saved.name}  ({len(photo[1]) // 1024} KB from {self.client_address[0]})")
        if note:
            with open(self.folder / f"{base}.txt", "a", encoding="utf-8") as f:
                f.write(f"{stamp:%Y-%m-%d %H:%M:%S}  {note}\n")
            print(f"{stamp:%H:%M:%S}  {base}.txt  note: {note}")
        if not saved and not note:
            self._send(400, "no photo and no note", "text/plain")
            return
        name = saved.name if saved else f"{base}.txt"
        note_html = f"<p>Note added to {html.escape(base)}.txt</p>" if note else ""
        self._send(200, DONE.replace("{name}", html.escape(name))
                             .replace("{base}", html.escape(base))
                             .replace("{note}", note_html))

    def log_message(self, fmt, *args):
        pass  # uploads are logged explicitly above; page hits are noise


def main():
    parser = argparse.ArgumentParser(
        description="Attach phone photos to scans: a LAN upload page plus a QR code to reach it",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--batch", default=None,
                        help="Batch name: photos go into batches/<name>/ next to its CSVs (default: current folder)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="TCP port to listen on")
    parser.add_argument("--host", default="0.0.0.0", help="Interface to listen on")
    parser.add_argument("--max-px", type=int, default=1600, help="Longest side of saved photos")
    args = parser.parse_args()
    sys.stdout.reconfigure(line_buffering=True)  # upload log shows up even when redirected

    folder = Path("batches") / args.batch if args.batch else Path(".")
    folder.mkdir(parents=True, exist_ok=True)
    Handler.folder = folder
    Handler.max_px = args.max_px

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    ip = lan_ip() if args.host == "0.0.0.0" else args.host
    url = f"http://{ip}:{args.port}/"
    print(f"Photo drop for {folder} ({len(list_scans(folder))} scans so far)")
    print(f"Open on the phone: {url}")
    print_qr(url)
    print("Ctrl-C to stop. If the phone cannot connect, check Windows Firewall allowed Python on private networks.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
