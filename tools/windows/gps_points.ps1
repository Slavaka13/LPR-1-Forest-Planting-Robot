# Посмотреть точки GPS на флешке в Pi (только чтение): текущая сессия, последние посадки, трек, состояние GPS.
#   powershell -NoProfile -ExecutionPolicy Bypass -File инструменты\gps_points.ps1            — последние 100 посадок
#   ... -File инструменты\gps_points.ps1 -N 500      — последние 500;  -N 0 — все точки сессии
#   ... -File инструменты\gps_points.ps1 -Follow     — следить (новые посадки сразу, трек и GPS раз в 10 с; Ctrl+C)
#   ... -File инструменты\gps_points.ps1 -All        — сводка по всем сессиям
# Пульт выключить — он глушит Wi-Fi Pi. На Pi работает ~/robot/gps_points.py.
param([int]$N = 100, [switch]$Follow, [switch]$All, [string]$PiHost = 'biba@192.168.31.81')
$arg = if ($All) { '--all' } elseif ($Follow) { '-f' } else { "$N" }
if ($Follow) { & ssh -t -o ConnectTimeout=10 $PiHost "python3 -u ~/robot/gps_points.py $arg" }
else { & ssh -o ConnectTimeout=10 $PiHost "python3 -u ~/robot/gps_points.py $arg" }
if ($LASTEXITCODE -eq 255) { Write-Host 'Нет связи с Pi: пульт выключен? VPN (Find-NetRoute -RemoteIPAddress 192.168.31.81)?' }
