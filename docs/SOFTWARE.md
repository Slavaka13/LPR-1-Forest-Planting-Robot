# Программное обеспечение ЛПР-1 — карта модулей

Этот файл — **краткая навигационная карта**. Подробные описания каждого собственного файла доступны в [FILE-CATALOG.md](FILE-CATALOG.md). Руководство для комиссии — [REVIEWER-GUIDE.md](REVIEWER-GUIDE.md).

## Точки входа и основные подсистемы

| Компонент | Файл | Подробности |
|---|---|---|
| Главная управляющая программа | [`rc_drive.py`](../raspberry-pi/rc_drive.py) | [CONTROL](CONTROL.md) |
| Приём радиокоманд | [`ibus.py`](../raspberry-pi/ibus.py) | [CONTROL](CONTROL.md) |
| STM32 и приводы шасси | [`hoverboard.py`](../raspberry-pi/hoverboard.py) | [CONTROL](CONTROL.md) |
| Контроль I²C / INA219 / PCA9685 | [`hw.py`](../raspberry-pi/hw.py) | [HARDWARE-INTERFACES](HARDWARE-INTERFACES.md) |
| Автоматическая посадка | [`seeder.py`](../raspberry-pi/seeder.py) | [PLANTING](PLANTING.md) |
| Челнок подачи семян | [`shuttle.py`](../raspberry-pi/shuttle.py) | [PLANTING](PLANTING.md) |
| Манипулятор | [`arm.py`](../raspberry-pi/arm.py) | [HARDWARE-INTERFACES](HARDWARE-INTERFACES.md) |
| GPS | [`gps_log.py`](../raspberry-pi/gps_log.py) | [GPS-AND-VIDEO](GPS-AND-VIDEO.md) |
| Видео посадки | [`seed_cam.py`](../raspberry-pi/seed_cam.py) | [GPS-AND-VIDEO](GPS-AND-VIDEO.md) |
| Переключение камер | [`camsel.py`](../raspberry-pi/camsel.py) | [GPS-AND-VIDEO](GPS-AND-VIDEO.md) |
| Служба автозапуска | [`rc-drive.service`](../raspberry-pi/rc-drive.service) | [DEVELOPMENT](DEVELOPMENT.md) |
| Прошивка контроллера | [`firmware/source/`](../firmware/source/) | [THIRD-PARTY](THIRD-PARTY.md) |

## Программная проверка

24 октября 2026 года проверена контрольная копия исходников: `compileall` — успешно, `test_modules.py` — 35/35, `test_gps_log.py` — успешно. Использовались программные тесты без физического оборудования. См. [TESTING](TESTING.md).

## Сторонние библиотеки

В дереве FOC-прошивки присутствуют CMSIS, STM32 HAL и другие сторонние компоненты. Они сохраняются по условиям исходных лицензий и не должны считаться самостоятельной разработкой команды. См. [THIRD-PARTY](THIRD-PARTY.md).

## Границы реализации

На текущей версии в коде присутствуют радиоуправление, автоматический цикл посева, диагностика и GPS-журналирование. Полностью автономная навигация, обнаружение препятствий и измерение фактической глубины остаются направлениями развития.
