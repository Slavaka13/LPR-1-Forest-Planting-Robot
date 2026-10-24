#!/bin/bash
# Наблюдение при подключении USB-устройства (например, PL2303 + GPS): каждые 2 с — питание (get_throttled), нагрузка,
# частота CPU, порты в /dev/serial/by-id; параллельно — сообщения ядра про USB / pl2303 / напряжение.
# Запуск: bash usb_watch.sh [секунд]   (по умолчанию 120). Только чтение, ничего не меняет.
DUR=${1:-120}
( timeout "$DUR" journalctl -k -f -n 0 --no-pager -o short-monotonic 2>&1 \
    | grep --line-buffered -i -E 'usb|pl2303|ttyUSB|voltage|error|reset' | sed -u 's/^/  ядро: /' ) &
N=$(( DUR / 2 ))
for ((i = 0; i < N; i++)); do
  T=$(vcgencmd get_throttled | cut -d= -f2)
  F=$(vcgencmd measure_clock arm | cut -d= -f2)
  L=$(cut -d' ' -f1 /proc/loadavg)
  P=$(ls /dev/serial/by-id/ 2>/dev/null | sed -e 's/usb-1a86_USB_Serial-if00-port0/HW-597/' -e 's/^usb-//' | tr '\n' ' ')
  NOW=""; (( T & 0x1 )) && NOW="  <-- СЕЙЧАС мало напряжения"
  echo "$(date +%T)  throttled=$T  cpu=$((F / 1000000)) МГц  нагрузка=$L  порты: $P$NOW"
  sleep 2
done
wait
