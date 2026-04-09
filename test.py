"""
Minimal test: open COM6 at 115200.
Do NOT toggle DTR/RTS. Just open, send, and listen.
"""

import serial
import time

PORT = "COM6"
BAUD = 115200


def main():
    # Simplest possible open - let pyserial use defaults
    ser = serial.Serial(PORT, BAUD, timeout=3.0)
    time.sleep(2.0)  # Wait for device to settle after port open
    
    # Read any unsolicited data
    n = ser.in_waiting
    if n:
        raw = ser.read(n)
        print(f"Unsolicited ({n} bytes): hex={raw.hex(' ')}, ascii={raw.decode('ascii', errors='replace')!r}")
    else:
        print("No unsolicited data")
    
    # Send ?LT and wait
    ser.reset_input_buffer()
    ser.write(b"?LT\r\n")
    print("Sent ?LT\\r\\n, waiting 3s...")
    time.sleep(3.0)
    n = ser.in_waiting
    if n:
        raw = ser.read(n)
        print(f"Response ({n} bytes): hex={raw.hex(' ')}, ascii={raw.decode('ascii', errors='replace')!r}")
    else:
        print("No response to ?LT")
    
    # Try blocking read with timeout
    ser.reset_input_buffer()
    ser.write(b"?HID\r\n")
    print("Sent ?HID\\r\\n, blocking read...")
    raw = ser.read(50)  # Will block up to 3s (timeout)
    if raw:
        print(f"Response ({len(raw)} bytes): hex={raw.hex(' ')}, ascii={raw.decode('ascii', errors='replace')!r}")
    else:
        print("No response to ?HID (timeout)")
    
    # Check if write is actually going out
    print(f"\nPort info: name={ser.name}, baudrate={ser.baudrate}")
    print(f"Modem: CTS={ser.cts}, DSR={ser.dsr}, RI={ser.ri}, CD={ser.cd}")
    print(f"Bytes written: {ser.write(b'test')}")
    
    ser.close()
    print("Done.")


if __name__ == "__main__":
    main()
