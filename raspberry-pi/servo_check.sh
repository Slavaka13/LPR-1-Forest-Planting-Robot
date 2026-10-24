#!/bin/bash
# Что на Pi готово для серво посадочного модуля (только чтение, ничего не меняет):
# аппаратный ШИМ (dtoverlay pwm-2chan, GPIO12/13), I2C и PCA9685 (0x40), библиотеки Python, права.
#   bash ~/robot/servo_check.sh
cfg=/boot/firmware/config.txt; [ -f "$cfg" ] || cfg=/boot/config.txt
echo "== $cfg (pwm / i2c / uart / audio)"
grep -nE '^[^#]*(dtoverlay|dtparam)' "$cfg" | grep -iE 'pwm|i2c|uart|audio|bt' || echo "  (нет строк pwm/i2c)"
echo "== аппаратный ШИМ (/sys/class/pwm)"
ls /sys/class/pwm 2>/dev/null || echo "  нет"
for c in /sys/class/pwm/pwmchip*; do [ -d "$c" ] && echo "  $c npwm=$(cat $c/npwm) права: $(stat -c '%U:%G %a' $c/export)"; done
echo "== функции выводов GPIO12/13"
if command -v pinctrl >/dev/null; then pinctrl get 12,13; else raspi-gpio get 12,13 2>/dev/null || echo "  pinctrl/raspi-gpio нет"; fi
echo "== I2C"
ls -l /dev/i2c-* 2>/dev/null || echo "  /dev/i2c-* нет (I2C выключен)"
if command -v i2cdetect >/dev/null && [ -e /dev/i2c-1 ]; then i2cdetect -y 1; else echo "  i2cdetect нет или шины 1 нет"; fi
echo "== GPIO"
ls -l /dev/gpiochip* /dev/gpiomem* 2>/dev/null
echo "== пользователь $(id -un): $(id -Gn)"
echo "== Python-библиотеки"
for m in lgpio pigpio gpiozero RPi.GPIO smbus2 smbus adafruit_pca9685 adafruit_servokit; do
  python3 -c "import $m" 2>/dev/null && echo "  есть  $m" || echo "  нет   $m"
done
command -v pigpiod >/dev/null && echo "  pigpiod установлен" || echo "  pigpiod нет"
echo "== служба rc-drive: $(systemctl --user is-active rc-drive)"
echo "== ядро: $(uname -r), $(grep -m1 PRETTY_NAME /etc/os-release | cut -d= -f2)"
