#!/bin/bash
# Вторая половина заливки (первую делает с ПК инструменты/deploy_pi.ps1): новые файлы лежат в ~/robot/_deploy.
# 1) проверка во временной папке: компиляция + test_modules.py (без железа) — не прошла → ничего не заменяется;
# 2) прежние версии заменяемых файлов → ~/robot/backup/<дата>/;  3) замена;  4) что есть на Pi для модулей;
# 5) перезапуск службы rc-drive и её журнал (с --no-restart — без перезапуска).
# Откат: cp ~/robot/backup/<дата>/* ~/robot/ && systemctl --user restart rc-drive
set -u
NEW="$HOME/robot/_deploy"
cd "$HOME/robot" || exit 1
[ -d "$NEW" ] || { echo "нет $NEW — сначала инструменты/deploy_pi.ps1 на ПК"; exit 1; }

TEST=$(mktemp -d)
cp "$HOME"/robot/*.py "$TEST"/ 2>/dev/null
cp "$NEW"/* "$TEST"/
if ! ( cd "$TEST" && python3 -m py_compile ./*.py && python3 -u test_modules.py ); then
    echo "ПРОВЕРКА НЕ ПРОШЛА — на Pi ничего не заменено (новые файлы остались в $NEW)"
    rm -rf "$TEST"
    exit 1
fi
rm -rf "$TEST"

BK="$HOME/robot/backup/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BK"
for f in "$NEW"/*; do
    b=$(basename "$f")
    [ -f "$b" ] && cp -p "$b" "$BK/"
done
cp "$NEW"/* "$HOME/robot/" && rm -rf "$NEW"
echo "заменено; прежние версии — $BK"
(cd "$HOME/robot" && sha256sum hw.py camsel.py arm.py seeder.py seed_cam.py rc_drive.py ibus.py 2>/dev/null | cut -c1-8,65-)

echo "--- что нужно модулям ---"
python3 -c "import smbus2" 2>/dev/null && echo "smbus2: есть" || echo "smbus2: НЕТ (sudo apt install python3-smbus2 — владелец)"
[ -e /dev/i2c-1 ] && echo "I2C: /dev/i2c-1 есть" || echo "I2C: НЕ ВКЛЮЧЁН (dtparam=i2c_arm=on — владелец)"
if [ -e /dev/i2c-1 ]; then echo "шина I2C (нужно 40 41 42 44 70):"; /usr/sbin/i2cdetect -y 1; fi
command -v ffmpeg >/dev/null && echo "ffmpeg: есть" || echo "ffmpeg: НЕТ (камера Pi не будет писать)"
command -v v4l2-ctl >/dev/null && echo "v4l2-ctl: есть" || echo "v4l2-ctl: нет (выдержка камеры не настроится)"
ls /dev/v4l/by-id/ 2>/dev/null | grep -i video-index0 | sed 's/^/камера: /' || true
ls /dev/v4l/by-id/ 2>/dev/null | grep -qi video-index0 || echo "камера: не подключена"
id -nG | grep -qw video && echo "группа video: есть" || echo "группа video: НЕТ (sudo usermod -aG video $USER — владелец, потом перезагрузка)"
id -nG | grep -qw i2c && echo "группа i2c: есть" || echo "группа i2c: НЕТ (sudo usermod -aG i2c $USER — владелец)"

if [ "${1:-}" != "--no-restart" ]; then
    echo "--- перезапуск службы ---"
    systemctl --user restart rc-drive
    sleep 6
    systemctl --user is-active rc-drive
    journalctl _SYSTEMD_USER_UNIT=rc-drive.service -n 30 --no-pager -o cat
fi
