"""Сырые регистры INA219 (только чтение, ничего не пишет): настройка, шунт, шина — в hex, двумя способами чтения.

  python3 -u ina_raw.py            # 0x41 и 0x44, 10 раз
  python3 -u ina_raw.py 0x41 20
  python3 -u ina_raw.py --fast        # без усреднения 400 замеров: выбросы, медиана (настройку потом возвращает)

Шунт: 10 мкВ на единицу (ток = код × 10 мкВ / шунт). Шина: (код >> 3) × 4 мВ. Настройка после seeder.py / hw.py — 0x19C7
(шина 16 В, PGA /8, 12 бит, шунт — 1 замер, непрерывно); после включения питания — 0x399F.
У наших модулей аппаратное усреднение 2…128 врёт (22.10.2026: --modes) — только 1 замер.
Если «шунт» прыгает кратно 0x0800 (0x1000, 0x2800 …) — смотреть, совпадает ли чтение блоком и словом (порядок байтов).
"""

import sys
import time

from smbus2 import SMBus


def s16(v):
    return v - 0x10000 if v & 0x8000 else v


def fast(addrs, n):
    """Без усреднения (1 замер, 532 мкс): распределение отдельных замеров шунта — видно выбросы. Настройку потом
    возвращает (0x19C7, как у программы)."""
    b = SMBus(1)
    try:
        for a in addrs:
            b.write_i2c_block_data(a, 0x00, [0x19, 0xC7])
        time.sleep(0.01)
        for a in addrs:
            v = []
            for _ in range(n):
                hi, lo = b.read_i2c_block_data(a, 0x01, 2)
                v.append(s16((hi << 8) | lo) * 0.01)
                time.sleep(0.0006)
            v.sort()
            big = sum(1 for x in v if abs(x) > 300)
            print(f"0x{a:02x}: замеров {n}: мин {v[0]:.2f} мВ, медиана {v[n // 2]:.2f}, макс {v[-1]:.2f}; "
                  f"|U| > 300 мВ (упор шкалы) — {big} ({100 * big / n:.0f} %); "
                  f"10 % / 90 %: {v[n // 10]:.2f} / {v[n * 9 // 10]:.2f} мВ")
    finally:
        for a in addrs:
            b.write_i2c_block_data(a, 0x00, [0x19, 0xC7])
        b.close()


def modes(addrs, n=20):
    """Все режимы усреднения шунта по очереди (1 … 128 замеров): у каких модулей какие врут. Потом возвращает 0x19C7."""
    conv_ms = {1: 0.532, 2: 1.06, 4: 2.13, 8: 4.26, 16: 8.51, 32: 17.02, 64: 34.05, 128: 68.1}
    b = SMBus(1)
    try:
        for k, sadc in ((1, 0b1000), (2, 0b1001), (4, 0b1010), (8, 0b1011), (16, 0b1100), (32, 0b1101), (64, 0b1110), (128, 0b1111)):
            cfg = (3 << 11) | (0b0011 << 7) | (sadc << 3) | 0b111
            for a in addrs:
                b.write_i2c_block_data(a, 0x00, [cfg >> 8, cfg & 0xFF])
            time.sleep(conv_ms[k] * 3 / 1000)
            out = []
            for a in addrs:
                v = []
                for _ in range(n):
                    hi, lo = b.read_i2c_block_data(a, 0x01, 2)
                    v.append(s16((hi << 8) | lo) * 0.01)
                    time.sleep(conv_ms[k] / 1000 * 1.2)
                v.sort()
                out.append(f"0x{a:02x}: медиана {v[n // 2]:8.2f} мВ, разброс {v[0]:8.2f} … {v[-1]:8.2f}")
            print(f"усреднение {k:3d} (0x{cfg:04x}):  " + "   ".join(out), flush=True)
    finally:
        for a in addrs:
            b.write_i2c_block_data(a, 0x00, [0x19, 0xC7])
        b.close()


def main():
    if "--modes" in sys.argv:
        sys.argv.remove("--modes")
        modes([int(sys.argv[1], 0)] if len(sys.argv) > 1 else [0x41, 0x44])
        return
    if "--fast" in sys.argv:
        sys.argv.remove("--fast")
        fast([int(sys.argv[1], 0)] if len(sys.argv) > 1 else [0x41, 0x44], int(sys.argv[2]) if len(sys.argv) > 2 else 400)
        return
    addrs = [int(sys.argv[1], 0)] if len(sys.argv) > 1 else [0x41, 0x44]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    b = SMBus(1)
    for a in addrs:
        hi, lo = b.read_i2c_block_data(a, 0x00, 2)
        print(f"0x{a:02x}: настройка 0x{(hi << 8) | lo:04x}")
    for _ in range(n):
        row = []
        for a in addrs:
            sh = b.read_i2c_block_data(a, 0x01, 2)
            bu = b.read_i2c_block_data(a, 0x02, 2)
            w = b.read_word_data(a, 0x01)                     # SMBus: младший байт первым
            raw = (sh[0] << 8) | sh[1]
            row.append(f"0x{a:02x} шунт {sh[0]:02x} {sh[1]:02x} ({s16(raw):6d} = {s16(raw) * 0.01:7.2f} мВ) "
                       f"слово {w:04x}  шина {bu[0]:02x} {bu[1]:02x} ({(((bu[0] << 8) | bu[1]) >> 3) * 0.004:5.2f} В)")
        print("   ".join(row), flush=True)
        time.sleep(0.1)
    b.close()


if __name__ == "__main__":
    main()
