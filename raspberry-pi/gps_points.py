"""Просмотр точек GPS на флешке VALLI (только чтение): текущая сессия, последние посадки, трек, состояние GPS.

  python3 gps_points.py          — последние 100 точек посадки текущей (последней) сессии + последняя точка трека
  python3 gps_points.py 500      — последние 500 точек;  0 — все точки сессии
  python3 gps_points.py -f       — следить: новые посадки сразу, раз в 10 с — трек и состояние GPS (Ctrl+C — выход)
  python3 gps_points.py --all    — сводка по всем сессиям на флешке
С ПК одной строкой: инструменты\\gps_points.ps1 [-N 500 | -N 0] [-Follow] [-All]   (пульт выключить — глушит Wi-Fi Pi)
Порт GPS не открывает (его читает служба rc-drive), пишет только на экран.
"""

import csv
import os
import subprocess
import sys
import time

LOG_DIRS = ("/mnt/valli/valli_logs", "/media/biba/VALLI/valli_logs")   # как в gps_log.py


def log_dir():
    return next((d for d in LOG_DIRS if os.path.isdir(d)), None)


def sessions(base):
    return sorted(d for d in os.listdir(base) if d[:4].isdigit() and os.path.isdir(os.path.join(base, d)))


def rows(path):
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f, delimiter=";"))
    except OSError:
        return []


def gps_status():
    """Последнее «GPS: …» из журнала службы rc-drive (там без координат)."""
    try:
        out = subprocess.run(["journalctl", "_SYSTEMD_USER_UNIT=rc-drive.service", "-n", "40", "--no-pager", "-o", "cat"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return "журнал службы недоступен"
    for line in reversed(out.splitlines()):
        i = line.find("GPS:")
        if i >= 0:
            return line[i:]
    return "в журнале службы нет строк GPS (служба не запущена?)"


def link(r):
    return f"https://maps.google.com/?q={r['широта']},{r['долгота']}" if r.get("широта") else ""


def point_line(r):
    if not r.get("широта"):
        return f"{r['n']:>4}  {r['время_utc'][:19]:19}  {'— нет координат —':^25}  {r.get('заметка', '')}"
    return (f"{r['n']:>4}  {r['время_utc'][:19]:19}  {float(r['широта']):11.7f} {float(r['долгота']):12.7f}  "
            f"сп {r['спутники']:>2}  HDOP {r['hdop']:>4}  усредн. {r['точек_усреднено']:>2}  {r['модуль']} {r.get('заметка', '')}".rstrip())


def trek_line(trek):
    if not trek:
        return "трек: точек нет (пока нет решения GPS)"
    t = trek[-1]
    return (f"трек: точек {len(trek)}, последняя {t['время_utc'][:19]}  {float(t['широта']):.7f} {float(t['долгота']):.7f}"
            f"  скорость {t['скорость_мс'] or '?'} м/с  сп {t['спутники']}  HDOP {t['hdop']}")


def show(base, sess, n):
    d = os.path.join(base, sess)
    pts = rows(os.path.join(d, "posadka.csv"))
    trek = rows(os.path.join(d, "trek.csv"))
    with_xy = [r for r in pts if r.get("широта")]
    print(f"сессия {sess}  ({d})")
    print(gps_status())
    last_n = max((int(r["n"]) for r in pts if r["n"].isdigit()), default=0)
    print(f"посадок в сессии: {len(pts)}, с координатами {len(with_xy)}, без {len(pts) - len(with_xy)};"
          f" последний сквозной № {last_n}" + (f" (показаны последние {n})" if n and len(pts) > n else ""))
    if pts:
        print(f"{'№':>4}  {'время UTC':19}  {'широта':>11} {'долгота':>12}")
        for r in (pts[-n:] if n else pts):
            print(point_line(r))
    print(trek_line(trek))
    if with_xy:
        print("последняя посадка на карте:", link(with_xy[-1]))


def follow(base):
    sess, seen, t_stat = None, 0, 0.0
    while True:
        cur = (sessions(base) or [None])[-1]
        if cur != sess:
            sess, seen = cur, 0
            print(f"\n=== сессия {sess} ===" if sess else "\nсессий нет — жду")
        if sess:
            pts = rows(os.path.join(base, sess, "posadka.csv"))
            for r in pts[seen:]:
                print("посадка", point_line(r), link(r))
            seen = len(pts)
            if time.monotonic() - t_stat >= 10:
                t_stat = time.monotonic()
                print(time.strftime("%H:%M:%S"), "|", gps_status(), "|", trek_line(rows(os.path.join(base, sess, "trek.csv"))))
        sys.stdout.flush()
        time.sleep(2)


def summary(base):
    print(f"{'сессия':28} {'посадок':>8} {'с коорд.':>9} {'трек':>7}")
    tot = [0, 0, 0]
    for s in sessions(base):
        pts = rows(os.path.join(base, s, "posadka.csv"))
        xy = sum(1 for r in pts if r.get("широта"))
        tr = len(rows(os.path.join(base, s, "trek.csv")))
        print(f"{s:28} {len(pts):>8} {xy:>9} {tr:>7}")
        tot = [tot[0] + len(pts), tot[1] + xy, tot[2] + tr]
    print(f"{'ВСЕГО':28} {tot[0]:>8} {tot[1]:>9} {tot[2]:>7}")


def main():
    base = log_dir()
    if not base:
        print("флешки VALLI с журналами нет: " + ", ".join(LOG_DIRS))
        return 1
    args = sys.argv[1:]
    if "--all" in args:
        summary(base)
        return 0
    if "-f" in args:
        try:
            follow(base)
        except KeyboardInterrupt:
            return 0
    ss = sessions(base)
    if not ss:
        print(f"в {base} сессий нет")
        return 1
    n = next((int(a) for a in args if a.isdigit()), 100)
    show(base, ss[-1], n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
