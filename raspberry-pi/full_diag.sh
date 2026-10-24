#!/bin/bash
# Полная диагностика Pi (только чтение, без sudo). Запуск: bash ~/robot/full_diag.sh
# Питание, температура, память, диски, сообщения ядра, Wi-Fi, USB, UART/I2C, PCA9685, службы, библиотеки.
h() { echo; echo "== $*"; }
have() { command -v "$1" >/dev/null 2>&1; }

h "система"
echo "$(hostname), $(tr -d '\0' < /proc/device-tree/model 2>/dev/null)"
. /etc/os-release 2>/dev/null; echo "$PRETTY_NAME, ядро $(uname -r)"
uptime; echo "загрузка с: $(uptime -s)"
have timedatectl && timedatectl show -p NTPSynchronized -p Timezone 2>/dev/null | tr '\n' ' '; echo

h "питание (get_throttled: 0x0 = норма)"
T=$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2); echo "$T"
if [ -n "$T" ]; then
  V=$((T))
  (( V & 0x1 ))     && echo "  СЕЙЧАС: пониженное напряжение (< 4.63 В)"
  (( V & 0x2 ))     && echo "  СЕЙЧАС: частота ограничена"
  (( V & 0x4 ))     && echo "  СЕЙЧАС: троттлинг"
  (( V & 0x8 ))     && echo "  СЕЙЧАС: мягкий температурный предел"
  (( V & 0x10000 )) && echo "  С ЗАГРУЗКИ: было пониженное напряжение"
  (( V & 0x20000 )) && echo "  С ЗАГРУЗКИ: частота ограничивалась"
  (( V & 0x40000 )) && echo "  С ЗАГРУЗКИ: был троттлинг"
  (( V & 0x80000 )) && echo "  С ЗАГРУЗКИ: был мягкий температурный предел"
  [ "$V" -eq 0 ] && echo "  норма"
fi
echo "ядро $(vcgencmd measure_volts core | cut -d= -f2), sdram_c $(vcgencmd measure_volts sdram_c | cut -d= -f2)"
echo "температура $(vcgencmd measure_temp | cut -d= -f2), частота ARM $(( $(vcgencmd measure_clock arm | cut -d= -f2) / 1000000 )) МГц"

h "нагрузка и память"
cat /proc/loadavg
free -h | head -n 3
echo "-- самые прожорливые процессы:"
ps -eo pid,pcpu,pmem,etime,comm --sort=-pcpu | head -n 6

h "диски"
df -h / /boot/firmware 2>/dev/null | sed 1d
for m in /media/biba/VALLI /mnt/valli; do mountpoint -q "$m" && df -h "$m" | sed 1d; done
echo "корень: $(findmnt -no OPTIONS / | cut -d, -f1)  (rw = норма, ro = файловая система с ошибкой)"

h "сообщения ядра с загрузки"
K=$(journalctl -k -b --no-pager -o cat 2>/dev/null)
echo "строк всего: $(echo "$K" | wc -l)"
for p in 'under-voltage|Undervoltage|voltage normal' 'mmc[0-9].*(error|timeout)|I/O error|EXT4-fs (error|warning)' \
         'FAT-fs|not properly unmounted' 'usb .*(disconnect|reset)|device descriptor read' 'i2c.*(error|timeout|NACK)' \
         'oom|Out of memory|segfault' 'brcmfmac.*(error|fail)' 'watchdog'; do
  n=$(echo "$K" | grep -c -i -E "$p")
  printf '  %-60s %s\n' "$p" "$n"
done
echo "-- последние предупреждения/ошибки:"
journalctl -k -b -p warning --no-pager -o short-monotonic 2>/dev/null | tail -n 8 | cut -c1-180

h "перезагрузки"
journalctl --list-boots --no-pager 2>/dev/null | tail -n 3
[ -d /var/log/journal ] && echo "журнал хранится на диске" || echo "журнал только в памяти — причины прошлых перезагрузок не видны"

h "Wi-Fi"
ip -4 -br addr show wlan0 2>/dev/null
ip route show default 2>/dev/null
if have iw; then iw dev wlan0 link 2>/dev/null | grep -E 'SSID|freq|signal|tx bitrate'; iw dev wlan0 get power_save 2>/dev/null
elif [ -x /usr/sbin/iw ]; then /usr/sbin/iw dev wlan0 link 2>/dev/null | grep -E 'SSID|freq|signal|tx bitrate'; /usr/sbin/iw dev wlan0 get power_save 2>/dev/null
else awk 'NR>2{print "  уровень:",$4,"дБм (качество",$3")"}' /proc/net/wireless; fi
GW=$(ip route show default | awk '{print $3; exit}')
[ -n "$GW" ] && ping -c 5 -i 0.3 -W 1 "$GW" | tail -n 2

h "USB"
lsusb | grep -v -E 'root hub|0424:'
ls -l /dev/serial/by-id/ 2>/dev/null | tail -n +2 | awk '{print "  "$9" -> "$11}'
ls /dev/ttyUSB* 2>/dev/null | tr '\n' ' '; echo
[ -f ~/robot/usb_ports.sh ] && bash ~/robot/usb_ports.sh

h "UART, I2C, config.txt"
ls -l /dev/serial0 2>/dev/null | awk '{print "serial0 -> "$11}'
grep -E '^\s*(enable_uart|dtparam=i2c|dtparam=audio|dtoverlay)' /boot/firmware/config.txt 2>/dev/null
ls /dev/i2c-* 2>/dev/null | tr '\n' ' '; echo
if have pinctrl; then pinctrl get 2,3,14,15 2>/dev/null; fi
[ -e /dev/i2c-1 ] && /usr/sbin/i2cdetect -y 1 2>/dev/null | grep -E '^[0-7]0:' | grep -v -E '^[0-7]0:( --)+ *$'

h "PCA9685 (только чтение)"
[ -e /dev/i2c-1 ] && (cd ~/robot && python3 -u pca_diag.py 2>&1 | head -n 4)

h "службы"
echo "rc-drive: $(systemctl --user is-active rc-drive), перезапусков $(systemctl --user show rc-drive -p NRestarts --value), с $(systemctl --user show rc-drive -p ActiveEnterTimestamp --value)"
journalctl _SYSTEMD_USER_UNIT=rc-drive.service -b --no-pager -o cat 2>/dev/null | grep -v -E '^\s*$' | tail -n 3 | cut -c1-200
echo "упавшие системные: $(systemctl --failed --no-legend --plain 2>/dev/null | awk '{print $1}' | tr '\n' ' ')"
echo "упавшие пользовательские: $(systemctl --user --failed --no-legend --plain 2>/dev/null | awk '{print $1}' | tr '\n' ' ')"
echo "ошибки служб с загрузки: $(journalctl -b -p err --no-pager -o cat 2>/dev/null | wc -l) строк"
journalctl -b -p err --no-pager -o short-monotonic 2>/dev/null | tail -n 5 | cut -c1-180

h "библиотеки Python"
python3 - <<'EOF'
import importlib
for m in ("serial", "smbus2", "lgpio", "gpiozero"):
    try:
        x = importlib.import_module(m)
        print(f"  {m}: {getattr(x, '__version__', getattr(x, 'VERSION', 'есть'))}")
    except Exception as e:
        print(f"  {m}: НЕТ ({e.__class__.__name__})")
EOF
id -nG | tr ' ' '\n' | grep -E '^(dialout|gpio|i2c|audio)$' | tr '\n' ' '; echo "← группы пользователя"

h "программы ~/robot (sha256, первые 8 знаков)"
cd ~/robot && sha256sum *.py *.sh 2>/dev/null | awk '{print substr($1,1,8), $2}'
