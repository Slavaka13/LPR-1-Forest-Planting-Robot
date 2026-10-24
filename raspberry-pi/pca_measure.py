"""Замер настоящего импульса PCA9685 самой Pi: выход канала PCA (по умолчанию 4) → вход Pi GPIO27 (pin 13).

  python3 -u pca_measure.py                       # канал 4, 1500 мкс, GPIO27, 2 с
  python3 -u pca_measure.py --ch 4 --us 1700
  python3 -u pca_measure.py --addr 0x42 --ch 3      # PCA №2 (рука): провод с PWM кан. 3 (резерв) → pin 13

Внутренний генератор PCA9685 не ровно 25 МГц (у многих плат 26–27): тогда и импульс, и частота другие, чем задано,
а мультиметр этого не видит (доля импульса та же). Скрипт ставит импульс, ловит фронты на GPIO (lgpio) и печатает
настоящие импульс, период и частоту генератора — её подставить в программы (OSC_HZ). Без sudo.
Провод: штырёк PWM (верхний ряд) канала → pin 13. Уровень выхода PCA = VCC 3.3 В — для Pi безопасно.
"""

import argparse
import statistics
import time

import lgpio

from hw import PCA_ARM_ADDR, PCA_ARM_OSC_HZ, PCA_SEED_OSC_HZ


def scale(addr):
    """Делитель и шаг импульса по замеренной частоте генератора своей платы (после замера импульс = заданному)."""
    osc = PCA_ARM_OSC_HZ if addr == PCA_ARM_ADDR else PCA_SEED_OSC_HZ
    pre = round(osc / (4096 * 50)) - 1
    return pre, (pre + 1) * 1e6 / osc


def static(a):
    """Канал в постоянную 1 и в постоянный 0 — уровень на GPIO должен повторять (проверка провода и выхода)."""
    try:
        from smbus2 import SMBus
    except ImportError:
        from smbus import SMBus
    b = SMBus(1)
    b.write_byte_data(a.addr, 0x00, (b.read_byte_data(a.addr, 0x00) & ~0x10) | 0x20)   # не спать
    h = lgpio.gpiochip_open(0)
    try:
        lgpio.gpio_claim_input(h, a.gpio, lgpio.SET_PULL_DOWN)
        res = []
        for name, regs in (("1 (ON full)", [0, 0x10, 0, 0]), ("0 (OFF full)", [0, 0, 0, 0x10]),
                           ("1 (ON full)", [0, 0x10, 0, 0]), ("0 (OFF full)", [0, 0, 0, 0x10])):
            b.write_i2c_block_data(a.addr, 0x06 + 4 * a.ch, regs)
            time.sleep(0.05)
            res.append((name, lgpio.gpio_read(h, a.gpio)))
        for name, lv in res:
            print(f"  канал {a.ch} = {name:13s} → GPIO{a.gpio} читает {lv}")
        ok = [lv for _, lv in res] == [1, 0, 1, 0]
        print("провод и выход канала в порядке" if ok else
              "GPIO не повторяет выход — провод не туда / нет общей земли / выход канала не работает (или OE не на GND)")
    finally:
        b.write_i2c_block_data(a.addr, 0x06 + 4 * a.ch, [0, 0, 0, 0x10])
        b.close()
        lgpio.gpio_free(h, a.gpio)
        lgpio.gpiochip_close(h)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ch", type=int, default=4, help="канал PCA9685, провод с его PWM на GPIO")
    ap.add_argument("--us", type=int, default=1500, help="заданный импульс, мкс (по частоте из hw.py)")
    ap.add_argument("--gpio", type=int, default=27, help="вход Pi (BCM), 27 = pin 13")
    ap.add_argument("--time", type=float, default=2.0)
    ap.add_argument("--addr", type=lambda s: int(s, 0), default=0x40, help="адрес PCA9685: 0x40 посев, 0x42 рука")
    ap.add_argument("--static", action="store_true", help="проверка провода: постоянная 1, потом 0 — что читает GPIO")
    a = ap.parse_args()
    if a.static:
        static(a)
        return
    try:
        from smbus2 import SMBus
    except ImportError:
        from smbus import SMBus

    b = SMBus(1)
    PCA_PRESCALE, PCA_TICK_US = scale(a.addr)
    if b.read_byte_data(a.addr, 0xFE) != PCA_PRESCALE:
        b.write_byte_data(a.addr, 0x00, 0x10)
        b.write_byte_data(a.addr, 0xFE, PCA_PRESCALE)
    b.write_byte_data(a.addr, 0x00, 0x20)
    time.sleep(0.005)
    pre = b.read_byte_data(a.addr, 0xFE)
    off = round(a.us / PCA_TICK_US)
    b.write_i2c_block_data(a.addr, 0x06 + 4 * a.ch, [0, 0, off & 0xFF, off >> 8])
    time.sleep(0.1)

    h = lgpio.gpiochip_open(0)
    edges = []
    try:
        lgpio.gpio_claim_alert(h, a.gpio, lgpio.BOTH_EDGES, lgpio.SET_PULL_DOWN)
        cb = lgpio.callback(h, a.gpio, lgpio.BOTH_EDGES, lambda c, g, lv, ts: edges.append((ts, lv)) if lv in (0, 1) else None)
        time.sleep(a.time)
        cb.cancel()
    finally:
        lgpio.gpio_free(h, a.gpio)
        lgpio.gpiochip_close(h)
        b.write_i2c_block_data(a.addr, 0x06 + 4 * a.ch, [0, 0, 0, 0x10])   # канал выключить
        b.close()

    highs, rises = [], []
    for (t, lv), nxt in zip(edges, edges[1:] + [(None, None)]):
        if lv == 1:
            rises.append(t)
            if nxt[0] is not None and nxt[1] == 0:
                highs.append(nxt[0] - t)
    periods = [y - x for x, y in zip(rises, rises[1:])]
    print(f"канал {a.ch} → GPIO{a.gpio}: фронтов {len(edges)}, импульсов {len(highs)}")
    if len(highs) < 5 or len(periods) < 5:
        print("сигнала нет — проверить провод PWM канала → pin 13 и общую землю")
        return
    pw = statistics.median(highs) / 1000
    T = statistics.median(periods) / 1000
    osc = (pre + 1) * 4096 / (T * 1e-6)
    print(f"задано {a.us} мкс → на деле {pw:.0f} мкс; период {T:.0f} мкс ({1e6 / T:.2f} Гц, надо 50)")
    print(f"генератор PCA ≈ {osc / 1e6:.2f} МГц (по паспорту 25); импульсы в {pw / a.us:.3f} раза "
          f"{'короче' if pw < a.us else 'длиннее'} заданных")
    print(f"чтобы получить {a.us} мкс, задавать ≈ {a.us * a.us / pw:.0f} мкс (или вписать {osc:.0f}: 0x40 — PCA_SEED_OSC_HZ в hw.py и PCA_OSC_HZ в servo_test.py, 0x42 — PCA_ARM_OSC_HZ в hw.py)")


if __name__ == "__main__":
    main()
