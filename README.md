# scan-mosfet
Charatarize a MOSFET with an Ids versus Vg and Vd scan

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

The program will save the data to a CSV file. By default the file is named "scan-mosfet-<timestamp>.csv", butr this can be configured via command line arguments.

The data will be in the form of a matrix with Vds values along the top and Vgs values down the left side. Each cell in the matrix will contain the Ids value at that Vds and Vgs combination.

### Progress    

By default the program will print the current scan values and the samples taken to the console. This can be disabled via command line arguments.