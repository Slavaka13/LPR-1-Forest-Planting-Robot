"""Прогон журналов тока клюва (~/robot/ina_logs/beak_*.csv) через нынешний детектор seeder.stall_for():
где сработал бы упор / перескок зуба / касание, а где тогда сработало. Для подбора порогов STALL_*, SKIP_* без движения клюва.

  scp biba@192.168.31.81:robot/ina_logs/beak_20261022_062637_cycle.csv <латинская папка>
  & "$env:USERPROFILE\\.platformio\\penv\\Scripts\\python.exe" инструменты\\beak_replay.py <файлы.csv> [--show N]

--show N — ещё распечатать движение N (время, сырые и средние токи, сумма) — посмотреть рост тока глазами.
Колонки CSV: t; d; сырой лев; сырой прав; среднее лев; среднее прав; 0 / 1 упор / 2 перескок.
"""

import os
import sys
from collections import deque

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "raspberry"))
import seeder as sm  # noqa: E402


def segments(path):
    """Движения клюва: время в журнале начинается с 0 у каждого."""
    segs, prev = [[]], -1.0
    for line in list(open(path, encoding="utf-8"))[1:]:
        x = [float(v) for v in line.strip().split(";")]
        if x[0] < prev:
            segs.append([])
        prev = x[0]
        segs[-1].append(x)
    return segs


def main():
    args = sys.argv[1:]
    show = None
    if "--show" in args:
        k = args.index("--show")
        show = int(args[k + 1])
        del args[k:k + 2]
    for path in args:
        for i, xs in enumerate(segments(path)):
            d = int(xs[0][1])
            det = sm.stall_for(d)
            win = (deque(maxlen=sm.AVG_N), deque(maxlen=sm.AVG_N))
            short = (deque(maxlen=sm.SKIP_N), deque(maxlen=sm.SKIP_N))
            res = "нет"
            for x in xs:
                for w, s, v in zip(win, short, (x[2], x[3])):
                    w.append(v)
                    s.append(v)
                avg = tuple(sum(w) / len(w) for w in win)
                if det.update(x[0], avg, tuple(sum(s) / len(s) for s in short)):
                    res = f"{det.kind} через {x[0]:.3f} с"
                    break
            was = next((f"{'перескок' if r[6] == 2 else 'упор'} {r[0]:.3f}" for r in xs if r[6]), "-")
            print(f"{os.path.basename(path)} #{i} {'вниз' if d * sm.DOWN > 0 else 'вверх'} d={d:+d} ({xs[-1][0]:.2f} с): "
                  f"сейчас — {res}; тогда — {was}")
            if i == show:
                for x in xs:
                    print(f"   {x[0]:5.3f}  сырой {x[2]:6.3f} {x[3]:6.3f}  ср {x[4]:6.3f} {x[5]:6.3f}  "
                          f"сумма {x[4] + x[5]:6.3f}  {'*' if x[6] else ''}")


if __name__ == "__main__":
    main()
