"""Журнал GPS (NEO-M8N через USB-UART PL2303): точки посадки + трек движения — ТОЛЬКО на флешку в USB Pi.

Работает в своём потоке; езда от GPS не зависит: нет порта, нет координат, нет флешки — только сообщение в журнал.
Модуль шлёт NMEA 9600 бод; берутся $..GGA (координаты, тип решения, спутники, HDOP, высота) и $..RMC (дата, скорость, курс).

На SD-карту Pi координаты не пишутся (решение владельца 15.10.2026). Флешка — том с меткой VALLI, первая смонтированная
точка из FLASH_MOUNTS (/media/biba/VALLI — система монтирует сама ~40 с после загрузки; /mnt/valli — если прописать
в /etc/fstab). Пока флешки нет — строки копятся в памяти (до MAX_BUF строк) и дописываются, когда она появится;
выключить питание без флешки — накопленное пропадёт. В журнал службы координаты тоже не выводятся (журнал на SD).

Файлы сессии (новая папка при каждом запуске, на флешке): <флешка>/valli_logs/NNNN_<дата-время Pi>/
  posadka.csv — точки посадки (mark(): на стоянке среднее за AVG_S с, на ходу — между двумя соседними фиксами по
                времени) — пишется сразу; номер n — СКВОЗНОЙ по всем сессиям на флешке (перезапуск Pi посреди заезда
                продолжает счёт; новый заезд с 1 — очистить valli_logs после выгрузки)
  trek.csv    — трек раз в TRACK_PERIOD с, пока есть решение          — дописывается раз в FLUSH_S с
  nmea.log    — всё, что прислал модуль (для перепроверки)            — дописывается раз в FLUSH_S с
Время в строках — UTC из GPS (у Pi 3B+ нет часов реального времени, поэтому в имени папки — порядковый номер); без
решения — время Pi с пометкой. CSV: разделитель «;», точка в числах, UTF-8 с BOM (Excel покажет кириллицу).
Вынимать флешку — только при выключенной Pi (FAT при выдёргивании портится).

Проверка без робота:  python3 -u gps_log.py          (печатает решение раз в секунду, Ctrl+C — выход)
                      python3 -u gps_log.py --mark   (+ точка посадки через 5 с после старта)
"""

import glob
import math
import os
import queue
import sys
import threading
import time
from collections import deque

FLASH_MOUNTS = ("/mnt/valli", "/media/biba/VALLI")   # точки монтирования флешки VALLI (первая смонтированная)
SUBDIR = "valli_logs"
PORT_GLOB = "/dev/serial/by-id/usb-Prolific*"
BAUD = 9600
TRACK_PERIOD = 1.0      # с между точками трека
AVG_S = 3.0             # с усреднения точки посадки (окно вокруг события)
MOVING_MS = 0.3         # м/с: если робот едет — не усреднять, брать ближайшую точку
FLUSH_S = 5.0           # с между дописываниями трека и NMEA на флешку (посадка — сразу)
# Строк в памяти, пока нет флешки: посадки — без ограничения (каждое семя важно; 12 600 строк ≈ 1.5 МБ),
# трек — сутки при 1 Гц (~9 МБ), сырой NMEA — ~30 мин (старое отбрасывается).
MAX_BUF = {"posadka.csv": None, "trek.csv": 90000, "nmea.log": 20000}
WARN_S = 60.0           # с между напоминаниями «флешки нет»
# Положение лунки относительно антенны, м (вперёд по ходу, вправо; влево / назад — минус). 0, 0 — без поправки.
# Поправка — только если известен курс (робот ехал ≥ COURSE_MS за последние COURSE_AGE_S с), иначе — место антенны.
HOLE_FWD_M = 0.05       # клюв на 5 см впереди антенны   (замер владельца 23.10.2026)
HOLE_RIGHT_M = -0.15    # и на 15 см левее
COURSE_MS = 0.5
COURSE_AGE_S = 30.0

POSADKA_HDR = "n;время_utc;широта;долгота;высота_м;решение;спутники;hdop;точек_усреднено;скорость_мс;поправка_антенны;модуль;заметка"
TREK_HDR = "время_utc;широта;долгота;высота_м;решение;спутники;hdop;скорость_мс;курс_град"
HEADERS = {"posadka.csv": "﻿" + POSADKA_HDR + "\n", "trek.csv": "﻿" + TREK_HDR + "\n", "nmea.log": ""}
FIX_NAMES = {0: "нет", 1: "GPS", 2: "DGPS", 4: "RTK", 5: "RTK float", 6: "счисление"}


def _say(s):
    """Строка в журнал целиком (print из двух потоков склеивает строки)."""
    sys.stdout.write(s + "\n")
    sys.stdout.flush()


def nmea_ok(line):
    """Строка NMEA с верной контрольной суммой -> список полей без '$' и '*hh', иначе None."""
    if not line.startswith("$") or "*" not in line:
        return None
    body, _, cs = line[1:].partition("*")
    try:
        if int(cs[:2], 16) != _xor(body):
            return None
    except ValueError:
        return None
    return body.split(",")


def _xor(s):
    x = 0
    for c in s:
        x ^= ord(c)
    return x


def _deg(v, hemi):
    """ddmm.mmmm / dddmm.mmmm + N/S/E/W -> градусы."""
    if not v:
        return None
    d = int(float(v) / 100)
    val = d + (float(v) - d * 100) / 60
    return -val if hemi in ("S", "W") else val


def _f(v, default=None):
    try:
        return float(v)
    except ValueError:
        return default


def _s(v, nd):
    return "" if v is None else f"{v:.{nd}f}"


class GpsLogger:
    def __init__(self, flash_mounts=FLASH_MOUNTS, port_glob=PORT_GLOB, printer=_say):
        self.flash_mounts = (flash_mounts,) if isinstance(flash_mounts, str) else tuple(flash_mounts)
        self.port_glob, self.say = port_glob, printer
        self.fixes = deque(maxlen=600)     # (monotonic, fix dict) последних ~10 мин при 1 Гц
        self.last = None                   # последний фикс (dict) или None
        self.port_ok = False
        self.events = queue.Queue()
        self.pending = []                  # события посадки, ждущие конца окна усреднения
        self.course = None                 # (monotonic, курс°) последний надёжный курс
        self.n_points = 0                  # посадок за этот запуск
        self.n_total = None                # последний сквозной номер на флешке (None — ещё не прочитан)
        self.session_dir = None            # папка сессии на флешке; None — флешки ещё нет
        self.buf = {name: [] for name in HEADERS}
        self.dropped = 0
        self._stop = threading.Event()
        self._rmc = {}
        self._trek_t = 0.0
        self._flush_t = 0.0
        self._warn_t = -1e9
        self.thread = threading.Thread(target=self._run, name="gps", daemon=True)

    # ---------- для rc_drive ----------
    def start(self):
        self._flush(force=True)            # флешка уже есть — сразу создать папку сессии
        self.thread.start()
        return self

    def mark(self, module="посев", note=""):
        """Отметить посадку (вызывать из основного цикла: не блокирует)."""
        self.events.put((time.monotonic(), module, note))

    def status(self):
        """Короткая строка для журнала службы (без координат)."""
        flash = "" if self.session_dir else "  ФЛЕШКИ НЕТ — не сохраняется!"
        if not self.port_ok:
            return "GPS: нет порта" + flash
        f = self.last
        if f is None or time.monotonic() - f["mono"] > 3:
            return "GPS: нет данных" + flash
        cnt = f"  посадок {self.n_points}" + (f" (всего на флешке {self.n_total})" if self.n_total is not None else "")
        if f["q"] == 0:
            return f"GPS: нет решения, спутников {f['sats']}{cnt}" + flash
        return f"GPS: {FIX_NAMES.get(f['q'], f['q'])} спутников {f['sats']} HDOP {f['hdop']:.1f}{cnt}" + flash

    def stop(self):
        self._stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=3)
        self._flush_pending(force=True)
        self._flush(force=True)

    # ---------- флешка ----------
    def _mounted(self, path):
        return os.path.ismount(path)

    def _flash(self):
        for m in self.flash_mounts:
            if self._mounted(m):
                return m
        return None

    @staticmethod
    def _last_number(base):
        """Наибольший номер посадки во всех сессиях на флешке (сквозной счёт)."""
        best = 0
        for d in os.listdir(base):
            p = os.path.join(base, d, "posadka.csv")
            if d[:4].isdigit() and os.path.isfile(p):
                with open(p, encoding="utf-8-sig", errors="replace") as f:
                    for line in f:
                        head = line.split(";", 1)[0]
                        if head.isdigit():
                            best = max(best, int(head))
        return best

    def _open_session(self, mount):
        base = os.path.join(mount, SUBDIR)
        os.makedirs(base, exist_ok=True)
        self.n_total = self._last_number(base)
        nums = [int(d[:4]) for d in os.listdir(base) if d[:4].isdigit()]
        path = os.path.join(base, f"{max(nums, default=0) + 1:04d}_{time.strftime('%Y-%m-%d_%H%M')}")
        os.makedirs(path)
        for name, hdr in HEADERS.items():
            if hdr:
                self._append(os.path.join(path, name), hdr)
        self.session_dir = path
        self.say(f"GPS: журнал на флешке {path} (посадок на флешке до этого: {self.n_total})")

    @staticmethod
    def _append(path, text):
        with open(path, "a", encoding="utf-8", errors="replace") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())

    def _put(self, name, line, now=False):
        """posadka.csv: line — строка БЕЗ номера (номер сквозной, ставится при записи на флешку)."""
        self.buf[name].append(line)
        cap = MAX_BUF.get(name)
        if cap and len(self.buf[name]) > cap:
            del self.buf[name][: len(self.buf[name]) - cap]
            self.dropped += 1
        if now:
            self._flush(force=True)

    def _flush(self, force=False):
        """Дописать накопленное на флешку: раз в FLUSH_S с или сразу (force)."""
        t = time.monotonic()
        if not force and t - self._flush_t < FLUSH_S:
            return
        self._flush_t = t
        if not any(self.buf.values()) and self.session_dir:
            return
        try:
            if self.session_dir and not os.path.isdir(self.session_dir):
                self.session_dir = None            # флешку вынули / перемонтировали
            if self.session_dir is None:
                mount = self._flash()
                if mount is None:
                    if t - self._warn_t >= WARN_S:
                        self._warn_t = t
                        n = sum(len(v) for v in self.buf.values())
                        self.say(f"GPS: ФЛЕШКИ НЕТ ({', '.join(self.flash_mounts)}) — координаты не сохраняются, в памяти строк {n}")
                    return
                self._open_session(mount)
            for name, lines in self.buf.items():
                if not lines:
                    continue
                if name == "posadka.csv":          # сквозной номер: счётчик двигаем только после удачной записи
                    out = [f"{self.n_total + i + 1};{x}" for i, x in enumerate(lines)]
                    self._append(os.path.join(self.session_dir, name), "\n".join(out) + "\n")
                    self.n_total += len(lines)
                else:
                    self._append(os.path.join(self.session_dir, name), "\n".join(lines) + "\n")
                lines.clear()
        except OSError as e:
            self.session_dir = None
            if t - self._warn_t >= WARN_S:
                self._warn_t = t
                self.say(f"GPS: ошибка записи на флешку: {e} — данные в памяти, повтор")

    # ---------- чтение ----------
    def _run(self):
        import serial
        ser = None
        said_noport = False
        while not self._stop.is_set():
            try:
                if ser is None:
                    ports = sorted(glob.glob(self.port_glob))
                    if not ports:
                        if self.port_ok or not said_noport:
                            self.say(f"GPS: порт {self.port_glob} не найден — жду")
                            said_noport = True
                        self.port_ok = False
                        self._stop.wait(5)
                        self._flush_pending()
                        self._flush()
                        continue
                    ser = serial.Serial(ports[0], BAUD, timeout=1)
                    self.port_ok = True
                    self.say(f"GPS: порт {ports[0]}")
                raw = ser.readline()
                try:
                    if raw:
                        self._line(raw.decode("ascii", "replace").strip())
                    self._flush_pending()
                    self._flush()
                except Exception as e:             # ошибка разбора/записи не должна останавливать поток
                    self.say(f"GPS: ошибка обработки: {e!r}")
                    self._stop.wait(1)
            except (OSError, ValueError) as e:     # serial.SerialException — подкласс OSError
                self.say(f"GPS: ошибка порта: {e} — переподключение")
                self.port_ok = False
                try:
                    ser and ser.close()
                except Exception:
                    pass
                ser = None
                self._stop.wait(2)
        if ser:
            ser.close()

    def _line(self, line):
        if line.startswith("$"):
            self._put("nmea.log", line)
        f = nmea_ok(line)
        if not f or len(f[0]) < 5:
            return
        kind = f[0][-3:]
        if kind == "RMC" and len(f) >= 10:
            self._rmc = {"time": f[1], "date": f[9], "valid": f[2] == "A",
                         "speed": _f(f[7], 0.0) * 0.514444, "course": _f(f[8])}
        elif kind == "GGA" and len(f) >= 10:
            self._gga(f)

    def _gga(self, f):
        now = time.monotonic()
        q = int(f[6] or 0)
        rmc = self._rmc if self._rmc.get("time") == f[1] else {}
        utc = ""
        if rmc.get("date") and len(f[1]) >= 6:
            d, t = rmc["date"], f[1]
            utc = f"20{d[4:6]}-{d[2:4]}-{d[0:2]} {t[0:2]}:{t[2:4]}:{t[4:]}"
        fix = {"mono": now, "utc": utc, "q": q, "sats": int(f[7] or 0), "hdop": _f(f[8], 99.0),
               "lat": _deg(f[2], f[3]), "lon": _deg(f[4], f[5]), "alt": _f(f[9]),
               "speed": rmc.get("speed"), "course": rmc.get("course")}
        self.last = fix
        if q == 0 or fix["lat"] is None:
            return
        self.fixes.append((now, fix))
        if fix["speed"] is not None and fix["speed"] >= COURSE_MS and fix["course"] is not None:
            self.course = (now, fix["course"])
        if now - self._trek_t >= TRACK_PERIOD - 0.05:
            self._trek_t = now
            self._put("trek.csv", ";".join([utc, f"{fix['lat']:.7f}", f"{fix['lon']:.7f}", _s(fix["alt"], 1), str(q),
                                            str(fix["sats"]), f"{fix['hdop']:.1f}", _s(fix["speed"], 2), _s(fix["course"], 1)]))

    # ---------- точки посадки ----------
    def _flush_pending(self, force=False):
        while True:
            try:
                self.pending.append(self.events.get_nowait())
            except queue.Empty:
                break
        now = time.monotonic()
        keep = []
        for ev in self.pending:
            if force or now >= ev[0] + AVG_S / 2 + 0.5:
                self._point(*ev)
            else:
                keep.append(ev)
        self.pending = keep

    def _point(self, t_ev, module, note):
        win = [fx for t, fx in self.fixes if abs(t - t_ev) <= AVG_S / 2]
        self.n_points += 1
        if not win:   # нет решения — посадку всё равно записать (счёт семян), без координат; время — GPS или Pi
            utc = (self.last or {}).get("utc") or time.strftime("%Y-%m-%d %H:%M:%S (время Pi)")
            line = ";".join([utc, "", "", "", "0",
                             str(self.last["sats"]) if self.last else "", "", "0", "", "", module, note or "нет решения GPS"])
            used = 0
        else:
            speeds = sorted(fx["speed"] for fx in win if fx["speed"] is not None)
            moving = speeds and speeds[len(speeds) // 2] > MOVING_MS
            near = min(win, key=lambda fx: abs(fx["mono"] - t_ev))
            before = [fx for fx in win if fx["mono"] <= t_ev]
            after = [fx for fx in win if fx["mono"] > t_ev]
            if moving and before and after:      # на ходу — между соседними фиксами по времени события
                a, b = before[-1], after[0]
                k = (t_ev - a["mono"]) / (b["mono"] - a["mono"])
                lat = a["lat"] + (b["lat"] - a["lat"]) * k
                lon = a["lon"] + (b["lon"] - a["lon"]) * k
                use = [a, b]
            else:                                # стоит — среднее за окно; на ходу у края данных — ближайшая
                use = [near] if moving else win
                lat = sum(fx["lat"] for fx in use) / len(use)
                lon = sum(fx["lon"] for fx in use) / len(use)
            alts = [fx["alt"] for fx in use if fx["alt"] is not None]
            corr = "нет"
            if (HOLE_FWD_M or HOLE_RIGHT_M) and self.course and t_ev - self.course[0] <= COURSE_AGE_S:
                c = math.radians(self.course[1])
                north = HOLE_FWD_M * math.cos(c) - HOLE_RIGHT_M * math.sin(c)
                east = HOLE_FWD_M * math.sin(c) + HOLE_RIGHT_M * math.cos(c)
                lat += north / 111320.0
                lon += east / (111320.0 * math.cos(math.radians(lat)))
                corr = f"да, курс {self.course[1]:.0f}"
            line = ";".join([near["utc"], f"{lat:.7f}", f"{lon:.7f}",
                             _s(sum(alts) / len(alts) if alts else None, 1), str(near["q"]), str(near["sats"]),
                             f"{near['hdop']:.1f}", str(len(use)), _s(near["speed"], 2), corr, module, note])
            used = len(use)
        self._put("posadka.csv", line, now=True)       # номер — сквозной, при записи на флешку
        if self.session_dir and not self.buf["posadka.csv"]:
            where = f"на флешку под №{self.n_total}"
        else:
            where = f"в память (ФЛЕШКИ НЕТ, в очереди {len(self.buf['posadka.csv'])})"
        self.say(f"GPS: посадка {self.n_points} за запуск записана {where}: "
                 + ("без координат" if not win else f"точек {used}" + (" (на ходу, между фиксами)" if moving and used == 2 else "")))


if __name__ == "__main__":
    g = GpsLogger().start()
    t0 = time.monotonic()
    marked = "--mark" not in sys.argv
    try:
        while True:
            time.sleep(1)
            print(g.status())
            if not marked and time.monotonic() - t0 > 5:
                g.mark("тест", "gps_log.py --mark")
                marked = True
    except KeyboardInterrupt:
        pass
    finally:
        g.stop()
