"""Общие драйверы I2C для программы робота: PCA9685 (серво) и INA219 (ток серво клюва).

Шина передаётся снаружи — так модули работают на Pi (open_bus()), а на ПК проверяются с подставной шиной
(test_modules.py). Одна шина на всю программу, с замком: модуль посева работает в своём потоке, а smbus2 ставит
адрес и читает/пишет двумя отдельными вызовами — без замка два потока перепутали бы адреса.

Адреса на шине (i2cdetect -y 1 → 40, 41, 42, 44, 70):
  0x40 — PCA9685 №1, посев: кан. 0/1 клюв, 2 челнок, 8 — W8010S (S);   0x42 — PCA9685 №2, рука (перемычка A1);
  0x41 — INA219 левая (A0), 0x44 — INA219 правая (A1);                    0x70 — All-Call обеих PCA (не трогать).
"""

import threading
import time

PCA_SEED_ADDR = 0x40
PCA_SEED_OSC_HZ = 25_840_000    # замер pca_measure.py 16.10.2026 (импульсы точны до ±1 мкс)
PCA_ARM_ADDR = 0x42
PCA_ARM_OSC_HZ = 25_185_000     # замер pca_measure.py --addr 0x42 --ch 3, 22.10.2026: 25.18–25.19 МГц
INA_LEFT_ADDR = 0x41
INA_RIGHT_ADDR = 0x44
INA_SHUNT_OHM = 0.1             # родные R100 на модулях (до 3.2 А; одно MG996R в упоре ~2.5 А)


class LockedBus:
    """SMBus с замком: каждый вызов целиком, без вклинивания другого потока."""

    def __init__(self, bus):
        self._b, self._lock = bus, threading.Lock()

    def __getattr__(self, name):
        f = getattr(self._b, name)
        if not callable(f):
            return f

        def call(*a, **kw):
            with self._lock:
                return f(*a, **kw)
        return call


def open_bus(n=1):
    try:
        from smbus2 import SMBus
    except ImportError:
        from smbus import SMBus
    return LockedBus(SMBus(n))


class Pca9685:
    """Одна плата PCA9685, 50 Гц по настоящей частоте генератора. Каналы не трогает, пока их не попросят."""

    MODE1, PRESCALE, LED0 = 0x00, 0xFE, 0x06

    def __init__(self, bus, addr=PCA_SEED_ADDR, osc_hz=PCA_SEED_OSC_HZ, freq=50):
        self.b, self.addr = bus, addr
        self.prescale = round(osc_hz / (4096 * freq)) - 1
        self.tick_us = (self.prescale + 1) * 1e6 / osc_hz
        mode1 = self.b.read_byte_data(addr, self.MODE1)
        if self.b.read_byte_data(addr, self.PRESCALE) != self.prescale:
            # делитель меняется только во сне; это остановит выходы на миг — бывает лишь после включения питания
            self.b.write_byte_data(addr, self.MODE1, (mode1 & 0x7F) | 0x10)
            self.b.write_byte_data(addr, self.PRESCALE, self.prescale)
        self.b.write_byte_data(addr, self.MODE1, 0x20)        # проснуться, автоинкремент
        time.sleep(0.005)

    def set_us(self, ch, us):
        off = max(1, min(4095, round(us / self.tick_us)))
        self.b.write_i2c_block_data(self.addr, self.LED0 + 4 * ch, [0, 0, off & 0xFF, off >> 8])

    def off(self, ch):
        """Канал выключен: импульсов нет, серво отпущено."""
        self.b.write_i2c_block_data(self.addr, self.LED0 + 4 * ch, [0, 0, 0, 0x10])

    def get_us(self, ch):
        """Импульс, который канал выдаёт сейчас, мкс; None — канал выключен (или PCA после включения питания)."""
        r = self.b.read_i2c_block_data(self.addr, self.LED0 + 4 * ch, 4)
        on, off = r[0] | (r[1] << 8), r[2] | (r[3] << 8)
        if (on | off) & 0x1000:
            return None
        width = (off - on) & 0xFFF
        return width * self.tick_us if width else None


SADC = {1: 0b1000, 2: 0b1001, 4: 0b1010, 8: 0b1011, 16: 0b1100, 32: 0b1101, 64: 0b1110, 128: 0b1111}


class Ina219:
    """INA219: шина 16 В, PGA /8 (±320 мВ на шунте → 3.2 А при 0.1 Ом), avg замеров шунта на одно значение.
    avg = 1: у наших модулей (замер 22.10.2026, ina_raw.py --modes) аппаратное усреднение 2…128 врёт — шунт показывает
    до ±327 мВ кратно 20.48 мВ при нуле тока (клоны). Один замер (532 мкс) — верно: 0 ± 0.04 мВ. Ток серво идёт пачками
    50 Гц — сглаживать программно (seeder.py: среднее по окну AVG_N замеров)."""

    def __init__(self, bus, addr, shunt=INA_SHUNT_OHM, avg=1):
        self.b, self.addr, self.shunt = bus, addr, shunt
        cfg = (0 << 13) | (3 << 11) | (0b0011 << 7) | (SADC[avg] << 3) | 0b111
        self.b.write_i2c_block_data(addr, 0x00, [cfg >> 8, cfg & 0xFF])
        time.sleep(0.04)

    def _reg(self, r):
        hi, lo = self.b.read_i2c_block_data(self.addr, r, 2)
        return (hi << 8) | lo

    def current(self):
        raw = self._reg(0x01)
        if raw & 0x8000:
            raw -= 0x10000
        return raw * 10e-6 / self.shunt          # А (шаг 10 мкВ на шунте)

    def voltage(self):
        return (self._reg(0x02) >> 3) * 0.004    # В на VIN− (на «+» серво)
