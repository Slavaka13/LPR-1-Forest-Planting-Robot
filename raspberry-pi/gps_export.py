"""Выгрузка журналов GPS (gps_log.py) в GPX / KML / GeoJSON — на той же флешке.

  python3 gps_export.py                 — все сессии в valli_logs на флешке VALLI
  python3 gps_export.py ПАПКА [ПАПКА…]  — только указанные сессии
  python3 gps_export.py --dir           — только напечатать папку журналов (для gps_download.ps1)
В каждой папке сессии появляются posadka.gpx (точки посадки + трек), posadka.kml, posadka.geojson;
в корне журналов — vse_posadki.gpx / .kml / .geojson: все точки посадки всех сессий (без треков) — для телефона.
GPX открывают OsmAnd / Locus / Garmin / QGIS / Google Earth Pro, KML — Google Earth, GeoJSON — QGIS / geojson.io.
На SD-карту Pi ничего не пишется.
"""

import csv
import json
import os
import sys
from xml.sax.saxutils import escape

# папка журналов на флешке — первая существующая (как в gps_log.py: fstab /mnt/valli или автомонтирование)
LOG_DIRS = ("/mnt/valli/valli_logs", "/media/biba/VALLI/valli_logs")
GAP_S = 10   # разрыв трека дольше — новый отрезок


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [r for r in csv.DictReader(f, delimiter=";") if r.get("широта")]


def iso(utc):
    """'2026-10-15 12:34:56.00' -> '2026-10-15T12:34:56.00Z'"""
    return utc.replace(" ", "T") + "Z" if utc else ""


def seconds(utc):
    try:
        h, m, s = utc.split(" ")[1].split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    except (IndexError, ValueError):
        return None


def segments(trek):
    segs, prev = [], None
    for r in trek:
        t = seconds(r["время_utc"])
        if not segs or prev is None or t is None or abs(t - prev) > GAP_S:
            segs.append([])
        segs[-1].append(r)
        prev = t
    return [s for s in segs if len(s) >= 2]


def desc(p):
    return (f"{p.get('модуль', '')}; решение {p.get('решение', '')}; спутников {p.get('спутники', '')}; "
            f"HDOP {p.get('hdop', '')}; усреднено {p.get('точек_усреднено', '')}; {p.get('заметка', '')}").strip("; ")


def gpx(points, segs, title):
    o = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<gpx version="1.1" creator="valli gps_export" xmlns="http://www.topografix.com/GPX/1/1">',
         f"<metadata><name>{escape(title)}</name></metadata>"]
    for p in points:
        o.append(f'<wpt lat="{p["широта"]}" lon="{p["долгота"]}">'
                 + (f'<ele>{p["высота_м"]}</ele>' if p.get("высота_м") else "")
                 + (f'<time>{iso(p["время_utc"])}</time>' if p.get("время_utc") else "")
                 + f'<name>{escape(p["_name"])}</name><desc>{escape(desc(p))}</desc><sym>Flag, Green</sym></wpt>')
    if segs:
        o.append(f"<trk><name>{escape(title)} — трек</name>")
        for s in segs:
            o.append("<trkseg>" + "".join(
                f'<trkpt lat="{r["широта"]}" lon="{r["долгота"]}">'
                + (f'<ele>{r["высота_м"]}</ele>' if r.get("высота_м") else "")
                + (f'<time>{iso(r["время_utc"])}</time>' if r.get("время_utc") else "") + "</trkpt>" for r in s) + "</trkseg>")
        o.append("</trk>")
    o.append("</gpx>")
    return "\n".join(o) + "\n"


def kml(points, segs, title):
    o = ['<?xml version="1.0" encoding="UTF-8"?>', '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
         f"<name>{escape(title)}</name>",
         '<Style id="trek"><LineStyle><color>ff0000ff</color><width>3</width></LineStyle></Style>',
         '<Style id="pt"><IconStyle><color>ff00ff00</color><scale>0.8</scale></IconStyle></Style>']
    for p in points:
        o.append(f'<Placemark><name>{escape(p["_name"])}</name><description>{escape(p.get("время_utc", "") + " — " + desc(p))}'
                 f'</description><styleUrl>#pt</styleUrl><Point><coordinates>{p["долгота"]},{p["широта"]},0</coordinates></Point></Placemark>')
    for i, s in enumerate(segs, 1):
        o.append(f'<Placemark><name>трек {i}</name><styleUrl>#trek</styleUrl><LineString><tessellate>1</tessellate><coordinates>'
                 + " ".join(f'{r["долгота"]},{r["широта"]},0' for r in s) + "</coordinates></LineString></Placemark>")
    o.append("</Document></kml>")
    return "\n".join(o) + "\n"


def geojson(points, segs):
    feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [float(p["долгота"]), float(p["широта"])]},
              "properties": {k: v for k, v in p.items() if k not in ("широта", "долгота")}} for p in points]
    if segs:
        feats.append({"type": "Feature", "properties": {"тип": "трек"}, "geometry": {
            "type": "MultiLineString", "coordinates": [[[float(r["долгота"]), float(r["широта"])] for r in s] for s in segs]}})
    return json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False, indent=1) + "\n"


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def export_session(d):
    name = os.path.basename(os.path.normpath(d))
    points = read_csv(os.path.join(d, "posadka.csv"))
    for p in points:
        p["_name"] = p["n"]   # номер посадки сквозной по всем сессиям на флешке
    segs = segments(read_csv(os.path.join(d, "trek.csv")))
    title = f"Валли, сессия {name}"
    write(os.path.join(d, "posadka.gpx"), gpx(points, segs, title))
    write(os.path.join(d, "posadka.kml"), kml(points, segs, title))
    clean = [{k: v for k, v in p.items() if k != "_name"} | {"имя": p["_name"]} for p in points]
    write(os.path.join(d, "posadka.geojson"), geojson(clean, segs))
    print(f"{name}: точек посадки {len(points)}, отрезков трека {len(segs)}")
    return points


def main():
    log_dir = next((d for d in LOG_DIRS if os.path.isdir(d)), None)
    if sys.argv[1:] == ["--dir"]:
        print(log_dir or "")
        return 0 if log_dir else 1
    if log_dir is None and not sys.argv[1:]:
        print("журналов на флешке нет: " + ", ".join(LOG_DIRS))
        return 1
    dirs = sys.argv[1:] or sorted(os.path.join(log_dir, x) for x in os.listdir(log_dir)
                                  if x[:4].isdigit() and os.path.isdir(os.path.join(log_dir, x)))
    allp = []
    for d in dirs:
        allp += export_session(d)
    if not sys.argv[1:]:
        write(os.path.join(log_dir, "vse_posadki.gpx"), gpx(allp, [], "Валли — все посадки"))
        write(os.path.join(log_dir, "vse_posadki.kml"), kml(allp, [], "Валли — все посадки"))
        clean = [{k: v for k, v in p.items() if k != "_name"} | {"имя": p["_name"]} for p in allp]
        write(os.path.join(log_dir, "vse_posadki.geojson"), geojson(clean, []))
        print(f"все сессии: точек посадки {len(allp)} -> {log_dir}/vse_posadki.*")


if __name__ == "__main__":
    sys.exit(main())
