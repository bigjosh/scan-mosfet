# scan-mosfet
Charatarize a MOSFET with an Ids versus Vg and Vd scan

> **Arduino-based scanner (v1):** there is now a standalone ~$30 rig — Arduino
> Uno + 2× MCP4725 — that runs a 3-phase device cycle (gate leakage,
> positive- and negative-Vgs Ids maps) with a guided bring-up wizard and
> self-tests. See [arduino-scanner/README.md](arduino-scanner/README.md) and
> [arduino-scanner/plan.md](arduino-scanner/plan.md). The Siglent+Joulescope
> bench below remains the reference instrument.

## Hardware

We have connected via USB a power supply and a Joulescope.

### Power supply (PS)

Siligent SPD3303C. We have installed NI VISA software and it is talking to the power supply.

### Joulescope (JS)

JS110. We have installed the Joulescope SDK and it is talking to the Joulescope.

## Connnections

We will use channel 1 of the PS to drive the Vds of the MOSFET, so both the ground and positive of the PS channel 1 are connected to INPUT terminals of the Joulsescope.

The OUT terminals of the Joulescope are connected to the POSITIVE to the Drain and NEGATIVE to the Source of the MOSFET.

We will use channel 2 of the PS to drive the Vgs of the MOSFET. The positive terminal of PS channel 2 is connected to the Gate of the MOSFET, and the negative terminal of PS channel 2 is connected to the Source of the MOSFET (also connected to the ground of the Joulescope OUT terminal).

## Installation

1. Install Python 3.8 or later
2. Install the required packages:
   ```bash
   pip install -r requirements.txt
   ```
3. Ensure NI-VISA is installed (for power supply communication)
4. Ensure Joulescope drivers are installed

## Usage

Run the program with default settings:
```bash
python scan_mosfet.py
```

Or customize the scan parameters (see Command line arguments section below).

## The program

This Python CLI program will sweep a configureable range of Vds and for each Vds it will sweep a   configureable range of Vgs and measure the Ids at each combination.

There is a configurable set-up time delay after each Vgs change to let the Ids settle before it is sampled. 

After the setup time delay the Ids is sampled a configurable number of times and the average is taken. The delay between samples is also configurable.

Default values are:

Vds = 0V to 15V in 0.1V steps
Vgs = 0V to Vds (whatever the current scanned value is) in 0.1V steps
set-up time delay = 1000ms (1 second)
number of samples = 10
consecutive sample delay = 10ms

Note that the program never changes either the ON/OFF state or current limit of either power supply channel- it only ever changes voltage. This is for safety. 

### Command line arguments

All scan parameters can be configured via command line arguments:

**Voltage Sweep:**
- `--vds-start FLOAT`: Starting Vds voltage in volts (default: 0.0)
- `--vds-stop FLOAT`: Stopping Vds voltage in volts (default: 15.0)
- `--vds-step FLOAT`: Vds voltage step in volts (default: 0.1)
- `--vgs-step FLOAT`: Vgs voltage step in volts (default: 0.1)

**Timing:**
- `--setup-delay FLOAT`: Setup delay after Vgs change in seconds (default: 1.0)
- `--num-samples INT`: Number of current samples to average (default: 10)
- `--sample-delay FLOAT`: Delay between consecutive samples in seconds (default: 0.01)

**Hardware:**
- `--current-limit FLOAT`: Current limit for power supply in amps (default: 3.0)
- `--visa-resource STRING`: VISA resource name for power supply (auto-detect if not specified)

**Output:**
- `--output STRING`: Output CSV filename (default: scan-mosfet-<timestamp>.csv)
- `--quiet`: Disable progress output
- `--dry-run`: Run without Joulescope (generates mock current data for testing)

**Examples:**
```bash
# Full scan with default settings
python scan_mosfet.py

# Custom voltage range
python scan_mosfet.py --vds-stop 20 --vds-step 0.5 --output my_scan.csv

# Test run without Joulescope connected
python scan_mosfet.py --dry-run --vds-stop 1.0
```

### Output

The program saves data to a CSV file **incrementally** during the scan. By default the file is named "scan-mosfet-<timestamp>.csv", but this can be configured via command line arguments.

**Incremental Writing:**
- CSV file is created at scan start with the header
- After each Vds row completes, the file is updated with new data
- This allows real-time visualization while the scan is running
- If the scan is interrupted, partial data is still saved

The data is in matrix format with Vds values along the top and Vgs values down the left side. Each cell contains the Ids value at that (Vgs, Vds) combination.

### Progress    

By default the program will print the current scan values and the samples taken to the console. This can be disabled via command line arguments.

## Real-time Visualization

The `visualize_scan.py` program provides live visualization of the scan data as a heatmap:

**Features:**
- **X-axis**: Vgs (Gate-Source Voltage)
- **Y-axis**: Vds (Drain-Source Voltage)  
- **Color**: Ids (Drain Current) in milliamps
- **Live updates**: Automatically refreshes as new data is written to the CSV
- **Dynamic color scale**: Adjusts to the data range as the scan progresses

**Usage:**

```bash
# Monitor the most recent scan file (auto-detected)
python visualize_scan.py

# Monitor a specific file
python visualize_scan.py scan-mosfet-20251103_120344.csv

# Adjust refresh rate (default: 1 second)
python visualize_scan.py --refresh 0.5

# Show static plot (no live updates)
python visualize_scan.py --static scan-mosfet-20251103_120344.csv
```

**Tip:** Run the visualization in a separate terminal window while the scan is running to see the MOSFET characteristics develop in real-time!

**Example workflow:**
```bash
# Terminal 1: Start the scan
python scan_mosfet.py

# Terminal 2: Start live visualization (in a separate window)
python visualize_scan.py

# Watch the heatmap update as each Vds row completes!
```