# Полный каталог файлов ЛПР-1

**Назначение:** сделать исходный комплект прозрачным для экспертной комиссии. Этот документ описывает все **собственные программы Raspberry Pi и инструменты Windows**, а также структуру **сторонних библиотек прошивки STM32**. Сотни файлов CMSIS/HAL перечисляются группами: они не являются отдельными разработками команды.

Обозначения: **основной** — штатная работа робота; **проверка** — диагностика, потенциально обращается к аппаратуре; **служебный** — обслуживание; **тест** — проверка логики без реального железа при штатной настройке фиктивных объектов.

## 1. Raspberry Pi: основные модули Python

| Файл | Категория | Что делает | См. |
|---|---|---|---|
| [`rc_drive.py`](../raspberry-pi/rc_drive.py) | основной | Центральный цикл, обработка 10 каналов FlySky, ARM, переключатели, разрешения хода, маршрутизация команд посадки, камеры и манипулятора | [Управление](CONTROL.md) |
| [`ibus.py`](../raspberry-pi/ibus.py) | основной | Чтение кадров i-BUS из последовательного порта, проверка кадров, предоставление актуальных значений каналов | [Управление](CONTROL.md) |
| [`hoverboard.py`](../raspberry-pi/hoverboard.py) | основной | Сериализация команд ходовой части для STM32, обратная связь, фоновая периодическая передача и программный таймаут | [Управление](CONTROL.md) |
| [`seeder.py`](../raspberry-pi/seeder.py) | основной | Управление двумя сервоприводами рабочего органа; состояния заглубления, остановки, набора/сброса, подъёма; токовая защита и отказ | [Посадка](PLANTING.md) |
| [`shuttle.py`](../raspberry-pi/shuttle.py) | основной | Управление челноком дозатора MG90S: набор 1000 мкс, сброс/дом 1400 мкс; хранение состояния канала | [Посадка](PLANTING.md) |
| [`hw.py`](../raspberry-pi/hw.py) | основной | Вспомогательный слой для общей шины I²C, доступа к PCA9685 и INA219; синхронизация обращения | [Оборудование](HARDWARE-INTERFACES.md) |
| [`arm.py`](../raspberry-pi/arm.py) | основной | Управление приводами манипулятора и захвата на отдельной PCA9685 | [Оборудование](HARDWARE-INTERFACES.md) |
| [`camsel.py`](../raspberry-pi/camsel.py) | основной | Переключение видеоканалов через W8010S | [Оборудование](HARDWARE-INTERFACES.md) |
| [`seed_cam.py`](../raspberry-pi/seed_cam.py) | основной | Работа с Logitech C270, запись коротких видеоматериалов процесса посадки и обработка фрагментов | [GPS и видео](GPS-AND-VIDEO.md) |
| [`gps_log.py`](../raspberry-pi/gps_log.py) | основной | Парсинг NMEA, журнал трека и посадочных точек, буферизация и запись на USB-накопитель | [GPS и видео](GPS-AND-VIDEO.md) |
| [`seed_drum.py`](../raspberry-pi/seed_drum.py) | вспомогательный | Программное представление механизма подачи семенного материала; не путать с основным `shuttle.py` | [Посадка](PLANTING.md) |

## 2. Raspberry Pi: диагностика и утилиты Python

| Файл | Что проверяет или делает | Условия запуска |
|---|---|---|
| [`failsafe_test.py`](../raspberry-pi/failsafe_test.py) | Показывает значения каналов и исчезновение кадров i-BUS при отключении пульта | Требуется приёмник, не проверяет движение сам по себе |
| [`rc_ranges.py`](../raspberry-pi/rc_ranges.py) | Вывод диапазонов каналов FlySky для калибровки стиков/переключателей | Требуется подключённый приёмник |
| [`listen.py`](../raspberry-pi/listen.py) | Приём и отображение телеметрии платы гироскутера через драйвер | UART; учитывать конкуренцию за порт |
| [`uart_loop.py`](../raspberry-pi/uart_loop.py) | Диагностика последовательного интерфейса | Только по инструкции, при свободном порте |
| [`spin_test.py`](../raspberry-pi/spin_test.py) | Тест движения приводов с заданными скоростями | **Движение моторов**: только надёжно вывешенное шасси |
| [`i2c_probe.py`](../raspberry-pi/i2c_probe.py) | Диагностика связи I²C с использованием GPIO `lgpio` | Подготовленная Pi |
| [`ina_raw.py`](../raspberry-pi/ina_raw.py) | Чтение низкоуровневых регистров датчиков INA219 | Подключённые датчики |
| [`ina_test.py`](../raspberry-pi/ina_test.py) | Измерение тока INA219 и диагностика превышения порогов | Подключённые датчики |
| [`pca_diag.py`](../raspberry-pi/pca_diag.py) | Проверка регистров/параметров генератора PWM PCA9685 | Может обращаться к управляющему PWM; читать код перед запуском |
| [`pca_measure.py`](../raspberry-pi/pca_measure.py) | Измерение параметров PWM и проверка частоты/длительности | Подготовленный стенд |
| [`pca_regs.py`](../raspberry-pi/pca_regs.py) | Просмотр регистров PCA9685 | Подготовленная Pi |
| [`servo_test.py`](../raspberry-pi/servo_test.py) | Низкоуровневое управление сервоприводом через PCA/ШИМ | **Может двигать сервопривод** |
| [`servo_deadband.py`](../raspberry-pi/servo_deadband.py) | Поиск нейтрали серво непрерывного вращения | **Движение механизма** |
| [`gps_cat.py`](../raspberry-pi/gps_cat.py) | Просмотр потока последовательных данных GPS | Требуется приёмник GPS |
| [`gps_gpio.py`](../raspberry-pi/gps_gpio.py) | Диагностика GPS по GPIO/программному чтению | Требуется подключённая аппаратура |
| [`gps_points.py`](../raspberry-pi/gps_points.py) | Просмотр, выборка и сводка сохранённых посадочных точек | Читает журналы на накопителе |
| [`gps_export.py`](../raspberry-pi/gps_export.py) | Экспорт журналов CSV в GPX, KML и GeoJSON | Работает с файлами данных |
| [`test_gps_log.py`](../raspberry-pi/test_gps_log.py) | Тесты парсинга NMEA, GPS-журналирования и экспорта с фиктивными данными | Обычно без физического GPS |
| [`test_modules.py`](../raspberry-pi/test_modules.py) | Программные сценарии тестирования управления, посадки и периферии с Fake-объектами | Без аппаратного запуска при штатном тестовом окружении |

## 3. Raspberry Pi: оболочка Linux и сервис

| Файл | Действие | Важно |
|---|---|---|
| [`rc-drive.service`](../raspberry-pi/rc-drive.service) | Запускает управляющую программу через user systemd | Требует правильного пути и пользователя |
| [`deploy_remote.sh`](../raspberry-pi/deploy_remote.sh) | Приём обновления, программная проверка, резервная копия и перезапуск службы | **Меняет файлы и может перезапускать управление** |
| [`check_channels.sh`](../raspberry-pi/check_channels.sh) | Останавливает сервис и проверяет радиоканалы | Временно выключает штатное управление |
| [`dir_check.sh`](../raspberry-pi/dir_check.sh) | Проверяет каналы и Failsafe | Останавливает и перезапускает сервис |
| [`cam_check.sh`](../raspberry-pi/cam_check.sh) | Проверка камеры посева, ffmpeg и последних записанных фрагментов | В режиме `auto` временно изменяет экспозицию |
| [`diag.sh`](../raspberry-pi/diag.sh) | Питание, частота CPU, состояния устройств | Предназначен для диагностики |
| [`full_diag.sh`](../raspberry-pi/full_diag.sh) | Подробная диагностика ОС, питания, USB, UART, I²C, служб и пакетов | Преимущественно чтение |
| [`power_watch.sh`](../raspberry-pi/power_watch.sh) | Наблюдает напряжение и троттлинг Pi | Чтение |
| [`servo_check.sh`](../raspberry-pi/servo_check.sh) | Проверка конфигурации PWM/I²C для сервоприводов | Чтение |
| [`usb_ports.sh`](../raspberry-pi/usb_ports.sh) | Текущие состояния USB-портов и счётчики перегрузок | Чтение |
| [`usb_watch.sh`](../raspberry-pi/usb_watch.sh) | Наблюдение за USB, сообщениями ядра и питанием | Чтение |

## 4. Windows: инструменты обслуживания

| Файл | Назначение | Ограничения |
|---|---|---|
| [`tools/windows/add-ssh-key.ps1`](../tools/windows/add-ssh-key.ps1) | Однократная установка публичного SSH-ключа на Raspberry Pi | Жёстко указаны пользователь и локальный IP из экспериментального стенда |
| [`tools/windows/deploy_pi.ps1`](../tools/windows/deploy_pi.ps1) | Копирует выбранные файлы по SSH на Pi и запускает `deploy_remote.sh` | **Меняет код на реальном роботе**; пути адаптировать |
| [`tools/windows/gps_download.ps1`](../tools/windows/gps_download.ps1) | Запускает экспорт GPS на Pi и копирует журналы на ПК | Локальный путь Windows задан под компьютер разработчиков |
| [`tools/windows/gps_points.ps1`](../tools/windows/gps_points.ps1) | Считывает точки посева на удалённом Pi через SSH | Зависит от сети/учётной записи |
| [`tools/windows/beak_replay.py`](../tools/windows/beak_replay.py) | Воспроизведение и разбор записанных токовых данных рабочего органа | Для анализа журналов |

## 5. Прошивка STM32: где собственная логика, а где библиотеки

| Путь | Роль |
|---|---|
| [`firmware/source/README.md`](../firmware/source/README.md) | Документация исходного проекта hoverboard FOC |
| [`firmware/source/LICENSE`](../firmware/source/LICENSE) | Условия использования стороннего кода |
| [`firmware/source/Inc/config.h`](../firmware/source/Inc/config.h) | Сборочные варианты, режимы управления, параметры |
| [`firmware/source/Inc/defines.h`](../firmware/source/Inc/defines.h) | Общие определения |
| [`firmware/source/Src/main.c`](../firmware/source/Src/main.c) | Главный цикл STM32 и аппаратная инициализация |
| [`firmware/source/Src/comms.c`](../firmware/source/Src/comms.c) | Обмен данными |
| [`firmware/source/Src/control.c`](../firmware/source/Src/control.c) | Обработка управляющих воздействий |
| [`firmware/source/Src/bldc.c`](../firmware/source/Src/bldc.c) | Работа с BLDC-приводами |
| [`firmware/source/Src/BLDC_controller.c`](../firmware/source/Src/BLDC_controller.c) | Генерируемая/математическая часть контроллера |
| [`firmware/source/Drivers/CMSIS/`](../firmware/source/Drivers/CMSIS/) | Библиотеки ARM CMSIS, примеры DSP, заголовки ядра и устройств |
| [`firmware/source/Drivers/STM32F1xx_HAL_Driver/`](../firmware/source/Drivers/STM32F1xx_HAL_Driver/) | Библиотеки STM32 HAL/LL |
| [`firmware/source/Makefile`](../firmware/source/Makefile) | Сборка прошивки |
| [`firmware/source/platformio.ini`](../firmware/source/platformio.ini) | Конфигурация PlatformIO |
| [`firmware/source/Arduino/hoverserial/hoverserial.ino`](../firmware/source/Arduino/hoverserial/hoverserial.ino) | Пример обмена данными с платой |

Большая часть количества файлов репозитория — именно *зависимости микроконтроллерной прошивки*, а не сотни отдельных функций ЛПР-1. Их нельзя приписывать разработчикам проекта без указания происхождения.

## 6. Документация проекта

| Файл | Назначение |
|---|---|
| [`README.md`](../README.md) | Введение для комиссии |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Блоки аппаратной и программной архитектуры |
| [`SOFTWARE.md`](SOFTWARE.md) | Краткая карта программ |
| [`FILE-CATALOG.md`](FILE-CATALOG.md) | Этот полный каталог |
| [`CONTROL.md`](CONTROL.md) | Режимы, команды, движение и защиты |
| [`PLANTING.md`](PLANTING.md) | Состояния посадочного цикла |
| [`GPS-AND-VIDEO.md`](GPS-AND-VIDEO.md) | GPS-журналы и видео |
| [`HARDWARE-INTERFACES.md`](HARDWARE-INTERFACES.md) | Интерфейсы и карта адресов |
| [`TESTING.md`](TESTING.md) | Протоколы и тестовые сценарии |
| [`INSTALLATION.md`](INSTALLATION.md) | Требования запуска |
| [`CAD-MODELS.md`](CAD-MODELS.md) | Статус моделей и крупных CAD-файлов |
| [`electrical-schematic.pdf`](electrical-schematic.pdf) | Исходная электрическая схема |
| [`code-explanation-2026-10-23.pdf`](code-explanation-2026-10-23.pdf) | Пояснения к коду, исходный PDF |
| [`CHANGELOG.md`](../CHANGELOG.md) | Изменения в репозитории |

Документ составлен по исходникам от 23–24 октября 2026 года. Для спорных параметров приоритет имеют конкретная версия кода и протокол фактического испытания.
