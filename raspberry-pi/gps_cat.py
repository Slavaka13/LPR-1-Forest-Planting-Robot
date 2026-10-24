"""Показать сырые строки NMEA от GPS (PL2303) — проверка, что модуль шлёт данные. Только чтение, журнал не пишет.

  python3 -u gps_cat.py [секунд]     (по умолчанию 5)
В конце — сводка по последней $..GGA: решение (0 — нет), спутники, HDOP.
"""

import glob
import sys
import time

import serial

dur = float(sys.argv[1]) if len(sys.argv) > 1 else 5
ports = sorted(glob.glob("/dev/serial/by-id/usb-Prolific*"))
if not ports:
    sys.exit("порт /dev/serial/by-id/usb-Prolific* не найден — PL2303 не подключён?")
s = serial.Serial(ports[0], 9600, timeout=1)
print("порт", ports[0])
t0, n, gga = time.monotonic(), 0, None
while time.monotonic() - t0 < dur:
    line = s.readline().decode("ascii", "replace").strip()
    if line:
        n += 1
        print(line)
        if line[3:6] == "GGA":
            gga = line.split(",")
s.close()
print(f"--- строк за {dur:.0f} с: {n}")
if gga and len(gga) > 8:
    print(f"GGA: решение {gga[6] or '?'} (0 — нет), спутников {gga[7] or '?'}, HDOP {gga[8] or '?'}")
elif n == 0:
    print("данных нет: проверить TX GPS -> RXD адаптера, питание GPS (светодиод), скорость 9600")
