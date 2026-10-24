"""Рука «манипулятор 5» на PCA9685 №2 (0x42): поворот, плечо, предплечье (MG996R 180°), захват (MG90S).

Управление из rc_drive.py (SwD вниз — режим «манипулятор», платформа стоит):
  правый стик ←→ — поворот основания, правый ↑↓ — плечо, левый ←→ — предплечье: стик = СКОРОСТЬ сустава
  (отпустил — сустав стоит, серво держит); левый стик ↑↓ (без пружины) — захват: вниз открыт, вверх закрыт
  (захват плавно идёт за стиком); крутилка VrB — скорость руки (30…100 %).
Рука двигается только при SwA вниз (ARM) и после того, как в режиме «манипулятор» стики побывали в центре.
В режиме «ход» рука держит последнее положение (PCA выдаёт импульсы сама).

Поворот основания — MG996R 180° (с 23.10.2026; серво 360° сняли: скорость плавала 90…140°/с, угол по времени не
держался, а провода дают только ≈ ±90° от полоски). Теперь угол = импульс, как у плеча; пределы — в JOINTS.
Ставить так: серво подключить, канал поворота держит 1500 → серво встаёт в середину хода → основание (качалку)
надеть так, чтобы рука смотрела на полоску. Тогда 1500 = полоска, а пределы — поровну в обе стороны.

Пределы откалиброваны 23.10.2026 (JOINTS, GRIP). Перекалибровка (после переборки суставов) (ARM не нужен; рука собрана, всё убрано из-под неё; службу можно не останавливать, пока SwD вверху):
  python3 -u arm.py status                 # что выдают каналы руки
  python3 -u arm.py set поворот 1600       # плавно (200 мкс/с) в пределах JOINTS; имя или номер канала
  python3 -u arm.py set захват 1300 --speed=50   # медленнее (мкс/с)
  python3 -u arm.py set плечо 1950 --wide  # за пределами (500…2500) — найти, где звено упирается / параллелограмм
                                           #   складывается / провода поворота натягиваются
  python3 -u arm.py park                   # все суставы в «старт», захват открыт
  python3 -u arm.py off                    # выключить каналы (рука обвиснет — держать рукой!)
  python3 -u arm.py off захват             # выключить один канал (имя или номер)
Найденные границы (с запасом 50–100 мкс до упора) вписать в JOINTS (мин / макс) и направление стиков — в rc_drive.py
(ARM_AXES, инверсия), если сустав едет не в ту сторону.
"""

import signal
import sys
import time

# канал PCA №2, имя, мин, старт, макс (мкс), скорость при стике до упора (мкс/с)
JOINTS = [
    # MG996R 180° (23.10.2026): больший импульс — вправо, ≈ 7–8° на 100 мкс; 500 и 2500 мкс — провода свободны, серво
    # тихая → пределы 550…2450 (запас 50); «старт» 1150 — рука на полоску (подобрал владелец; люфт основания ≈ 5°)
    (0, "поворот", 550, 1150, 2450, 300),
    (1, "плечо", 1580, 1600, 2400, 200),       # «вверх» — больший импульс; 1500 — в нижнем упоре, 1600 — горизонтально;
                                               #   2000 — ≈ +20–25°; 2400 — самый верх (владелец 23.10.2026)
    (2, "предплечье", 600, 1500, 2100, 200),   # «вверх» — больший импульс; 1500 — смотрит вниз; 2100 — самый верх;
                                               #   низ: 1200 («самый низ» при плече на 2000), с пульта вниз мало →
                                               #   1100 → 900 → 500 → 600 (подобрал владелец с пульта)
]
# захват (MG90S): канал, открыт, закрыт (мкс), скорость (мкс/с). 23.10.2026 (владелец, губки переставлены): 900 — открыт,
# 1900 — закрыт, туда-обратно по 200 мкс/с работает. 2500 — губки сомкнуты, но после быстрого хода (500 мкс/с) с 1300
# губки не пошли, серво перегрелась → не быстрее 200 и не дальше 1900
GRIP = (4, 900, 1900, 200)
# кан. 3 — резерв (4-я ось со стойкой не нужна)
CLI_SPEED = 200              # мкс/с для arm.py set / park
WIDE = (500, 2500)           # set --wide: крайние импульсы серво (MG996R 180° поворота — ход до ≈ 500…2500)


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


class Arm:
    def __init__(self, pca, joints=JOINTS, grip=GRIP):
        """grip=None — захват не трогать (канал не включается и не двигается; пока не откалиброван)."""
        self.pca = pca
        self.joints = {name: (ch, lo, start, hi, speed) for ch, name, lo, start, hi, speed in joints}
        self.has_grip = grip is not None
        if self.has_grip:
            self.grip_ch, self.grip_open, self.grip_close, self.grip_speed = grip
        self.pos = {}              # имя -> текущий импульс, мкс
        self.written = {}          # имя -> последний записанный (пишем только при сдвиге ≥ 1 мкс)
        self.active = False

    def activate(self):
        """Принять управление: суставы, у которых PCA уже держит импульс, — с того же места (без рывка);
        выключенные (после включения питания) — сразу в «старт» (рывком!). -> список «прыгнувших»."""
        jumped = []
        items = [(n, ch, lo, st, hi) for n, (ch, lo, st, hi, _) in self.joints.items()]
        if self.has_grip:
            items.append(("захват", self.grip_ch, min(self.grip_open, self.grip_close),
                          self.grip_open, max(self.grip_open, self.grip_close)))
        for name, ch, lo, start, hi in items:
            cur = self.pca.get_us(ch)
            if cur is None or not lo - 60 <= cur <= hi + 60:
                cur = start
                jumped.append(name)
            cur = _clamp(round(cur), lo, hi)
            self.pca.set_us(ch, cur)
            self.pos[name] = self.written[name] = cur
        self.active = True
        return jumped

    def update(self, dt, rates, grip=None, k=1.0):
        """rates: {имя: -1..1} — скорость суставов (доля от предельной); grip: 0 (открыт) … 1 (закрыт) или None;
        k: общий множитель скорости (крутилка). Двигает только пока активна."""
        if not self.active:
            self.activate()
        for name, r in rates.items():
            if not r or name not in self.joints:
                continue
            ch, lo, _, hi, speed = self.joints[name]
            self.pos[name] = _clamp(self.pos[name] + r * speed * k * dt, lo, hi)
            self._write(name, ch)
        if grip is not None and self.has_grip:
            target = self.grip_open + _clamp(grip, 0.0, 1.0) * (self.grip_close - self.grip_open)
            step = self.grip_speed * dt
            p = self.pos["захват"]
            self.pos["захват"] = target if abs(target - p) <= step else p + step * (1 if target > p else -1)
            self._write("захват", self.grip_ch)

    def _write(self, name, ch):
        if abs(self.pos[name] - self.written[name]) >= 1:
            self.pca.set_us(ch, self.pos[name])
            self.written[name] = self.pos[name]

    def status(self):
        if not self.active:
            return "рука: не активна"
        names = list(self.joints) + (["захват"] if self.has_grip else [])
        return "рука: " + " ".join(f"{n} {self.pos[n]:.0f}" for n in names)


def _smooth(pca, ch, target, speed=CLI_SPEED):
    cur = pca.get_us(ch)
    if cur is None:
        print(f"  кан. {ch}: был выключен — сразу {target} мкс (рывком)")
        pca.set_us(ch, target)
        time.sleep(0.5)
        return
    step = speed * 0.02 * (1 if target > cur else -1)
    while abs(target - cur) > abs(step):
        cur += step
        pca.set_us(ch, cur)
        time.sleep(0.02)
    pca.set_us(ch, target)


def _interrupt(sig, frame):
    raise KeyboardInterrupt      # обрыв ssh / kill — как Ctrl+C: сустав остаётся там, куда успел дойти


def main():
    from hw import PCA_ARM_ADDR, PCA_ARM_OSC_HZ, Pca9685, open_bus
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    wide = "--wide" in sys.argv
    speed = next((float(x.split("=", 1)[1]) for x in sys.argv if x.startswith("--speed=")), CLI_SPEED)   # мкс/с
    if not a:
        raise SystemExit(__doc__)
    signal.signal(signal.SIGHUP, _interrupt)
    signal.signal(signal.SIGTERM, _interrupt)
    pca = Pca9685(open_bus(), PCA_ARM_ADDR, PCA_ARM_OSC_HZ)
    by_name = {name: (ch, lo, st, hi) for ch, name, lo, st, hi, _ in JOINTS}
    by_name["захват"] = (GRIP[0], min(GRIP[1], GRIP[2]), GRIP[1], max(GRIP[1], GRIP[2]))
    try:
        if a[0] == "status":
            for name, (ch, lo, st, hi) in by_name.items():
                cur = pca.get_us(ch)
                print(f"кан. {ch} {name:11} " + (f"{cur:6.0f} мкс" if cur else "выключен") + f"   пределы {lo}…{hi}, старт {st}")
        elif a[0] == "set":
            key, us = a[1], int(a[2])
            name = next((n for n, v in by_name.items() if n == key or str(v[0]) == key), None)
            if name is None:
                raise SystemExit(f"нет сустава «{key}»: " + ", ".join(by_name))
            ch, lo, _, hi = by_name[name]
            lo, hi = WIDE if wide else (lo, hi)
            if not lo <= us <= hi:
                raise SystemExit(f"{us} мкс вне пределов {lo}…{hi}" + ("" if wide else " (шире — с --wide, осторожно)"))
            _smooth(pca, ch, us, speed)
            print(f"{name}: {us} мкс — держит")
        elif a[0] == "park":
            for name, (ch, lo, st, hi) in by_name.items():
                _smooth(pca, ch, st)
                print(f"{name}: {st} мкс")
        elif a[0] == "off":
            names = [n for n, v in by_name.items() if len(a) < 2 or a[1] in (n, str(v[0]))]
            if not names:
                raise SystemExit(f"нет сустава «{a[1]}»: " + ", ".join(by_name))
            for name in names:
                pca.off(by_name[name][0])
            print(("каналы руки выключены" if len(a) < 2 else f"{names[0]}: канал выключен") + " — серво отпущены")
        else:
            raise SystemExit(__doc__)
    except KeyboardInterrupt:
        print("прервано — каналы держат последнее положение")


if __name__ == "__main__":
    main()
