"""Проверка INA219 (одна, в общем «+» 6 В перед PCA9685) — стенд «ПРОВЕРКА INA» (модуль «семена 112»).

Читает ток и напряжение INA219 (по умолчанию 0x41, шунт 0.025 Ом), пишет в CSV ~/robot/ina_logs/ и печатает сводку.
Может крутить серво 360° на PCA9685 (одно или пару клюва зеркально) и само остановить их при упоре (рост тока).

  python3 -u ina_test.py --read --time 3                                 # ток покоя и напряжение шины 6 В
  python3 -u ina_test.py --pca 0 --us 1560 --time 1                      # одно серво (канал 0) 1 с, запись тока
  python3 -u ina_test.py --pair 1500 1497 --d 60 --time 0.5              # клюв: канал 0 = N0+d, канал 1 = N1-d
  python3 -u ina_test.py --pair 1500 1497 --d -40 --time 3 --stop 1.8    # до упора: стоп, когда ток > 1.8 × ток хода
  python3 -u ina_test.py --pair 1500 1497 --d 60 --time 2 --stop 1.5 --after 0.6 --label grunt   # в грунт
  python3 -u ina_test.py --pair 1500 1497 --d 40 --time 0.3 --no-ina     # без INA219: только толчок по времени
  python3 -u ina_test.py --pair 1480 1480 --d -280 --same --time 3 --no-ina --brake 0.5   # оба 1200, стоп командой

Упор — как будет в программе: первые 0.15 с ток не смотреть (пуск), 0.15–0.35 с — база (ток хода), дальше упор =
ток > k × база дольше 0.05 с (и не раньше --after с). Время — не больше 5 с. Ctrl+C — серво сразу отпускаются.
INA219 — по 1 замеру: у наших модулей аппаратное усреднение 2…128 врёт (22.10.2026, ina_raw.py --modes; клоны).
Нужно: I2C включён (i2cdetect -y 1 → 40, 41, 70), servo_test.py рядом (класс PcaOut), smbus2.
"""

import argparse
import os
import statistics
import time

LOG_DIR = os.path.expanduser("~/robot/ina_logs")
SADC = {1: 0b1000, 2: 0b1001, 4: 0b1010, 8: 0b1011, 16: 0b1100, 32: 0b1101, 64: 0b1110, 128: 0b1111}


class Ina219:
    """INA219: шина 16 В, PGA /8 (±320 мВ на шунте → 12.8 А при 0.025 Ом), шунт с усреднением avg замеров."""

    def __init__(self, addr=0x41, shunt=0.025, avg=1, bus=1):
        try:
            from smbus2 import SMBus
        except ImportError:
            from smbus import SMBus
        self.b, self.addr, self.shunt = SMBus(bus), addr, shunt
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
        return (self._reg(0x02) >> 3) * 0.004    # В на VIN− (шина серво)

    def close(self):
        self.b.close()


class Stall:
    """Упор по току: пуск не смотреть, потом база (ток хода), потом ток > k × база дольше hold."""

    def __init__(self, k, blank=0.15, base_end=0.35, hold=0.05, after=0.0, min_base=0.03):
        self.k, self.blank, self.base_end, self.hold, self.after, self.min_base = k, blank, base_end, hold, after, min_base
        self.samples, self.base, self.over_since = [], None, None

    def update(self, t, i):
        if t < self.blank:
            return False
        if t < self.base_end:
            self.samples.append(i)
            return False
        if self.base is None:
            self.base = max(statistics.median(self.samples) if self.samples else 0.0, self.min_base)
        if t >= self.after and i > self.k * self.base:
            if self.over_since is None:
                self.over_since = t
            return t - self.over_since >= self.hold
        self.over_since = None
        return False


def main():
    ap = argparse.ArgumentParser(description="Проверка INA219 и упоров по току")
    mv = ap.add_mutually_exclusive_group(required=True)
    mv.add_argument("--read", action="store_true", help="только читать ток и напряжение")
    mv.add_argument("--pca", type=int, help="одно серво 360°: канал PCA9685 (0, 1 — клюв, 2 — челнок)")
    mv.add_argument("--pair", type=int, nargs=2, metavar=("N0", "N1"), help="клюв: «стоп» левого (канал 0) и правого (канал 1)")
    ap.add_argument("--us", type=int, help="для --pca: импульс, мкс")
    ap.add_argument("--d", type=int, default=0, help="для --pair: отклонение от «стопа», мкс (канал 0 = N0+d, канал 1 = N1-d)")
    ap.add_argument("--time", type=float, default=1.0, help="сколько секунд (не больше 5)")
    ap.add_argument("--stop", type=float, help="остановить при упоре: ток > STOP × ток хода")
    ap.add_argument("--after", type=float, default=0.0, help="упор признавать не раньше, с (для грунта)")
    ap.add_argument("--addr", type=lambda s: int(s, 0), default=0x41, help="адрес INA219 (0x41)")
    ap.add_argument("--shunt", type=float, default=0.025, help="шунт, Ом (R025 = 0.025, родной R100 = 0.1)")
    ap.add_argument("--label", default="", help="подпись в имени файла журнала")
    ap.add_argument("--no-ina", action="store_true", help="без INA219: только крутить серво заданное время (ток не мерить)")
    ap.add_argument("--same", action="store_true", help="для --pair: оба серво N+d (без зеркала)")
    ap.add_argument("--brake", type=float, default=0.3,
                    help="в конце «стоп» (--pair: N0/N1, --pca: --neutral) столько секунд, потом выключить (без сигнала серво докручивает)")
    ap.add_argument("--neutral", type=int, default=1480, help="для --pca: импульс «стоп», мкс (клюв: 1480)")
    a = ap.parse_args()
    if a.no_ina and (a.read or a.stop):
        raise SystemExit("--no-ina: без INA219 нельзя --read и --stop (упор не увидеть) — только время")

    t_max = min(a.time, 5.0)
    pulses = []
    if a.pca is not None:
        if a.us is None or not 1000 <= a.us <= 2000:
            raise SystemExit("--us 1000..2000 обязателен с --pca")
        pulses = [(a.pca, a.us)]
    elif a.pair:
        pulses = [(0, a.pair[0] + a.d), (1, a.pair[1] + a.d if a.same else a.pair[1] - a.d)]
        if not all(1000 <= us <= 2000 for _, us in pulses):
            raise SystemExit("импульсы вне 1000..2000 мкс")

    ina = None if a.no_ina else Ina219(a.addr, a.shunt)
    outs = []
    if pulses:
        from servo_test import PcaOut
        outs = [(PcaOut(ch), ch, us) for ch, us in pulses]
    os.makedirs(LOG_DIR, exist_ok=True)
    name = time.strftime("ina_%Y%m%d_%H%M%S") + (f"_{a.label}" if a.label else "") + ".csv"
    path = os.path.join(LOG_DIR, name)
    det = Stall(a.stop, after=a.after) if a.stop else None
    rows, reason = [], f"время {t_max:.2f} с"
    try:
        for dev, ch, us in outs:
            dev.set(us)
        t0 = time.monotonic()
        while True:
            t = time.monotonic() - t0
            if t >= t_max:
                break
            if ina is None:
                time.sleep(0.01)
                continue
            i, u = ina.current(), ina.voltage()
            rows.append((t, i, u))
            if det and det.update(t, i):
                reason = f"упор по току через {t:.2f} с"
                break
            time.sleep(0.01)
    except KeyboardInterrupt:
        reason = "Ctrl+C"
    finally:
        if outs and a.brake > 0:
            stops = a.pair if a.pair else [a.neutral]
            for (dev, _, _), n in zip(outs, stops):      # «стоп» командой: двигатель встаёт сразу
                dev.set(n)
            time.sleep(min(a.brake, 2.0))
        for dev, _, _ in outs:
            dev.off()
        if ina is not None:
            ina.close()
    if ina is None:
        print(f"стоп: {reason}; импульсы: " + ", ".join(f"канал {ch} = {us} мкс" for _, ch, us in outs) + "; ток не мерился (--no-ina)")
        return
    with open(path, "w") as f:
        f.write("t_s;I_A;U_V\n")
        for t, i, u in rows:
            f.write(f"{t:.3f};{i:.4f};{u:.3f}\n")

    cur = [r[1] for r in rows]
    move = [r[1] for r in rows if 0.15 <= r[0] < 0.35]
    base = statistics.median(move) if move else None
    print(f"стоп: {reason}; замеров {len(rows)}; журнал {path}")
    if cur:
        print(f"ток: средний {statistics.mean(cur):.3f} А, макс {max(cur):.3f} А"
              + (f", ход (0.15–0.35 с) {base:.3f} А, макс/ход {max(cur) / base:.1f}" if base and base > 0.01 else ""))
        print(f"напряжение шины: мин {min(r[2] for r in rows):.2f} В, макс {max(r[2] for r in rows):.2f} В")


if __name__ == "__main__":
    main()
