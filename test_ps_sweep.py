#!/usr/bin/env python3
"""
Test power supply voltage sweep - simulates the scan behavior
Steps through voltages so you can verify on the display
"""

import pyvisa
import time

print("Connecting to power supply...")
rm = pyvisa.ResourceManager()

# The known resource
RESOURCE = "USB0::0x0483::0x7540::SPD3EEDC5R0500::INSTR"

inst = rm.open_resource(RESOURCE)
inst.timeout = 10000

# Get ID
inst.write("*IDN?")
time.sleep(0.1)
idn = inst.read()
print(f"Connected: {idn.strip()}\n")

print("="*60)
print("Testing Voltage Sweep")
print("="*60)
print("\nThis will sweep CH1 (Vds) and CH2 (Vgs) like the scan does.")
print("Watch your power supply display to verify voltages change.\n")

# Turn off outputs first
print("Turning off outputs...")
inst.write("OUTPut CH1,OFF")
inst.write("OUTPut CH2,OFF")
time.sleep(0.2)

# Set current limits
print("Setting current limits...")
inst.write("CH1:CURRent 3.000")
time.sleep(0.05)
inst.write("CH2:CURRent 0.100")
time.sleep(0.05)

# Set initial voltages to 0
print("Setting initial voltages to 0...")
inst.write("CH1:VOLTage 0.000")
time.sleep(0.05)
inst.write("CH2:VOLTage 0.000")
time.sleep(0.05)

# Turn on outputs
print("Turning on outputs...\n")
inst.write("OUTPut CH1,ON")
time.sleep(0.1)
inst.write("OUTPut CH2,ON")
time.sleep(0.5)

print("-"*60)
print("Starting sweep... (watch the power supply display)")
print("-"*60)

# Sweep Vds from 0 to 2V in 0.5V steps
for vds in [0.0, 0.5, 1.0, 1.5, 2.0]:
    print(f"\nVds = {vds:.1f}V")
    inst.write(f"CH1:VOLTage {vds:.3f}")
    time.sleep(0.05)
    
    # For each Vds, sweep Vgs from 0 to Vds in 0.5V steps
    vgs = 0.0
    while vgs <= vds:
        inst.write(f"CH2:VOLTage {vgs:.3f}")
        time.sleep(0.05)
        
        # Read back values
        inst.write("MEASure:VOLTage? CH1")
        time.sleep(0.05)
        v1 = inst.read().strip()
        
        inst.write("MEASure:VOLTage? CH2")
        time.sleep(0.05)
        v2 = inst.read().strip()
        
        print(f"  Vgs = {vgs:.1f}V  →  CH1={v1}V, CH2={v2}V")
        
        time.sleep(1.0)  # 1 second per step so you can observe
        vgs += 0.5

print("\n" + "-"*60)
print("✓ Sweep complete!")
print("-"*60)

print("\nSetting both channels back to 0V...")
inst.write("CH1:VOLTage 0.000")
time.sleep(0.05)
inst.write("CH2:VOLTage 0.000")
time.sleep(0.2)

print("Turning off outputs...")
inst.write("OUTPut CH1,OFF")
inst.write("OUTPut CH2,OFF")
time.sleep(0.2)

inst.close()
rm.close()

print("\n✓ Done! Both channels are off and set to 0V")
