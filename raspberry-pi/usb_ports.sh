#!/bin/bash
# Состояние портов USB-хабов Pi (только чтение): что воткнуто, есть ли перегрузка по току.
# state: not attached = пусто, configured = устройство работает; oc = сколько раз срабатывала защита от перегрузки.
for p in /sys/bus/usb/devices/*/*-port*; do
  [ -d "$p" ] || continue
  printf '  %-10s state=%-13s oc=%-3s %s\n' "${p##*/}" "$(cat "$p/state" 2>/dev/null)" \
    "$(cat "$p/over_current_count" 2>/dev/null)" "$(cat "$p/connect_type" 2>/dev/null)"
done
echo "  (на Pi 3B+: 1-1.1.1 — сеть LAN7800, 1-1.1.2 / 1-1.1.3 / 1-1.2 / 1-1.3 — четыре гнезда USB)"
