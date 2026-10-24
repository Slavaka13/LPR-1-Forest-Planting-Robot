"""Чтение каналов пульта FlySky по протоколу i-BUS (приёмник FS-X6B, порт SERVO i-BUS).

Подключение через USB-UART адаптер HW-597 (CH340):
    FS-X6B i-BUS SERVO: сигнал -> RXD адаптера, "+" -> 5V адаптера, "-" -> GND адаптера.
    TXD адаптера не подключается.

Кадр i-BUS: 115200 8N1, каждые ~7 мс, 32 байта:
    0x20 0x40, 14 каналов uint16 LE (1000..2000), контрольная сумма uint16 LE = 0xFFFF - сумма байт 0..29.

Запуск как монитор каналов: python3 ibus.py [порт]  (по умолчанию DEFAULT_PORT — постоянное имя HW-597)
"""

import struct
import sys
import threading
import time

import serial  # pip install pyserial

# Постоянное имя HW-597 (CH340): не зависит от того, станет он ttyUSB0 или ttyUSB1 после переподключения.
DEFAULT_PORT = "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"

FRAME_LEN = 32
HEADER = b"\x20\x40"
CHANNELS = 14


class IBusReader:
    """Порт пропал (сброс USB, например при подключении другого USB-устройства) — поток не падает, а каждые 0.5 с
    переоткрывает порт по постоянному имени; пока кадров нет, channels() возвращает None (программа стоит)."""

    def __init__(self, port=DEFAULT_PORT, baud=115200):
        self.port, self.baud = port, baud
        try:
            self.ser = serial.Serial(port, baud, timeout=0.02)
        except (serial.SerialException, OSError) as e:      # HW-597 не подключён — ждём его, программа не падает
            print(f"i-BUS: порта нет ({e}) — жду приёмник, пока «НЕТ СИГНАЛА»", flush=True)
            self.ser = None
        self._lock = threading.Lock()
        self._channels = None
        self._t_last = 0.0
        self.frames_ok = 0
        self.frames_bad = 0
        self.reconnects = 0
        self._rx = bytearray()
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def channels(self, max_age=0.3):
        """Список из 14 значений (1000..2000) или None, если свежих кадров нет дольше max_age секунд."""
        with self._lock:
            if self._channels is None or time.monotonic() - self._t_last > max_age:
                return None
            return list(self._channels)

    def close(self):
        self._running = False
        self._thread.join()
        if self.ser:
            self.ser.close()

    def _loop(self):
        while self._running:
            try:
                if self.ser is None:
                    time.sleep(0.5)
                    self.ser = serial.Serial(self.port, self.baud, timeout=0.02)
                    self.reconnects += 1
                    print(f"i-BUS: порт снова открыт ({self.reconnects}-й раз)", flush=True)
                self._rx += self.ser.read(self.ser.in_waiting or 1)
            except (serial.SerialException, OSError) as e:
                if self.ser is not None:
                    print(f"i-BUS: порт пропал ({e}) — переподключение", flush=True)
                    try:
                        self.ser.close()
                    except Exception:
                        pass
                    self.ser = None
                    self._rx.clear()
                continue
            while True:
                idx = self._rx.find(HEADER)
                if idx < 0:
                    del self._rx[:-1]       # последний байт может быть началом заголовка
                    break
                del self._rx[:idx]
                if len(self._rx) < FRAME_LEN:
                    break
                frame = bytes(self._rx[:FRAME_LEN])
                checksum = (0xFFFF - sum(frame[:30])) & 0xFFFF
                if checksum == struct.unpack_from("<H", frame, 30)[0]:
                    values = struct.unpack_from("<14H", frame, 2)
                    with self._lock:
                        self._channels = values
                        self._t_last = time.monotonic()
                    self.frames_ok += 1
                    del self._rx[:FRAME_LEN]
                else:
                    self.frames_bad += 1
                    del self._rx[:1]        # ложный заголовок, ищем дальше


if __name__ == "__main__":
    # Монитор каналов: подвигайте стики и тумблеры, чтобы увидеть, какой канал за что отвечает.
    rc = IBusReader(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PORT)
    try:
        while True:
            time.sleep(0.2)
            ch = rc.channels()
            if ch is None:
                print(f"нет сигнала (кадров: верных {rc.frames_ok}, с ошибкой {rc.frames_bad})")
            else:
                print(" ".join(f"CH{i + 1}={v}" for i, v in enumerate(ch[:10])))
    except KeyboardInterrupt:
        pass
    finally:
        rc.close()
