# Забрать журналы GPS с флешки VALLI в Pi на ПК: на Pi — gps_export.py (GPX / KML / GeoJSON пишутся на ту же флешку),
# затем копия папки valli_logs в папку проекта. SD-карта Pi не используется.
#   powershell -NoProfile -ExecutionPolicy Bypass -File инструменты\gps_download.ps1
# Результат: D:\ai\telega\журналы GPS\NNNN_<дата>\ (posadka.csv, trek.csv, nmea.log, posadka.gpx/.kml/.geojson)
# и vse_posadki.gpx / .kml / .geojson — все точки посадки (для телефона: OsmAnd / Locus — открыть .gpx).
# Пульт на время выгрузки выключить — он глушит Wi-Fi Pi. Другой путь: выключить Pi, вынуть флешку, открыть на ПК
# (CSV читаются сразу; GPX/KML — после запуска этого скрипта или gps_export.py на Pi).
param([string]$PiHost = 'biba@192.168.31.81', [string]$Dest = 'D:\ai\telega\журналы GPS')
$ssh = @('-o', 'ConnectTimeout=10', '-o', 'BatchMode=yes')
$dir = (& ssh @ssh $PiHost 'python3 ~/robot/gps_export.py --dir' | Select-Object -Last 1)
if ($LASTEXITCODE -ne 0 -or -not $dir) {
    Write-Host 'Нет связи с Pi или на Pi нет флешки VALLI с журналами. Пульт выключен? Если включён AmneziaVPN:'
    Write-Host '  Find-NetRoute -RemoteIPAddress 192.168.31.81 (tun2 / шлюз 192.168.31.1 = VPN).'
    exit 1
}
$dir = $dir.Trim()
& ssh @ssh $PiHost 'python3 ~/robot/gps_export.py'
if ($LASTEXITCODE -ne 0) { Write-Host 'Ошибка выгрузки на Pi (gps_export.py)'; exit 1 }
# scp не любит кириллицу в пути — сначала во временную папку с латинским именем
$tmp = Join-Path $env:TEMP ('valli_gps_' + [guid]::NewGuid().ToString('N').Substring(0, 6))
New-Item -ItemType Directory -Force $tmp | Out-Null
& scp @ssh -r -q "${PiHost}:$dir" $tmp
if ($LASTEXITCODE -ne 0) { Write-Host 'Ошибка scp'; exit 1 }
New-Item -ItemType Directory -Force $Dest | Out-Null
Copy-Item -Path (Join-Path $tmp ((Split-Path $dir -Leaf) + '\*')) -Destination $Dest -Recurse -Force
$n = (Get-ChildItem -LiteralPath $Dest -Directory | Where-Object { $_.Name -match '^\d{4}_' }).Count
Write-Host "Готово: $Dest (сессий: $n; на флешке: $dir)"
