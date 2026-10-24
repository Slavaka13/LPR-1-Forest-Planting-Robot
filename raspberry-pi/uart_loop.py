"""Проверка USB-UART адаптера петлёй: на адаптере TXD соединить с RXD перемычкой (провода устройства отключить).

  python3 -u uart_loop.py                                   # PL2303 (GPS), 9600 и 115200
  python3 -u uart_loop.py /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0
  python3 -u uart_loop.py --listen                          # без перемычки: сколько байт приходит на 4800…115200

Шлёт строку и читает обратно. Вернулось то же — адаптер исправен (виноваты провода / устройство).
Ничего не вернулось — адаптер или перемычка. Перед запуском остановить службу: systemctl --user stop rc-drive.
"""

import sys
import time

import serial

PORT = "/dev/serial/by-id/usb-Prolific_Technology_Inc._USB-Serial_Controller-if00-port0"


def listen(port):
    """Слушать порт на разных скоростях: 0 байт везде — устройство молчит; байты без строк — не та скорость."""
    for baud in (4800, 9600, 38400, 57600, 115200):
        with serial.Serial(port, baud, timeout=0.2) as s:
            s.reset_input_buffer()
            got, t0 = b"", time.monotonic()
            while time.monotonic() - t0 < 2.5:
                got += s.read(512)
        lines = got.count(b"\n")
        nmea = got.count(b"$G")
        print(f"  {baud:6d}: байт {len(got):5d}, строк {lines:3d}, начал NMEA «$G» {nmea:3d}"
              + (f"  {got[:50]!r}" if got else ""))


def main():
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    port = args[0] if args else PORT
    print(f"порт {port}")
    if "--listen" in sys.argv:
        listen(port)
        return
    ok_all = True
    for baud in (9600, 115200):
        msg = f"VALLI-LOOP-{baud}-0123456789\r\n".encode()
        with serial.Serial(port, baud, timeout=0.5) as s:
            s.reset_input_buffer()
            time.sleep(0.05)
            s.write(msg)
            s.flush()
            got = b""
            t0 = time.monotonic()
            while len(got) < len(msg) and time.monotonic() - t0 < 1.5:
                got += s.read(len(msg) - len(got))
        ok = got == msg
        ok_all &= ok
        print(f"  {baud:6d}: послал {len(msg)} байт, вернулось {len(got)} — {'совпало ✓' if ok else 'НЕ совпало ✗'}"
              + ("" if ok else f"  {got[:40]!r}"))
    print("адаптер исправен — искать провода / модуль" if ok_all else "петля не работает — адаптер или перемычка")


if __name__ == "__main__":
    main()
