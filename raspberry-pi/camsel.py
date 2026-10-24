"""Выбор камеры FPV: переключатель W8010S, вход S ← PCA9685 №1 (0x40), канал 8 (в разъёме только S и −).

Камера выбирается длиной импульса на S. Замер на своей плате (22.10.2026, в очках; ходовая — IN1, посев — IN2,
манипулятор — IN3): **1000 мкс — манипулятор, 1500 — посев, 1800 и 2000 — ходовая**. До этого замера сигнал S до платы
не доходил (вход висел на 1.1–1.2 В) — плата стояла на IN1 при любом импульсе; после переподключения проводов и
перезагрузки Pi заработало.
  1 — ходовая  2000 мкс      2 — манипулятор  1000 мкс      3 — посев  1500 мкс
Режимы (rc_drive.py): SwD вниз — манипулятор → камера 2; иначе SwC вверх → 1, середина / вниз → 3; нет сигнала → 1.
Импульс держит PCA сама — пишется только при смене камеры.

Проверка (службу остановить: systemctl --user stop rc-drive; смотреть в очки):
  python3 -u camsel.py status               # какой импульс сейчас на кан. 8 и какой это камере по PULSES
  python3 -u camsel.py 1                    # камера 1 (2, 3)
  python3 -u camsel.py us 1250              # любой импульс, мкс
  python3 -u camsel.py scan                 # 900 → 2100 через 100 мкс, по 3 с: какая камера на каком импульсе
  python3 -u camsel.py scan 1100 1900 50 2  # свой диапазон, шаг, секунд на шаг
Если не переключается ни на каком импульсе — плата не понимает 3.3 В от PCA: нужен сдвиг уровня до 5 В (транзистор).
"""

import sys
import time

CH = 8
PULSES = {1: 2000, 2: 1000, 3: 1500}   # 22.10.2026 в очках: 2000 (и 1800) ходовая, 1500 посев, 1000 манипулятор
NAMES = {1: "ходовая", 2: "манипулятор", 3: "посев"}


class CamSelect:
    def __init__(self, pca, ch=CH, pulses=PULSES):
        self.pca, self.ch, self.pulses = pca, ch, dict(pulses)
        self.cam = None

    def set(self, cam):
        """Выбрать камеру 1/2/3; True — переключили (импульс записан), False — уже стояла."""
        if cam == self.cam:
            return False
        self.pca.set_us(self.ch, self.pulses[cam])
        self.cam = cam
        return True

    def status(self):
        return f"камера {self.cam} ({NAMES.get(self.cam, '?')})" if self.cam else "камера ?"


def main():
    from hw import Pca9685, open_bus
    a = sys.argv[1:]
    if not a:
        raise SystemExit(__doc__)
    pca = Pca9685(open_bus())
    if a[0] == "status":
        us = pca.get_us(CH)
        near = min(PULSES, key=lambda c: abs(PULSES[c] - us)) if us else None
        print(f"кан. {CH}: " + (f"{us:.0f} мкс → камера {near} ({NAMES[near]})" if us else "выключен (импульса нет)"))
    elif a[0] in ("1", "2", "3"):
        cs = CamSelect(pca)
        cs.set(int(a[0]))
        print(f"{cs.status()}: {PULSES[int(a[0])]} мкс на кан. {CH} — держит")
    elif a[0] == "us":
        pca.set_us(CH, int(a[1]))
        print(f"кан. {CH}: {int(a[1])} мкс — держит")
    elif a[0] == "scan":
        lo, hi, step, hold = (int(a[1]), int(a[2]), int(a[3]), float(a[4])) if len(a) >= 5 else (900, 2100, 100, 3.0)
        try:
            for us in range(lo, hi + 1, step):
                pca.set_us(CH, us)
                print(f"{us} мкс — какая камера в очках?", flush=True)
                time.sleep(hold)
        except KeyboardInterrupt:
            pass
        pca.set_us(CH, PULSES[1])
        print(f"вернул камеру 1 ({PULSES[1]} мкс)")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
