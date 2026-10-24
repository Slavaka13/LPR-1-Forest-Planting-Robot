"""Камера в Pi (Logitech C270 / C170, USB) — ролик падения семени на каждый посев, на флешку VALLI.

Как: ffmpeg непрерывно пишет с камеры кусками по SEG_S с (MJPEG как есть, без пережатия — процессор почти не занят)
в кольцо из SEG_WRAP файлов в памяти (/dev/shm/seedcam). На посев (сброс семени, seeder.py) через POST_S берутся куски
от PRE_S до POST_S вокруг сброса и склеиваются (тоже без пережатия) в один ролик на флешку:
  <флешка>/valli_logs/<папка сессии GPS>/seedcam/posev_ЧЧММСС_NNN.mkv — имя ролика пишется в заметку точки посадки.
Нет камеры / флешки — посев идёт как обычно, в журнале сообщение. Камеру включать в USB только при выключенной Pi.

Распознавание «семя упало» — позже, по роликам: на Pi нет python3-opencv (ставит владелец: sudo apt install
python3-opencv). Если он есть — каждый ролик проверяется analyze() и результат пишется в журнал (пока только для
подбора порогов, на посев не влияет). Подсветка «на просвет» и выдержка ~1 мс — «видео/камера Pi для посева.jpg».

  python3 -u seed_cam.py info              # какая камера найдена, её режимы и настройки (v4l2-ctl)
  python3 -u seed_cam.py test 5            # записать 5 с (на флешку в valli_logs/seedcam, без флешки — ~/robot/)
  python3 -u seed_cam.py analyze файл.mkv  # проверить ролик (нужен OpenCV)
"""

import glob
import os
import subprocess
import sys
import threading
import time

DEV_GLOBS = ("/dev/v4l/by-id/usb-*Logitech*-video-index0", "/dev/v4l/by-id/usb-*-video-index0")
SIZE = "640x480"
FPS = 30
INPUT_FORMAT = "mjpeg"   # C270 / C170 отдают MJPEG — пишем как есть
EXPOSURE_100US = 10      # ручная выдержка в единицах 100 мкс (10 = 1 мс); None — автоматическая
RING_DIR = "/dev/shm/seedcam"
SEG_S = 1                # длина куска, с
SEG_WRAP = 12            # кусков в кольце (12 с в памяти, ~3–4 МБ)
PRE_S = 1.0              # сохранить до сброса, с
POST_S = 1.5             # и после сброса (семя падает 0.11–0.16 с — с запасом на задержки)
RETRY_S = 10.0           # нет камеры / ffmpeg упал — пробовать снова через столько секунд
FLASH_MOUNTS = ("/mnt/valli", "/media/biba/VALLI")   # как в gps_log.py
# analyze (OpenCV) — пороги подобрать по своим роликам
DIFF_T = 40              # яркость: пиксель «изменился», если отличается от фона больше
AREA_MIN, AREA_MAX = 6, 400   # площадь пятна-семени, пикселей (в кадре 320×240)


def _say(s):
    sys.stdout.write(s + "\n")
    sys.stdout.flush()


def find_camera():
    for g in DEV_GLOBS:
        devs = sorted(glob.glob(g))
        if devs:
            return devs[0]
    return None


def default_out_dir():
    """Папка роликов без GPS-сессии: <флешка>/valli_logs/seedcam; None — флешки нет."""
    for m in FLASH_MOUNTS:
        if os.path.ismount(m):
            return os.path.join(m, "valli_logs", "seedcam")
    return None


def set_controls(dev):
    """Ручная выдержка: имена настроек в новых ядрах и в старых — пробуем оба, ошибки не важны."""
    if EXPOSURE_100US is None:
        return
    for ctl in (f"auto_exposure=1,exposure_time_absolute={EXPOSURE_100US}",
                f"exposure_auto=1,exposure_absolute={EXPOSURE_100US}"):
        try:
            if subprocess.run(["v4l2-ctl", "-d", dev, "-c", ctl], capture_output=True, timeout=5).returncode == 0:
                return
        except (OSError, subprocess.SubprocessError):
            return


class SeedCam:
    def __init__(self, out_dir_fn=default_out_dir, say=_say):
        self.out_dir_fn, self.say = out_dir_fn, say
        self.proc = None
        self.dev = None
        self.n = 0
        self.saved = 0
        self._stop = threading.Event()
        self._warned = False
        self.thread = threading.Thread(target=self._watch, name="seedcam", daemon=True)

    # ---------- для rc_drive / seeder ----------
    def start(self):
        self.thread.start()
        return self

    def recording(self):
        return self.proc is not None and self.proc.poll() is None

    def event(self):
        """Сброс семени — сейчас. -> имя ролика, который сохранится через ~3 с (None — камера не пишет)."""
        if not self.recording():
            return None
        self.n += 1
        name = time.strftime("posev_%H%M%S") + f"_{self.n:03d}.mkv"
        threading.Thread(target=self._save, args=(time.time(), name), daemon=True).start()
        return name

    def status(self):
        return f"камера Pi: пишет, роликов {self.saved}" if self.recording() else "камера Pi: нет"

    def stop(self):
        self._stop.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    # ---------- внутри ----------
    def _watch(self):
        while not self._stop.is_set():
            if not self.recording():
                if self.proc is not None:
                    try:
                        with open(os.path.join(RING_DIR, "ffmpeg.log"), errors="replace") as f:
                            err = f.read().strip().splitlines()[-1:]
                    except OSError:
                        err = []
                    self.say(f"камера Pi: запись остановилась {err} — перезапуск через {RETRY_S:.0f} с")
                    self.proc = None
                    self._stop.wait(RETRY_S)
                    continue
                dev = find_camera()
                if dev is None:
                    if not self._warned:
                        self.say("камера Pi: не найдена (/dev/v4l/by-id/…) — посев без роликов")
                        self._warned = True
                    self._stop.wait(RETRY_S)
                    continue
                self._start(dev)
            self._stop.wait(1.0)

    def _start(self, dev):
        os.makedirs(RING_DIR, exist_ok=True)
        for p in glob.glob(os.path.join(RING_DIR, "seg*.mkv")):
            os.remove(p)
        set_controls(dev)
        cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "v4l2", "-input_format", INPUT_FORMAT,
               "-video_size", SIZE, "-framerate", str(FPS), "-i", dev, "-c", "copy",
               "-f", "segment", "-segment_time", str(SEG_S), "-segment_wrap", str(SEG_WRAP), "-reset_timestamps", "1",
               os.path.join(RING_DIR, "seg%02d.mkv")]
        try:
            with open(os.path.join(RING_DIR, "ffmpeg.log"), "wb") as log:   # не PIPE: непрочитанная труба повесит ffmpeg
                self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log)
        except OSError as e:
            self.say(f"камера Pi: ffmpeg не запустился ({e})")
            self._stop.wait(RETRY_S)
            return
        self.dev, self._warned = dev, False
        self.say(f"камера Pi: пишет {os.path.basename(dev)} {SIZE} {FPS} к/с в кольцо {SEG_WRAP} с")

    def _save(self, t, name):
        time.sleep(POST_S + SEG_S + 0.3)              # дождаться, пока закроется кусок с концом окна
        segs = pick_segments(t)
        if not segs:
            self.say(f"камера Pi: для {name} нет кадров")
            return
        out_dir = self.out_dir_fn()
        if not out_dir:
            self.say(f"камера Pi: флешки нет — ролик {name} не сохранён")
            return
        try:
            os.makedirs(out_dir, exist_ok=True)
            lst = os.path.join(RING_DIR, f"list_{self.n}.txt")
            with open(lst, "w") as f:
                for p in segs:
                    f.write(f"file '{p}'\n")
            out = os.path.join(out_dir, name)
            r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                                "-i", lst, "-c", "copy", out], capture_output=True, timeout=30)
            os.remove(lst)
            if r.returncode != 0:
                self.say(f"камера Pi: ролик {name} не склеился: {r.stderr.decode(errors='replace')[-200:]}")
                return
            self.saved += 1
        except (OSError, subprocess.SubprocessError) as e:
            self.say(f"камера Pi: ролик {name} не сохранён ({e})")
            return
        res = analyze(out)
        if res:
            self.say(f"камера Pi: {name} — кадров {res['frames']}, с пятном {res['hits']} (пороги не подобраны)")


def pick_segments(t, ring_dir=RING_DIR):
    """Куски кольца, перекрывающие окно [t − PRE_S, t + POST_S], по порядку. Время конца куска — время изменения файла
    (кусок — [m − SEG_S, m]); кусок, который пишется сейчас, сюда не попадает: он начался позже окна."""
    segs = []
    for p in glob.glob(os.path.join(ring_dir, "seg*.mkv")):
        try:
            m = os.path.getmtime(p)
        except OSError:
            continue
        if t - PRE_S <= m and m - SEG_S <= t + POST_S:
            segs.append((m, p))
    return [p for _, p in sorted(segs)]


def analyze(path):
    """Грубо: фон — медиана кадров, «семя» — пятно площадью AREA_MIN…AREA_MAX. None — нет OpenCV / ролик не читается."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    cap = cv2.VideoCapture(path)
    frames = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(cv2.cvtColor(cv2.resize(f, (320, 240)), cv2.COLOR_BGR2GRAY))
    cap.release()
    if len(frames) < 5:
        return None
    bg = np.median(np.stack(frames), axis=0).astype(np.uint8)
    hits = 0
    for g in frames:
        mask = (cv2.absdiff(g, bg) > DIFF_T).astype(np.uint8)
        n, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        if any(AREA_MIN <= stats[i, cv2.CC_STAT_AREA] <= AREA_MAX for i in range(1, n)):
            hits += 1
    return {"frames": len(frames), "hits": hits}


def main():
    a = sys.argv[1:] or ["info"]
    if a[0] == "info":
        dev = find_camera()
        print("камера:", dev or "не найдена (ls -l /dev/v4l/by-id/)")
        if dev:
            for args in (["--list-formats-ext"], ["--list-ctrls"]):
                subprocess.run(["v4l2-ctl", "-d", dev] + args)
    elif a[0] == "test":
        dev = find_camera()
        if not dev:
            raise SystemExit("камера не найдена")
        set_controls(dev)
        d = default_out_dir() or os.path.expanduser("~/robot")
        os.makedirs(d, exist_ok=True)
        out = os.path.join(d, time.strftime("test_%H%M%S.mkv"))
        sec = a[1] if len(a) > 1 else "5"
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "v4l2", "-input_format", INPUT_FORMAT,
                        "-video_size", SIZE, "-framerate", str(FPS), "-i", dev, "-t", sec, "-c", "copy", out])
        print("записано:", out)
        print(analyze(out) or "OpenCV нет — проверка роликов пропущена")
    elif a[0] == "analyze":
        print(analyze(a[1]) or "OpenCV нет или ролик не читается")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
