#!/bin/bash
# Наблюдение за питанием Pi: каждые 3 с — флаги get_throttled и частота CPU.
# Запуск: bash power_watch.sh [секунд]   (по умолчанию 30)
# 0x0 — норма; бит 0x1 — СЕЙЧАС напряжение < 4.63 В; 0x4 — СЕЙЧАС снижена частота;
# 0x10000 / 0x40000 — то же, но «было с момента загрузки».
DUR=${1:-30}
N=$(( (DUR + 2) / 3 ))
# счётчик, а не часы: сразу после загрузки время Pi прыгает при синхронизации по сети
for ((i = 0; i < N; i++)); do
  T=$(vcgencmd get_throttled | cut -d= -f2)
  F=$(vcgencmd measure_clock arm | cut -d= -f2)
  NOW=""; (( T & 0x1 )) && NOW="  <-- СЕЙЧАС мало напряжения"
  echo "$(date +%T)  throttled=$T  cpu=$((F / 1000000)) МГц$NOW"
  sleep 3
done
echo "== сообщения ядра о напряжении (последние)"
journalctl -k -b --no-pager -o short-monotonic 2>/dev/null | grep -i 'voltage' | tail -6
