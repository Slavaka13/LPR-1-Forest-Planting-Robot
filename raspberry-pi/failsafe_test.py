"""Проверка failsafe: python3 failsafe_test.py [секунд]
Запустите с включённым тумблером ARM и выключите пульт - CH5 должен уйти в 1000 или кадры должны пропасть."""

import sys
import time

from ibus import IBusReader

seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 30
rc = IBusReader()                   # постоянное имя HW-597 (ibus.DEFAULT_PORT)
t0 = time.monotonic()
while time.monotonic() - t0 < seconds:
    time.sleep(0.5)
    ch = rc.channels()
    t = time.monotonic() - t0
    if ch is None:
        print(f"{t:5.1f} с  НЕТ КАДРОВ i-BUS (кадров всего {rc.frames_ok})", flush=True)
    else:
        print(f"{t:5.1f} с  " + " ".join(f"CH{i + 1}={v}" for i, v in enumerate(ch[:10])), flush=True)
rc.close()
