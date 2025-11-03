#!/usr/bin/env python3
"""
VISA diagnostic script to check connected devices
"""

import pyvisa

print("Testing VISA connection...\n")

rm = pyvisa.ResourceManager()

print(f"VISA Backend: {rm}")
print(f"VISA Library: {rm.visalib}\n")

resources = rm.list_resources()

print(f"Found {len(resources)} VISA resources:")
print("-" * 60)

if len(resources) == 0:
    print("No VISA resources found!")
    print("\nPossible issues:")
    print("1. Device not connected via USB")
    print("2. NI-VISA not installed or configured properly")
    print("3. Device drivers not installed")
    print("4. Try using '@py' backend: ResourceManager('@py')")
else:
    for i, res in enumerate(resources, 1):
        print(f"{i}. {res}")
        try:
            inst = rm.open_resource(res)
            inst.timeout = 2000
            try:
                idn = inst.query("*IDN?")
                print(f"   ID: {idn.strip()}")
            except Exception as e:
                print(f"   Could not query *IDN?: {e}")
            inst.close()
        except Exception as e:
            print(f"   Could not open: {e}")
        print()

print("-" * 60)

# Try alternative backend
print("\nTrying pyvisa-py backend...")
try:
    rm_py = pyvisa.ResourceManager('@py')
    resources_py = rm_py.list_resources()
    print(f"Found {len(resources_py)} resources with @py backend:")
    for res in resources_py:
        print(f"  - {res}")
except Exception as e:
    print(f"pyvisa-py backend error: {e}")
    print("You may need to install: pip install pyvisa-py")
