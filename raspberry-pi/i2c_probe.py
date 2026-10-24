#!/usr/bin/env python3
"""Проверка шины I2C на пинах 3 (GPIO2, SDA) и 5 (GPIO3, SCL) БЕЗ включения I2C в config.txt и без sudo.

Программная (bit-bang) шина через lgpio: линию «отпускаем» входом (её тянут вверх резисторы Pi 1.8 кОм
и платы) и прижимаем к земле выходом 0 — как настоящий открытый сток.
Только чтение: поиск адресов + чтение регистров MODE1/MODE2/PRE_SCALE у PCA9685 (ничего не записывает,
кроме указателя регистра). Серво не двигаются.

Если I2C-1 уже включён (/dev/i2c-1 есть) — скрипт не нужен (пины заняты драйвером):
    /usr/sbin/i2cdetect -y 1

Запуск на Pi:  python3 -u ~/robot/i2c_probe.py
"""
import os
import sys
import time

import lgpio

SDA, SCL = 2, 3
T = 0.00005  # полупериод ~50 мкс (реально медленнее из-за Python) — для проверки годится

PCA_DEFAULT = {"MODE1": 0x11, "MODE2": 0x04, "PRE_SCALE": 0x1E}


class BitBangI2C:
    def __init__(self):
        self.h = lgpio.gpiochip_open(0)
        for g in (SDA, SCL):
            lgpio.gpio_claim_input(self.h, g)

    def _rel(self, g):
        lgpio.gpio_claim_input(self.h, g)

    def _low(self, g):
        lgpio.gpio_claim_output(self.h, g, 0)

    def _rd(self, g):
        return lgpio.gpio_read(self.h, g)

    def _scl_hi(self):
        self._rel(SCL)
        t0 = time.monotonic()
        while not self._rd(SCL):  # растяжение такта или линия прижата
            if time.monotonic() - t0 > 0.02:
                raise IOError("SCL прижата к земле")
        time.sleep(T)

    def idle_levels(self):
        return self._rd(SDA), self._rd(SCL)

    def start(self):
        self._rel(SDA)
        self._scl_hi()
        self._low(SDA)
        time.sleep(T)
        self._low(SCL)
        time.sleep(T)

    def stop(self):
        self._low(SDA)
        time.sleep(T)
        self._scl_hi()
        self._rel(SDA)
        time.sleep(T)

    def _wbit(self, b):
        if b:
            self._rel(SDA)
        else:
            self._low(SDA)
        time.sleep(T)
        self._scl_hi()
        self._low(SCL)
        time.sleep(T)

    def _rbit(self):
        self._rel(SDA)
        time.sleep(T)
        self._scl_hi()
        b = self._rd(SDA)
        self._low(SCL)
        time.sleep(T)
        return b

    def wbyte(self, v):
        for i in range(8):
            self._wbit((v >> (7 - i)) & 1)
        return self._rbit() == 0  # True = ACK

    def rbyte(self, ack):
        v = 0
        for _ in range(8):
            v = (v << 1) | self._rbit()
        self._wbit(0 if ack else 1)
        return v

    def probe(self, addr):
        try:
            self.start()
            ok = self.wbyte(addr << 1)
        finally:
            self.stop()
        return ok

    def read_reg(self, addr, reg):
        try:
            self.start()
            if not self.wbyte(addr << 1) or not self.wbyte(reg):
                return None
            self.start()  # повторный START
            if not self.wbyte((addr << 1) | 1):
                return None
            return self.rbyte(ack=False)
        finally:
            self.stop()

    def close(self):
        for g in (SDA, SCL):
            try:
                self._rel(g)
                lgpio.gpio_free(self.h, g)
            except Exception:
                pass
        lgpio.gpiochip_close(self.h)


def main():
    if os.path.exists("/dev/i2c-1"):
        print("I2C-1 уже включён (/dev/i2c-1 есть) — проверяйте так:  /usr/sbin/i2cdetect -y 1")
        return 0
    bus = BitBangI2C()
    try:
        sda, scl = bus.idle_levels()
        print(f"Линии в покое: SDA (pin 3) = {'1' if sda else '0 (!)'}, SCL (pin 5) = {'1' if scl else '0 (!)'}")
        if not (sda and scl):
            print("Линия прижата к земле: перепутаны провода / замыкание на GND / SDA-SCL на другом пине.")
            return 1
        found = [a for a in range(0x08, 0x78) if bus.probe(a)]
        if not found:
            print("Ни одно устройство не ответило.")
            print("Проверить: VCC платы ← pin 1 (3.3 В), GND ← pin 9/14, SDA ← pin 3, SCL ← pin 5 (не перепутаны?),")
            print("пайку гребёнки; напряжение VCC–GND на самой плате ≈ 3.3 В.")
            return 1
        print("Ответили адреса: " + ", ".join(f"0x{a:02X}" for a in found))
        for a in found:
            if 0x40 <= a <= 0x7F and a != 0x70:
                regs = {n: bus.read_reg(a, r) for n, r in (("MODE1", 0x00), ("MODE2", 0x01), ("PRE_SCALE", 0xFE))}
                txt = ", ".join(f"{n}=0x{v:02X}" if v is not None else f"{n}=нет ответа" for n, v in regs.items())
                same = all(regs[n] == PCA_DEFAULT[n] for n in regs)
                print(f"  0x{a:02X}: {txt}" + ("  — PCA9685 после включения питания (заводские значения) ✓" if same else ""))
        if 0x40 in found:
            print("0x40 — PCA9685 посевного модуля (перемычки A0–A5 не запаяны).")
        if 0x42 in found:
            print("0x42 — PCA9685 с запаянной A1 (по плану — плата руки).")
        if 0x70 in found:
            print("0x70 — общий адрес «All Call» всех PCA9685, отвечает всегда, это норма.")
        return 0
    finally:
        bus.close()


if __name__ == "__main__":
    sys.exit(main())
