#!/bin/bash
# Диагностика питания и связи робота. Запуск: bash diag.sh
echo "== аптайм"; uptime
echo "== питание (get_throttled): 0x0 = норма"
T=$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2); echo "$T"
if [ -n "$T" ]; then
  V=$((T))
  (( V & 0x1 ))     && echo "  СЕЙЧАС: пониженное напряжение (< 4.63 В)"
  (( V & 0x4 ))     && echo "  СЕЙЧАС: частота снижена (троттлинг)"
  (( V & 0x10000 )) && echo "  С МОМЕНТА ЗАГРУЗКИ: было пониженное напряжение"
  (( V & 0x40000 )) && echo "  С МОМЕНТА ЗАГРУЗКИ: был троттлинг"
fi
echo "== напряжение ядра"; vcgencmd measure_volts core 2>/dev/null
echo "== сообщения ядра о питании (последние)"; journalctl -k -b --no-pager -o cat 2>/dev/null | grep -i -E 'under-voltage|voltage|brownout' | tail -5
echo "== USB"; lsusb | grep -v -E 'root hub|0424:' ; ls -l /dev/serial/by-id/ 2>&1 | tail -n +2
echo "== служба rc-drive"; systemctl --user is-active rc-drive
journalctl _SYSTEMD_USER_UNIT=rc-drive.service -b --no-pager -o cat | tail -4
