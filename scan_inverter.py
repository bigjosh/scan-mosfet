#!/usr/bin/env python3
"""
Inverter Characterization Program

Sweeps input voltage using Siglent SPD3303C Power Supply (CH1)
Measures intermediate and final output voltages using Rigol DS1054Z Oscilloscope (CH1, CH2)
"""

import argparse
import csv
import time
import sys
import numpy as np
from datetime import datetime

try:
    import pyvisa
except ImportError:
    print("Error: pyvisa not installed. Run: pip install pyvisa")
    sys.exit(1)


class PowerSupply:
    """Interface to Siglent SPD3303C Power Supply via VISA"""
    
    def __init__(self, resource_name: str = None):
        self.rm = pyvisa.ResourceManager()
        self.inst = None
        
        if resource_name is None:
            # Auto-detect Siglent power supply
            print("Scanning for Power Supply...")
            resources = self.rm.list_resources()
            for res in resources:
                try:
                    # skip common serial ports to save time if needed, but unsafe to assume
                    inst = self.rm.open_resource(res)
                    inst.timeout = 2000
                    
                    # SPD3303C usually requires write+read for IDN, not query
                    inst.write("*IDN?")
                    time.sleep(0.1)
                    try:
                        idn = inst.read()
                    except pyvisa.errors.VisaIOError:
                        inst.close()
                        continue
                        
                    if "SPD" in idn or "Siglent" in idn:
                        resource_name = res
                        print(f"  ✓ Found Power Supply: {idn.strip()}")
                        inst.close()
                        break
                    inst.close()
                except Exception:
                    pass
            
        if resource_name is None:
            raise RuntimeError("Could not find Siglent Power Supply")
        
        self.inst = self.rm.open_resource(resource_name)
        self.inst.timeout = 5000
    
    def set_voltage(self, channel: int, voltage: float):
        self.inst.write(f"CH{channel}:VOLTage {voltage:.3f}")
    
    def output_on(self, channel: int):
        self.inst.write(f"OUTPut CH{channel},ON")
        
    def output_off(self, channel: int):
        self.inst.write(f"OUTPut CH{channel},OFF")
        
    def close(self):
        if self.inst:
            self.inst.close()
        self.rm.close()


class Oscilloscope:
    """Interface to Rigol DS1000Z Series Oscilloscope via VISA"""
    
    def __init__(self, resource_name: str = None):
        self.rm = pyvisa.ResourceManager()
        self.inst = None
        
        if resource_name is None:
            # Auto-detect Rigol Oscilloscope
            print("Scanning for Oscilloscope...")
            resources = self.rm.list_resources()
            for res in resources:
                try:
                    inst = self.rm.open_resource(res)
                    inst.timeout = 2000
                    idn = inst.query("*IDN?")
                    if "Rigol" in idn or "DS1" in idn:
                        resource_name = res
                        print(f"  ✓ Found Oscilloscope: {idn.strip()}")
                        inst.close()
                        break
                    inst.close()
                except Exception:
                    pass
                    
        if resource_name is None:
            raise RuntimeError("Could not find Rigol Oscilloscope")
            
        self.inst = self.rm.open_resource(resource_name)
        self.inst.timeout = 5000
        
    def measure_voltage(self, channel: int) -> float:
        """Measure Vavg on specified channel"""
        try:
            # DS1000Z Syntax: :MEASure:ITEM? <mode>,<source>
            # Mode VAVG returns average voltage
            val = self.inst.query(f":MEASure:ITEM? VAVG,CHAN{channel}")
            return float(val)
        except Exception as e:
            print(f"Error reading Scope CH{channel}: {e}")
            return float('nan')
            
    def close(self):
        if self.inst:
            self.inst.close()
        self.rm.close()


def main():
    parser = argparse.ArgumentParser(description='Inverter Characterization Scanner')
    parser.add_argument('--start', type=float, default=0.0, help='Start Voltage (V)')
    parser.add_argument('--stop', type=float, default=5.0, help='Stop Voltage (V)')
    parser.add_argument('--step', type=float, default=0.1, help='Step Voltage (V)')
    parser.add_argument('--delay', type=float, default=0.5, help='Settling delay (s)')
    parser.add_argument('--output', type=str, default=None, help='Output CSV file')
    
    args = parser.parse_args()
    
    # Create output filename
    if args.output is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        args.output = f"scan-inverter-{timestamp}.csv"
        
    ps = None
    scope = None
    
    try:
        # Initialize Hardware
        ps = PowerSupply()
        scope = Oscilloscope()
        
        # Create sweep values (Up and Down for Hysteresis)
        # Use numpy arange to generate sequence, handling float precision
        up_sweep = np.arange(args.start, args.stop + args.step/100.0, args.step)
        down_sweep = np.arange(args.stop - args.step, args.start - args.step/100.0, -args.step)
        voltages = np.concatenate([up_sweep, down_sweep])
        
        print(f"\nStarting scan: {args.start}V -> {args.stop}V -> {args.start}V (Hysteresis Scan)")
        print(f"Using Power Supply Channel 2 for Input")
        print(f"Writing to: {args.output}")
        print("-" * 60)
        
        # Open CSV
        with open(args.output, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Input_V', 'Intermediate_V', 'Final_V'])
            
            # Reset Channel 2 to 0V
            ps.set_voltage(2, 0)
            time.sleep(1.0)
            
            for vin in voltages:
                # Set Input Voltage on CH2
                ps.set_voltage(2, vin)
                time.sleep(args.delay)
                
                # Measure
                v_inter = scope.measure_voltage(1) # Channel 1: Intermediate
                v_final = scope.measure_voltage(2) # Channel 2: Final
                
                # Log
                writer.writerow([f"{vin:7.3f}", f"{v_inter:10.5f}", f"{v_final:10.5f}"])
                f.flush() # Ensure data is written
                
                print(f"In={vin:.2f}V | Inter={v_inter:.3f}V | Final={v_final:.3f}V")
                
        print("-" * 60)
        print("Scan Complete!")
        
    except KeyboardInterrupt:
        print("\nInterrupted by user")
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if ps: ps.close()
        if scope: scope.close()

if __name__ == "__main__":
    main()
