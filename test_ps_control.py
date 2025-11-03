#!/usr/bin/env python3
"""
Simple test to verify power supply control
Sets both channels to 1.23V and reads back the values
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
print("Setting both channels to 1.23V")
print("="*60)

# Turn off outputs first
print("\n1. Turning off outputs...")
inst.write("OUTPut CH1,OFF")
inst.write("OUTPut CH2,OFF")
time.sleep(0.2)

# Set voltage for both channels
print("\n2. Setting voltages...")
inst.write("CH1:VOLTage 1.230")
time.sleep(0.1)
inst.write("CH2:VOLTage 1.230")
time.sleep(0.1)

# Set current limits
print("\n3. Setting current limits...")
inst.write("CH1:CURRent 3.000")
time.sleep(0.1)
inst.write("CH2:CURRent 0.100")
time.sleep(0.1)

# Turn on outputs
print("\n4. Turning on outputs...")
inst.write("OUTPut CH1,ON")
time.sleep(0.1)
inst.write("OUTPut CH2,ON")
time.sleep(0.5)

# Read back the settings
print("\n5. Reading back settings...")
print("-"*60)

# CH1 Voltage
inst.write("CH1:VOLTage?")
time.sleep(0.1)
ch1_v_set = inst.read().strip()
print(f"CH1 Voltage Set: {ch1_v_set} V")

# CH1 Measured Voltage
inst.write("MEASure:VOLTage? CH1")
time.sleep(0.1)
ch1_v_meas = inst.read().strip()
print(f"CH1 Voltage Measured: {ch1_v_meas} V")

# CH1 Current
inst.write("MEASure:CURRent? CH1")
time.sleep(0.1)
ch1_i = inst.read().strip()
print(f"CH1 Current: {ch1_i} A")

print()

# CH2 Voltage
inst.write("CH2:VOLTage?")
time.sleep(0.1)
ch2_v_set = inst.read().strip()
print(f"CH2 Voltage Set: {ch2_v_set} V")

# CH2 Measured Voltage
inst.write("MEASure:VOLTage? CH2")
time.sleep(0.1)
ch2_v_meas = inst.read().strip()
print(f"CH2 Voltage Measured: {ch2_v_meas} V")

# CH2 Current
inst.write("MEASure:CURRent? CH2")
time.sleep(0.1)
ch2_i = inst.read().strip()
print(f"CH2 Current: {ch2_i} A")

print("-"*60)

print("\n✓ Channels are now set to 1.23V")
print("\nCheck the power supply display to verify!")
print("\nPress Enter to turn off outputs and exit...")
input()

# Turn off outputs
print("\nTurning off outputs...")
inst.write("OUTPut CH1,OFF")
inst.write("OUTPut CH2,OFF")

inst.close()
rm.close()

print("✓ Done!")
