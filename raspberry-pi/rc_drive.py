"""Управление роботом с пульта FlySky через Raspberry Pi: ход, посев, рука (манипулятор), выбор камеры FPV.

Пульт -> FS-X6B (i-BUS) -> HW-597 (USB, /dev/serial/by-id/...) -> Raspberry Pi -> USART2 (/dev/serial0) -> плата гироскутера.
Pi -> I2C: PCA9685 №1 0x40 (клюв кан. 0/1, челнок 2, переключатель камер W8010S 8), INA219 0x41/0x44 (ток клюва),
PCA9685 №2 0x42 (рука: 0 поворот, 1 плечо, 2 предплечье, 4 захват). Камера Logitech в USB — ролик на каждый посев.

Тумблеры (решение владельца 17.10.2026):
  SwA (CH5)  вниз — ARM: колёса, клюв и рука двигаются только при нём; вверх — всё стоп (посев прерывается сразу);
  SwD (CH7)  вверх — ХОД; вниз — МАНИПУЛЯТОР: платформа стоит, стики двигают руку (arm.py), камера 2;
  SwC (CH10) вверх — ходовая камера 1; середина — камера посева 3 + клюв к верхнему упору;
             вниз — ОДИН посев на каждое переключение в «вниз» (платформа стоит, стики в центре, режим «ход»);
  VrB (CH6)  крутилка — предел хода и поворота (ход), скорость руки 30…100 % (манипулятор).

Безопасность:
  - колёса — только при ARM; после включения ARM, смены режима, посева и потери сигнала — когда стики в центре;
  - нет кадров i-BUS дольше RC_TIMEOUT -> стоп, камера 1, идущий посев прерывается и клюв поднимается до упора;
  - во время посева и при «клине» клюва колёса стоят (клин сбрасывает поиск верха SwC вверх → середина или ARM выкл → вкл);
  - посев и рука — никогда одновременно (общий источник 6 В);
  - программа зависла -> драйвер платы через 0.3 с сам шлёт стоп; упала -> плата через 0.8 с делает аварийную остановку;
  - нет I2C / какой-то платы — едем без неё (сообщение в журнале).
  На пульте ОБЯЗАТЕЛЬНО настройте Failsafe: канал ARM -> положение "выключено", стики -> центр.

Запуск: python3 rc_drive.py (служба rc-drive). Проверка логики без железа: python3 test_modules.py
"""

import os
import signal
import sys
import time

# ---------- Настройки ----------
IBUS_PORT = None                # None — постоянное имя HW-597 из ibus.py (/dev/serial/by-id/...)
BOARD_PORT = "/dev/serial0"     # плата гироскутера

SPEED_CH = 2        # правый стик вверх/вниз (FS-i6/i6X, режим 2)
STEER_CH = 4        # левый стик влево/вправо (1 - правый стик)
ARM_CH = 5          # тумблер SwA: вниз (на себя) = 2000 = моторы включены, вверх = 1000 = выключены
# Прошивка платы в SPD_MODE: команда = обороты колеса (об/мин), плата держит их под нагрузкой.
# Ведущее колесо Ø 18 см: км/ч = об/мин * 0.0339. См. SPEED.md.
SPEED_KNOB_CH = 6   # крутилка (VrB): предел хода и поворота, None - не использовать (MAX_SPEED / MAX_STEER)
KNOB_MIN_SPEED = 40   # крутилка в минимуме: правый стик до упора = 40 об/мин (~1.4 км/ч)
KNOB_MAX_SPEED = 200  # крутилка в максимуме: правый стик до упора = 200 об/мин (~6.8 км/ч)
KNOB_MIN_STEER = 60   # крутилка в минимуме: левый стик до упора = 60 (колёса ±30 об/мин)
KNOB_MAX_STEER = 160  # крутилка в максимуме: левый стик до упора = 160 (колёса ±80 об/мин)
# Прошивка платы смешивает поворот с коэффициентом 0.5 (STEER_COEFFICIENT): колесо получает ±steer/2.

MAX_SPEED = 150     # предел без крутилки: об/мин при стике до упора (~5.1 км/ч)
MAX_STEER = 100     # поворот без крутилки при стике до упора (колёса ±50 об/мин)
# 14.10.2026: ходовые испытания показали реверс вперёд/назад. Исправлено в прошивке (INVERT_R/L_DIRECTION,
# чтобы плата пищала при движении назад, а не вперёд); инверсия обоих моторов меняет и знак поворота -> INVERT_STEER.
INVERT_SPEED = False
INVERT_STEER = True

MODE_CH = 7         # SwD: вверх — ход, вниз — манипулятор; None — режима манипулятора нет
SEED_SW_CH = 10     # SwC, 3 положения (1000 / 1500 / 2000): камера и посев; None — посев с пульта выключен

# Модули (False — не запускать; нет железа — модуль выключится сам с сообщением, езда не зависит)
SEEDER = True       # посевной модуль «семена 112» (seeder.py): клюв, челнок, INA219
SHUTTLE_HOME = True  # при запуске — челнок в «дом» 1400 мкс и держать (PCA выдаёт импульс сама)
ARM_MODULE = True   # рука на PCA9685 №2 0x42 (arm.py)
CAMSEL = True       # переключатель камер W8010S (camsel.py)
SEED_CAM = True     # камера Logitech в Pi: ролик на каждый посев (seed_cam.py)
GPS_LOG = True      # GPS (gps_log.py): трек + точка на каждый посев — только на флешку VALLI

# Рука в режиме «манипулятор»: сустав -> (канал пульта, инверсия). Стик = скорость сустава.
# Едет не в ту сторону — поменять инверсию здесь (пределы и скорости суставов — в arm.py).
# 23.10.2026 (проверено с пульта): стик вправо — поворот вправо, правый стик вверх — плечо вверх; предплечье владелец
# попросил наоборот → инверсия: левый стик влево — предплечье вверх, вправо — вниз.
ARM_AXES = {"поворот": (1, False),       # правый стик ←→
            "плечо": (2, False),         # правый стик ↑↓
            "предплечье": (4, True)}     # левый стик ←→
# Захват: левый стик ↑↓ (CH3, без пружины): вниз — открыт, вверх — закрыт (захват идёт за стиком, пределы и скорость —
# GRIP в arm.py); None — захват не трогать (канал не включается). 23.10.2026 — откалиброван (900 / 1900), включён.
GRIP_CH = 3
GRIP_INVERT = False
ARM_KNOB_MIN = 0.3  # крутилка VrB в минимуме — 30 % скорости руки

DEADBAND = 0.08     # мёртвая зона стиков (доля хода). В SPD_MODE даже малая команда двигает колесо - не меньше 0.08
ARM_THRESHOLD = 1700  # канал ARM выше этого значения = включено
RC_TIMEOUT = 0.3    # с без кадров i-BUS = потеря сигнала
LOOP_PERIOD = 0.02  # 50 Гц
TEMP_WARN = 60.0    # °C платы: пометка ПЕРЕГРЕВ в журнале (= TEMP_WARNING в прошивке, там 5 сигналов)
LOG_PERIOD = float(os.environ.get("RC_LOG_PERIOD", "0.5"))  # с между строками телеметрии (служба: 5)
# --------------------------------


def _say(s):
    """Строка в журнал целиком (print из нескольких потоков склеивает строки)."""
    sys.stdout.write(s + "\n")
    sys.stdout.flush()


def _interrupt(sig, frame):
    raise KeyboardInterrupt


def stick(value, invert=False):
    """1000..2000 -> -1..1 с мёртвой зоной."""
    x = max(-1.0, min(1.0, (value - 1500) / 500))
    if abs(x) < DEADBAND:
        return 0.0
    x = (abs(x) - DEADBAND) / (1 - DEADBAND) * (1 if x > 0 else -1)
    return -x if invert else x


def pos3(value):
    """Трёхпозиционный тумблер: 0 — вверх (1000), 1 — середина (1500), 2 — вниз (2000)."""
    return 0 if value < 1250 else 1 if value < 1750 else 2


class Robot:
    """Вся логика пульта: на каждый кадр каналов (или None — нет сигнала) решает, что делают колёса, посев, рука и
    камеры. Железо передаётся снаружи (на ПК в test_modules.py — подставное)."""

    def __init__(self, hb, seeder=None, arm=None, cams=None, say=_say):
        self.hb, self.seeder, self.arm, self.cams, self.say = hb, seeder, arm, cams, say
        self.armed = False          # колёса разрешены
        self.arm_live = False       # рука разрешена (стики побывали в центре в режиме «манипулятор»)
        self.manip = False
        self.state = ""
        self.speed = self.steer = 0
        self.max_speed, self.max_steer = MAX_SPEED, MAX_STEER
        self.k_arm = 1.0
        self.swc_prev = None        # прошлое положение SwC; None — неизвестно (старт, потеря сигнала)
        self.arm_on_prev = None
        self._io_err = {}

    # ---------- железо: ошибка I2C не роняет программу ----------
    def _io(self, name, fn, *a):
        try:
            r = fn(*a)
        except OSError as e:
            if name not in self._io_err:
                self.say(f"{name}: ошибка I2C ({e}) — работаем без неё")
            self._io_err[name] = str(e)
            return None
        if self._io_err.pop(name, None) is not None:
            self.say(f"{name}: снова отвечает")
        return r

    def _cam(self, n):
        if self.cams:
            self._io("камеры", self.cams.set, n)

    def _stop_platform(self):
        self.speed = self.steer = 0
        self.hb.stop()

    # ---------- один шаг ----------
    def step(self, ch, dt):
        sd = self.seeder
        if ch is None:
            self.armed = self.arm_live = False
            self.swc_prev = None
            self.state = "НЕТ СИГНАЛА"
            if sd and sd.busy:
                sd.abort("up")
            self._cam(1)
            self._stop_platform()
            return

        arm_on = ch[ARM_CH - 1] >= ARM_THRESHOLD
        manip = bool(MODE_CH) and ch[MODE_CH - 1] > 1500
        swc = pos3(ch[SEED_SW_CH - 1]) if SEED_SW_CH else 0
        speed_n = stick(ch[SPEED_CH - 1], INVERT_SPEED)
        steer_n = stick(ch[STEER_CH - 1], INVERT_STEER)
        centered = speed_n == 0 and steer_n == 0
        if SPEED_KNOB_CH:
            k = max(0.0, min(1.0, (ch[SPEED_KNOB_CH - 1] - 1000) / 1000))
            self.max_speed = round(KNOB_MIN_SPEED + (KNOB_MAX_SPEED - KNOB_MIN_SPEED) * k)
            self.max_steer = round(KNOB_MIN_STEER + (KNOB_MAX_STEER - KNOB_MIN_STEER) * k)
            self.k_arm = ARM_KNOB_MIN + (1 - ARM_KNOB_MIN) * k

        # ARM: выключили — посев стоп сразу; включили снова — сброс «клина» клюва
        if sd:
            if not arm_on and sd.busy:
                sd.abort("stop")
            if arm_on and self.arm_on_prev is False:
                sd.reset_jam()
        self.arm_on_prev = arm_on

        self._cam(2 if manip else 1 if swc == 0 else 3)

        prev, self.swc_prev = self.swc_prev, swc
        if sd and prev is not None and swc != prev:
            if swc == 2:
                self._sow(arm_on, manip, centered)
            elif swc == 1 and prev == 0 and arm_on and not manip and not sd.ready and not sd.busy:
                sd.request("home")
                self.say("посев: клюв к верхнему упору (SwC — середина)")

        if manip != self.manip:
            self.manip = manip
            self.armed = self.arm_live = False
            self.say("режим: МАНИПУЛЯТОР — платформа стоит" if manip else "режим: ХОД")
        if manip:
            self._stop_platform()
            self._manip(ch, dt, arm_on)
            return

        if sd and sd.blocks_drive():
            self.armed = False
            self.state = "ПОСЕВ — СТОИМ" if sd.busy else "КЛИН КЛЮВА — СТОИМ"
            self._stop_platform()
            return
        if not arm_on:
            self.armed = False
            self.state = "ВЫКЛ (тумблер ARM)"
        elif not self.armed:
            if centered:
                self.armed = True
            else:
                self.state = "ОТПУСТИТЕ СТИКИ В ЦЕНТР"
        if self.armed:
            self.speed = round(speed_n * self.max_speed)
            self.steer = round(steer_n * self.max_steer)
            self.state = "ЕДЕМ"
            self.hb.drive(self.speed, self.steer)
        else:
            self._stop_platform()

    def _sow(self, arm_on, manip, centered):
        sd = self.seeder
        if not arm_on:
            self.say("посев: ARM выключен — пропуск")
        elif manip:
            self.say("посев: включён манипулятор (SwD вниз) — пропуск")
        elif not centered or self.speed or self.steer:
            self.say("посев: платформа едет — отпустите стики, потом SwC снова вниз")
        elif not sd.request("cycle"):
            self.say("посев: модуль ещё работает — пропуск")
        else:
            self.armed = False      # после посева — снова стики в центр
            self.say("посев: цикл")

    def _manip(self, ch, dt, arm_on):
        if self.arm is None:
            self.state = "МАНИПУЛЯТОР: руки нет"
            return
        if self.seeder and self.seeder.busy:
            self.state = "МАНИПУЛЯТОР: ждём конца посева"
            return
        if not arm_on:
            self.arm_live = False
            self.state = "МАНИПУЛЯТОР: ARM выкл"
            return
        rates = {name: stick(ch[c - 1], inv) for name, (c, inv) in ARM_AXES.items()}
        if not self.arm_live:
            if any(rates.values()):
                self.state = "МАНИПУЛЯТОР: ОТПУСТИТЕ СТИКИ В ЦЕНТР"
                return
            self.arm_live = True
            if not self.arm.active:
                jumped = self._io("рука", self.arm.activate)
                if jumped:
                    self.say("рука: " + ", ".join(jumped) + " — канал был выключен, встал в «старт» рывком")
        grip = None
        if GRIP_CH:
            grip = max(0.0, min(1.0, (ch[GRIP_CH - 1] - 1000) / 1000))
            if GRIP_INVERT:
                grip = 1 - grip
        self._io("рука", self.arm.update, dt, rates, grip, self.k_arm)
        self.state = "МАНИПУЛЯТОР"


def main():
    from hoverboard import Hoverboard
    from ibus import DEFAULT_PORT, IBusReader

    bus = pca1 = pca2 = None
    try:
        from hw import PCA_ARM_ADDR, PCA_ARM_OSC_HZ, PCA_SEED_ADDR, PCA_SEED_OSC_HZ, Pca9685, open_bus
        bus = open_bus()
    except Exception as e:
        _say(f"I2C: недоступна ({e!r}) — посев, рука и камеры выключены")
    if bus:
        try:
            pca1 = Pca9685(bus, PCA_SEED_ADDR, PCA_SEED_OSC_HZ)
        except OSError as e:
            _say(f"PCA9685 №1 (0x40) не отвечает ({e}) — посев и переключение камер выключены")
        if ARM_MODULE:
            try:
                pca2 = Pca9685(bus, PCA_ARM_ADDR, PCA_ARM_OSC_HZ)
            except OSError as e:
                _say(f"PCA9685 №2 (0x42) не отвечает ({e}) — рука выключена")

    cams = None
    if CAMSEL and pca1:
        from camsel import CamSelect
        cams = CamSelect(pca1)
        try:
            cams.set(1)
        except OSError as e:
            _say(f"камеры: не выставлена ({e})")

    gps = None
    if GPS_LOG:
        try:
            from gps_log import GpsLogger
            gps = GpsLogger().start()
        except Exception as e:  # ехать можно и без GPS
            _say(f"GPS выключен: {e!r}")

    seedcam = None
    if SEED_CAM:
        try:
            from seed_cam import SeedCam, default_out_dir

            def cam_dir():
                if gps and gps.session_dir:
                    return os.path.join(gps.session_dir, "seedcam")
                return default_out_dir()
            seedcam = SeedCam(cam_dir).start()
        except Exception as e:
            _say(f"камера Pi выключена: {e!r}")

    seeder = None
    if SEEDER and pca1:
        try:
            from seeder import Seeder, open_inas
            inas = open_inas(bus)
            seeder = Seeder(pca1, inas, gps, seedcam, shuttle_home=SHUTTLE_HOME)
            _say(f"посев: режим «{seeder.mode}»" + ("" if inas else " — INA219 0x41 / 0x44 не отвечают, верх и низ по времени")
                 + (", челнок в «доме» 1400 мкс" if SHUTTLE_HOME else ""))
            expect, limit = seeder.depth_plan()
            _say(f"посев: до земли {seeder.ground_mm:.0f} мм, лунка {seeder.depth_mm:.0f}, ямка до +{seeder.dip_mm:.0f} → "
                 f"вниз на ровном {expect:.0f} мм, не дальше {limit:.0f} (seeder.py set … — до следующего посева)")
        except Exception as e:
            _say(f"посев выключен: {e!r}")

    arm = None
    if pca2:
        from arm import GRIP, Arm
        arm = Arm(pca2, grip=GRIP if GRIP_CH else None)

    rc = IBusReader(IBUS_PORT or DEFAULT_PORT)
    hb = Hoverboard(BOARD_PORT, command_timeout=0.3)
    robot = Robot(hb, seeder, arm, cams)
    # systemctl stop / restart (заливка) шлёт SIGTERM — без этого Python выходит сразу, мимо finally, и серво 360°
    # клюва крутились бы дальше на последнем импульсе PCA (finally → seeder.close() ставит «стоп»)
    signal.signal(signal.SIGTERM, _interrupt)
    print("Управление с пульта запущено. Ctrl+C - выход.", flush=True)
    last_state = None
    t_print = 0.0
    t_prev = time.monotonic()
    try:
        while True:
            time.sleep(LOOP_PERIOD)
            now = time.monotonic()
            dt, t_prev = min(now - t_prev, 0.1), now
            robot.step(rc.channels(max_age=RC_TIMEOUT), dt)

            if now - t_print >= LOG_PERIOD or robot.state != last_state:  # периодически и при смене состояния
                t_print, last_state = now, robot.state
                fb = hb.fresh_feedback()
                # обратная связь — физическое вращение моторов; знаки так, чтобы вперёд было > 0.
                # Плата стоит развёрнутой: её правый мотор (speed_r) — левая гусеница, левый (speed_l) — правая.
                tel = (f"гусеницы лев={fb.speed_r:4} прав={-fb.speed_l:4}  батарея {fb.bat_voltage:.1f} В  плата {fb.board_temp:.1f} °C"
                       + ("  ПЕРЕГРЕВ!" if fb.board_temp >= TEMP_WARN else "")
                       if fb else "нет связи с платой")
                extra = [m.status() for m in (cams, seeder) if m]
                if arm and robot.manip:
                    extra.append(arm.status())
                extra += [m.status() for m in (seedcam, gps) if m]
                _say(f"{robot.state:24} предел={robot.max_speed:4}/{robot.max_steer:3} speed={robot.speed:5} "
                     f"steer={robot.steer:5}  {tel}  " + "  ".join(extra))
    except KeyboardInterrupt:
        pass
    finally:
        for m, f in ((seeder, "close"), (seedcam, "stop"), (gps, "stop"), (hb, "close"), (rc, "close")):
            if m:
                try:
                    getattr(m, f)()
                except Exception as e:
                    _say(f"при остановке {f}: {e!r}")
        print("Стоп.", flush=True)


if __name__ == "__main__":
    main()
