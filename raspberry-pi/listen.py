"""Пассивная проверка связи: только слушает порт, моторам ничего не отправляет."""

import struct
import sys
import time

import serial

from hoverboard import FB_FORMAT, FB_SIZE, START_BYTES

port = sys.argv[1] if len(sys.argv) > 1 else "/dev/serial0"
seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 3

ser = serial.Serial(port, 115200, timeout=0.1)
ser.reset_input_buffer()
data = bytearray()
t_end = time.monotonic() + seconds
while time.monotonic() < t_end:
    data += ser.read(256)
ser.close()

print(f"Принято байт за {seconds:.0f} с: {len(data)}")
print("Первые байты:", data[:40].hex(" "))

ok = bad = 0
last = None
i = data.find(START_BYTES)
while 0 <= i and i + FB_SIZE <= len(data):
    fields = struct.unpack(FB_FORMAT, data[i:i + FB_SIZE])
    cs = 0
    for v in fields[:-1]:
        cs ^= v & 0xFFFF
    if cs == fields[-1]:
        ok += 1
        last = fields
        i = data.find(START_BYTES, i + FB_SIZE)
    else:
        bad += 1
        i = data.find(START_BYTES, i + 1)

print(f"Пакетов обратной связи: верных {ok}, с ошибкой {bad}")
if last:
    _, cmd1, cmd2, sr, sl, bat, temp, led, _ = last
    print(f"cmd1={cmd1} cmd2={cmd2} speedR={sr} speedL={sl} "
          f"батарея={bat / 100:.2f} В температура={temp / 10:.1f} °C")
