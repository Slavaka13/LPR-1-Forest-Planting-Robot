# Залить программу робота на Pi одной командой: файлы из raspberry/ → Pi ~/robot/_deploy → на Pi проверка без железа
# (test_modules.py), копия прежних в ~/robot/backup/<дата>/, замена, перезапуск службы rc-drive, журнал.
#   powershell -NoProfile -ExecutionPolicy Bypass -File инструменты\deploy_pi.ps1
#   ... -NoRestart                 # залить и проверить, службу не трогать
#   ... -Files rc_drive.py,arm.py  # только эти файлы
# Перед запуском: пульт выключить (глушит Wi-Fi Pi); AmneziaVPN выключить (или удалить её маршруты — CLAUDE.md).
# Проверка на ПК до заливки (Python из PlatformIO): ...\penv\Scripts\python.exe raspberry\test_modules.py
param([string]$Pi = 'biba@192.168.31.81',
      [string[]]$Files = @('hw.py', 'camsel.py', 'arm.py', 'seeder.py', 'seed_cam.py', 'rc_drive.py', 'ibus.py',
                           'pca_measure.py', 'test_modules.py', 'deploy_remote.sh'),
      [switch]$NoRestart)
$ErrorActionPreference = 'Stop'
# через powershell -File список «a.py,b.py» приходит одной строкой — разобрать; deploy_remote.sh нужен всегда
$Files = @($Files | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if ($Files -notcontains 'deploy_remote.sh') { $Files += 'deploy_remote.sh' }
$root = Split-Path -Parent $PSScriptRoot
$src = Join-Path $root 'raspberry'

# AmneziaVPN уводит адрес Pi в туннель / через шлюз — тогда ssh не пройдёт
$ip = $Pi.Split('@')[-1]
$route = Find-NetRoute -RemoteIPAddress $ip -ErrorAction SilentlyContinue | Where-Object { $_.PSObject.Properties['NextHop'] } | Select-Object -First 1
if ($route -and $route.NextHop -ne '0.0.0.0') {
    Write-Warning "Маршрут к $ip идёт через $($route.NextHop) ($($route.InterfaceAlias)) — это AmneziaVPN. Отключите VPN или удалите маршрут (CLAUDE.md, «Ловушка split tunneling»)."
    exit 1
}

# копия в папку с латинским именем (scp не любит кириллицу), .sh — с переводами строк LF
$tmp = Join-Path $env:TEMP ('valli_deploy_' + [guid]::NewGuid().ToString('N').Substring(0, 6))
New-Item -ItemType Directory -Force $tmp | Out-Null
foreach ($f in $Files) {
    $p = Join-Path $src $f
    if (-not (Test-Path -LiteralPath $p)) { throw "нет файла $p" }
    if ($f -like '*.sh') {
        $t = [IO.File]::ReadAllText($p) -replace "`r`n", "`n"
        [IO.File]::WriteAllText((Join-Path $tmp $f), $t, (New-Object Text.UTF8Encoding($false)))
    } else {
        Copy-Item -LiteralPath $p -Destination $tmp
    }
}
"файлов: $($Files.Count) → ${Pi}:~/robot/_deploy"
ssh -o ConnectTimeout=10 $Pi 'rm -rf ~/robot/_deploy && mkdir -p ~/robot/_deploy'
if ($LASTEXITCODE -ne 0) { throw 'ssh не прошёл (Pi включена? пульт выключен? VPN?)' }
$paths = @(Get-ChildItem -LiteralPath $tmp -File | ForEach-Object { $_.FullName })
scp -q -o ConnectTimeout=10 @paths "${Pi}:robot/_deploy/"
if ($LASTEXITCODE -ne 0) { throw 'scp не прошёл' }
$arg = if ($NoRestart) { ' --no-restart' } else { '' }
ssh -o ConnectTimeout=10 $Pi "bash ~/robot/_deploy/deploy_remote.sh$arg"
$code = $LASTEXITCODE
Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
if ($code -ne 0) { Write-Warning "на Pi что-то не так (код $code) — см. вывод выше"; exit $code }
"готово"
