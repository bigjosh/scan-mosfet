#!/usr/bin/env python3
"""
Test Joulescope API to find correct current reading method
"""

import joulescope
import time

print("Connecting to Joulescope...")
devices = joulescope.scan()
if not devices:
    print("No devices found!")
    exit(1)

js = devices[0]
js.open()
print(f"Connected: {js.device_serial_number}")

# List available methods and attributes
print("\nAvailable methods and attributes:")
attrs = [a for a in dir(js) if not a.startswith('_')]
for attr in sorted(attrs):
    print(f"  - {attr}")

# Try to start streaming and read data
print("\nStarting stream...")
js.start()

print("Waiting for data...")
time.sleep(1)

# Try different methods to read current
print("\nTrying to read current...")

# Method 1: Try read method
try:
    print("\n1. Using read():")
    data = js.read()
    print(f"   Type: {type(data)}")
    if isinstance(data, dict):
        print(f"   Keys: {data.keys()}")
except Exception as e:
    print(f"   Error: {e}")

# Method 2: Try stream_buffer
try:
    print("\n2. Using stream_buffer:")
    buffer = js.stream_buffer
    print(f"   Type: {type(buffer)}")
    if buffer:
        samples = buffer.samples_get(0, buffer.sample_id_range[1], ['current'])
        print(f"   Current samples shape: {samples['signals']['current']['value'].shape}")
        print(f"   Mean current: {samples['signals']['current']['value'].mean()}")
except Exception as e:
    print(f"   Error: {e}")

# Method 3: Try view
try:
    print("\n3. Using view:")
    view = js.view
    print(f"   Type: {type(view)}")
    if hasattr(view, 'statistics_get'):
        stats = view.statistics_get()
        print(f"   Stats: {stats}")
except Exception as e:
    print(f"   Error: {e}")

# Method 4: Check for statistics on device
try:
    print("\n4. Checking statistics on device:")
    if hasattr(js, 'statistics'):
        stats = js.statistics()
        print(f"   Stats: {stats}")
except Exception as e:
    print(f"   Error: {e}")

print("\nStopping stream...")
js.stop()
js.close()
print("Done!")
