#!/bin/bash
# Проверка направлений каналов и failsafe: останавливает службу rc-drive, N секунд печатает каналы
# (каждые 0.5 с), запускает службу обратно. Запуск: bash dir_check.sh [секунд]
cd "$(dirname "$0")"
systemctl --user stop rc-drive
timeout "${1:-40}" python3 -u failsafe_test.py "${1:-40}" 2>/dev/null
systemctl --user start rc-drive
echo "служба rc-drive: $(systemctl --user is-active rc-drive)"
