$ProjectRoot = 'D:\code_re\mas-failure-attribution'
$RunRoot = 'D:\mas_runs\kodcode_full_batches'
$Watchdog = Join-Path $ProjectRoot 'scripts\kodcode_watchdog_windows.ps1'
$LogRoot = Join-Path $RunRoot 'logs'
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null

$watchdogPattern = ('* -File ' + $Watchdog + '*')
$existing = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like $watchdogPattern -and $_.CommandLine -notlike '* -Command *' })
if ($existing.Count -gt 0) {
    Write-Output "watchdog already running: $($existing.ProcessId -join ',')"
    exit 0
}

$stdout = Join-Path $LogRoot 'kodcode_watchdog_windows.stdout.log'
$stderr = Join-Path $LogRoot 'kodcode_watchdog_windows.stderr.log'
Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',$Watchdog,'-IntervalSeconds','600','-ProjectRoot',$ProjectRoot,'-RunRoot',$RunRoot) -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
Write-Output "watchdog started -> $stdout"
