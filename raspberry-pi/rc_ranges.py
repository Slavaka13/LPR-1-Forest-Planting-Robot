"""Запись диапазонов каналов пульта: python3 rc_ranges.py [секунд]
Двигайте стики до упора и переключайте тумблеры - в конце будет таблица min/max по каналам."""

import sys
import time

from ibus import IBusReader

seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 30
rc = IBusReader()                   # постоянное имя HW-597 (ibus.DEFAULT_PORT)
lo, hi, first, last = [9999] * 14, [0] * 14, None, None
seen = [set() for _ in range(14)]
t_end = time.monotonic() + seconds
while time.monotonic() < t_end:
    time.sleep(0.01)
    ch = rc.channels()
    if ch:
        first = first or ch
        last = ch
        lo = [min(a, b) for a, b in zip(lo, ch)]
        hi = [max(a, b) for a, b in zip(hi, ch)]
        for s, v in zip(seen, ch):
            s.add(v // 10)              # с точностью 10 единиц
rc.close()

print(f"Кадров: верных {rc.frames_ok}, с ошибкой {rc.frames_bad}")
if last:
    print("Канал   min   max  начало  конец  разных значений")
    for i in range(10):
        moved = ""
        if hi[i] - lo[i] > 50:
            moved = "  <- плавно (стик/крутилка)" if len(seen[i]) > 5 else "  <- скачком (тумблер)"
        print(f"CH{i + 1:<3} {lo[i]:5} {hi[i]:5} {first[i]:7} {last[i]:6} {len(seen[i]):8}{moved}")
