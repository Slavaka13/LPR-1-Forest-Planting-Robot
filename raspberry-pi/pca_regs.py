"""Только чтение: отвечают ли обе PCA9685 и что в их регистрах — PCA руки (0x42) и PCA посева (0x40).

  python3 -u pca_regs.py

Печатает MODE1 (спит / работает), MODE2, PRE_SCALE (частоту по замеренному генератору из hw.py) и что выдаёт каждый
канал: импульс в мкс, «выключен» (full OFF) или «постоянная 1» (full ON). Ничего не пишет — можно при работающей службе.
Канал выдаёт импульс, а серво стоит → смотреть питание 6 В на гребёнке и разъём (у серво 360° — ещё мёртвую зону).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hw import PCA_ARM_ADDR, PCA_ARM_OSC_HZ, PCA_SEED_ADDR, PCA_SEED_OSC_HZ  # noqa: E402

try:
    from smbus2 import SMBus
except ImportError:
    from smbus import SMBus

BOARDS = ((PCA_ARM_ADDR, PCA_ARM_OSC_HZ, "рука", (0, 1, 2, 3, 4)),
          (PCA_SEED_ADDR, PCA_SEED_OSC_HZ, "посев", (0, 1, 2, 8)))


def main():
    b = SMBus(1)
    for addr, osc, name, chans in BOARDS:
        try:
            m1, m2, pre = b.read_byte_data(addr, 0x00), b.read_byte_data(addr, 0x01), b.read_byte_data(addr, 0xFE)
        except OSError as e:
            print(f"0x{addr:02X} ({name}): НЕ ОТВЕЧАЕТ ({e})")
            continue
        print(f"0x{addr:02X} ({name}): MODE1 0x{m1:02X} ({'спит' if m1 & 0x10 else 'работает'}), MODE2 0x{m2:02X}, "
              f"PRE_SCALE {pre} (≈ {osc / (4096 * (pre + 1)):.1f} Гц)")
        tick = (pre + 1) * 1e6 / osc
        for ch in chans:
            r = b.read_i2c_block_data(addr, 0x06 + 4 * ch, 4)
            on, off = r[0] | (r[1] << 8), r[2] | (r[3] << 8)
            if off & 0x1000:
                s = "выключен (full OFF)"
            elif on & 0x1000:
                s = "постоянная 1 (full ON)"
            else:
                s = f"{((off - on) & 0xFFF) * tick:.0f} мкс"
            print(f"   кан. {ch}: {s}   [{' '.join(f'{x:02X}' for x in r)}]")
    b.close()


if __name__ == "__main__":
    main()
