"""Проверка программы без железа (на ПК и на Pi): подставные PCA9685 / INA219 на подставной шине I2C, модель клюва
(рейки, верхний упор, грунт, клин), подставные пульт, плата гироскутера, рука и камеры.

  python3 test_modules.py        # на Pi: перед перезапуском службы после заливки; на ПК — Python 3.10+
Печатает каждую проверку; в конце «ВСЁ ВЕРНО» или список ошибок (код выхода 1).
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import arm as arm_mod          # noqa: E402
import camsel                  # noqa: E402
import hw                      # noqa: E402
import rc_drive                # noqa: E402
import seed_cam                # noqa: E402
import seeder as sm            # noqa: E402

hw.time.sleep = lambda s: None  # задержки в драйверах на ПК не нужны


# ---------------- подставное железо ----------------
class FakePca:
    def __init__(self):
        self.r = bytearray(256)
        self.r[0x00], self.r[0xFE] = 0x11, 0x1E          # как после включения питания
        self.writes = []

    def read_byte_data(self, reg):
        return self.r[reg]

    def write_byte_data(self, reg, v):
        self.r[reg] = v

    def write_i2c_block_data(self, reg, data):
        self.r[reg:reg + len(data)] = bytes(data)
        if reg >= 6:
            self.writes.append(((reg - 6) // 4, list(data)))

    def read_i2c_block_data(self, reg, n):
        return list(self.r[reg:reg + n])

    def us(self, ch, tick):
        b = self.r[6 + 4 * ch: 10 + 4 * ch]
        off = b[2] | (b[3] << 8)
        return None if off & 0x1000 or off == 0 else off * tick


class FakeIna:
    def __init__(self, amps_fn, shunt=hw.INA_SHUNT_OHM):
        self.amps_fn, self.shunt, self.cfg = amps_fn, shunt, None
        self.volts = 6.0

    def write_i2c_block_data(self, reg, data):
        self.cfg = data

    def read_i2c_block_data(self, reg, n):
        if reg == 0x01:
            raw = int(round(self.amps_fn() * self.shunt / 10e-6)) & 0xFFFF
        else:
            raw = int(self.volts / 0.004) << 3
        return [raw >> 8, raw & 0xFF]


class FakeBus:
    def __init__(self, devices):
        self.dev = devices

    def _d(self, addr):
        if addr not in self.dev:
            raise OSError(121, "Remote I/O error")
        return self.dev[addr]

    def read_byte_data(self, a, reg):
        return self._d(a).read_byte_data(reg)

    def write_byte_data(self, a, reg, v):
        self._d(a).write_byte_data(reg, v)

    def write_i2c_block_data(self, a, reg, data):
        self._d(a).write_i2c_block_data(reg, data)

    def read_i2c_block_data(self, a, reg, n):
        return self._d(a).read_i2c_block_data(reg, n)


class Beak:
    """Модель клюва: pos — мм ниже верхнего упора; скорость по импульсу кан. 0 (мёртвая зона ±200 мкс);
    ground — глубина касания упора глубины (мм от верха), None — воздух; jam_at — препятствие на подъёме;
    broken — зубья срезаны: не двигается и тока нет."""

    def __init__(self, fake_pca, tick, pos=30.0, ground=None, jam_at=None, broken=False, soft=False):
        self.p, self.tick, self.pos = fake_pca, tick, pos
        self.soft = soft           # рыхлый грунт: после касания клюв тонет дальше, ток растёт плавно (+4 % на мм)
        self.max_pos = pos
        self.ground, self.jam_at, self.broken = ground, jam_at, broken
        self.v, self.stalled = 0.0, False
        self.skip = False          # у верхнего упора шестерни проскакивают по рейке: ток как на ходу, клюв стоит
        self.skip_spikes = False   # … и на каждом зубе всплеск тока (как на железе 22.10.2026: ~×1.9 на ~30 мс)
        self.skip_time = 0.0       # сколько секунд проскакивали
        self.skip_mm = 0.0         # сколько мм рейки «прокрутили» (шаг зуба m1 — 3.14 мм)

    def step(self, dt):
        u0, u1 = self.p.us(sm.CH_L, self.tick), self.p.us(sm.CH_R, self.tick)
        self.v, self.stalled = 0.0, False
        if self.broken or u0 is None or u1 is None:
            return
        dl, dr = (u0 - sm.N_L) * sm.DOWN, (sm.N_R - u1) * sm.DOWN
        assert abs(dr - dl) < 6 + sm.TRIM_CCW_US, "правое серво не зеркально левому"
        d = (dl + dr) / 2                                  # добавка TRIM у одной стороны — не прибавка скорости
        d -= sm.TRIM_CCW_US / 2 * (1 if d > 0 else -1)
        if abs(d) < 200:
            return
        self.v = 0.585 * (abs(d) - 176) if d > 0 else -0.64 * (abs(d) - 176)   # 22.10.2026, d 270: вниз 55, вверх 60 мм/с
        new = self.pos + self.v * dt
        if new <= 0:
            new, self.stalled = 0.0, self.v < 0
            if self.stalled and self.skip:
                self.stalled = False
                self.skip_time += dt
                self.skip_mm += abs(self.v) * dt
        if self.jam_at is not None and self.v < 0 and self.pos >= self.jam_at > new:
            new, self.stalled = self.jam_at, True
        if self.ground is not None and self.v > 0 and new >= self.ground:
            if self.soft:
                new = min(self.pos + 0.7 * self.v * dt, self.ground + 40)
            else:
                new, self.stalled = self.ground, True
        if new >= 72:
            new, self.stalled = 72.0, self.v > 0
        self.pos = new
        self.max_pos = max(self.max_pos, new)

    def amps(self):
        if self.broken:
            return 0.02
        if self.soft and self.v > 0 and self.ground is not None and self.pos > self.ground:
            return 0.4 * (1 + 0.04 * (self.pos - self.ground))
        if self.skip_spikes and self.skip_mm and self.v < 0 and self.pos == 0 and self.skip_mm % 3.14 > 1.9:
            return 0.75
        return 2.2 if self.stalled else 0.4 if self.v else 0.02


class FakeClock:
    def __init__(self, beak=None):
        self.t, self.beak, self.hooks = 0.0, beak, []

    def now(self):
        return self.t

    def sleep(self, s):
        end = self.t + s
        while self.t < end - 1e-9:
            dt = min(0.005, end - self.t)
            self.t += dt
            if self.beak:
                self.beak.step(dt)
            for h in list(self.hooks):
                if h[0] <= self.t:
                    self.hooks.remove(h)
                    h[1]()


class FakeGps:
    def __init__(self):
        self.marks = []

    def mark(self, module="посев", note=""):
        self.marks.append((module, note))


def make_seeder(ground=None, jam_at=None, broken=False, pos=30.0, ina=True, soft=False, pos_file=None):
    fp = FakePca()
    devs = {0x40: fp}
    pca = hw.Pca9685(FakeBus(devs))
    beak = Beak(fp, pca.tick_us, pos, ground, jam_at, broken, soft)
    if ina:
        devs[0x41] = FakeIna(beak.amps)
        devs[0x44] = FakeIna(beak.amps)
    inas = sm.open_inas(FakeBus(devs)) if ina else None
    clock = FakeClock(beak)
    gps = FakeGps()
    log = []
    sd = sm.Seeder(pca, inas, gps, None, say=log.append, clock=clock, pos_file=pos_file, settings_file=None)
    return sd, beak, fp, pca, gps, log, clock


# ---------------- проверки ----------------
def t_pca():
    fp = FakePca()
    pca = hw.Pca9685(FakeBus({0x40: fp}), 0x40, 25_840_000)
    assert fp.r[0xFE] == 125, f"делитель {fp.r[0xFE]} вместо 125"
    assert fp.r[0x00] == 0x20
    pca.set_us(3, 1500)
    assert abs(pca.get_us(3) - 1500) < 3
    pca.off(3)
    assert pca.get_us(3) is None
    assert pca.get_us(4) is None, "канал после включения питания — выключен"


def t_camsel():
    fp = FakePca()
    pca = hw.Pca9685(FakeBus({0x40: fp}))
    cs = camsel.CamSelect(pca)
    assert cs.set(1) and abs(pca.get_us(8) - camsel.PULSES[1]) < 3
    n = len(fp.writes)
    assert not cs.set(1) and len(fp.writes) == n, "та же камера — без записи"
    assert cs.set(3) and abs(pca.get_us(8) - camsel.PULSES[3]) < 3
    assert cs.set(2) and abs(pca.get_us(8) - camsel.PULSES[2]) < 3
    assert len(set(camsel.PULSES.values())) == 3, "у каждой камеры свой импульс"


def t_arm():
    fp = FakePca()
    pca = hw.Pca9685(FakeBus({0x42: fp}), 0x42, 25_000_000)
    pca.set_us(1, 1600)                                   # плечо уже держит 1600 (служба перезапущена)
    a = arm_mod.Arm(pca)
    jumped = a.activate()
    assert "плечо" not in jumped and "поворот" in jumped and "захват" in jumped
    assert abs(a.pos["плечо"] - 1600) < 3, "плечо подхвачено с места, без рывка"
    _, _, t_lo, t_start, t_hi, t_speed = arm_mod.JOINTS[0]
    assert abs(pca.get_us(0) - t_start) < 6, "поворот (MG996R 180°) — в «старт» = на полоску"
    _, _, s_lo, _, s_hi, s_speed = arm_mod.JOINTS[1]
    for _ in range(50):                                   # 1 с, правый стик ↑ до упора
        a.update(0.02, {"плечо": 1.0, "поворот": 0.0}, None, 1.0)
    assert abs(a.pos["плечо"] - min(s_hi, 1600 + s_speed)) < 3, a.pos["плечо"]
    for _ in range(int((s_hi - s_lo) / s_speed / 0.02) + 50):
        a.update(0.02, {"плечо": 1.0}, None, 1.0)
    assert a.pos["плечо"] == s_hi, "предел не превышен"
    _, g_open, g_close, g_speed = arm_mod.GRIP
    for _ in range(25):                                   # 0.5 с: захват закрыть — со скоростью GRIP
        a.update(0.02, {}, 1.0, 1.0)
    assert abs(a.pos["захват"] - (g_open + 0.5 * g_speed * (1 if g_close > g_open else -1))) < 3, a.pos["захват"]
    for _ in range(int(abs(g_close - g_open) / g_speed / 0.02) + 25):
        a.update(0.02, {}, 1.0, 1.0)
    assert a.pos["захват"] == g_close, "захват дальше «закрыт» не идёт"
    for _ in range(50):
        a.update(0.02, {"поворот": -0.5}, None, 0.3)      # 1 с: половина стика, крутилка 30 %
    assert abs(a.pos["поворот"] - (t_start - 0.5 * 0.3 * t_speed)) < 3, a.pos["поворот"]
    assert abs(pca.get_us(0) - a.pos["поворот"]) < 6
    for _ in range(int((t_hi - t_lo) / t_speed / 0.02) + 50):   # стик до упора дольше полного хода — не дальше предела
        a.update(0.02, {"поворот": -1.0}, None, 1.0)
    assert a.pos["поворот"] == t_lo, a.pos["поворот"]
    fp2 = FakePca()                                       # без захвата (не откалиброван): канал 4 не трогать
    pca2 = hw.Pca9685(FakeBus({0x42: fp2}), 0x42, 25_000_000)
    b = arm_mod.Arm(pca2, grip=None)
    jumped = b.activate()
    assert "захват" not in jumped and pca2.get_us(arm_mod.GRIP[0]) is None
    b.update(0.02, {"плечо": 0.5}, 1.0, 1.0)              # захват со стика — игнорируется
    assert pca2.get_us(arm_mod.GRIP[0]) is None and "захват" not in b.status()


def t_home():
    sd, beak, fp, pca, *_ = make_seeder(pos=30)
    assert sd.inas[0].b.dev[0x41].cfg == [0x19, 0xC7], "INA — 1 замер (усреднение у клонов врёт)"
    assert abs(pca.get_us(sm.CH_SHUTTLE) - sm.HOME_US) < 3, "челнок в «доме» при запуске"
    sd._job("home")
    assert sd.ready and sd.state == "ГОТОВ", sd.state
    assert beak.pos == 0
    assert pca.get_us(sm.CH_L) is None and pca.get_us(sm.CH_R) is None, "после торможения каналы клюва выключены"


def t_cycle_ground():
    sd, beak, fp, pca, gps, log, clock = make_seeder(ground=40, pos=0)      # упор глубины касается на 40 мм хода
    shuttle = []
    orig = pca.set_us
    pca.set_us = lambda ch, us: (shuttle.append((round(clock.t, 2), round(us), round(beak.pos, 1))) if ch == sm.CH_SHUTTLE else None, orig(ch, us))
    bottom = []                                           # когда клюв дошёл до упора глубины
    step = beak.step

    def track(dt):
        step(dt)
        if not bottom and beak.pos >= 39.9:
            bottom.append(clock.t)
    beak.step = track
    sd._job("cycle")
    assert sd.ready and sd.state == "ГОТОВ", sd.state
    assert beak.pos == 0, beak.pos
    assert len(gps.marks) == 1 and "касание" in gps.marks[0][1], gps.marks
    fills = [s for s in shuttle if s[1] == sm.FILL_US]
    drops = [s for s in shuttle if s[1] == sm.DROP_US]
    assert fills and drops and bottom, (shuttle, bottom)
    assert shuttle[0][2] >= 39, f"набор начат, когда клюв уже внизу (pos {shuttle[0][2]})"
    assert shuttle[0][0] - bottom[0] >= sm.BRAKE_S + sm.DOWN_PAUSE_S - 0.05, \
        f"между клювом внизу ({bottom[0]:.2f} с) и набором ({shuttle[0][0]} с) — пауза"
    assert fills[0][0] < drops[-1][0], "сначала набор, потом сброс"
    assert drops[-1][2] >= 39, f"сброс — только когда клюв внизу (pos {drops[-1][2]})"
    assert pca.get_us(sm.CH_L) is None
    assert abs(pca.get_us(sm.CH_SHUTTLE) - sm.HOME_US) < 3, "челнок остался в «доме» (сброс)"


def t_cam_event_at_drop():
    sd, beak, fp, pca, gps, log, clock = make_seeder(ground=40, pos=0)

    class Cam:
        def __init__(self):
            self.at = []

        def event(self):
            self.at.append((clock.t, round(pca.get_us(sm.CH_SHUTTLE)), round(beak.pos, 1)))
            return "posev_test.mkv"
    sd.cam = Cam()
    sd._job("cycle")
    assert len(sd.cam.at) == 1, sd.cam.at
    t, us, pos = sd.cam.at[0]
    assert us == sm.FILL_US and pos >= 39, f"ролик отмечен до хода челнока в «сброс», клюв внизу ({us} мкс, {pos} мм)"
    assert "posev_test.mkv" in gps.marks[0][1], gps.marks


def t_cycle_from_unknown():
    sd, beak, *_rest = make_seeder(ground=55, pos=20)
    sd._job("cycle")                                      # верх не проверен — сначала поиск верха
    assert sd.ready and beak.pos == 0 and len(sd.gps.marks) == 1


def t_cycle_air():
    sd, beak, fp, pca, gps, log, clock = make_seeder(ground=None, pos=0)
    sd._job("cycle")
    assert "предел глубины" in gps.marks[0][1], gps.marks
    lim = sd.depth_plan()[1]
    assert lim == sm.GROUND_MM + sm.DEPTH_MM + sm.GROUND_DIP_MM
    assert lim - 1 < beak.max_pos < lim + 4, f"вниз {beak.max_pos:.1f} мм при пределе {lim}"
    assert sd.ready and beak.pos == 0, (sd.state, beak.pos)


def t_ground_option():
    sd, beak, fp, pca, gps, log, clock = make_seeder(ground=None, pos=0)
    sd.override["ground_mm"] = 20                         # --ground=20: модуль ниже — предел 20 + 15
    sd._job("cycle")
    assert 34 < beak.max_pos < 39, beak.max_pos


def t_dip_lower_ground():
    # земля на 7 мм ниже ожидаемой (упор глубины касается на 52 мм хода вместо 45)
    sd, beak, fp, pca, gps, log, clock = make_seeder(ground=52, pos=0)
    sd._job("cycle")
    assert "предел глубины" in gps.marks[0][1] and beak.max_pos < 49, (gps.marks, beak.max_pos)
    sd, beak, fp, pca, gps, log, clock = make_seeder(ground=52, pos=0)
    sd.override["dip_mm"] = 10                            # --dip=10: искать грунт по току ещё 10 мм
    sd._job("cycle")
    assert "касание" in gps.marks[0][1] and "мелко" not in gps.marks[0][1], gps.marks
    assert 51.5 < beak.max_pos < 53, beak.max_pos
    sd, beak, fp, pca, gps, log, clock = make_seeder(ground=None, pos=0)
    sd.override["dip_mm"] = 10                            # грунта нет и ниже — не дальше 30 + 15 + 10
    sd._job("cycle")
    assert "не найден до 55" in gps.marks[0][1] and beak.max_pos < 59, (gps.marks, beak.max_pos)


def t_rack_limit():
    sd, beak, fp, pca, gps, log, clock = make_seeder(ground=None, pos=0)
    sd.override["ground_mm"] = 55                         # модуль высоко: 55 + 15 = 70 > рейки 63
    sd._job("cycle")
    assert sd.depth_plan() == (70, sm.RACK_MAX_MM)
    assert "модуль высоко" in gps.marks[0][1], gps.marks
    assert beak.max_pos < sm.RACK_MAX_MM + 4, beak.max_pos


def t_settings_file():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "seeder_settings.json")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert sm.settings_cmd(["ground", "40"], path) and sm.settings_cmd(["dip", "5"], path)
        assert not sm.settings_cmd(["ground", "100"], path), "вне 0…60 — не записывать"
        assert not sm.settings_cmd(["высота", "40"], path)
    with open(path) as f:
        assert json.load(f) == {"ground_mm": 40.0, "dip_mm": 5.0}
    sd = sm.Seeder(None, settings_file=path, shuttle_home=False, pos_file=None)
    assert (sd.ground_mm, sd.depth_mm, sd.dip_mm) == (40, sm.DEPTH_MM, 5)
    assert sd.depth_plan() == (55, 60)
    with open(path, "w") as f:                            # руками испортили файл: негодное — по умолчанию
        json.dump({"ground_mm": "сорок", "depth_mm": 99, "dip_mm": 3}, f)
    sd._load_settings()
    assert (sd.ground_mm, sd.depth_mm, sd.dip_mm) == (sm.GROUND_MM, sm.DEPTH_MM, 3)
    sd.override["ground_mm"] = 25                         # ключ командной строки — поверх файла
    sd._load_settings()
    assert sd.ground_mm == 25
    with contextlib.redirect_stdout(out):
        assert sm.settings_cmd(["dip", "default"], path)
    with open(path) as f:
        assert "dip_mm" not in json.load(f)


def t_cycle_shallow():
    sd, beak, fp, pca, gps, *_ = make_seeder(ground=15, pos=0)
    sd._job("cycle")
    assert "мелко" in gps.marks[0][1], gps.marks
    assert sd.ready and beak.pos == 0, sd.state


def t_jam():
    sd, beak, fp, pca, gps, log, clock = make_seeder(ground=60, jam_at=35, pos=0)
    sd._job("cycle")
    assert sd.jammed and not sd.ready and sd.state.startswith("КЛИН"), sd.state
    assert sd.blocks_drive()
    sd.reset_jam()
    assert not sd.blocks_drive() and not sd.ready
    beak.jam_at = None
    sd._job("home")
    assert sd.ready and beak.pos == 0


def t_broken():
    sd, beak, *_ = make_seeder(broken=True)
    sd._job("home")
    assert not sd.ready and sd.state.startswith("ОШИБКА"), sd.state


def t_home_from_top():
    sd, beak, fp, pca, gps, log, clock = make_seeder(pos=0)
    seen = []
    sd.trace = lambda t, d, raw, avg, hit: seen.append((d, hit))
    sd._job("home")
    assert sd.ready and beak.pos == 0, sd.state
    assert any(hit for d, hit in seen if d < 0), "упор найден по току, а не по времени"


def t_soft_ground():
    sd, beak, fp, pca, gps, log, clock = make_seeder(ground=36, pos=0, soft=True)   # упор глубины на 36 мм хода
    sd.ready, sd.pos = True, 0.0
    sd._job("cycle")
    assert "касание" in gps.marks[0][1], gps.marks
    assert beak.max_pos - 36 < 6, f"рыхлый грунт: упор утонул на {beak.max_pos - 36:.1f} мм"
    assert sd.ready and beak.pos == 0, sd.state


def t_pos_tracking():
    sd, beak, fp, pca, gps, log, clock = make_seeder(ground=50, pos=0)
    assert sd.pos is None, "в проверках положение из файла не читается"
    sd._job("cycle")
    assert sd.ready and sd.pos == 0.0, sd.pos


def t_pos_file_trust():
    path = os.path.join(tempfile.mkdtemp(), "pos.json")

    def write(pos):
        with open(path, "w") as f:
            json.dump({"pos": pos, "t": time.time()}, f)

    def read():
        with open(path) as f:
            return json.load(f)["pos"]
    write(0.0)                                            # клюв вверху по записи → готов без поиска верха
    sd, beak, fp, pca, gps, log, clock = make_seeder(ground=40, pos=0, pos_file=path)
    assert sd.ready and "по записи" in sd.state, sd.state
    seen = []
    clock.hooks.append((clock.t + 0.3, lambda: seen.append(read())))
    sd._job("cycle")
    assert seen == [None], f"во время посева в файле «в движении»: {seen}"
    assert read() == 0.0 and sd.ready and len(gps.marks) == 1
    write(None)                                           # питание пропало посреди хода
    sd, *_ = make_seeder(pos=0, pos_file=path)
    assert not sd.ready and sd.pos is None, sd.state
    write(12.0)                                           # клюв остался ниже верха (ошибка, ARM выкл)
    sd, *_ = make_seeder(pos=12, pos_file=path)
    assert not sd.ready and sd.pos == 12.0, sd.state


def t_skip_at_top_cycle():
    sd, beak, fp, pca, gps, log, clock = make_seeder(ground=50, pos=0)
    sd.ready, sd.pos = True, 0.0
    beak.skip = True
    sd._job("cycle")
    assert sd.ready and "по времени" in sd.state, sd.state
    assert beak.pos == 0 and beak.skip_time < 0.45, f"проскакивали {beak.skip_time:.2f} с"
    assert len(gps.marks) == 1


def t_skip_at_top_home():
    sd, beak, fp, pca, gps, log, clock = make_seeder(pos=0)
    sd.pos = 0.0                                          # положение известно (из файла)
    beak.skip = True
    sd._job("home")
    assert sd.ready and "по времени" in sd.state, sd.state
    assert beak.skip_time < 0.45, f"проскакивали {beak.skip_time:.2f} с"


def t_skip_spikes_cycle():
    sd, beak, fp, pca, gps, log, clock = make_seeder(ground=50, pos=0)
    sd.ready, sd.pos = True, 0.0
    beak.skip = beak.skip_spikes = True
    sd._job("cycle")
    assert sd.ready and "перескок" in sd.state, sd.state
    assert beak.pos == 0 and beak.skip_time < 0.12, f"проскакивали {beak.skip_time:.2f} с"
    assert len(gps.marks) == 1


def t_skip_spikes_home():
    sd, beak, fp, pca, gps, log, clock = make_seeder(pos=20)
    assert sd.pos is None                                 # положение неизвестно — без перескока была бы ошибка
    beak.skip = beak.skip_spikes = True
    sd._job("home")
    assert sd.ready and "перескок" in sd.state, sd.state
    assert beak.skip_time < 0.12, f"проскакивали {beak.skip_time:.2f} с"


def t_ina_not_in_circuit():
    sd, beak, fp, pca, gps, log, clock = make_seeder(pos=0)
    for a in (0x41, 0x44):
        sd.inas[0].b.dev[a].amps_fn = lambda: 0.0             # провода серво мимо шунтов: клюв едет, INA — 0
    t0 = clock.t
    sd._job("home")
    assert "не видят ток" in sd.state and not sd.ready, sd.state
    assert clock.t - t0 < 1.5, f"остановился быстро, а не через HOME_MAX_S ({clock.t - t0:.2f} с)"
    assert pca.get_us(sm.CH_L) is None


def t_time_mode():
    sd, beak, fp, pca, gps, *_ = make_seeder(ground=None, pos=10, ina=False)
    assert sd.mode == "время"
    sd._job("cycle")
    assert sd.ready and "по времени" in gps.marks[0][1]
    assert beak.pos == 0, beak.pos


def t_abort_stop():
    sd, beak, fp, pca, gps, log, clock = make_seeder(ground=50, pos=0)
    sd.ready, sd.busy = True, True
    clock.hooks.append((clock.t + 1.5, lambda: sd.abort("stop")))   # посреди спуска (набор ≈ 0.95 с)
    sd._job("cycle")
    assert not sd.busy and not sd.ready and "ARM" in sd.state, sd.state
    assert 5 < beak.pos < 50, beak.pos
    assert pca.get_us(sm.CH_L) is None and not gps.marks


def t_abort_up():
    sd, beak, fp, pca, gps, log, clock = make_seeder(ground=50, pos=0)
    sd.ready, sd.busy = True, True
    clock.hooks.append((clock.t + 1.5, lambda: sd.abort("up")))
    sd._job("cycle")
    assert sd.ready and beak.pos == 0 and not gps.marks, (sd.state, beak.pos)


def t_no_pca_i2c_error():
    sd, beak, fp, pca, gps, log, clock = make_seeder(ground=50, pos=0)
    del sd.inas[0].b.dev[0x41]                            # INA отвалилась посреди работы
    sd._job("cycle")
    assert sd.state.startswith("ОШИБКА") and not sd.ready, sd.state
    assert pca.get_us(sm.CH_L) is None, "клюв остановлен"


def t_no_6v():
    sd, beak, fp, pca, gps, log, clock = make_seeder(ground=50, pos=20)
    for a in (0x41, 0x44):
        sd.inas[0].b.dev[a].volts = 0.0                   # понижайка 6 В выключена
    sd._job("cycle")
    assert "нет 6 В" in sd.state and not sd.ready, sd.state
    assert beak.pos == 20 and pca.get_us(sm.CH_L) is None, "клюв не трогали"
    assert not gps.marks


def t_floating_ina():
    sd, beak, fp, pca, gps, log, clock = make_seeder(ground=50, pos=20)
    sd.inas[0].b.dev[0x41].amps_fn = lambda: 2.05        # «плавающие» входы: амперы без движения
    sd._job("home")
    assert "ток покоя" in sd.state and not sd.ready, sd.state
    assert beak.pos == 20 and pca.get_us(sm.CH_L) is None


def t_seed_cam_segments():
    d = tempfile.mkdtemp()
    t = 1_000_000.0
    for i, end in enumerate([t - 3, t - 1.5, t - 0.5, t + 0.5, t + 1.5, t + 2.6]):
        p = os.path.join(d, f"seg{i:02d}.mkv")
        open(p, "w").close()
        os.utime(p, (end, end))
    got = [os.path.basename(p) for p in seed_cam.pick_segments(t, d)]
    assert got == ["seg02.mkv", "seg03.mkv", "seg04.mkv"], got


# ---------------- логика пульта (rc_drive.Robot) ----------------
class FakeHB:
    def __init__(self):
        self.last = None

    def drive(self, s, st):
        self.last = ("drive", s, st)

    def stop(self):
        self.last = ("stop",)


class FakeSeeder:
    def __init__(self):
        self.busy = self.ready = self.jammed = False
        self.requests, self.aborts, self.resets = [], [], 0

    def request(self, job):
        if self.busy:
            return False
        self.requests.append(job)
        return True

    def abort(self, kind):
        self.aborts.append(kind)

    def reset_jam(self):
        self.resets += 1

    def blocks_drive(self):
        return self.busy or self.jammed


class FakeArm:
    def __init__(self):
        self.active, self.updates = False, []

    def activate(self):
        self.active = True
        return ["плечо"]

    def update(self, dt, rates, grip, k):
        self.updates.append((rates, grip, k))


class FakeCams:
    def __init__(self):
        self.cam = None

    def set(self, n):
        self.cam = n


def chans(arm=True, swd=1000, swc=1000, speed=1500, steer=1500, knob=2000, rx=1500, thr=1000):
    c = [1500] * 14
    c[0], c[1], c[2], c[3] = rx, speed, thr, steer
    c[4], c[5], c[6], c[9] = 2000 if arm else 1000, knob, swd, swc
    return c


def make_robot():
    hb, sd, a, cams, log = FakeHB(), FakeSeeder(), FakeArm(), FakeCams(), []
    return rc_drive.Robot(hb, sd, a, cams, say=log.append), hb, sd, a, cams, log


def t_robot_drive():
    r, hb, sd, a, cams, log = make_robot()
    r.step(None, 0.02)
    assert hb.last == ("stop",) and cams.cam == 1 and r.state == "НЕТ СИГНАЛА"
    r.step(chans(speed=2000), 0.02)
    assert hb.last == ("stop",) and r.state == "ОТПУСТИТЕ СТИКИ В ЦЕНТР"
    r.step(chans(), 0.02)
    r.step(chans(speed=2000), 0.02)
    assert hb.last == ("drive", 200, 0), hb.last              # крутилка до упора — 200 об/мин
    r.step(chans(steer=2000), 0.02)
    assert hb.last == ("drive", 0, -160), hb.last             # INVERT_STEER
    r.step(chans(arm=False, speed=2000), 0.02)
    assert hb.last == ("stop",) and cams.cam == 1


def t_robot_sow():
    r, hb, sd, a, cams, log = make_robot()
    r.step(chans(swc=2000), 0.02)                              # старт с SwC внизу — только запомнить
    assert not sd.requests and cams.cam == 3
    r.step(chans(swc=1000), 0.02)
    r.step(chans(swc=1500), 0.02)
    assert sd.requests == ["home"] and cams.cam == 3
    sd.ready = True
    r.step(chans(swc=2000), 0.02)
    assert sd.requests == ["home", "cycle"]
    sd.busy = True
    r.step(chans(swc=2000, speed=2000), 0.02)
    assert hb.last == ("stop",) and r.state == "ПОСЕВ — СТОИМ"
    sd.busy = False
    r.step(chans(swc=1500, speed=2000), 0.02)
    assert hb.last == ("stop",), "после посева — снова стики в центр"
    r.step(chans(swc=1500), 0.02)
    r.step(chans(swc=1500, speed=1800), 0.02)
    assert hb.last[0] == "drive"
    r.step(chans(swc=2000, speed=1800), 0.02)                  # едет — посев не начинать
    assert sd.requests == ["home", "cycle"] and any("платформа едет" in s for s in log)


def t_robot_abort_and_jam():
    r, hb, sd, a, cams, log = make_robot()
    r.step(chans(), 0.02)
    sd.busy = True
    r.step(chans(arm=False), 0.02)
    assert sd.aborts == ["stop"]
    sd.busy, sd.jammed = False, True
    r.step(chans(arm=False), 0.02)
    r.step(chans(arm=True), 0.02)
    assert sd.resets == 1, "ARM выкл → вкл сбрасывает клин"
    sd.busy = True
    r.step(None, 0.02)
    assert sd.aborts == ["stop", "up"] and cams.cam == 1


def t_robot_manip():
    r, hb, sd, a, cams, log = make_robot()
    r.step(chans(), 0.02)
    r.step(chans(speed=2000), 0.02)
    assert hb.last[0] == "drive"
    r.step(chans(swd=2000, speed=2000), 0.02)                  # манипулятор: платформа стоит, стик отклонён
    assert hb.last == ("stop",) and cams.cam == 2 and not a.updates and "ОТПУСТИТЕ" in r.state
    r.step(chans(swd=2000), 0.02)
    assert a.active and len(a.updates) == 1 and any("рывком" in s for s in log)
    r.step(chans(swd=2000, speed=2000, rx=1000, thr=2000), 0.02)
    rates, grip, k = a.updates[-1]
    assert rates["плечо"] == 1.0 and rates["поворот"] == -1.0 and k == 1.0, a.updates[-1]
    assert (grip == 1.0) if rc_drive.GRIP_CH else (grip is None), "захват выключен в GRIP_CH — с пульта не трогается"
    r.step(chans(swd=2000, swc=1000), 0.02)
    r.step(chans(swd=2000, swc=2000), 0.02)                    # посев в режиме манипулятора — нельзя
    assert not sd.requests
    r.step(chans(swd=1000, speed=2000), 0.02)                  # обратно в ход со стиком — стоим
    assert hb.last == ("stop",) and cams.cam == 1


TESTS = [t_pca, t_camsel, t_arm, t_home, t_cycle_ground, t_cycle_from_unknown, t_cycle_air, t_cycle_shallow, t_jam,
         t_broken, t_home_from_top, t_ina_not_in_circuit, t_soft_ground, t_pos_tracking, t_skip_at_top_cycle, t_skip_at_top_home,
         t_skip_spikes_cycle, t_skip_spikes_home, t_ground_option, t_dip_lower_ground, t_rack_limit, t_settings_file,
         t_cam_event_at_drop, t_pos_file_trust,
         t_time_mode, t_abort_stop, t_abort_up, t_no_pca_i2c_error, t_no_6v, t_floating_ina, t_seed_cam_segments,
         t_robot_drive, t_robot_sow, t_robot_abort_and_jam, t_robot_manip]


def main():
    bad = []
    t0 = time.time()
    for t in TESTS:
        try:
            t()
            print(f"  ok  {t.__name__}")
        except Exception:
            bad.append(t.__name__)
            print(f"  ОШИБКА  {t.__name__}")
            traceback.print_exc()
    print(f"{len(TESTS) - len(bad)} из {len(TESTS)} за {time.time() - t0:.1f} с — " + ("ВСЁ ВЕРНО" if not bad else "ОШИБКИ: " + ", ".join(bad)))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
