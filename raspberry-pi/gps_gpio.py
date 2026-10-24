"""Проверка GPS без USB-адаптера: TX модуля GPS → свободный вывод Pi (по умолчанию GPIO27, pin 13), чтение «на слух».

  python3 -u gps_gpio.py            # GPIO27 (pin 13), 4 с
  python3 -u gps_gpio.py 22 6       # GPIO22 (pin 15), 6 с
  python3 -u gps_gpio.py 27 3 down  # подтяжка к 0: уровень 1 останется, только если TX модуля сам держит линию

Подключение: TX GPS → pin 13 (уровень GPS 3.3 В — для Pi безопасно), GND общий (GPS питается от адаптера по USB
или от Pi — земля уже общая). RX GPS и адаптер для проверки не нужны.
Считает переключения линии и сам разбирает UART (скорость — по самому короткому импульсу): есть «$GNGGA…» — модуль
жив, виноват адаптер / провод к нему; переключений 0 — модуль молчит. Только чтение, без sudo (lgpio, группа gpio).
"""

import bisect
import sys
import time

import lgpio

STD = (4800, 9600, 19200, 38400, 57600, 115200)


def decode(edges, level0, baud):
    """UART 8N1 из списка переключений [(t_нс, уровень)]: старт-бит — спад, биты — посередине."""
    times = [t for t, _ in edges]
    levels = [lv for _, lv in edges]

    def level_at(t):
        i = bisect.bisect_right(times, t) - 1
        return levels[i] if i >= 0 else level0

    T = 1e9 / baud
    out, i = bytearray(), 0
    while i < len(edges):
        t, lv = edges[i]
        if lv != 0 or level_at(t + 0.5 * T) != 0:
            i += 1
            continue
        b = 0
        for k in range(8):
            if level_at(t + (1.5 + k) * T):
                b |= 1 << k
        out.append(b)
        i = bisect.bisect_right(times, t + 9.25 * T, lo=i)
    return bytes(out)


def main():
    gpio = int(sys.argv[1]) if len(sys.argv) > 1 else 27
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 4.0
    down = len(sys.argv) > 3 and sys.argv[3] == "down"
    h = lgpio.gpiochip_open(0)
    edges = []
    try:
        lgpio.gpio_claim_alert(h, gpio, lgpio.BOTH_EDGES, lgpio.SET_PULL_DOWN if down else lgpio.SET_PULL_UP)
        time.sleep(0.05)
        level0 = lgpio.gpio_read(h, gpio)
        cb = lgpio.callback(h, gpio, lgpio.BOTH_EDGES, lambda c, g, lv, ts: edges.append((ts, lv)) if lv in (0, 1) else None)
        print(f"GPIO{gpio}: подтяжка к {'0' if down else '3.3 В'}, слушаю {secs:.0f} с, уровень в начале {level0}"
              + (" (1 = TX модуля сам держит 3.3 В, 0 = линию никто не держит)" if down else " (1 = линия в 3.3 В)"))
        time.sleep(secs)
        cb.cancel()
        level_end = lgpio.gpio_read(h, gpio)
    finally:
        lgpio.gpio_free(h, gpio)
        lgpio.gpiochip_close(h)

    print(f"переключений: {len(edges)}, уровень в конце {level_end}")
    if len(edges) < 20:
        if level0 == 1 and level_end == 1:
            print("линия в 1 без передачи — TX модуля держит 3.3 В, но данных нет" if down
                  else "линия молчит — модуль GPS ничего не передаёт (проверить ещё с подтяжкой к 0: аргумент down)")
        else:
            print("линия в 0 — TX никто не держит: модуль не работает или провод не на этом выводе")
        return
    gaps = sorted(b[0] - a[0] for a, b in zip(edges, edges[1:]) if b[0] > a[0])
    bit_ns = gaps[max(0, len(gaps) // 20)]                     # ~5-й процентиль — один бит
    est = 1e9 / bit_ns
    baud = min(STD, key=lambda s: abs(s - est))
    data = decode(edges, level0, baud)
    text = "".join(chr(c) if 32 <= c < 127 or c in (10, 13) else "·" for c in data)
    print(f"скорость по импульсам ≈ {est:.0f} → {baud}; байт {len(data)}, строк NMEA «$G»: {data.count(b'$G')}")
    for line in [s for s in text.splitlines() if s.strip()][:6]:
        print("  " + line[:100])
    print("модуль GPS ЖИВ — виноват адаптер PL2303 или провод к нему" if b"$G" in data
          else "переключения есть, но NMEA не разобрать — прислать вывод")


if __name__ == "__main__":
    main()
