"""Поиск «стопа» серво 360° на PCA9685: толчки с шагом вверх и вниз от центра — с какого вал начинает крутиться.

  python3 -u servo_deadband.py --ch 0                 # 1520, 1540, 1560, 1580 | пауза | 1480, 1460, 1440, 1420
  python3 -u servo_deadband.py --ch 1 --step 10 --n 6 --on 2 --pause 1.5 --gap 4
  python3 -u servo_deadband.py --ch 0 --center 1510 --only up

Каждый толчок — --on с (не больше 3), между толчками — --pause с (канал выключен), между группами «вверх» и «вниз» —
--gap с. Стоп = середина между первыми импульсами, на которых вал пошёл в одну и в другую сторону.
Пределы 1000–2000 мкс. Ctrl+C — канал сразу выключается. Нужен servo_test.py рядом (класс PcaOut).
"""

import argparse
import time

from servo_test import PcaOut


def main():
    ap = argparse.ArgumentParser(description="Поиск стопа серво 360° на PCA9685")
    ap.add_argument("--ch", type=int, required=True, help="канал PCA9685")
    ap.add_argument("--center", type=int, default=1500, help="от какого импульса шагать, мкс")
    ap.add_argument("--step", type=int, default=20, help="шаг, мкс")
    ap.add_argument("--n", type=int, default=4, help="толчков в группе")
    ap.add_argument("--on", type=float, default=2.0, help="длительность толчка, с (≤ 3)")
    ap.add_argument("--pause", type=float, default=1.5, help="пауза между толчками, с")
    ap.add_argument("--gap", type=float, default=4.0, help="пауза между группами «вверх» и «вниз», с")
    ap.add_argument("--only", choices=["up", "down"], help="только одна группа")
    a = ap.parse_args()

    on = min(a.on, 3.0)
    groups = []
    if a.only != "down":
        groups.append(("вверх", [a.center + a.step * k for k in range(1, a.n + 1)]))
    if a.only != "up":
        groups.append(("вниз", [a.center - a.step * k for k in range(1, a.n + 1)]))
    if not all(1000 <= us <= 2000 for _, lst in groups for us in lst):
        raise SystemExit("импульсы вне 1000..2000 мкс")

    out = PcaOut(a.ch)
    t0 = time.monotonic()
    try:
        for gi, (name, lst) in enumerate(groups):
            if gi:
                print(f"{time.monotonic() - t0:5.1f} с  пауза {a.gap:.0f} с между группами")
                time.sleep(a.gap)
            for i, us in enumerate(lst, 1):
                print(f"{time.monotonic() - t0:5.1f} с  канал {a.ch}, группа «{name}», толчок {i}/{len(lst)}: {us} мкс")
                out.set(us)
                time.sleep(on)
                out._w(0, 0x1000)                           # канал выключен на паузу
                time.sleep(a.pause)
    except KeyboardInterrupt:
        print("Ctrl+C")
    finally:
        out.off()
    print(f"{time.monotonic() - t0:5.1f} с  готово, канал {a.ch} выключен")


if __name__ == "__main__":
    main()
