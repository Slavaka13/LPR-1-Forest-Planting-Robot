"""Диагностика PCA9685: регистры, частота, что стоит на каналах; по желанию — держать импульс на каналах N секунд.

  python3 -u pca_diag.py                         # только прочитать (ничего не меняет)
  python3 -u pca_diag.py --ch 0 1 --us 1700 --hold 20   # 1700 мкс на каналах 0 и 1 на 20 с (серво 360° будут крутиться!)
  python3 -u pca_diag.py --ch 0 1 4 --high --hold 40    # постоянная 1 на выходах: на штырьке PWM ↔ GND ≈ 3.3 В

Пока держит импульс — можно мерить мультиметром на гребёнке канала: V+ ↔ GND ≈ 6 В; сигнал ↔ GND ≈ 0.25–0.3 В
(средний уровень импульса 1.7 мс из 20 мс при 3.3 В). После — каналы выключаются (полностью).
"""

import argparse
import time

from servo_test import PCA_OSC_HZ, PCA_PRESCALE, PCA_TICK_US   # поправка на настоящую частоту генератора PCA

ADDR = 0x40


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ch", type=int, nargs="*", default=[])
    ap.add_argument("--us", type=int, default=1500)
    ap.add_argument("--hold", type=float, default=0)
    ap.add_argument("--high", action="store_true", help="постоянная 1 (≈ VCC 3.3 В) на выходах вместо импульса — мерить мультиметром")
    a = ap.parse_args()
    try:
        from smbus2 import SMBus
    except ImportError:
        from smbus import SMBus
    b = SMBus(1)

    def show(title):
        m1, m2, pre = b.read_byte_data(ADDR, 0x00), b.read_byte_data(ADDR, 0x01), b.read_byte_data(ADDR, 0xFE)
        freq = PCA_OSC_HZ / (4096 * (pre + 1))
        print(f"{title}: MODE1=0x{m1:02X} ({'спит' if m1 & 0x10 else 'работает'}{', автоинкремент' if m1 & 0x20 else ''}) "
              f"MODE2=0x{m2:02X} ({'двухтактный выход' if m2 & 0x04 else 'открытый сток'}) PRE_SCALE={pre} (~{freq:.0f} Гц)")
        for ch in sorted(set([0, 1, 2] + a.ch)):
            r = b.read_i2c_block_data(ADDR, 0x06 + 4 * ch, 4)
            on, off = r[0] | (r[1] << 8), r[2] | (r[3] << 8)
            if off & 0x1000:
                st = "выключен полностью"
            elif on & 0x1000:
                st = "включён полностью (1 постоянно)"
            else:
                st = f"импульс {((off - on) & 0xFFF) * 1e6 / (4096 * freq):.0f} мкс"
            print(f"  канал {ch}: ON=0x{on:04X} OFF=0x{off:04X} → {st}")

    show("сейчас")
    if a.ch:
        if not a.high and not 1000 <= a.us <= 2000:
            raise SystemExit("--us 1000..2000")
        if b.read_byte_data(ADDR, 0xFE) != PCA_PRESCALE:
            b.write_byte_data(ADDR, 0x00, 0x10)          # сон — только в нём меняется PRE_SCALE
            b.write_byte_data(ADDR, 0xFE, PCA_PRESCALE)  # 50 Гц по настоящему генератору
        b.write_byte_data(ADDR, 0x00, 0x20)              # проснуться, автоинкремент
        time.sleep(0.005)
        off = round(a.us / PCA_TICK_US)
        for ch in a.ch:
            if a.high:
                b.write_i2c_block_data(ADDR, 0x06 + 4 * ch, [0, 0x10, 0, 0])      # ON full: постоянная 1
            else:
                b.write_i2c_block_data(ADDR, 0x06 + 4 * ch, [0, 0, off & 0xFF, off >> 8])
        show("поставил постоянную 1" if a.high else f"поставил {a.us} мкс")
        try:
            print(f"держу {a.hold:.0f} с — можно мерить на гребёнке (Ctrl+C — раньше)")
            time.sleep(a.hold)
        except KeyboardInterrupt:
            pass
        finally:
            for ch in a.ch:
                b.write_i2c_block_data(ADDR, 0x06 + 4 * ch, [0, 0, 0, 0x10])
            show("выключил")
    b.close()


if __name__ == "__main__":
    main()
