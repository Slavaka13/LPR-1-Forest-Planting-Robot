#!/bin/bash
# Проверка каналов пульта: останавливает службу rc-drive, пишет каналы N секунд, запускает службу обратно.
# Запуск: bash check_channels.sh [секунд]
cd "$(dirname "$0")"
systemctl --user stop rc-drive
python3 rc_ranges.py "${1:-40}"
systemctl --user start rc-drive
echo "служба rc-drive: $(systemctl --user is-active rc-drive)"
