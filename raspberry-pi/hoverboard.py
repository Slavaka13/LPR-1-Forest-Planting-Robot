"""Драйвер платы гироскутера (прошивка hoverboard-firmware-hack-FOC, VARIANT_USART, USART2).

Подключение: левый разъём платы (Left Sideboard)
    PA2 / TX  -> RXD Raspberry Pi (GPIO15, pin 10)
    PA3 / RX  -> TXD Raspberry Pi (GPIO14, pin 8)
    GND       -> GND Raspberry Pi
    15V       -> НЕ ПОДКЛЮЧАТЬ!
На Raspberry Pi 3: включить UART (raspi-config -> Interface -> Serial: login shell = No, hardware = Yes)
и использовать /dev/serial0 (лучше вернуть PL011 на GPIO: dtoverlay=disable-bt в /boot/config.txt).

Команды нужно слать постоянно: если пакетов нет дольше ~0.8 с, плата делает аварийную остановку.
Поэтому драйвер сам повторяет последнюю команду в фоновом потоке.
"""

import struct
import threading
import time
from dataclasses import dataclass

import serial  # pip install pyserial

START_FRAME = 0xABCD
CMD_FORMAT = "<HhhH"            # start, steer, speed, checksum = 8 байт
FB_FORMAT = "<HhhhhhhHH"        # start, cmd1, cmd2, speedR, speedL, batV, temp, cmdLed, checksum = 18 байт
FB_SIZE = struct.calcsize(FB_FORMAT)
START_BYTES = struct.pack("<H", START_FRAME)   # b"\xcd\xab"


@dataclass
class Feedback:
    cmd1: int
    cmd2: int
    speed_r: int        # об/мин правого мотора
    speed_l: int        # об/мин левого мотора
    bat_voltage: float  # В
    board_temp: float   # °C
    cmd_led: int


class Hoverboard:
    def __init__(self, port="/dev/serial0", baud=115200, send_period=0.02, command_timeout=None):
        """command_timeout: если drive() не вызывался дольше этого времени (с), отправлять стоп.
        None - повторять последнюю команду бесконечно."""
        self.ser = serial.Serial(port, baud, timeout=0.01)
        self.send_period = send_period
        self.command_timeout = command_timeout
        self._steer = 0
        self._speed = 0
        self._t_cmd = time.monotonic()
        self._lock = threading.Lock()
        self._rx = bytearray()
        self.feedback = None
        self.t_feedback = 0.0           # time.monotonic() последнего верного пакета обратной связи
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def drive(self, speed, steer=0):
        """speed, steer: -1000..1000."""
        with self._lock:
            self._speed = max(-1000, min(1000, int(speed)))
            self._steer = max(-1000, min(1000, int(steer)))
            self._t_cmd = time.monotonic()

    def stop(self):
        self.drive(0, 0)

    def fresh_feedback(self, max_age=0.5):
        """Последняя обратная связь или None, если пакетов не было дольше max_age (плата выключена / нет связи)."""
        if self.feedback is None or time.monotonic() - self.t_feedback > max_age:
            return None
        return self.feedback

    def close(self):
        self.stop()
        time.sleep(3 * self.send_period)
        self._running = False
        self._thread.join()
        self.ser.close()

    def _send(self):
        with self._lock:
            steer, speed = self._steer, self._speed
            if self.command_timeout is not None and time.monotonic() - self._t_cmd > self.command_timeout:
                steer = speed = 0           # управляющая программа зависла - стоп
        checksum = (START_FRAME ^ (steer & 0xFFFF) ^ (speed & 0xFFFF)) & 0xFFFF
        self.ser.write(struct.pack(CMD_FORMAT, START_FRAME, steer, speed, checksum))

    def _receive(self):
        self._rx += self.ser.read(self.ser.in_waiting or 1)
        while True:
            idx = self._rx.find(START_BYTES)
            if idx < 0:
                del self._rx[:-1]       # оставить последний байт: он может быть началом заголовка
                return
            del self._rx[:idx]
            if len(self._rx) < FB_SIZE:
                return
            fields = struct.unpack(FB_FORMAT, self._rx[:FB_SIZE])
            checksum = 0
            for v in fields[:-1]:
                checksum ^= v & 0xFFFF
            if checksum == fields[-1]:
                _, cmd1, cmd2, sr, sl, bat, temp, led, _ = fields
                self.feedback = Feedback(cmd1, cmd2, sr, sl, bat / 100.0, temp / 10.0, led)
                self.t_feedback = time.monotonic()
                del self._rx[:FB_SIZE]
            else:
                del self._rx[:1]        # ложный заголовок, ищем дальше

    def _loop(self):
        next_send = time.monotonic()
        while self._running:
            now = time.monotonic()
            if now >= next_send:
                self._send()
                next_send = now + self.send_period
            self._receive()


if __name__ == "__main__":
    # Тест: поставьте платформу на подставку (колёса в воздухе)!
    hb = Hoverboard()
    try:
        for speed in (0, 100, 200, 100, 0, -100, 0):
            hb.drive(speed)
            time.sleep(2)
            print(speed, hb.feedback)
    finally:
        hb.close()
