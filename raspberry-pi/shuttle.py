"""Челнок бункера «семена 112»: серва на угол (MG90S 180°) на PCA9685, канал 2.

Положения (калибровка 17.10.2026, «посадка семян/СЕМЕНА 112.md», раздел 8):
  НАБОР 1000 мкс — отверстие верхней пластины под выходом бункера (подход справа, от «сброса»);
  СБРОС 1400 мкс — отверстие нижней пластины над каналом (подход слева, от «набора»).
  ДОМ (покой) = 1400 — решение владельца 17.10.2026.

Почему положение возвращается само: импульс выдаёт PCA9685 (питание 3.3 В от Pi), а не серва. Пропали 6 В и серву
сдвинули — когда 6 В вернутся, серва сама встанет туда, импульс чего держит PCA. Поэтому в покое канал НЕ выключаем.
PCA сбрасывается только при пропадании питания Pi — тогда home() при запуске rc_drive.py снова ставит 1400.

  python3 -u shuttle.py status      # что сейчас выдаёт канал 2
  python3 -u shuttle.py home        # 1400 и держать
  python3 -u shuttle.py fill        # набор (1000)
  python3 -u shuttle.py drop        # сброс (1400)
  python3 -u shuttle.py cycle 3     # 3 цикла: набор → сброс (пауза 1 с), в конце — дом
"""

import json
import os
import sys
import time

from servo_test import PCA_TICK_US, STATE, PcaOut

CH = 2
FILL_US = 1000        # набор
DROP_US = 1400        # сброс
HOME_US = 1400        # покой: держим здесь, сюда же — при каждом запуске
SPEED_US_S = 800      # 400 мкс хода (40°, 6.9 мм) за 0.5 с — проверено 5 циклами
SETTLE_S = 0.15       # дать серве доехать после плавного хода


class Shuttle:
    def __init__(self, ch=CH):
        self.ch = ch
        self.out = PcaOut(ch)         # настраивает PCA (50 Гц по настоящему генератору), регистры канала не трогает
        self.cur = self.current()

    def current(self):
        """Импульс, который сейчас выдаёт канал, мкс; None — канал выключен / после сброса PCA."""
        r = self.out.b.read_i2c_block_data(self.out.addr, 0x06 + 4 * self.ch, 4)
        on, off = r[0] | (r[1] << 8), r[2] | (r[3] << 8)
        if (on | off) & 0x1000:
            return None
        width = (off - on) & 0xFFF
        return width * PCA_TICK_US if width else None

    def move(self, us, speed=SPEED_US_S):
        if self.cur is None:          # положение неизвестно (канал был выключен) — сразу, серва доедет сама
            self.out.set(us)
            self.cur = us
            time.sleep(0.5)
        else:
            step = speed * 0.02 * (1 if us > self.cur else -1)
            while abs(us - self.cur) > abs(step):
                self.cur += step
                self.out.set(self.cur)
                time.sleep(0.02)
            self.cur = us
            self.out.set(us)
            time.sleep(SETTLE_S)
        self._save()

    def home(self):
        self.move(HOME_US)

    def fill(self):
        self.move(FILL_US)

    def drop(self):
        self.move(DROP_US)

    def _save(self):
        """Положение — и в файл servo_test.py, чтобы он потом не дёргал серву из старого положения."""
        try:
            with open(STATE) as f:
                st = json.load(f)
        except (OSError, ValueError):
            st = {}
        st[f"pca{self.ch}"] = round(self.cur)
        try:
            with open(STATE, "w") as f:
                json.dump(st, f)
        except OSError:
            pass

    def close(self):
        self.out.b.close()            # канал НЕ выключаем — серва держит положение


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    sh = Shuttle()
    try:
        if cmd == "status":
            c = sh.current()
            print(f"канал {CH}: " + (f"{c:.0f} мкс" if c else "выключен (серва не держит)"))
        elif cmd in ("home", "fill", "drop"):
            getattr(sh, cmd)()
            print(f"канал {CH}: {sh.cur:.0f} мкс — держит")
        elif cmd == "cycle":
            n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
            for i in range(n):
                sh.fill()
                time.sleep(1)
                sh.drop()
                time.sleep(1)
                print(f"цикл {i + 1}/{n}")
            sh.home()
            print(f"дом {HOME_US} мкс — держит")
        else:
            raise SystemExit("команды: status, home, fill, drop, cycle [N]")
    finally:
        sh.close()


if __name__ == "__main__":
    main()
