"""Тест моторов: 1.5 с ноль (включение моторов), скорость SPEED в течение DURATION с, затем стоп.

Запуск: python3 spin_test.py [SPEED] [DURATION]
"""

import sys
import time

from hoverboard import Hoverboard

SPEED = int(sys.argv[1]) if len(sys.argv) > 1 else 100
DURATION = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0


def log(hb, phase, seconds):
    t_end = time.monotonic() + seconds
    while time.monotonic() < t_end:
        time.sleep(0.2)
        fb = hb.feedback
        if fb:
            print(f"{phase:6} cmd1={fb.cmd1:5} cmd2={fb.cmd2:5} speedR={fb.speed_r:5} speedL={fb.speed_l:5} bat={fb.bat_voltage:.2f}")


hb = Hoverboard()
try:
    hb.stop()
    log(hb, "ноль", 1.5)
    hb.drive(SPEED)
    log(hb, "ход", DURATION)
    hb.stop()
    log(hb, "стоп", 1.5)
finally:
    hb.close()
