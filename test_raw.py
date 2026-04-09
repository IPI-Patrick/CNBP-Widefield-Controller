import ctypes
import ctypes.wintypes as w
import time

k32 = ctypes.windll.kernel32

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3

h = k32.CreateFileW('\\\\.\\COM6', GENERIC_READ | GENERIC_WRITE, 0, None, OPEN_EXISTING, 0, None)
print(f'Handle: {h}')
if h == -1:
    print('Failed to open COM6')
    exit(1)

class COMMTIMEOUTS(ctypes.Structure):
    _fields_ = [('ReadIntervalTimeout', w.DWORD),
                 ('ReadTotalTimeoutMultiplier', w.DWORD),
                 ('ReadTotalTimeoutConstant', w.DWORD),
                 ('WriteTotalTimeoutMultiplier', w.DWORD),
                 ('WriteTotalTimeoutConstant', w.DWORD)]

t = COMMTIMEOUTS(50, 0, 3000, 0, 3000)
k32.SetCommTimeouts(h, ctypes.byref(t))

# Purge
k32.PurgeComm(h, 0x000F)
time.sleep(1.0)

# Write
cmd = b'?LT\r\n'
written = w.DWORD(0)
result = k32.WriteFile(h, cmd, len(cmd), ctypes.byref(written), None)
print(f'WriteFile: ok={result}, written={written.value}')

time.sleep(2.0)

# Read
buf = ctypes.create_string_buffer(1024)
read_bytes = w.DWORD(0)
result = k32.ReadFile(h, buf, 1024, ctypes.byref(read_bytes), None)
print(f'ReadFile: ok={result}, read={read_bytes.value}')
if read_bytes.value > 0:
    raw = buf.raw[:read_bytes.value]
    print('Hex:', raw.hex(' '))
    print('Ascii:', raw.decode('ascii', errors='replace'))
else:
    print('No data received')

k32.CloseHandle(h)
print('Done')
