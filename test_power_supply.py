#!/usr/bin/env python3
"""
Detailed power supply communication test
"""

import pyvisa
import time

# The detected resource
RESOURCE = "USB0::0x0483::0x7540::SPD3EEDC5R0500::INSTR"

print(f"Testing connection to: {RESOURCE}\n")

rm = pyvisa.ResourceManager()

print("Attempting to open resource...")
try:
    inst = rm.open_resource(RESOURCE)
    print(f"✓ Resource opened successfully")
    print(f"  Timeout: {inst.timeout} ms")
    print(f"  Backend: {type(inst)}")
    
    # Try different timeout values
    for timeout_ms in [2000, 5000, 10000, 20000]:
        print(f"\nTrying timeout: {timeout_ms}ms")
        inst.timeout = timeout_ms
        
        try:
            print("  Sending: *IDN?")
            start = time.time()
            response = inst.query("*IDN?")
            elapsed = time.time() - start
            print(f"  ✓ Response ({elapsed:.2f}s): {response.strip()}")
            break  # Success!
        except pyvisa.errors.VisaIOError as e:
            print(f"  ✗ Error: {e}")
            
    # Try without query (just write then read)
    print("\n\nTrying write + read separately:")
    try:
        inst.timeout = 10000
        print("  Writing: *IDN?")
        inst.write("*IDN?")
        time.sleep(0.5)  # Give it time
        print("  Reading response...")
        response = inst.read()
        print(f"  ✓ Response: {response.strip()}")
    except Exception as e:
        print(f"  ✗ Error: {e}")
    
    # Try different terminators
    print("\n\nTrying different read/write terminators:")
    for term in ['\n', '\r\n', '\r']:
        try:
            inst.timeout = 10000
            inst.read_termination = term
            inst.write_termination = term
            print(f"  Terminator: {repr(term)}")
            response = inst.query("*IDN?")
            print(f"  ✓ Response: {response.strip()}")
            break
        except Exception as e:
            print(f"  ✗ Error: {e}")
    
    # Try clearing and resetting
    print("\n\nTrying with clear/reset:")
    try:
        inst.timeout = 10000
        inst.clear()
        time.sleep(0.1)
        response = inst.query("*IDN?")
        print(f"  ✓ Response: {response.strip()}")
    except Exception as e:
        print(f"  ✗ Error: {e}")
    
    # Show resource info
    print("\n\nResource information:")
    try:
        print(f"  Interface type: {inst.interface_type}")
        print(f"  Resource class: {inst.resource_class}")
        print(f"  Resource name: {inst.resource_name}")
    except:
        pass
    
    inst.close()
    print("\n✓ Resource closed")
    
except Exception as e:
    print(f"✗ Failed to open resource: {e}")
    import traceback
    traceback.print_exc()

rm.close()
