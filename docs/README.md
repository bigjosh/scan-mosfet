# MOSFET Scanner — web UI

Browser UI for the [Arduino scanner rig](../arduino-scanner/README.md), served
by GitHub Pages from this folder. Replicates `scan_arduino.py` (3-phase scan,
live charts, CSVs) and `bring-up.py` (guided wizard) — no install, no build.

**Use it:** open the Pages URL in Chrome, plug the Uno into the phone (USB-OTG)
or PC, tap **Connect**. Android uses WebUSB (we speak CDC-ACM ourselves —
genuine/16U2 Unos only, not CH340 clones); desktop Chrome uses Web Serial.
**Demo:** append `?demo` or tap the Demo button for a simulated rig, no
hardware needed.

- Scans save to in-app History (IndexedDB); CSVs download on demand with the
  same columns/filenames as the Python tools.
- Installable as a PWA; works offline after first load. Wake lock keeps the
  screen on during scans.

## Dev

No build step — this folder is the source (vanilla ES modules, hand-rolled
canvas charts).

```
python -m http.server 8123 --directory docs    # from the repo root
# http://localhost:8123/?demo
```

| file | role |
| --- | --- |
| `js/transport.js` | Web Serial + WebUSB CDC-ACM byte transports |
| `js/mock.js`      | simulated firmware + synthetic FET (demo/testing; `bench` selects socket contents) |
| `js/protocol.js`  | line protocol, command queue, `Rig` driver |
| `js/convert.js`   | dual-ref pick, measure_point math, CSV format (port of scan_arduino.py) |
| `js/scan.js`      | 3-phase cycle engine with abort + live callbacks |
| `js/bringup.js`   | wizard steps + limits (port of bring-up.py) |
| `js/chart.js`     | canvas line-family + leak-bar charts (viridis) |
| `js/store.js`     | IndexedDB history, CSV builders, params persistence |
| `js/app.js`       | UI wiring |

Caveat: background tabs get timer-throttled by Chrome (~1 s per `setTimeout`),
so keep the app foregrounded during scans — the wake lock handles that on
phones.
