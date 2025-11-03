#!/usr/bin/env python3
"""
Joulescope connection test
"""

import sys

try:
    import joulescope
    print(f"✓ Joulescope library imported")
    print(f"  Version: {joulescope.__version__}")
except ImportError as e:
    print(f"✗ Cannot import joulescope: {e}")
    sys.exit(1)

print("\n" + "="*60)
print("Testing Joulescope Connection")
print("="*60)

# Method 1: Using scan
print("\nMethod 1: Using joulescope.scan()...")
try:
    devices = joulescope.scan()
    print(f"  Found {len(devices) if devices else 0} device(s)")
    if devices:
        for i, dev in enumerate(devices):
            print(f"  Device {i}: {dev}")
    else:
        print("  No devices found")
except Exception as e:
    print(f"  ✗ Error: {e}")
    import traceback
    traceback.print_exc()

# Method 2: Using scan with name filter
print("\nMethod 2: Using joulescope.scan(name='JS110')...")
try:
    devices = joulescope.scan(name='JS110')
    print(f"  Found {len(devices) if devices else 0} JS110 device(s)")
    if devices:
        for i, dev in enumerate(devices):
            print(f"  Device {i}: {dev}")
except Exception as e:
    print(f"  ✗ Error: {e}")
    import traceback
    traceback.print_exc()

# Method 3: Using scan_require_one
print("\nMethod 3: Using joulescope.scan_require_one()...")
try:
    device = joulescope.scan_require_one()
    print(f"  ✓ Found device: {device}")
except Exception as e:
    print(f"  ✗ Error: {e}")
    import traceback
    traceback.print_exc()

# Check USB devices
print("\n" + "="*60)
print("Checking for USB devices...")
print("="*60)

try:
    import usb.core
    devices = usb.core.find(find_all=True)
    joulescope_devices = []
    
    for dev in devices:
        try:
            # Joulescope JS110 VID:PID is 0x16d0:0x0e93
            if dev.idVendor == 0x16d0 and dev.idProduct == 0x0e93:
                joulescope_devices.append(dev)
                print(f"  ✓ Found Joulescope: VID={hex(dev.idVendor)}, PID={hex(dev.idProduct)}")
                print(f"    Bus={dev.bus}, Address={dev.address}")
        except:
            pass
    
    if not joulescope_devices:
        print("  ✗ No Joulescope USB devices found")
        print("\n  Is the Joulescope JS110 connected via USB?")
        
except ImportError:
    print("  pyusb not installed (pip install pyusb)")
except Exception as e:
    print(f"  Error scanning USB: {e}")

print("\n" + "="*60)
print("Recommendation:")
print("="*60)

print("""
If no Joulescope is detected:
1. Ensure JS110 is connected via USB
2. Check Windows Device Manager for the device
3. Reinstall Joulescope drivers if needed
4. Try unplugging and reconnecting the device

To run the scan without Joulescope:
  python scan_mosfet.py --dry-run
""")
