"""Барабан посевного модуля на серво MG996R 360° (постоянного вращения): один оборот = одна ячейка = одно семя.

Импульс задаёт скорость: 1500 мкс — стоп, больше/меньше — вращение в разные стороны; без импульса серво стоит.
Без датчика оборот отмеряется временем: rev_s секунд при run_us (замер — servo_test.py --run). Время копит ошибку
(напряжение, трение, семена) — для точности нужен датчик метки index_gpio: контакт на GND раз за оборот
(концевик от выступа на ступице или датчик Холла с открытым коллектором); тогда барабан крутится до метки.
GPIO занимается только на время оборота — в покое тем же выводом может пользоваться servo_test.py.
"""

import time


class SeedDrum:
    def __init__(self, gpio=13, run_us=1600, rev_s=2.0, index_gpio=None):
        import lgpio
        self.lg, self.gpio, self.run_us, self.rev_s, self.index = lgpio, gpio, run_us, rev_s, index_gpio
        self.h = None
        self.t0 = 0.0
        self.left_mark = False
        self.count = 0

    @property
    def busy(self):
        return self.h is not None

    def start(self):
        """Начать один оборот. False — если барабан ещё крутится."""
        if self.busy:
            return False
        lg = self.lg
        self.h = lg.gpiochip_open(0)
        lg.gpio_claim_output(self.h, self.gpio)
        if self.index is not None:
            lg.gpio_claim_input(self.h, self.index, lg.SET_PULL_UP)
        self.left_mark = False
        self.t0 = time.monotonic()
        lg.tx_servo(self.h, self.gpio, self.run_us, 50)
        return True

    def update(self, period=0.02):
        """Вызывать каждый цикл программы. Возвращает текст события, когда оборот закончен."""
        if not self.busy:
            return None
        t = time.monotonic() - self.t0
        if self.index is None:
            if t + period < self.rev_s:
                return None
            time.sleep(max(0.0, self.rev_s - t))   # досыпаем остаток, чтобы не копить ошибку в шаг цикла
            return self._stop(f"оборот {self.rev_s:.2f} с по времени")
        mark = self.lg.gpio_read(self.h, self.index) == 0    # 0 = метка (контакт замкнут на GND)
        if not self.left_mark:
            self.left_mark = not mark and t > 0.2 * self.rev_s
        elif mark:
            return self._stop(f"оборот {t:.2f} с по метке")
        if t > 1.6 * self.rev_s:
            return self._stop(f"МЕТКА НЕ НАЙДЕНА за {t:.2f} с — проверить датчик")
        return None

    def _stop(self, why):
        lg = self.lg
        lg.tx_servo(self.h, self.gpio, 0)
        lg.gpio_free(self.h, self.gpio)
        if self.index is not None:
            lg.gpio_free(self.h, self.index)
        lg.gpiochip_close(self.h)
        self.h = None
        self.count += 1
        return f"барабан: семя №{self.count} — {why}"

    def close(self):
        if self.busy:
            self._stop("остановлен при выходе")
