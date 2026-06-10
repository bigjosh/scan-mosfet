# Arduino MOSFET Scanner — Build Plan (v1: 0–5 V)

## Context & goal
The repo currently characterizes MOSFETs with a **Siglent SPD3303C** PSU + **Joulescope JS110** driven by `scan_mosfet.py` over SCPI/VISA (CSV schema `Vds (V), Vgs (V), Ids (uA)`). `arduino-scanner/arduino-scanner-spec.md` asks for a from-scratch **Arduino Uno** scanner that drives 3 pins and measures **Ids 0–1000 µA** and **Igs 0–100 µA** to **< 5 %**.

**Strategy:** build **v1 at 0–5 V** using **MCP4725** DACs to set pin voltages and **burden resistors** read by the Arduino ADC to measure current — get the full set/sweep/measure chain working first. **v2** later adds op-amps to reach 0–15 V and independent Low drive.

Note that we will use this bench primarily to evaluate *if* mostfets are viable (can be reliabily cascaded with digital signals) so we don't stronly care about absolute accuracy. 

## Naming convention
Pins are **High / Gate / Low** (not drain/source/gate) so the same rig works for **N- and P-channel**. Channel current flows High↔Low; Gate controls it. **Low is the grounded current-return in v1.**

## v1 scope (this plan)
- All control voltages **0–5 V**, straight from MCP4725 (no op-amps yet).
- **Low IO pin = Arduino D3**, a GPIO pin that is usually driven LOW to act as ground, but can be set to float or drive HIGH for other testing. It is connected through burden resistor **R_low**; channel current is read as the drop across R_low: **A0** senses the DUT side, **A3** senses the LOW_IO pin side (cancels the GPIO's ~25–40 Ω output resistance, which would otherwise cost up to ~4 % of R_low = 1 kΩ at 1 mA).
- **High** and **Gate** are DAC-driven; **Low** is GND-referenced (a Low DAC, if ever fitted, sits at 0 V in v1).
- Gate current sensed via a deliberately large **1 MΩ** series resistor (we only care *whether* the gate leaks).
- **Out of scope → v2:** 0–15 V range (op-amps), independent Low drive + negative Vgs/Vds (source-pedestal + differential sense), wider/bipolar current ranges (INA219/226 modules).

## Topology
```
            MCP4725 #1 (SDA=D4, SCL=D5)
 soft-I2C ──►[ DAC_H ]──[ 100Ω ]────────────────► HIGH pin ──┐
                                                               │
            MCP4725 #2 (SDA=D4, SCL=D6)                     [ DUT ]   N/P MOSFET: channel High↔Low, Gate controls
 soft-I2C ──►[ DAC_G ]──[ R_gate = 1MΩ ]──┬──────► GATE pin   │
                                          │                   │
                                   A1 ◄───┘  V_gate node (5 V ref)
                                                              ▼
                                               LOW pin ──┬──[ R_low ]──┬──► LOW_IO (D3)
                                                         │             │
                                                  A0 ◄───┘      A3 ◄───┘   (both dual-ref)
```
Derived quantities (compute in Python; keep firmware dumb):
- **Channel current:** `I_low = (V_A0 − V_A3) / R_low`  (signed; positive = High→Low)
- **Gate current:** `I_gate = (V_cmd_G − V_A1) / R_gate`  (V_A1 ≈ V_cmd_G when not leaking)
- **Burden-corrected bias:** `V_low_node = V_A0`; `Vgs = V_A1 − V_low_node`; `Vds = V_A2 − V_low_node`

## R_low sizing
Pick so the **expected** max current → ≤ ~1.0 V (just under the 1.1 V ref):
- Full spec 0–1000 µA → **R_low = 1 kΩ** (1 mA → 1.0 V), ≈ 1.07 µA/LSB at 10-bit/1.1 V. **← default**
- Low-current DUTs (≤100 µA) → **R_low = 10 kΩ** (100 µA → 1.0 V, ~0.1 µA/LSB), clips above ~110 µA.
- Mount R_low in a socket/header so it's easy to swap per DUT.
- Burden note: 1 mA × 1 kΩ lifts the Low node 1 V above GND — we **measure** that (A0) and use it to correct Vgs/Vds, so it costs headroom, not accuracy.

## Gate (1 MΩ) behavior — intentional v1 limitation
- Great sensitivity to leakage (1 µA → 1 V sag) and a built-in current limit: at 5 V the gate can supply ≈ 5 µA before its node collapses → the "device is leaky, don't care" regime.
- So v1 measures Igs cleanly only to **~1 µA** (within ~1 V sag), well short of the 100 µA spec — deliberate. Revisit in v2 (smaller R_gate / proper sense) if real gate-current range is needed.

## Bill of materials (v1) 
- Arduino Uno R3 / clone, ATmega328P (have).
- **2× MCP4725** breakout — any vendor/variant, **no address straps needed** (per-DAC SCL lines). Firmware probes 0x60–0x67 on each bus and takes the first ACK; roles are physical: the module clocked by **D5 is DAC_H**, by **D6 is DAC_G**.
- **R_low**: 1 kΩ 0.1 % (+ 10 kΩ spare), socketed.
- **R_gate**: 1 MΩ (1 % fine — used only in a ratio).
- **100 Ω** series R on High (short protection; A2 senses after it, so no accuracy cost).
- **10 nF** ceramic, A1 → GND (the 1 MΩ gate node otherwise starves the ADC sample cap — AVR wants <10 kΩ source impedance). Anything ~5–15 nF works; **5 nF fitted** (τ = 5 ms, still ≪ the 200 ms gate-settle).
- Optional: clamp diodes; DUT socket; breadboard + jumpers.

## Wiring / pin map
- I2C (**bit-banged**, open-drain emulation — lines only ever driven low or released): **D4 = SDA** shared by both modules, **D5 = SCL → DAC_H**, **D6 = SCL → DAC_G**. One SCL per DAC means identical modules need **no address straps** (both can be 0x60); the idle DAC sees SDA wiggle without clock edges, which shifts no bits. VDD = 5 V; common GND (Uno + DACs + DUT). A4/A5 are free again (usable as extra ADC inputs in v2).
- **A0** ← Low node. **A1** ← Gate node (+ 10 nF to GND). **A2** ← High pin (after the 100 Ω) for readback under load. **A3** ← LOW_IO pin (D3).
- DAC_H OUT → 100 Ω → High pin. DAC_G OUT → R_gate → Gate pin. Low pin → R_low → **D3** (LOW_IO).

## ADC reference strategy & calibration (the key to < 5 %)
- **Dual-read everything:** each `MEAS?` reads all four analog pins (A0–A3) under **both** references — INTERNAL 1.1 V (resolution) and DEFAULT ≈ 5 V (range). Python picks whichever reading is in range per node. No range modes, no firmware state; clipping (Low node near 5 V in phase 3, or >1.1 mA in phase 2) is handled automatically.
- Only one `analogReference()` is active at a time → firmware groups reads by reference, discards reads + settles after every ref/mux switch (the Uno has 100 nF on AREF; the bandgap charges it through ~32 kΩ, so allow ~10 ms settling into INTERNAL).
- **VDD self-measurement:** firmware reads the internal bandgap against AVcc each `MEAS?` and reports `VDD_MV`, so the DAC scale and 5 V-ref conversions track the real (USB-drifting) rail. One-time cal: store the true bandgap voltage via `CALBG <mV>` (persisted in MCU EEPROM, default 1100).
- **Current calibration:** verify against the known-resistor test below.

## Firmware (Arduino sketch) — v1
Line-based ASCII serial @ 115200; one command per line, one reply line per command:
- `IDN?` → `ArduinoMosfetScanner v1 DACH=0x60 DACG=0x61 VREFINT_MV=1100`
- `SETH <volts>` / `SETG <volts>` → set DAC_H / DAC_G (0–5.000, clamped, VDD-compensated against the last measured rail)
- `RAWH <code>` / `RAWG <code>` → raw 12-bit DAC codes, for bring-up/debug
- `LOWIO 0|1|Z` → drive LOW_IO (D3) low / high / float
- `MEAS?` → oversample (default 32 per pin per ref, `AVG <n>` to change), grouped by reference with discards + settle on every switch, reply (averaged counts, 2 dp):
  `VDD_MV=5012.3 A0_1V1=… A1_1V1=… A2_1V1=… A3_1V1=… A0_5V=… A1_5V=… A2_5V=… A3_5V=…`
- `AVG <n>` / `CALBG <mV>` / `CALBG?` / `VDD?` / `SAVEZERO` — `SAVEZERO` writes 0 V into both DAC EEPROMs so cold power-up is safe (MCP4725s ship with **mid-scale 2.5 V** in EEPROM!). `setup()` also zeroes both DACs immediately at boot. *(SAVEZERO was run 2026-06-10 — both fitted DACs cold-boot at 0 V.)*
- Diagnostics: `SCAN?` (probe 0x08–0x77 on both buses), `RESCAN` (re-detect after rewiring, no replug), `PINTEST [sec]` (per-line idle / external-pullup / short-to-5V / bridged checks with guarded 1 ms probes, optional hold-low window for a voltmeter).
- DAC addressing: probe 0x60–0x67 per bus at boot, first ACK wins; bus defines role (D5 → DAC_H, D6 → DAC_G).
- No libraries: bit-banged open-drain I2C (~30–50 kHz; a DAC write ≈ 1 ms, negligible vs. MEAS). Non-printable RX bytes are dropped (USB-bridge glitch immunity). Keep math in Python.

## Python tester  v1 (light)
`arduino-scanner/scan_arduino.py` (pyserial + numpy + matplotlib):
- Auto-detects the Uno's COM port (`--port` to override), waits out the DTR auto-reset, checks the banner.
- `--repl` raw-command passthrough for bring-up; `--cal-vdd <DMM volts>` automates the bandgap cal (queries `VDD?`, rescales, writes `CALBG`).
- Per-phase CSVs `scan-arduino-<ts>_phase{1,2,3}.csv`. First 3 columns exactly match the legacy `Vds (V), Vgs (V), Ids (uA)` schema using the **commanded grid** values (so existing pivot tools drop in), then measured extras: `Vds_meas, Vgs_meas, Igs (uA), Vhigh, Vlow, Vgate, Vlowio, flag`.
- Default steps: **100 mV (H) / 250 mV (G)** → ~2–3 min/device quick-look (~65 ms/point incl. dual-ref MEAS); `--h-step/--g-step` (and start/stop flags) for finer runs.
- `bring-up.py`: guided interactive bring-up for a fresh rig (link → PINTEST → DAC comms → no-DUT self-test → jumper-wire functional tests → DMM cal). `scan_arduino.py --selftest` reruns the no-DUT electrical check headlessly.

## Each device test cycle 
1. Measure gate leakage
  1. measure forward gate leakage by setting DAC_G=5V, DAC_H=0V, LOW_IO=0 and measuring forward drop across R_gate
  2, measure reverse gate leakage by setting DAC_G=0V, DAC_H=5V, LOW_IO=5V, reverse drop across R_gate
2. Scan device charicoristics for positive gate voltages
  1. LOW_IO=0V (A0/A3 near GND → the 1.1 V-ref readings are the in-range ones).
  2. Scan DAC_G 0V to 5V in 250mV steps (default). 
    1. For each DAC_G step, scan DAC_H from 0V-5V in 100mV steps (default).
3. Scan device charicoristics for negative gate voltages
  1. LOW_IO=5V (A0/A3 near 5 V → the 5 V-ref readings are the in-range ones, auto-picked; resolution drops to ~4.9 µA/LSB there).
  2. Scan DAC_G 0V to 5V in 250mV steps (default). 
    1. For each DAC_G step, scan DAC_H from 0V-5V in 100mV steps (default).
  3. Note: on an N-channel DUT this phase mostly shows the **body diode** (conducts below Vds ≈ −0.6 V); current is bounded by R_low to ~4.3 mA worst case — safe for GPIO and DAC sink.

Each test cycle generates 3 charts (matplotlib PNGs):
- Phase 1: leakage bars (|Igs| fwd/rev) against the ~1 µA "leaky" ceiling.
- Phases 2 & 3: Ids-vs-Vds curve families, one line per commanded Vgs.


## Bring-up & verification
Run the guided wizard: **`python bring-up.py`** — link → PINTEST harness diagnostic → DAC comms + SAVEZERO → no-DUT electrical self-test → jumper-wire functional tests → DMM cal. `scan_arduino.py --selftest` reruns the electrical check on demand.
- The jumper tests replace the old known-resistor step: **High–Low** validates the current loop against Ohm's law through 100 Ω + R_low, **Gate–Low** validates the Igs scale through R_gate, **Gate–High** cross-checks the A1/A2 senses on one node.
- Status: full bring-up **green on 2026-06-10** (bandgap cal 1085 mV persisted). The High–Low test caught a real fault on its first run — a 100 kΩ part fitted in the 100 Ω position (constant −98.8 % ohmic error; the zero-current self-test cannot see series-R faults, which is exactly why the jumper tests exist).
- First device scanned the same day: N-ch enhancement, Vth ≈ 1.3–1.6 V, clean gate (~0.15 µA fwd), body diode + reverse-channel conduction resolved in phase 3.

## Decisions (2026-06-10 interview)
- Board: **Uno R3/clone (ATmega328P)**. Toolchain: **arduino-cli** (`firmware/build.ps1` compiles/uploads; cli lives at `%LOCALAPPDATA%\arduino-cli`).
- DAC breakouts not bought yet → firmware auto-detects addresses 0x60–0x63; lower → DAC_H. Either GY-4725 or Adafruit works unmodified (second board needs its A0 address pin strapped).
- Extras fitted: A2 High sense, **A3 LOW_IO sense (added post-interview: cancels GPIO output-resistance error, ~4 % at 1 mA)**, 10 nF on A1, 100 Ω on High.
- `MEAS?` dual-reads all four pins under both refs + reports measured VDD; Python auto-picks in-range readings (resolves the old phase-3 ref contradiction).
- Defaults 100 mV (H) / 250 mV (G); per-phase CSVs (legacy 3 cols = commanded grid, + measured extras); 3 matplotlib PNGs per cycle.
- LOW_IO = **D3**.

### Addendum (2026-06-10 bring-up)
- Hardware-TWI on A4/A5 never ACKed on the bench (and a drive-low test browned out the board → suspected contact with a hard 5 V source). Switched to **bit-banged I2C: SDA=D4 shared, SCL1=D5 (DAC_H), SCL2=D6 (DAC_G)** at the user's request — also the planned path to a 3rd DAC (one more SCL pin, e.g. D2/D7), and identical unstrapped modules now work.
- Both DACs verified ACKing at 0x60 (one per bus); `SETH/SETG 2.5` confirmed with VDD-compensated codes; `SAVEZERO` written. Analog sense wiring (A0–A3, R_low, R_gate, 10 nF) still to be built.
- Keep the Arduino IDE Serial Monitor **closed** while `scan_arduino.py` runs — they contend for the COM port.

## v2 roadmap (after v1 works)
- Op-amp stage (LM358/LM324 on +18 V, **force-sense at the terminal**) to extend drives to **0–15 V**.
- Add a Low DAC + level-shift/differential current sense → independent Low drive and the **source-pedestal trick** for **negative Vgs/Vds**. 3rd DAC options: **MCP4728** quad, an address-variant MCP4725, or a TCA9548A I2C mux.
- If real bipolar / up-to-1 mA gate or channel current range is needed: drop in **INA219/INA226** high-side current-sense modules (bidirectional, handle the 0–26/36 V common-mode) in place of plain burden resistors.
