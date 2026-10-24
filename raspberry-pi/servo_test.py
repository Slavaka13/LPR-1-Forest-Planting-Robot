"""Проверка серво посадочного модуля — по одному.

Импульс на GPIO через lgpio (sudo и правка config.txt не нужны) или на канал PCA9685 (I2C, адрес 0x40).
Движение плавное: от прошлого положения этого выхода (запоминается в ~/.servo_test.json) со скоростью --speed.
Самое первое положение выхода — рывком, поэтому первым всегда ставить середину 1500 при свободном механизме.
После --hold секунд импульсы снимаются (серво отпускается), если не задан --keep. Ctrl+C — сразу отпустить.

  python3 -u servo_test.py --gpio 12 --us 1500 --hold 3            # клюв (pin 32): середина
  python3 -u servo_test.py --gpio 12 --us 1700 --hold 3            # плавно до 1700 мкс
  python3 -u servo_test.py --gpio 13 --sweep 1300 1700             # барабан (pin 33): туда-обратно один раз
  python3 -u servo_test.py --pca 2 --us 1500                       # толкатель на PCA9685, канал 2
  python3 -u servo_test.py --gpio 13 --run 1600 --time 4           # серво 360° (барабан): крутить 4 с и остановить

Серво 360° (постоянного вращения, барабан): импульс = скорость, 1500 — стоп, > 1500 / < 1500 — в разные стороны.
Для неё только --run: импульс сразу, без плавного разгона и без запоминания положения.
Пределы по умолчанию 1000..2000 мкс; 500..2500 — только с --wide, когда механика проверена.
Питание серво — отдельный источник 6 В (не 5 В Pi!), минус общий с Pi (pin 34).
"""

import argparse
import json
import os
import time

STATE = os.path.expanduser("~/.servo_test.json")
PERIOD = 0.02  # 50 Гц

# Генератор PCA9685 — не ровно 25 МГц: у нашей платы (новая, 16.10.2026) замер pca_measure.py — 25.84 МГц
# (импульсы были на 3.4 % короче). Для другой платы — перемерить (PWM канала 4 → pin 13, pca_measure.py) и поправить.
PCA_OSC_HZ = 25_840_000
PCA_FREQ = 50
PCA_PRESCALE = round(PCA_OSC_HZ / (4096 * PCA_FREQ)) - 1          # 125 (по паспортным 25 МГц было бы 121)
PCA_TICK_US = (PCA_PRESCALE + 1) * 1e6 / PCA_OSC_HZ                # ≈ 4.88 мкс на шаг счётчика


class GpioOut:
    def __init__(self, pin):
        import lgpio
        self.lg, self.pin = lgpio, pin
        self.h = lgpio.gpiochip_open(0)
        lgpio.gpio_claim_output(self.h, pin)

    def set(self, us):
        self.lg.tx_servo(self.h, self.pin, int(us), 50)

    def off(self):
        self.lg.tx_servo(self.h, self.pin, 0)
        self.lg.gpio_free(self.h, self.pin)
        self.lg.gpiochip_close(self.h)


class PcaOut:
    def __init__(self, ch, addr=0x40, bus=1):
        try:
            from smbus2 import SMBus
        except ImportError:
            from smbus import SMBus
        self.b, self.ch, self.addr = SMBus(bus), ch, addr
        mode1 = self.b.read_byte_data(addr, 0x00)
        if self.b.read_byte_data(addr, 0xFE) != PCA_PRESCALE:   # 50 Гц по настоящей частоте генератора
            self.b.write_byte_data(addr, 0x00, (mode1 & 0x7F) | 0x10)  # sleep
            self.b.write_byte_data(addr, 0xFE, PCA_PRESCALE)
        self.b.write_byte_data(addr, 0x00, 0x20)          # проснуться, автоинкремент
        time.sleep(0.005)

    def _w(self, on, off):
        self.b.write_i2c_block_data(self.addr, 0x06 + 4 * self.ch, [on & 0xFF, on >> 8, off & 0xFF, off >> 8])

    def set(self, us):
        self._w(0, round(us / PCA_TICK_US))

    def off(self):
        self._w(0, 0x1000)                                # полное выключение канала
        self.b.close()


def load():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def main():
    ap = argparse.ArgumentParser(description="Проверка одного серво")
    out = ap.add_mutually_exclusive_group(required=True)
    out.add_argument("--gpio", type=int, help="номер GPIO (BCM): клюв 12, барабан 13, толкатель 6")
    out.add_argument("--pca", type=int, help="канал PCA9685 0..15: клюв 0, барабан 1, толкатель 2")
    mv = ap.add_mutually_exclusive_group(required=True)
    mv.add_argument("--us", type=int, help="поставить импульс, мкс")
    mv.add_argument("--sweep", type=int, nargs=2, metavar=("A", "B"), help="плавно A -> B -> A")
    mv.add_argument("--run", type=int, help="серво 360°: импульс (скорость) на --time секунд")
    ap.add_argument("--time", type=float, default=2, help="для --run: сколько секунд крутить")
    ap.add_argument("--speed", type=float, default=250, help="скорость, мкс/с (250 ≈ 25°/с)")
    ap.add_argument("--hold", type=float, default=2, help="держать в конце, с")
    ap.add_argument("--keep", action="store_true", help="не отпускать (держать до Ctrl+C)")
    ap.add_argument("--stay", action="store_true",
                    help="PCA9685: после движения импульс не снимать — серво держит положение (PCA выдаёт сама), без рывков потом")
    ap.add_argument("--wide", action="store_true", help="разрешить 500..2500 мкс")
    a = ap.parse_args()

    lo, hi = (500, 2500) if a.wide else (1000, 2000)
    if a.run is not None:
        if not lo <= a.run <= hi:
            raise SystemExit(f"{a.run} мкс вне пределов {lo}..{hi} (шире — только с --wide)")
        dev = GpioOut(a.gpio) if a.gpio is not None else PcaOut(a.pca)
        try:
            print(f"кручу {a.run} мкс {a.time} с")
            dev.set(a.run)
            time.sleep(a.time)
        except KeyboardInterrupt:
            pass
        finally:
            dev.off()
            print("стоп: импульсы сняты")
        return
    targets = [a.us] if a.us is not None else [a.sweep[1], a.sweep[0]]
    if a.sweep:
        targets.insert(0, a.sweep[0])
    for t in targets:
        if not lo <= t <= hi:
            raise SystemExit(f"{t} мкс вне пределов {lo}..{hi} (шире — только с --wide)")

    key = f"gpio{a.gpio}" if a.gpio is not None else f"pca{a.pca}"
    state = load()
    cur = state.get(key)
    dev = GpioOut(a.gpio) if a.gpio is not None else PcaOut(a.pca)
    stayed = False
    try:
        if cur is None:
            print(f"{key}: прошлое положение неизвестно — сразу {targets[0]} мкс (рывком)")
            cur = targets[0]
            dev.set(cur)
            time.sleep(0.5)
        for t in targets:
            step = a.speed * PERIOD * (1 if t > cur else -1)
            print(f"{key}: {cur:.0f} -> {t} мкс")
            while abs(t - cur) > abs(step):
                cur += step
                dev.set(cur)
                time.sleep(PERIOD)
            cur = t
            dev.set(cur)
            state[key] = cur
            with open(STATE, "w") as f:
                json.dump(state, f)
            time.sleep(0.3)
        print(f"{key}: стоит на {cur} мкс" + (" — держу, Ctrl+C отпустить" if a.keep else f", держу {a.hold} с"))
        if a.keep:
            while True:
                time.sleep(1)
        time.sleep(a.hold)
        if a.stay and a.pca is not None:
            dev.b.close()                                  # канал не выключаем — PCA держит импульс сама
            stayed = True
            print(f"{key}: оставил {cur} мкс — серво держит положение")
    except KeyboardInterrupt:
        pass
    finally:
        if not stayed:
            dev.off()
            print(f"{key}: импульсы сняты (серво отпущен)")


if __name__ == "__main__":
    main()
