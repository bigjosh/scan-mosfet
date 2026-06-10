#!/usr/bin/env python3
"""
MOSFET Characterization Program

Sweeps Vds and Vgs to measure Ids using a Siglent SPD3303C power supply
and Joulescope JS110 current meter.
"""

import argparse
import csv
import time
from datetime import datetime
from typing import List, Tuple
import sys

try:
    import pyvisa
except ImportError:
    print("Error: pyvisa not installed. Run: pip install pyvisa")
    sys.exit(1)

try:
    import joulescope
except ImportError:
    print("Error: joulescope not installed. Run: pip install joulescope")
    sys.exit(1)

import numpy as np


class PowerSupply:
    """Interface to Siglent SPD3303C Power Supply via VISA"""
    
    def __init__(self, resource_name: str = None):
        """Initialize connection to power supply"""
        self.rm = pyvisa.ResourceManager()
        
        if resource_name is None:
            # Auto-detect Siglent power supply
            resources = self.rm.list_resources()
            print(f"Scanning {len(resources)} VISA resources for Siglent power supply...")
            for res in resources:
                print(f"  Trying: {res}")
                try:
                    inst = self.rm.open_resource(res)
                    inst.timeout = 5000  # 5 second timeout for detection
                    # SPD3303C needs write/read instead of query
                    inst.write("*IDN?")
                    time.sleep(0.1)
                    idn = inst.read()
                    print(f"    Response: {idn.strip()}")
                    if "SPD3303C" in idn or "Siglent" in idn or "SPD" in idn:
                        resource_name = res
                        inst.close()
                        print(f"  ✓ Found power supply!")
                        break
                    inst.close()
                except Exception as e:
                    print(f"    Skipped: {e}")
                    continue
            
            if resource_name is None:
                print("\nAvailable VISA resources:")
                for res in resources:
                    print(f"  - {res}")
                raise RuntimeError(
                    "Could not find Siglent SPD3303C power supply. "
                    "Use --visa-resource to specify manually."
                )
        
        self.inst = self.rm.open_resource(resource_name)
        self.inst.timeout = 10000  # 10 second timeout
        
        # Get identification (SPD3303C needs write/read instead of query)
        self.inst.write("*IDN?")
        time.sleep(0.1)  # Small delay for device to process
        idn = self.inst.read()
        print(f"Connected to: {idn.strip()}")
    
    def set_voltage(self, channel: int, voltage: float):
        """Set voltage for specified channel (1 or 2)"""
        self.inst.write(f"CH{channel}:VOLTage {voltage:.3f}")
        time.sleep(0.05)  # Small delay for power supply to process command
    
    def set_current_limit(self, channel: int, current: float):
        """Set current limit for specified channel"""
        self.inst.write(f"CH{channel}:CURRent {current:.3f}")
        time.sleep(0.05)  # Small delay for power supply to process command
    
    def output_on(self, channel: int):
        """Turn on output for specified channel"""
        self.inst.write(f"OUTPut CH{channel},ON")
    
    def output_off(self, channel: int):
        """Turn off output for specified channel"""
        self.inst.write(f"OUTPut CH{channel},OFF")
    
    def close(self):
        """Close connection to power supply"""
        # NOTE: We do NOT turn off outputs - leave them as-is
        # User should manually control ON/OFF state on the power supply
        self.inst.close()
        self.rm.close()


class JoulescopeMeter:
    """Interface to Joulescope JS110 for current measurement"""
    
    def __init__(self, mock_mode: bool = False):
        """Initialize connection to Joulescope"""
        self.mock_mode = mock_mode
        
        if mock_mode:
            print("Running in MOCK MODE (no Joulescope required)")
            self.js = None
            return
            
        try:
            # Use scan() without name parameter to avoid library bug
            devices = joulescope.scan()
            if not devices:
                raise RuntimeError("No Joulescope found. Is it connected via USB?")
            
            # Filter for JS110 if multiple devices
            js110_devices = [d for d in devices if 'JS110' in str(d)]
            if js110_devices:
                self.js = js110_devices[0]
            else:
                self.js = devices[0]  # Use first device if no JS110 specifically found
            
            self.js.open()
            print(f"Connected to Joulescope: {self.js.device_serial_number}")
        except Exception as e:
            print(f"Error connecting to Joulescope: {e}")
            print("\nTry running with --dry-run to test without Joulescope")
            raise
    
    def measure_current(self, num_samples: int = 10, sample_delay: float = 0.01) -> float:
        """
        Measure current multiple times and return average
        
        Args:
            num_samples: Number of samples to take
            sample_delay: Delay between samples in seconds
            
        Returns:
            Average current in Amps
        """
        if self.mock_mode:
            # Return mock data for testing
            time.sleep(sample_delay * num_samples)
            return np.random.uniform(0.001, 0.1)  # Random current between 1mA and 100mA
        
        samples = []
        
        for _ in range(num_samples):
            # Read current from Joulescope stream buffer
            # Get the latest data from the stream buffer
            buf = self.js.stream_buffer
            sample_range = buf.sample_id_range
            
            # Get recent samples (last 0.01 seconds worth)
            sample_count = int(self.js.sampling_frequency * 0.01)  # 10ms of data
            start_id = max(sample_range[0], sample_range[1] - sample_count)
            
            data = buf.samples_get(start_id, sample_range[1], ['current'])
            current_mean = data['signals']['current']['value'].mean()
            samples.append(current_mean)
            
            if sample_delay > 0:
                time.sleep(sample_delay)
        
        return np.mean(samples)
    
    def start_streaming(self):
        """Start Joulescope streaming"""
        if self.mock_mode:
            return
        self.js.start()
    
    def stop_streaming(self):
        """Stop Joulescope streaming"""
        if self.mock_mode:
            return
        try:
            self.js.stop()
            self.js.close()
        except:
            pass


def create_voltage_range(start: float, stop: float, step: float) -> List[float]:
    """Create list of voltages from start to stop with given step"""
    # Use numpy to avoid floating point issues
    num_steps = int(np.round((stop - start) / step)) + 1
    return list(np.linspace(start, stop, num_steps))


def perform_scan(
    ps: PowerSupply,
    js: JoulescopeMeter,
    vds_start: float,
    vds_stop: float,
    vds_step: float,
    vgs_start: float,
    vgs_stop: float,
    vgs_step: float,
    setup_delay: float,
    reset_delay: float,
    num_samples: int,
    sample_delay: float,
    verbose: bool,
    csv_filename: str = None
) -> Tuple[List[float], List[List[float]], List[List[float]]]:
    """
    Perform MOSFET characterization scan
    
    Args:
        csv_filename: Optional CSV file to write incrementally during scan
    
    Returns:
        Tuple of (vds_values, vgs_matrix, ids_matrix)
        where vgs_matrix[i][j] is Vgs for vds_values[i] at step j
        and ids_matrix[i][j] is corresponding Ids
    """
    
    # Generate sweep values
    vds_values_full = create_voltage_range(vds_start, vds_stop, vds_step)
    
    if vgs_stop is None:
        # If vgs_stop not specified, default to vds_stop (square sweep estimate)
        # Note: Original triangular logic (Vgs <= Vds) is hard to map to Vgs-outer loop 
        # without explicit Vds dependency. We'll assume square max if not provided.
        vgs_stop_val = vds_stop
    else:
        vgs_stop_val = vgs_stop
        
    vgs_values_full = create_voltage_range(vgs_start, vgs_stop_val, vgs_step)
    
    # Initialize matrices
    vgs_matrix = []
    ids_matrix = []
    
    # Write CSV header if incremental writing is enabled
    if csv_filename:
        with open(csv_filename, 'w', newline='') as f:
            # Clean fixed-width alignment: 
            # Vds (8 chars), Vgs (8 chars), Ids (12 chars)
            f.write(f"{'Vds (V)':>8}, {'Vgs (V)':>8}, {'Ids (uA)':>12}\n")
        print(f"Writing incremental results to: {csv_filename}")
    
    total_measurements = len(vgs_values_full) * len(vds_values_full)
    measurement_count = 0
    
    print(f"\nStarting scan with {len(vgs_values_full)} Vgs points (Outer Loop)")
    print(f"Sweeping {len(vds_values_full)} Vds points per Vgs (Inner Loop)")
    print(f"Total measurements: {total_measurements}")
    print("-" * 60)
    
    start_time = time.time()
    
    # Outer loop: sweep Vgs (Slower)
    for vgs_idx, vgs in enumerate(vgs_values_full):
        # Reset both channels to 0V briefly before new Vgs step
        ps.set_voltage(1, 0)
        ps.set_voltage(2, 0)
        time.sleep(reset_delay)
        
        # Set Vgs (Channel 2)
        ps.set_voltage(2, vgs)
        
        # Inner loop: sweep Vds (Faster)
        # Note: For triangular sweep support (Vgs <= Vds), we could filter vds_values here.
        # But assuming rectangular based on user request.
        
        for vds_idx, vds in enumerate(vds_values_full):
            # Set Vds (Channel 1)
            ps.set_voltage(1, vds)
            
            # Wait for settling
            time.sleep(setup_delay)
            
            # Measure Ids
            ids = js.measure_current(num_samples, sample_delay)
            
            measurement_count += 1
            
            if verbose:
                elapsed = time.time() - start_time
                progress = (measurement_count / total_measurements) * 100
                print(f"[{progress:5.1f}%] Vgs={vgs:6.2f}V, Vds={vds:6.2f}V, Ids={ids*1000:8.3f}mA (avg of {num_samples} samples)")

            # Append to CSV file immediately
            if csv_filename:
                with open(csv_filename, 'a', newline='') as f:
                    # Fixed-point formatting, aligned
                    f.write(f"{vds:8.2f}, {vgs:8.2f}, {ids*1e6:12.2f}\n")
        
    elapsed = time.time() - start_time
    print("-" * 60)
    print(f"Scan complete! Total time: {elapsed:.1f} seconds")
    
    return vds_values_full, [], []  # Return empty matrices as we don't rebuild them


def update_csv_incremental(
    filename: str,
    vds_values: List[float],
    vgs_matrix: List[List[float]],
    ids_matrix: List[List[float]]
):
    """
    Update CSV file with current scan data (incremental)
    Rewrites the data rows with current progress
    """
    # Find the maximum number of Vgs points
    max_vgs_points = max(len(row) for row in vgs_matrix) if vgs_matrix else 0
    
    if max_vgs_points == 0:
        return
    
    # Read the header (first line)
    with open(filename, 'r', newline='') as csvfile:
        reader = csv.reader(csvfile)
        header = next(reader)
    
    # Rewrite file with header and updated data
    with open(filename, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(header)
        
        # Build rows by Vgs index
        for vgs_idx in range(max_vgs_points):
            row = []
            vgs_value = None
            
            # Collect Ids values for this Vgs index across all completed Vds
            for vds_idx in range(len(vds_values)):
                if vds_idx < len(vgs_matrix) and vgs_idx < len(vgs_matrix[vds_idx]):
                    if vgs_value is None:
                        vgs_value = vgs_matrix[vds_idx][vgs_idx]
                    row.append(f"{ids_matrix[vds_idx][vgs_idx]:.6e}")
                else:
                    row.append("")  # No measurement yet for this point
            
            if vgs_value is not None:
                writer.writerow([f"{vgs_value:.2f}"] + row)


def save_to_csv(
    filename: str,
    vds_values: List[float],
    vgs_matrix: List[List[float]],
    ids_matrix: List[List[float]]
):
    """
    Save scan data to CSV file in matrix format
    
    Format:
    - First row: "Vgs\\Vds", Vds1, Vds2, ...
    - Subsequent rows: Vgs value, Ids values for each Vds
    """
    
    # Find the maximum number of Vgs points (will vary by Vds)
    max_vgs_points = max(len(row) for row in vgs_matrix)
    
    with open(filename, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        
        # Write header with Vds values
        header = ['Vgs\\Vds'] + [f"{vds:.2f}" for vds in vds_values]
        writer.writerow(header)
        
        # Build rows by Vgs index
        for vgs_idx in range(max_vgs_points):
            row = []
            vgs_value = None
            
            # Collect Ids values for this Vgs index across all Vds
            for vds_idx in range(len(vds_values)):
                if vgs_idx < len(vgs_matrix[vds_idx]):
                    if vgs_value is None:
                        vgs_value = vgs_matrix[vds_idx][vgs_idx]
                    row.append(f"{ids_matrix[vds_idx][vgs_idx]:.6e}")
                else:
                    row.append("")  # No measurement at this point
            
            if vgs_value is not None:
                writer.writerow([f"{vgs_value:.2f}"] + row)
    
    print(f"\nData saved to: {filename}")


def main():
    parser = argparse.ArgumentParser(
        description='MOSFET Characterization Scanner',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    
    # Voltage sweep parameters
    parser.add_argument('--vds-start', type=float, default=0.0,
                        help='Starting Vds voltage (V)')
    parser.add_argument('--vds-stop', type=float, default=15.0,
                        help='Stopping Vds voltage (V)')
    parser.add_argument('--vds-step', type=float, default=0.1,
                        help='Vds voltage step (V)')
    parser.add_argument('--vgs-start', type=float, default=0.0,
                        help='Starting Vgs voltage (V)')
    parser.add_argument('--vgs-stop', type=float, default=None,
                        help='Stopping Vgs voltage (V). If not specified, sweeps up to current Vds.')
    parser.add_argument('--vgs-step', type=float, default=0.1,
                        help='Vgs voltage step (V)')
    
    # Timing parameters
    parser.add_argument('--setup-delay', type=float, default=1.0,
                        help='Setup delay after Vgs change (seconds)')
    parser.add_argument('--reset-delay', type=float, default=2.0,
                        help='Delay after resetting to 0V between sweeps (seconds)')
    parser.add_argument('--num-samples', type=int, default=10,
                        help='Number of current samples to average')
    parser.add_argument('--sample-delay', type=float, default=0.01,
                        help='Delay between consecutive samples (seconds)')
    
    # Output parameters
    parser.add_argument('--output', type=str, default=None,
                        help='Output CSV filename (default: scan-mosfet-<timestamp>.csv)')
    parser.add_argument('--quiet', action='store_true',
                        help='Disable progress output')
    
    # Hardware parameters
    parser.add_argument('--visa-resource', type=str, default=None,
                        help='VISA resource name for power supply (auto-detect if not specified)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Run without Joulescope (generate mock current data for testing)')
    
    args = parser.parse_args()
    
    # Generate output filename with timestamp if not specified
    if args.output is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        args.output = f"scan-mosfet-{timestamp}.csv"
    
    # Print configuration
    print("=" * 60)
    print("MOSFET Characterization Scanner")
    print("=" * 60)
    print(f"Vds range: {args.vds_start}V to {args.vds_stop}V in {args.vds_step}V steps")
    if args.vgs_stop is not None:
        print(f"Vgs range: {args.vgs_start}V to {args.vgs_stop}V in {args.vgs_step}V steps")
    else:
        print(f"Vgs range: {args.vgs_start}V to Vds in {args.vgs_step}V steps")
    print(f"Setup delay: {args.setup_delay*1000}ms")
    print(f"Samples per point: {args.num_samples}")
    print(f"Sample delay: {args.sample_delay*1000}ms")
    print(f"Output file: {args.output}")
    print("=" * 60)
    print("\nNOTE: Configure power supply ON/OFF and current limits manually")
    
    # Initialize hardware
    print("\nInitializing hardware...")
    
    ps = None
    js = None
    
    try:
        # Connect to power supply
        ps = PowerSupply(args.visa_resource)
        
        # NOTE: The program does NOT control ON/OFF state or current limits
        # Configure these manually on the power supply before running the scan
        
        # Set initial voltages to 0
        print("Setting initial voltages to 0V...")
        ps.set_voltage(1, 0)
        ps.set_voltage(2, 0)
        
        # Connect to Joulescope
        js = JoulescopeMeter(mock_mode=args.dry_run)
        js.start_streaming()
        
        # Wait a moment for everything to stabilize
        print("Waiting for system to stabilize...")
        time.sleep(1.0)
        
        # Perform scan (with incremental CSV writing)
        vds_values, vgs_matrix, ids_matrix = perform_scan(
            ps=ps,
            js=js,
            vds_start=args.vds_start,
            vds_stop=args.vds_stop,
            vds_step=args.vds_step,
            vgs_start=args.vgs_start,
            vgs_stop=args.vgs_stop,
            vgs_step=args.vgs_step,
            setup_delay=args.setup_delay,
            reset_delay=args.reset_delay,
            num_samples=args.num_samples,
            sample_delay=args.sample_delay,
            verbose=not args.quiet,
            csv_filename=args.output  # Enable incremental writing
        )
        
        # Final save to ensure data is complete (already written incrementally)
        # save_to_csv(args.output, vds_values, vgs_matrix, ids_matrix)
        
        print("\n✓ Scan completed successfully!")
        
    except KeyboardInterrupt:
        print("\n\n! Scan interrupted by user")
        
    except Exception as e:
        print(f"\n\n✗ Error during scan: {e}")
        import traceback
        traceback.print_exc()
        
    finally:
        # Clean up hardware
        print("\nShutting down hardware...")
        
        if ps is not None:
            try:
                ps.close()
                print("✓ Power supply connection closed")
            except Exception as e:
                print(f"✗ Error closing power supply: {e}")
        
        if js is not None:
            try:
                js.stop_streaming()
                print("✓ Joulescope stopped")
            except Exception as e:
                print(f"✗ Error closing Joulescope: {e}")


if __name__ == "__main__":
    main()
