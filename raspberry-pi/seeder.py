"""Посевной модуль «семена 112»: клюв (2 × MG996R 360°, PCA9685 №1 кан. 0 / 1), челнок (MG90S 180°, кан. 2),
две INA219 в «+» серво клюва (0x41 левая, 0x44 правая) — верх и низ клюва по току (концевиков нет).

Цикл посева (rc_drive.py: SwC в «вниз», платформа стоит, SwA вниз), ≈ 5 с:
  0. клюв не на верхнем упоре (после запуска, ошибки) — сначала поиск верха;
  1. клюв вниз до касания упора глубины (рост тока) или до предела глубины GROUND_MM + DEPTH_MM от верха (в рыхлом
     грунте ток растёт поздно); торможение «стопом»; пауза DOWN_PAUSE_S;
  2. челнок «набор» (1000) — семя в камеру, FILL_WAIT_S;
  3. челнок «сброс» (1400 = дом), ждать DROP_WAIT_S (семя падает в клюв), точка GPS (+ ролик камеры Pi);
  4. клюв вверх медленно UP_SLOW_S, пауза LIFT_PAUSE_S (створки раскрываются над лункой), вверх быстро, у верха —
     медленно до упора по току (или до перескока зуба шестерни по рейке — всплеск тока). Упор слишком рано → «КЛИН»
     (езда запрещена); упора нет → верх по расчётному положению, а если положение неизвестно — ошибка.
Без INA219 (не отвечают по I2C) — всё по времени (TIME_*), молча не путать: в журнале «режим: время».

Нет сигнала пульта во время цикла → клюв вверх до упора; SwA (ARM) вверх → всё стоп сразу, клюв где есть.
«КЛИН» сбрасывается поиском верха (SwC вверх → середина) или тумблером ARM (выкл → вкл).

Пороги и времена — по калибровке «посадка семян/СЕМЕНА 112.md», раздел 8 (клюв) — пороги тока ещё НЕ ОТКАЛИБРОВАНЫ:
проверить на ведре грунта (python3 -u seeder.py read / cycle) и поправить STALL_*, DOWN_*.

Ручная проверка (службу остановить: systemctl --user stop rc-drive; всё убрать из-под клюва):
  python3 -u seeder.py status          # INA (ток, напряжение), челнок, режим
  python3 -u seeder.py read 5          # ток обеих INA 5 с, 10 раз в секунду (покой / рукой давить на клюв)
  python3 -u seeder.py idle            # БЕЗ ДВИЖЕНИЯ: «стоп»-импульс — видят ли INA ток серво (проверка проводки)
  python3 -u seeder.py home            # поиск верха: вниз ≈ 19 мм, потом вверх медленно до упора по току
  python3 -u seeder.py up              # вверх медленно до упора, без опускания (клюв заведомо ≥ 25 мм ниже верха)
  python3 -u seeder.py jog 0.3        # вниз 0.3 с (≈ 14 мм; не больше 0.8), стоп, поиск верха
  python3 -u seeder.py soft 240        # вниз ≈ 19 мм, потом вверх мягко (d 240) до упора по току — треск? дошёл?
  (home / jog / soft / cycle печатают ток хода и упора и пишут его в ~/robot/ina_logs/beak_….csv — для порогов STALL_*)
  python3 -u seeder.py cycle           # один посев целиком (клюв уйдёт вниз!)
  python3 -u seeder.py cycle --top     # … клюв точно вверху (видно глазами) — без поиска верха перед посевом
  python3 -u seeder.py cycle --top --ground=25   # … от острия до земли 25 мм (по умолчанию GROUND_MM): вниз ≤ 25 + 15
  (положение клюва программа помнит в ~/.seeder_pos.json; подъём к верху — не дольше расчётного + TOP_MARGIN_S:
   у упора шестерни могут проскакивать по рейке; первый перескок обычно виден всплеском тока — стоп, SKIP_*)
  python3 -u seeder.py cycle 5         # пять подряд
"""

import statistics
import sys
import threading
import time
from collections import deque

# ---------- клюв (калибровка 16.10.2026) ----------
CH_L, CH_R, CH_SHUTTLE = 0, 1, 2
N_L = 1510               # «стоп» левого серво (кан. 0), мкс
N_R = 1510               # «стоп» правого (кан. 1)
DOWN = +1                # знак «вниз»: кан. 0 = N_L + d, кан. 1 = N_R − d при d > 0. После переборки шестерён — проверить!
FAST_D = 270             # 22.10.2026, просьба владельца «медленнее»: 270 ≈ 40 мм/с (285 — 48 мм/с: 65 мм за 1.4 с,
                         # 310 — 57 мм/с); ниже не даём — у порога одна сторона может не тронуться (±260, 16.10.2026)
SLOW_D = 270             # малая скорость (= быстрой: медленнее у этих серво 360° надёжно не получить)
SLOW_RATIO = 1.0         # малая / быстрая скорость
BRAKE_S = 0.3            # «стоп» командой столько секунд, потом каналы выключить (без сигнала левое докручивает)
TRIM_CCW_US = 15         # серво с импульсом выше «стопа» крутится на ~5 % медленнее (16.10.2026) — ему добавка к ходу:
                         # иначе в ходу тянет одно серво, второе едет следом (22.10.2026: ток 0.12–0.15 против 0.2–0.27 А)

# ---------- челнок (калибровка 17.10.2026) ----------
FILL_US = 1000           # набор
DROP_US = 1400           # сброс
HOME_US = 1400           # покой (решение владельца) = сброс
SHUTTLE_SPEED = 800      # мкс/с (400 мкс хода за 0.5 с — проверено 5 циклами)
SHUTTLE_SETTLE_S = 0.15  # дать серве доехать после плавного хода
FILL_WAIT_S = 0.3        # постоять в «наборе» — семя ложится в камеру
DROP_WAIT_S = 0.8        # после «сброса» — семя падает ~0.35–0.4 м до низа клюва
DOWN_PAUSE_S = 1.0       # клюв опустился → пауза → набор → сброс (порядок и пауза — просьба владельца 22.10.2026)

# ---------- верх / низ по току (две INA219) — ПОРОГИ НЕ ОТКАЛИБРОВАНЫ ----------
BLANK_S = 0.15           # вверх: пусковой ток не смотреть
BASE_END_S = 0.35        # вверх: 0.15…0.35 с — ток хода каждой стороны (база)
STALL_K = 1.5            # вверх, упор: ток любой стороны > k × её базы … (22.10.2026: у упора было ×1.7 и не поймали при 1.8;
                         # чистый упор — ×4–7)
STALL_ABS_A = 1.3        # … или > этого тока (А), даже пока база не замерена
HOLD_S = 0.05            # … дольше этого
# Вверх, перескок зуба: у верхнего упора шестерня часто не встаёт, а щёлкает по рейке — сглаженный ток не держится выше
# порога, а бьёт всплесками раз на зуб (≈ 0.08 с при 40 мм/с, до ×1.8–2 базы на 30–40 мс). Журналы 22.10.2026, 6 подъёмов:
# ловит верх за 50–90 мс до жёсткого упора (или там, где упора не было вовсе); на ходу всплески не выше ⅓ порога.
SKIP_N = 3               # среднее по последним 3 сырым замерам (≈ 13 мс) …
SKIP_K = 1.35            # … любой стороны > k × её базы … (22.10.2026: с 1.5 / 0.07 пропустил мягкий подход к верху после
SKIP_DA = 0.05           # … и больше базы на столько (А) …   неглубокого спуска — всплеск правой до 0.20 А при базе 0.14)
SKIP_HOLD_N = 3          # … столько замеров подряд
# Вниз — касание упора глубины по сумме токов обеих сторон. Рыхлый сухой грунт (22.10.2026): остриё входит почти без
# усилия, ток растёт, только когда давит упор глубины (остриё уже ~15 мм в грунте), и плавно (×1.1 → ×1.5 за ~0.3 с);
# ток в воздухе после пуска ещё 0.5 с медленно падает (0.24 → 0.19 А) → база «ползёт» за ним: сумма за последние
# DOWN_NOW_S против суммы DOWN_REF_S назад (журналы 22.10: касание на 0.1 с раньше прежнего, в воздухе — ½ порога).
# Глубину в рыхлом грунте держит ещё и предел хода GROUND_MM + DEPTH_MM (ниже).
DOWN_BLANK_S = 0.3       # пуск не смотреть
DOWN_BASE_END_S = 0.55   # 0.3…0.55 с — ток хода (только для проверки «INA видят ток»)
DOWN_NOW_S = 0.05        # сумма за последние 50 мс …
DOWN_REF_S = (0.30, 0.10)    # … против суммы 0.3…0.1 с назад …
STALL_K_DOWN = 1.10      # … больше в k раз …
DOWN_DA = 0.02           # … и на столько А …
HOLD_DOWN_S = 0.03       # … дольше этого
MIN_BASE_A = 0.05
SAMPLE_S = 0.003         # опрос INA при движении клюва; одно значение INA = 1 замер (аппаратное усреднение у клонов врёт)
AVG_N = 8                # … поэтому среднее по последним AVG_N замерам (≈ 25–30 мс — больше кадра серво 20 мс)
NO_CURRENT_A = 0.03      # ток хода (база) любой стороны меньше — INA не видят ток серво (провода мимо шунта) → стоп
IDLE_SEEN_A = 0.0008     # seeder.py idle: ток электроники серво при «стопе» больше — INA в цепи (22.10.2026: 1.3 мА без
                         # сигнала, 1.9 мА со «стопом»; провода мимо шунта — 0.0 ± 0.2 мА)
HOME_DIP_S = 0.4         # поиск верха: сначала вниз быстро столько (≈ 19 мм), чтобы ток хода вверх мерить на свободном
                         # ходу (из упора база = ток упора, и упор не увидеть — замер 22.10.2026)
FAST_MM_S = 55.0         # скорость клюва вниз при d 270, мм/с. 22.10.2026, цикл в ведро: вниз 1.20 с → 30 мм до грунта + 35
                         # в грунте (замер владельца) ≈ 65 мм; предел 45 мм за 0.87 с — «вошло идеально» (владелец).
                         # (Было 40 — по «32 мм за 0.8 с» на глаз.) SLOW_D = FAST_D — малой скорости отдельно нет.
UP_MM_S = 60.0           # вверх быстрее: 45 мм до первого касания верха за 0.71 с хода (≈ 63), 65 мм — за ~1.1 с (≈ 59)
START_LAG_S = 0.05       # после команды серво трогается не сразу — в оценке положения
# Высота модуля и глубина — по умолчанию; на Pi их задаёт файл SETTINGS_FILE (seeder.py set ground 40 / set dip 10 /
# set depth 15), читается перед каждым посевом — служба подхватывает без перезапуска; ручные команды: --ground= --dip= --depth=
GROUND_MM = 30           # от острия (клюв вверху) до земли на ровном: ведро 22.10.2026 — 30 мм; НА РОБОТЕ — ЗАМЕРИТЬ
DEPTH_MM = 15            # глубина лунки (= упор глубины над остриём): на ровной земле вниз не дальше GROUND_MM + DEPTH_MM —
                         # в рыхлом грунте ток растёт поздно (22.10.2026: стоп по току — остриё на 35 мм в грунте)
GROUND_DIP_MM = 0        # земля может быть ниже ожидаемой на столько (ямка, борозда, модуль выше): клюв ищет грунт по току
                         # ещё до GROUND_MM + DEPTH_MM + GROUND_DIP_MM. Но в РЫХЛОМ сухом грунте ток растёт, только когда упор
                         # глубины уже вдавился, — на ровном лунка выйдет глубже почти на GROUND_DIP_MM. 0 — как в ведре
                         # 22.10 («идеально»); 10 — для плотного / влажного грунта или с упором глубины ≥ 3 см²
RACK_MAX_MM = 63         # вниз не дальше никогда (рейки — ~72 мм хода по модели); GROUND_MM + DEPTH_MM больше — лунка мельче
SETTINGS_FILE = "~/robot/seeder_settings.json"
TOP_MARGIN_S = 0.06      # подъём к верху — не дольше «по расчётному положению + запас»: у верхнего упора шестерни могут
                         # проскакивать по рейке (22.10.2026: треск, ток не растёт — упор не виден) → стоп по времени.
                         # Было 0.2 / 0.12 — треск; расчёт верха сходится с касанием в пределах ±0.03 с (±2 мм) → 0.06 ≈ 3.6 мм
SKIP_ZONE_MM = 10        # перескок зуба искать только в последних 10 мм до верха по расчёту (или когда положение неизвестно):
                         # у правой стороны на ходу волна тока раз в ~0.13 с почти той же высоты (22.10.2026)
POS_FILE = "~/.seeder_pos.json"   # где клюв (мм ниже верха) — общий для службы и ручных команд; старше POS_MAX_AGE_S — не верить.
# Клюв вверху по записи → при запуске сразу «ГОТОВ», без поиска верха (просьба владельца 22.10.2026: лишний ход к упору —
# лишний треск шестерён). Перед первым движением задания в файл пишется «в движении» (pos = None) — если питание пропадёт
# посреди хода, записи не верим и ищем верх, как раньше.
POS_MAX_AGE_S = 30 * 24 * 3600
MIN_BUS_V = 5.0       # перед движением: на «+» серво клюва (VIN− обеих INA) не меньше — иначе 6 В выключены
IDLE_MAX_A = 0.5         # перед движением: ток покоя каждой стороны не больше — иначе INA врёт (без 6 В входы «плавают»)
SHALLOW_MM = 12          # касание раньше GROUND_MM + DEPTH_MM − SHALLOW_MM хода — «мелко» (камень, корень, кочка); сеем с пометкой
UP_SLOW_S = 0.25         # первые ~12 мм вверх медленно
LIFT_PAUSE_S = 0.3       # пауза: острие у поверхности, створки раскрываются над лункой
UP_RESERVE_S = 0.5       # последний участок до верха (в «быстрых» секундах) — медленно, чтобы успеть замерить базу
UP_FINAL_MAX_S = 1.2     # медленный подход к верхнему упору — не дольше
HOME_MAX_S = 2.5         # поиск верха из любого положения (малая скорость, 72 мм хода)
JAM_FRACTION = 0.6       # упор на подъёме, когда по расчёту пройдено меньше 60 % спуска — клин (камень, корень), а не верх

# ---------- без INA219 — по времени ----------
TIME_PUSH_S = 0.6        # дожим вверх на малой скорости в конце подъёма
TIME_HOME_S = 2.0        # поиск верха по времени


# Настройки, которые меняются без правки кода (seeder.py set …): ключ → (по умолчанию, от, до)
SETTINGS = {"ground_mm": (GROUND_MM, 0, 60), "depth_mm": (DEPTH_MM, 3, 30), "dip_mm": (GROUND_DIP_MM, 0, 30)}


def _say(s):
    sys.stdout.write(s + "\n")
    sys.stdout.flush()


class RealClock:
    now = staticmethod(time.monotonic)
    sleep = staticmethod(time.sleep)


class Aborted(Exception):
    pass


class Fault(Exception):
    """Неисправность, найденная во время движения: клюв стоп, текст — в состояние «ОШИБКА: …»."""


class Stall:
    """Упор по току двух INA219: пуск (blank) не смотреть, потом база — ток хода, потом ток > k × базы (или любой стороны
    > STALL_ABS_A) дольше hold. Вверх (по умолчанию) — каждая сторона отдельно, любая; вниз (total=True) — касание:
    сумма двух сторон за DOWN_NOW_S против неё же DOWN_REF_S назад (база ползёт за медленно падающим током хода).
    skip=True — ещё и перескок зуба: всплеск короткого среднего (fast, SKIP_N замеров) любой стороны. kind — чем
    сработал: «упор», «перескок» или «касание»."""

    def __init__(self, k=STALL_K, abs_a=STALL_ABS_A, blank=BLANK_S, base_end=BASE_END_S, hold=HOLD_S, total=False,
                 skip=False):
        self.k, self.abs_a, self.blank, self.base_end, self.hold, self.total = k, abs_a, blank, base_end, hold, total
        self.skip = skip
        self.samples = ([], [])
        self.base = None
        self.raw_base = None      # медиана тока хода каждой стороны без нижней границы — для проверки «INA видят ток»
        self.over_since = None
        self.skip_n = [0, 0]
        self.skip_on = True       # _run: перескок искать только у верха (SKIP_ZONE_MM)
        self.kind = None
        self.hist = deque()       # вниз: (t, сумма сторон) за последние DOWN_REF_S[0] с

    def _rise(self, t, now_sum):
        """Вниз: сумма за последние DOWN_NOW_S выше суммы DOWN_REF_S назад в k раз и на DOWN_DA."""
        self.hist.append((t, now_sum))
        while self.hist[0][0] < t - DOWN_REF_S[0]:
            self.hist.popleft()
        if t - self.blank < DOWN_REF_S[0]:
            return False                             # история ещё короче окна базы
        now = [s for tt, s in self.hist if tt > t - DOWN_NOW_S]
        ref = [s for tt, s in self.hist if tt < t - DOWN_REF_S[1]]
        if not ref:
            return False
        n, r = sum(now) / len(now), sum(ref) / len(ref)
        return n > max(self.k * r, r + DOWN_DA)

    def update(self, t, cur, fast=None):
        if t < self.blank:
            return False
        rise = self._rise(t, sum(fast if fast is not None else cur)) if self.total else False
        if t < self.base_end:
            for s, i in zip(self.samples, cur):
                s.append(i)
            over = any(i > self.abs_a for i in cur)
        else:
            if self.base is None:
                self.raw_base = tuple(statistics.median(s) if s else 0.0 for s in self.samples)
                self.base = tuple(max(b, MIN_BASE_A) for b in self.raw_base)
            if self.total:
                over = rise
            else:
                over = any(i > self.k * b for i, b in zip(cur, self.base))
            over = over or any(i > self.abs_a for i in cur)
            if self.skip and self.skip_on and fast is not None:
                for s, (f, b) in enumerate(zip(fast, self.base)):
                    self.skip_n[s] = self.skip_n[s] + 1 if f > max(SKIP_K * b, b + SKIP_DA) else 0
                if max(self.skip_n) >= SKIP_HOLD_N:
                    self.kind = "упор" if any(f > self.abs_a for f in fast) else "перескок"
                    return True
        if over:
            if self.over_since is None:
                self.over_since = t
            if t - self.over_since >= self.hold:
                self.kind = "касание" if self.total else "упор"
                return True
            return False
        self.over_since = None
        return False


def stall_for(d):
    """Детектор под направление: вниз — касание грунта по росту суммы, вверх — упор или перескок зуба по каждой стороне."""
    if d > 0:
        return Stall(k=STALL_K_DOWN, blank=DOWN_BLANK_S, base_end=DOWN_BASE_END_S, hold=HOLD_DOWN_S, total=True)
    return Stall(skip=True)


def open_inas(bus):
    """(левая, правая) или None, если хоть одна не отвечает."""
    from hw import INA_LEFT_ADDR, INA_RIGHT_ADDR, Ina219
    try:
        return Ina219(bus, INA_LEFT_ADDR), Ina219(bus, INA_RIGHT_ADDR)
    except OSError:
        return None


class Seeder:
    def __init__(self, pca, inas=None, gps=None, cam=None, say=_say, clock=RealClock, shuttle_home=True, pos_file=POS_FILE,
                 settings_file=SETTINGS_FILE):
        self.pca, self.inas, self.gps, self.cam, self.say, self.clock = pca, inas, gps, cam, say, clock
        self.pos_file = pos_file  # None — положение не сохранять (проверки)
        self.mode = "ток" if inas else "время"
        self.state = "НЕ ГОТОВ (верх не проверен)"
        self.ready = False        # клюв на верхнем упоре
        self.jammed = False       # клин на подъёме — езда запрещена до поиска верха / сброса ARM
        self.busy = False
        self.count = 0            # посевов за этот запуск
        self.trace = None         # функция (t, d, сырые токи, средние, 0 / 1 упор / 2 перескок) — запись тока (ручные команды)
        self.pos = self._load_pos()   # мм ниже верхнего упора (оценка по времени × скорость); None — неизвестно
        self._moving = False      # в файле положения уже отмечено «в движении» (до конца задания)
        if self.pos is not None and self.pos <= 0.5:
            self.ready, self.pos = True, 0.0
            self.state = "ГОТОВ (верх — по записи на Pi)"
        self.limited = False      # последний подъём закончился по расчётному положению, а не по току упора
        self.hit_kind = None      # последнее движение кончилось по току: «упор» / «перескок»; None — по времени
        self.hit_pos = None       # … и где был клюв по расчёту (мм ниже верха) в тот момент
        self.settings_file = settings_file    # None — только значения по умолчанию (проверки)
        self.override = {}        # ключи командной строки: ground_mm / depth_mm / dip_mm — поверх файла
        self._load_settings()     # ground_mm, depth_mm, dip_mm
        self._abort = None
        if shuttle_home:
            self._shuttle(HOME_US)

    # ---------- для rc_drive ----------
    def request(self, job):
        """job: 'home' (поиск верха) или 'cycle' (один посев). Не блокирует. False — модуль занят."""
        if self.busy:
            return False
        self.busy, self._abort = True, None
        threading.Thread(target=self._job, args=(job,), name="seeder", daemon=True).start()
        return True

    def abort(self, kind):
        """kind: 'up' — нет сигнала: прервать и поднять клюв; 'stop' — ARM выкл: прервать, ничего не двигать."""
        if self.busy and (kind == "stop" or self._abort is None):
            self._abort = kind

    def blocks_drive(self):
        return self.busy or self.jammed

    def reset_jam(self):
        if self.jammed and not self.busy:
            self.jammed = False
            self.state = "НЕ ГОТОВ (клин сброшен тумблером ARM)"
            self.say("посев: клин сброшен тумблером ARM — езда разрешена, перед посевом — поиск верха")

    def status(self):
        return f"посев: {self.state} [{self.mode}]"

    def close(self):
        self.abort("stop")
        for _ in range(100):
            if not self.busy:
                break
            time.sleep(0.02)

    # ---------- работа (в своём потоке) ----------
    def _job(self, job):
        try:
            try:
                self._home() if job == "home" else self._cycle()
            except Aborted:
                kind, self._abort = self._abort, None
                self._brake()
                self.ready = False
                if kind == "up":
                    self.say("посев: нет сигнала пульта — клюв вверх до упора")
                    try:
                        self._home()
                    except Aborted:
                        self._abort = None
                        self._brake()
                        self.state = "НЕ ГОТОВ (остановлен)"
                else:
                    self.state = "НЕ ГОТОВ (остановлен тумблером ARM)"
                    self.say("посев: остановлен тумблером ARM — клюв стоит где был")
        except Fault as e:
            self._brake()
            self.ready = False
            self.state = f"ОШИБКА: {e}"
            self.say("посев: " + self.state)
        except Exception as e:                       # I2C, INA — клюв стоп, посев до поиска верха нельзя
            try:
                self._brake()
            except Exception:
                pass
            self.ready = False
            self.state = f"ОШИБКА: {e!r}"
            self.say(f"посев: ОШИБКА {e!r} — клюв остановлен")
        finally:
            self._save_pos()
            self.busy = False

    def _load_settings(self):
        """Высота модуля и глубина: по умолчанию GROUND_MM / DEPTH_MM / GROUND_DIP_MM, поверх — файл настроек
        (seeder.py set …), поверх — ключи командной строки. Негодное значение в файле — пропустить (останется прежнее)."""
        import json
        import os
        st = {}
        if self.settings_file:
            try:
                with open(os.path.expanduser(self.settings_file)) as f:
                    st = json.load(f)
            except (OSError, ValueError):
                st = {}
        if not isinstance(st, dict):
            st = {}
        st.update(self.override)
        for key, default in SETTINGS.items():
            try:
                v = float(st.get(key, default[0]))
                if not default[1] <= v <= default[2]:
                    raise ValueError
            except (TypeError, ValueError):
                v = default[0]
            setattr(self, key, v)

    def depth_plan(self):
        """(ожидаемый ход до касания упора глубины на ровном, предел хода) в мм от верха."""
        expect = self.ground_mm + self.depth_mm
        return expect, min(expect + self.dip_mm, RACK_MAX_MM)

    def _load_pos(self):
        import json
        import os
        if not self.pos_file:
            return None
        try:
            with open(os.path.expanduser(self.pos_file)) as f:
                st = json.load(f)
            if time.time() - st["t"] < POS_MAX_AGE_S and st["pos"] is not None:
                return float(st["pos"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def _save_pos(self):
        import json
        import os
        if not self.pos_file:
            return
        try:
            with open(os.path.expanduser(self.pos_file), "w") as f:
                json.dump({"pos": self.pos, "t": time.time()}, f)
        except OSError:
            pass
        self._moving = False

    def _mark_moving(self):
        """Перед первым движением задания: «клюв в движении» — при пропаже питания посреди хода записи не верить."""
        if self._moving or not self.pos_file:
            return
        pos, self.pos = self.pos, None
        self._save_pos()
        self.pos, self._moving = pos, True

    def _check(self):
        if self._abort:
            raise Aborted()

    def _sleep(self, s):
        t_end = self.clock.now() + s
        while True:
            self._check()
            left = t_end - self.clock.now()
            if left <= 1e-4:
                return
            self.clock.sleep(min(0.01, left))

    def _beak(self, d):
        """d > 0 — вниз, d < 0 — вверх, мкс от «стопа»; зеркально на два серво (+ TRIM_CCW_US тому, у кого импульс выше)."""
        dl, dr = DOWN * d, -DOWN * d
        self.pca.set_us(CH_L, N_L + dl + (TRIM_CCW_US if dl > 0 else 0))
        self.pca.set_us(CH_R, N_R + dr + (TRIM_CCW_US if dr > 0 else 0))

    def _hold(self):
        self.pca.set_us(CH_L, N_L)
        self.pca.set_us(CH_R, N_R)

    def _brake(self):
        """«Стоп» командой BRAKE_S (двигатели встают сразу), потом выключить каналы клюва."""
        self._hold()
        self.clock.sleep(BRAKE_S)
        self.pca.off(CH_L)
        self.pca.off(CH_R)

    def _currents(self):
        return self.inas[0].current(), self.inas[1].current()

    def _power_ok(self):
        """Перед движением клюва (режим «ток»): 6 В есть и ток покоя похож на правду. Нет — ошибка, клюв не двигаем:
        без 6 В входы INA «плавают» и показывают амперы — это приняли бы за упор."""
        if not self.inas:
            return True
        u = [ina.voltage() for ina in self.inas]
        i = self._currents()
        if min(u) < MIN_BUS_V:
            self.state = f"ОШИБКА: нет 6 В на серво клюва (INA: {u[0]:.1f} / {u[1]:.1f} В)"
        elif max(abs(x) for x in i) > IDLE_MAX_A:
            self.state = f"ОШИБКА: ток покоя {i[0]:.2f} / {i[1]:.2f} А — проверить INA (VIN±, шунт)"
        else:
            return True
        self.ready = False
        self.say("посев: " + self.state + " — клюв не двигаю")
        return False

    def _run(self, d, t_max, detect=False):
        """Клюв со скоростью d, пока не упор по току (detect) или t_max. -> (упор?, секунд).
        Ведёт оценку положения self.pos; подъём при известном положении — не дольше «до верха + TOP_MARGIN_S»
        (тогда self.limited = True: верх по времени — упор не почувствован, шестерни могли проскочить)."""
        self._mark_moving()
        self._beak(d)
        det = stall_for(d) if detect and self.inas else None
        win = (deque(maxlen=AVG_N), deque(maxlen=AVG_N))
        short = (deque(maxlen=SKIP_N), deque(maxlen=SKIP_N))
        speed = FAST_MM_S if d > 0 else -UP_MM_S
        p0 = self.pos
        self.limited = False
        self.hit_kind = None
        if d < 0 and p0 is not None and START_LAG_S + p0 / -speed + TOP_MARGIN_S < t_max:
            t_max, self.limited = START_LAG_S + p0 / -speed + TOP_MARGIN_S, True
        t0 = self.clock.now()
        while True:
            self._check()
            t = self.clock.now() - t0
            if p0 is not None:
                self.pos = max(0.0, p0 + speed * max(0.0, t - START_LAG_S))
            if t >= t_max:
                return False, t
            if self.inas and (det or self.trace):
                raw = self._currents()
                for w, s, i in zip(win, short, raw):
                    w.append(i)
                    s.append(i)
                avg = tuple(sum(w) / len(w) for w in win)
                if det:
                    det.skip_on = self.pos is None or self.pos <= SKIP_ZONE_MM
                hit = det.update(t, avg, tuple(sum(s) / len(s) for s in short)) if det else False
                if self.trace:
                    self.trace(t, d, raw, avg, (2 if det.kind == "перескок" else 1) if hit else 0)
                if det and det.raw_base is not None and min(det.raw_base) < NO_CURRENT_A:
                    raise Fault(f"INA не видят ток серво при движении (лев {det.raw_base[0]:.3f} / прав "
                                f"{det.raw_base[1]:.3f} А) — проверить провода VIN− → красные провода серво")
                if hit:
                    self._hold()
                    self.limited = False
                    self.hit_kind = det.kind
                    self.hit_pos = self.pos              # где клюв по расчёту в момент упора (для «клина»)
                    if d < 0:
                        self.pos = 0.0               # упор вверху — положение известно точно
                    return True, t
            self.clock.sleep(SAMPLE_S if (det or self.trace) else 0.01)

    def _shuttle(self, us):
        cur = self.pca.get_us(CH_SHUTTLE)
        if cur is None:                              # канал был выключен — сразу, серва доедет сама
            self.pca.set_us(CH_SHUTTLE, us)
            self.clock.sleep(0.5)
            return
        if abs(cur - us) < 3:
            return
        step = SHUTTLE_SPEED * 0.02 * (1 if us > cur else -1)
        while abs(us - cur) > abs(step):
            cur += step
            self.pca.set_us(CH_SHUTTLE, cur)
            self.clock.sleep(0.02)
        self.pca.set_us(CH_SHUTTLE, us)
        self.clock.sleep(SHUTTLE_SETTLE_S)

    def _home(self, dip=True):
        if not self._power_ok():
            return
        self.state = "ВЕРХ: поиск"
        if self.inas:
            if dip:                                      # немного вниз: подъём к упору — со свободного хода
                self._run(+FAST_D, HOME_DIP_S)
                self._hold()
                self._sleep(0.2)
            hit, t = self._run(-SLOW_D, HOME_MAX_S, detect=True)
            self._brake()
            if not hit and self.limited:
                self._top_by_time()
                return
            if not hit:
                self.ready = False
                self.state = f"ОШИБКА: верх не найден за {HOME_MAX_S} с — нет тока упора (зубья? рейка? INA?)"
                self.say("посев: " + self.state)
                return
        else:
            self._run(-SLOW_D, TIME_HOME_S)
            self._brake()
        self._at_top()

    def _at_top(self):
        """Клюв вверху: по току (упор или перескок зуба) или, без INA, по времени."""
        self.ready, self.jammed, self.pos = True, False, 0.0
        self.state = "ГОТОВ (верх — по перескоку зуба)" if self.hit_kind == "перескок" else "ГОТОВ"

    def _jam(self, down_mm):
        """Упор на подъёме, а по расчёту пройдено меньше JAM_FRACTION спуска — клин (камень, корень), а не верх."""
        left = self.hit_pos
        if left is None or left <= (1 - JAM_FRACTION) * down_mm:
            return False
        self.jammed, self.ready, self.pos = True, False, None   # где клюв — неизвестно
        self.state = (f"КЛИН: упор на подъёме, по расчёту ещё ≈ {left:.0f} мм до верха из {down_mm:.0f} — езда запрещена "
                      "(SwC вверх → середина: поиск верха; или ARM выкл → вкл)")
        self.say("посев: " + self.state)
        return True

    def _top_by_time(self):
        """Подъём кончился по расчётному положению: верх (скорее всего) достигнут, но упор по току не почувствован."""
        self.ready, self.jammed, self.pos = True, False, 0.0
        self.state = "ГОТОВ (верх по времени — упор по току не почувствован: шестерни проскакивают по рейке?)"
        self.say("посев: " + self.state)

    def _cycle(self):
        if not self.ready:
            self._home()
            if not self.ready:
                return
        if not self._power_ok():
            return
        self.ready = False
        self.state = "ПОСЕВ: клюв вниз"
        self._load_settings()                                # seeder.py set … — без перезапуска службы
        expect_mm, limit_mm = self.depth_plan()              # глубже limit_mm от верха — никогда (рыхлый грунт, яма)
        t_lim = START_LAG_S + limit_mm / FAST_MM_S
        if self.inas:
            hit, t_down = self._run(+FAST_D, t_lim, detect=True)
            mm = FAST_MM_S * max(0.0, t_down - START_LAG_S)
            if hit and mm < expect_mm - SHALLOW_MM:
                depth = f"мелко: упор через {t_down:.2f} с, ≈ {mm:.0f} мм хода из {expect_mm:.0f} (камень, корень, кочка?)"
            elif hit:
                depth = f"касание через {t_down:.2f} с, ≈ {mm:.0f} мм хода (на ровном ≈ {expect_mm:.0f})"
            elif limit_mm > expect_mm:
                depth = (f"грунт по току не найден до {limit_mm:.0f} мм хода (на ровном ≈ {expect_mm:.0f}): рыхлый грунт "
                         "или ямка")
            else:
                depth = (f"предел глубины: {limit_mm:.0f} мм хода за {t_down:.2f} с — касания по току нет "
                         "(рыхлый грунт? яма?)")
        else:
            _, t_down = self._run(+FAST_D, t_lim)
            depth = f"по времени: {limit_mm:.0f} мм хода за {t_down:.2f} с"
        if expect_mm > RACK_MAX_MM:
            depth += f"; ⚠ модуль высоко: рейки дают {RACK_MAX_MM} мм хода — лунка мельче на ≈ {expect_mm - RACK_MAX_MM:.0f} мм"
        self._hold()
        self._sleep(BRAKE_S)
        self.state = "ПОСЕВ: пауза перед набором"
        self._sleep(DOWN_PAUSE_S)

        self.state = "ПОСЕВ: набор"                      # клюв уже в грунте (порядок — просьба владельца 22.10.2026)
        self._shuttle(FILL_US)
        self._sleep(FILL_WAIT_S)
        self.state = "ПОСЕВ: сброс"
        # ролик — окно seed_cam PRE_S…POST_S от НАЧАЛА сброса: челнок открывает канал к концу хода (~0.4 с), семя летит
        # ~0.15 с → падение в ~0.35–0.65 с после этой точки (раньше отмечали после DROP_WAIT_S — начало могло не попасть)
        clip = self.cam.event() if self.cam else None
        self._shuttle(DROP_US)
        self._sleep(DROP_WAIT_S)
        note = "клюв: " + depth + (f"; видео {clip}" if clip else "")
        if self.gps:
            self.gps.mark("посев", note)
        self.count += 1
        self.say(f"посев №{self.count} за запуск — {note}")

        self.state = "ПОСЕВ: клюв вверх"
        self._run(-SLOW_D, UP_SLOW_S)
        self._hold()
        self._sleep(LIFT_PAUSE_S)
        if not self.inas:
            fast_t = max(0.0, t_down - UP_SLOW_S * SLOW_RATIO - 0.3)
            if fast_t:
                self._run(-FAST_D, fast_t)
            self._run(-SLOW_D, TIME_PUSH_S)
            self._brake()
            self.ready, self.state, self.pos = True, "ГОТОВ", 0.0
            return
        down_mm = FAST_MM_S * max(0.0, t_down - START_LAG_S)
        fast_t = max(0.0, t_down - UP_SLOW_S * SLOW_RATIO - UP_RESERVE_S)
        hit, t1 = self._run(-FAST_D, fast_t, detect=True) if fast_t > 0 else (False, 0.0)
        if hit:
            self._brake()
            if not self._jam(down_mm):
                self._at_top()
            return
        hit, _ = self._run(-SLOW_D, UP_FINAL_MAX_S, detect=True)
        self._brake()
        if hit:
            if not self._jam(down_mm):
                self._at_top()
        elif self.limited:
            self._top_by_time()
        else:
            self.ready = False
            self.state = f"ОШИБКА: верх не найден за {UP_FINAL_MAX_S} с после посева (зубья? рейка?)"
            self.say("посев: " + self.state)


def settings_cmd(args, path=SETTINGS_FILE):
    """seeder.py set — показать; set ground 40 / set dip 10 / set depth 15 — записать; set ground default — убрать."""
    import json
    import os
    names = {"ground": "ground_mm", "depth": "depth_mm", "dip": "dip_mm"}
    p = os.path.expanduser(path)
    try:
        with open(p) as f:
            st = json.load(f)
        if not isinstance(st, dict):
            st = {}
    except (OSError, ValueError):
        st = {}
    if args:
        key = names.get(args[0])
        if key is None or len(args) < 2:
            print("так: seeder.py set ground 40 | set depth 15 | set dip 10 | set ground default")
            return False
        if args[1] == "default":
            st.pop(key, None)
        else:
            v = float(args[1])
            lo, hi = SETTINGS[key][1], SETTINGS[key][2]
            if not lo <= v <= hi:
                print(f"{args[0]}: от {lo} до {hi} мм")
                return False
            st[key] = v
        with open(p, "w") as f:
            json.dump(st, f)
    sd = Seeder(None, settings_file=path, shuttle_home=False, pos_file=None)
    expect, limit = sd.depth_plan()
    print(f"до земли {sd.ground_mm:.0f} мм (ground), лунка {sd.depth_mm:.0f} мм (depth), ямка до +{sd.dip_mm:.0f} мм (dip)"
          f" → на ровном {expect:.0f} мм хода, не дальше {limit:.0f}" + ("" if expect <= RACK_MAX_MM else
          f"; ⚠ рейки дают {RACK_MAX_MM} мм — лунка мельче на {expect - RACK_MAX_MM:.0f}") + f"   [{p}]")
    return True


def main():
    flags = [x for x in sys.argv[1:] if x.startswith("--")]
    a = [x for x in sys.argv[1:] if not x.startswith("--")] or ["status"]
    if a[0] == "set":                                # настройки — без железа; служба подхватит перед следующим посевом
        settings_cmd(a[1:])
        return
    from hw import Pca9685, open_bus
    bus = open_bus()
    pca = Pca9685(bus)
    inas = open_inas(bus)
    if a[0] in ("status", "read"):
        if not inas:
            print("INA219 0x41 / 0x44 не отвечают — режим «по времени»")
        cur = pca.get_us(CH_SHUTTLE)
        print(f"челнок (кан. {CH_SHUTTLE}): " + (f"{cur:.0f} мкс" if cur else "выключен"))
        n = int(float(a[1]) * 10) if a[0] == "read" and len(a) > 1 else 1
        for _ in range(n):
            if inas:
                (l, r) = inas
                print(f"левая {l.current():6.3f} А {l.voltage():5.2f} В   правая {r.current():6.3f} А {r.voltage():5.2f} В",
                      flush=True)
            if n > 1:
                time.sleep(0.1)
        return
    sd = Seeder(pca, inas, say=_say, shuttle_home=False)
    if "--top" in flags:                             # владелец видит: клюв вверху, на упоре
        sd.pos, sd.ready = 0.0, True
        sd.state = "ГОТОВ (верх — со слов оператора)"
    for f in flags:                                  # --ground=40 --dip=10 --depth=15 — только на этот запуск
        name, _, v = f[2:].partition("=")
        if name in ("ground", "depth", "dip") and v:
            sd.override[name + "_mm"] = float(v)
    sd._load_settings()
    expect, limit = sd.depth_plan()
    print(f"режим: {sd.mode}; положение клюва: " + (f"{sd.pos:.0f} мм ниже верха" if sd.pos is not None else "неизвестно")
          + f"; до земли {sd.ground_mm:.0f} мм, лунка {sd.depth_mm:.0f}, ямка до +{sd.dip_mm:.0f} → на ровном {expect:.0f} мм"
          f" хода, не дальше {limit:.0f}")
    rows = []
    if inas:
        sd.trace = lambda t, d, raw, avg, hit: rows.append((t, d, raw[0], raw[1], avg[0], avg[1], int(hit)))
    try:
        if a[0] == "home":
            sd._job("home")
        elif a[0] == "up":
            # вверх медленно до упора по току, без опускания — когда клюв заведомо не в упоре (≥ 25 мм ниже верха)
            sd.busy = True
            try:
                sd._home(dip=False)
            except Fault as e:
                sd._brake()
                sd.state = f"ОШИБКА: {e}"
            finally:
                sd._save_pos()
                sd.busy = False
        elif a[0] == "idle":
            # без движения: «стоп» N_L / N_R (в мёртвой зоне ±200 мкс вал стоит) — электроника серво берёт несколько мА;
            # INA должны это увидеть, если красный провод серво идёт через её шунт (VIN+ → шунт → VIN− → серво)
            def mean_cur(n=60):
                s = []
                for _ in range(n):
                    s.append(sd._currents())
                    time.sleep(0.005)
                return statistics.mean(x[0] for x in s), statistics.mean(x[1] for x in s)
            off = mean_cur()
            sd._hold()
            time.sleep(0.5)
            on = mean_cur()
            sd.pca.off(CH_L)
            sd.pca.off(CH_R)
            for side, i0, i1 in (("левая (кан. 0)", off[0], on[0]), ("правая (кан. 1)", off[1], on[1])):
                print(f"INA {side}: без сигнала {i0 * 1000:6.1f} мА, «стоп» {N_L} мкс {i1 * 1000:6.1f} мА — "
                      + ("ток серво идёт через INA ✓" if i1 > IDLE_SEEN_A else
                         "через INA тока нет ✗ (красный провод серво — на VIN−? шунт между VIN+ и VIN−?)"))
            rows.clear()
        elif a[0] == "jog":
            # немного вниз быстро (без поиска упора), стоп, потом поиск верха — ток хода и упора для порогов
            t_down = min(float(a[1]) if len(a) > 1 else 0.3, 0.8)
            if sd._power_ok():
                sd._run(+FAST_D, t_down)
                sd._brake()
                time.sleep(0.5)
                sd.busy = True
                try:
                    sd._home(dip=False)                  # уже опущен — второй раз не опускать
                except Fault as e:
                    sd._brake()
                    sd.state = f"ОШИБКА: {e}"
                finally:
                    sd._save_pos()
                    sd.busy = False
        elif a[0] == "soft":
            # мягкий подход к верху: вниз HOME_DIP_S (≈ 19 мм), потом вверх с малым d до упора по току (без предела по
            # расчёту). Слушать: есть ли треск у упора, хватает ли силы дойти; ток упора — в сводке и CSV
            d_soft = int(a[1]) if len(a) > 1 else 240
            if not 200 <= d_soft <= FAST_D:
                print(f"d от 200 до {FAST_D} (ниже ~200 серво стоят — мёртвая зона)")
            elif sd._power_ok():
                sd._run(+FAST_D, HOME_DIP_S)
                sd._brake()
                time.sleep(0.5)
                sd.pos = None
                hit, t = sd._run(-d_soft, 2.0, detect=True)
                sd._brake()
                sd.ready = hit
                sd.pos = 0.0 if hit else None
                sd.state = (f"ГОТОВ (мягкий подход d {d_soft}: {sd.hit_kind} через {t:.2f} с)" if hit else
                            f"НЕ ГОТОВ (мягкий подход d {d_soft}: упора по току нет за {t:.2f} с — дошёл ли до верха?)")
                sd._save_pos()
        elif a[0] == "cycle":
            for i in range(int(a[1]) if len(a) > 1 else 1):
                sd._job("cycle")
                print(sd.status())
                if not sd.ready:
                    break
                time.sleep(1)
        else:
            raise SystemExit(__doc__)
    except KeyboardInterrupt:
        sd._brake()
        print("прервано — клюв остановлен")
    print(sd.status())
    if rows:
        report(rows, a[0])


def report(rows, label):
    """Сводка по каждому движению клюва + CSV в ~/robot/ina_logs/ (для порогов STALL_*)."""
    import os
    segs, cur = [], []
    for r in rows:
        if cur and (r[0] < cur[-1][0] or r[1] != cur[-1][1]):
            segs.append(cur)
            cur = []
        cur.append(r)
    segs.append(cur)
    for s in segs:
        d = s[0][1]
        base = [r for r in s if BLANK_S <= r[0] < BASE_END_S]
        bl = statistics.median(r[4] for r in base) if base else float("nan")
        br = statistics.median(r[5] for r in base) if base else float("nan")
        hit = next((r for r in s if r[6]), None)
        print(f"{'вниз' if d * DOWN > 0 else 'вверх'} d={d:+d}: {s[-1][0]:.2f} с; ток хода (0.15–0.35 с) лев {bl:.3f} / прав {br:.3f} А; "
              f"макс лев {max(r[4] for r in s):.3f} / прав {max(r[5] for r in s):.3f} А; "
              + (f"{'перескок зуба' if hit[6] == 2 else 'касание' if d * DOWN > 0 else 'упор'} через {hit[0]:.2f} с"
                 if hit else "упора нет"))
    d = os.path.expanduser("~/robot/ina_logs")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, time.strftime("beak_%Y%m%d_%H%M%S_") + label + ".csv")
    with open(path, "w") as f:
        f.write("t_s;d_us;I_лев_А;I_прав_А;ср_лев_А;ср_прав_А;упор_1_перескок_2\n")
        for r in rows:
            f.write(f"{r[0]:.3f};{r[1]};{r[2]:.4f};{r[3]:.4f};{r[4]:.4f};{r[5]:.4f};{r[6]}\n")
    print(f"журнал тока: {path} ({len(rows)} замеров)")


if __name__ == "__main__":
    main()
