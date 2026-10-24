#!/bin/bash
# Проверка камеры посева (Logitech) без остановки службы — только чтение:
# устройство и режимы, настройки (выдержка, фокус), кольцо записи службы в /dev/shm/seedcam (куски, частота кадров),
# кадр из последнего готового куска → ~/robot/cam_check/frame_*.jpg, нагрузка ffmpeg, питание.
#   bash ~/robot/cam_check.sh        # кадр как пишет служба (выдержка 1 мс — без подсветки почти чёрный)
#   bash ~/robot/cam_check.sh auto   # + кадр с автовыдержкой на 3 с (проверить резкость и поле зрения), потом
#                                    #   выдержка возвращается как у службы (seed_cam.EXPOSURE_100US)
OUT=~/robot/cam_check
mkdir -p "$OUT"
DEV=$(ls /dev/v4l/by-id/usb-*-video-index0 2>/dev/null | head -n 1)
echo "== устройство: ${DEV:-НЕ НАЙДЕНО}"
[ -z "$DEV" ] && exit 1
echo "   → $(readlink -f "$DEV")"
lsusb | grep -i -E 'logitech|046d' || echo "   lsusb: Logitech не видно"
echo
echo "== режимы MJPEG (640x480 и больше)"
v4l2-ctl -d "$DEV" --list-formats-ext 2>&1 | grep -E "MJPG|Size|Interval" | head -n 24
echo
echo "== настройки (выдержка, фокус, усиление, частота сети)"
v4l2-ctl -d "$DEV" --list-ctrls 2>&1 | grep -E -i "exposure|focus|gain|power_line|brightness|backlight|white_balance_auto"
echo
echo "== кольцо записи службы /dev/shm/seedcam"
ls -la --time-style=+%H:%M:%S /dev/shm/seedcam 2>&1 | grep -v '^total'
LOG=/dev/shm/seedcam/ffmpeg.log
[ -s "$LOG" ] && { echo "-- ffmpeg.log (хвост):"; tail -n 5 "$LOG"; }
# последний готовый кусок — второй по свежести (самый свежий ещё пишется)
SEG=$(ls -t /dev/shm/seedcam/seg*.mkv 2>/dev/null | sed -n 2p)
if [ -n "$SEG" ]; then
  echo
  echo "== кусок $SEG"
  ffprobe -v error -count_frames -select_streams v:0 \
    -show_entries stream=codec_name,width,height,avg_frame_rate,nb_read_frames -of default=nw=1 "$SEG"
  N=$(date +%H%M%S)
  ffmpeg -nostdin -loglevel error -y -i "$SEG" -frames:v 1 -c:v copy -f image2 "$OUT/frame_$N.jpg" \
    && echo "кадр (как у службы): $OUT/frame_$N.jpg ($(stat -c %s "$OUT/frame_$N.jpg") байт)"
  if [ "$1" = "auto" ]; then
    EXP=$(cd ~/robot && python3 -c "import seed_cam; print(seed_cam.EXPOSURE_100US)")
    v4l2-ctl -d "$DEV" -c auto_exposure=3
    sleep 3
    SEG=$(ls -t /dev/shm/seedcam/seg*.mkv | sed -n 2p)
    ffmpeg -nostdin -loglevel error -y -i "$SEG" -frames:v 1 -c:v copy -f image2 "$OUT/frame_${N}_auto.jpg" \
      && echo "кадр (автовыдержка): $OUT/frame_${N}_auto.jpg ($(stat -c %s "$OUT/frame_${N}_auto.jpg") байт)"
    echo "   выдержка в авто: $(v4l2-ctl -d "$DEV" -C exposure_time_absolute)"
    if [ "$EXP" != "None" ]; then
      v4l2-ctl -d "$DEV" -c auto_exposure=1,exposure_time_absolute="$EXP"
    fi
    echo "   вернул: $(v4l2-ctl -d "$DEV" -C auto_exposure,exposure_time_absolute | tr '\n' ' ')"
  fi
else
  echo "кусков нет — служба камеру не пишет (камера не найдена при запуске службы?)"
fi
echo
echo "== ffmpeg службы: процессор / память"
ps -C ffmpeg -o pid,pcpu,pmem,etime,args --no-headers | cut -c1-160
echo
echo "== питание: $(vcgencmd get_throttled 2>/dev/null)   температура: $(vcgencmd measure_temp 2>/dev/null)"
