# Troubleshooting Guide

## Joulescope Connection Issues

### Error: "jsdrv_open failed" or "could not open device path"

**Cause:** The Joulescope is detected but cannot be opened, usually because:
1. Another program is using it (Joulescope UI, previous scan, etc.)
2. Insufficient permissions
3. Driver needs reset

**Solutions:**

1. **Close Joulescope UI** if it's running
   - The Joulescope can only be opened by one program at a time
   - Close any Joulescope applications before running the scan

2. **Disconnect and reconnect** the Joulescope
   - Unplug the USB cable
   - Wait 5 seconds
   - Plug it back in
   - Wait for Windows to recognize it
   - Try running the scan again

3. **Check Windows Device Manager**
   - Open Device Manager (Win+X → Device Manager)
   - Look for "Joulescope" under "Universal Serial Bus devices"
   - If you see a warning icon, right-click and "Update driver"

4. **Run as Administrator**
   - Try running the command prompt as Administrator
   - Then run the scan

5. **Test without Joulescope** (dry-run mode)
   - To verify the rest of the system works:
   ```bash
   python scan_mosfet.py --dry-run --vds-stop 0.5
   ```

6. **Reinstall Joulescope software**
   - Download from: https://www.joulescope.com/download
   - Reinstall to update drivers

## Power Supply Connection Issues

### Error: "Could not find Siglent SPD3303C power supply"

**Solutions:**

1. **Verify VISA installation**
   ```bash
   python test_visa.py
   ```

2. **Manually specify the resource**
   ```bash
   python scan_mosfet.py --visa-resource "USB0::0x0483::0x7540::SPD3EEDC5R0500::INSTR"
   ```

3. **Check USB connection**
   - Ensure the power supply is powered on
   - Check USB cable connection
   - Try a different USB port

## Testing Individual Components

Run these test scripts to diagnose issues:

```bash
# Test VISA/power supply
python test_visa.py
python test_power_supply.py

# Test Joulescope
python test_joulescope.py
```

## Getting Help

If issues persist, please provide:
1. Output from `test_visa.py`
2. Output from `test_joulescope.py`
3. Error messages from the scan
4. Windows version and Python version
