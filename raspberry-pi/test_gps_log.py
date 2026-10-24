"""Проверка gps_log.py и gps_export.py без GPS и без флешки: подставные строки NMEA, часы и «флешка» во временной папке.

  python3 test_gps_log.py      (на Pi или на ПК; порт и флешка не нужны, на SD ничего не пишется кроме временной папки)
"""

import json
import math
import os
import shutil
import sys
import tempfile
import types
import xml.dom.minidom

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gps_export  # noqa: E402
import gps_log     # noqa: E402


class Clock:
    t = 1000.0


gps_log.time = types.SimpleNamespace(monotonic=lambda: Clock.t, strftime=gps_log.time.strftime, sleep=lambda s: None)


def sentence(body):
    x = 0
    for c in body:
        x ^= ord(c)
    return f"${body}*{x:02X}"


def ddmm(v, lat):
    d = int(abs(v))
    m = (abs(v) - d) * 60
    return (f"{d:02d}{m:08.5f}" if lat else f"{d:03d}{m:08.5f}"), ("N" if v >= 0 else "S") if lat else ("E" if v >= 0 else "W")


def epoch(g, sec, lat, lon, kn, course, q=1):
    t = f"12{30 + sec // 60:02d}{sec % 60:02d}.00"
    if q:
        la, ns = ddmm(lat, True)
        lo, ew = ddmm(lon, False)
        g._line(sentence(f"GNRMC,{t},A,{la},{ns},{lo},{ew},{kn:.3f},{course:.2f},151026,,,A,V"))
        g._line(sentence(f"GNGGA,{t},{la},{ns},{lo},{ew},1,09,1.10,152.3,M,14.5,M,,"))
    else:                                    # как настоящий модуль без решения: пустые поля
        g._line(sentence("GNRMC,,V,,,,,,,,,,N"))
        g._line(sentence("GNGGA,,,,,,0,00,99.99,,,,,,"))
    g._line("$GNGGA,мусор*00")               # строка с неверной суммой — должна пропуститься


def main():
    tmp = tempfile.mkdtemp(prefix="gps_test_")
    ok = True
    try:
        mnt = os.path.join(tmp, "VALLI")
        os.makedirs(mnt)
        flash = {"in": False}
        msgs = []
        g = gps_log.GpsLogger(flash_mounts=(os.path.join(tmp, "nofstab"), mnt),
                              printer=lambda s: (msgs.append(s), print("   ", s)))
        g._mounted = lambda p: flash["in"] and p == mnt
        gps_log.HOLE_FWD_M = gps_log.HOLE_RIGHT_M = 0.0   # первые точки — без поправки (в программе своя)
        g._flush(force=True)                     # «старт» без флешки
        lat, lon = 55.7512345, 37.6178901
        for s in range(20):                      # едем на север 1 м/с (1.944 узла)
            epoch(g, s, lat + s / 111320, lon, 1.944, 0.0)
            if s == 10:
                Clock.t += 0.5
                g.mark("посев")                  # на ходу, между фиксами s=10 и s=11 -> lat + 10.5 м
                Clock.t += 0.5
            else:
                Clock.t += 1
            g._flush_pending()
            g._flush()
        files_before = sum(len(f) for _, _, f in os.walk(tmp))
        in_mem = len(g.buf["posadka.csv"])
        flash["in"] = True                       # флешку «вставили» — накопленное должно записаться
        for s in range(20, 30):                  # стоим, разброс ±0.3 м
            epoch(g, s, lat + 20 / 111320 + (0.3 if s % 2 else -0.3) / 111320, lon, 0.0, 0.0)
            Clock.t += 1
            if s == 24:
                g.mark("посев")                  # на стоянке — среднее за 3 с
            g._flush_pending()
            g._flush()
        gps_log.HOLE_FWD_M = 1.0                 # лунка в 1 м впереди антенны, курс 0 (север) — известен с проезда
        gps_log.HOLE_RIGHT_M = -0.15             # и на 15 см левее (как клюв на роботе) — на север это запад
        for s in range(30, 36):
            epoch(g, s, lat + 20 / 111320, lon, 0.0, 0.0)
            Clock.t += 1
            if s == 32:
                g.mark("посев", "поправка")
            g._flush_pending()
            g._flush()
        for s in range(36, 46):                  # решение пропало на 10 с
            epoch(g, s, 0, 0, 0, 0, q=0)
            Clock.t += 1
            g._flush_pending()
        g.mark("посев")
        Clock.t += 3
        g._flush_pending()
        g._flush(force=True)
        print("    status:", g.status())
        # «перезапуск Pi» посреди заезда: новая сессия на той же флешке, номер продолжается
        g2 = gps_log.GpsLogger(flash_mounts=(mnt,), printer=lambda s: (msgs.append(s), print("   ", s)))
        g2._mounted = lambda p: p == mnt
        g2._flush(force=True)
        g2.mark("посев")
        Clock.t += 3
        g2._flush_pending()
        g2._flush(force=True)
        with open(os.path.join(g2.session_dir, "posadka.csv"), encoding="utf-8-sig") as f:
            rows2 = f.read().splitlines()
        # много семян без флешки — ни одно не теряется (без ограничения в памяти)
        g3 = gps_log.GpsLogger(flash_mounts=(mnt,), printer=lambda s: None)
        g3._mounted = lambda p: False
        for _ in range(13000):
            g3._put("posadka.csv", "x;;;;0;;;0;;;посев;тест")
        kept = len(g3.buf["posadka.csv"])

        sess = g.session_dir or ""
        pos = gps_export.read_csv(os.path.join(sess, "posadka.csv"))
        trek = gps_export.read_csv(os.path.join(sess, "trek.csv"))
        with open(os.path.join(sess, "posadka.csv"), encoding="utf-8-sig") as f:
            rows = f.read().splitlines()
        with open(os.path.join(sess, "nmea.log"), encoding="utf-8") as f:
            nmea = f.read().splitlines()
        outside = [os.path.join(r, x) for r, _, fs in os.walk(tmp) for x in fs if not r.startswith(mnt)]
        print(f"    папка: {sess}")
        for r in rows:
            print("     ", r)
        checks = [
            ("без флешки — файлов нет", files_before == 0),
            ("без флешки — посадка в памяти", in_mem == 1),
            ("папка сессии на флешке", sess.startswith(os.path.join(mnt, "valli_logs"))),
            ("вне флешки файлов нет", not outside),
            ("4 строки посадки", len(rows) - 1 == 4),
            ("3 с координатами", len(pos) == 3),
            ("трек 36 точек (вкл. накопленные без флешки)", len(trek) == 36),
            ("nmea.log записан", len(nmea) >= 46 * 2),
            ("на ходу — между 2 фиксами", pos[0]["точек_усреднено"] == "2"),
            ("на ходу — координата по времени (+10.5 м)", abs(float(pos[0]["широта"]) - (lat + 10.5 / 111320)) < 0.05 / 111320),
            ("номера 1..4 по порядку", [r.split(";")[0] for r in rows[1:]] == ["1", "2", "3", "4"]),
            ("после перезапуска номер продолжается (5)", len(rows2) == 2 and rows2[1].split(";")[0] == "5"),
            ("13 000 посадок без флешки — все в памяти", kept == 13000),
            ("стоянка — среднее 3 точек", pos[1]["точек_усреднено"] == "3"),
            ("среднее ≈ середина (±0.3 м)", abs(float(pos[1]["широта"]) - (lat + 20 / 111320)) < 0.35 / 111320),
            ("поправка: +1 м на север", abs(float(pos[2]["широта"]) - (lat + 21 / 111320)) < 0.05 / 111320),
            ("поправка: 15 см левее = на запад", abs((float(pos[2]["долгота"]) - lon) * 111320 * math.cos(math.radians(lat))
                                                     + 0.15) < 0.02),
            ("время UTC из GPS", pos[0]["время_utc"].startswith("2026-10-15 12:30:")),
            ("без решения — строка без координат", rows[-1].split(";")[2] == ""),
            ("без решения — время Pi", "время Pi" in rows[-1].split(";")[1]),
            ("в журнал координаты не выводятся", not any("55.75" in m or "37.61" in m for m in msgs + [g.status()])),
        ]
        gps_export.LOG_DIRS = (os.path.join(mnt, "valli_logs"),)
        sys.argv = [sys.argv[0]]
        gps_export.main()
        for ext in ("gpx", "kml", "geojson"):
            checks.append((f"posadka.{ext}", os.path.getsize(os.path.join(sess, f"posadka.{ext}")) > 200))
        xml.dom.minidom.parse(os.path.join(sess, "posadka.gpx"))
        xml.dom.minidom.parse(os.path.join(sess, "posadka.kml"))
        with open(os.path.join(sess, "posadka.geojson"), encoding="utf-8") as f:
            checks.append(("geojson: 3 точки + трек", len(json.load(f)["features"]) == 4))
        checks.append(("vse_posadki.gpx на флешке", os.path.exists(os.path.join(mnt, "valli_logs", "vse_posadki.gpx"))))
        for name, res in checks:
            print(f"    {'OK ' if res else 'ОШИБКА'} {name}")
            ok &= bool(res)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("ИТОГ:", "всё в порядке" if ok else "ЕСТЬ ОШИБКИ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
