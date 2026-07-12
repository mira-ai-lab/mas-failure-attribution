param(
    [string]$RunRoot = "D:\mas_runs\hotpotqa_validation_from_0618_fixed",
    [string]$Dataset = "dataset\hotpotqa\hotpotqa_validation_full.parquet",
    [int]$MaxRounds = 3,
    [int]$AgentStepTimeoutSeconds = 360,
    [int]$WorkforceProcessTimeoutSeconds = 600,
    [int]$IntervalSeconds = 300
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$lanes = @(
    @{Name="key1"; EnvFile="owl\owl\.env.gaia"; EndExclusive=1976},
    @{Name="key2"; EnvFile="owl\owl\.env.key2"; EndExclusive=3334},
    @{Name="key3"; EnvFile="owl\owl\.env.key3"; EndExclusive=4691},
    @{Name="key4"; EnvFile="owl\owl\.env.key4"; EndExclusive=6048},
    @{Name="key5"; EnvFile="owl\owl\.env.key5"; EndExclusive=7405}
)

$watchdogLogs = Join-Path $RunRoot "watchdog_logs"
New-Item -ItemType Directory -Force -Path $watchdogLogs | Out-Null
$watchdogLog = Join-Path $watchdogLogs "watchdog.log"

function Write-WatchdogLog {
    param([string]$Message)
    "[$(Get-Date -Format s)] $Message" | Tee-Object -FilePath $watchdogLog -Append
}

function Get-LastSampleLog {
    param([string]$LaneRoot)
    $logs = Join-Path $LaneRoot "process_logs"
    if (!(Test-Path $logs)) {
        return $null
    }
    return Get-ChildItem $logs -Filter "sample_*.log" -File -ErrorAction SilentlyContinue |
        Sort-Object Name |
        Select-Object -Last 1
}

function Get-NextOffset {
    param([string]$LaneRoot, [int]$DefaultStart)
    $lastLog = Get-LastSampleLog $LaneRoot
    if ($null -eq $lastLog) {
        return $DefaultStart
    }
    if ($lastLog.BaseName -notmatch "sample_(\d+)") {
        return $DefaultStart
    }
    $lastOffset = [int]$matches[1]
    $content = Get-Content -Raw -Path $lastLog.FullName -ErrorAction SilentlyContinue
    if ($content -match "sample offset\s+$lastOffset\s+exited with code\s+\d+") {
        return ($lastOffset + 1)
    }
    return $lastOffset
}

function Test-LaneRunning {
    param([string]$LaneRoot)
    $escaped = $LaneRoot.Replace("\", "\\")
    $procs = Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -like "*$LaneRoot*" -or $_.CommandLine -like "*$escaped*"
    }
    return (($procs | Measure-Object).Count -gt 0)
}

function Start-Lane {
    param(
        [hashtable]$Lane,
        [int]$Start
    )
    $laneRoot = Join-Path $RunRoot $Lane.Name
    $count = [Math]::Max(0, $Lane.EndExclusive - $Start)
    if ($count -le 0) {
        Write-WatchdogLog "$($Lane.Name) complete or out of range: start=$Start end=$($Lane.EndExclusive)"
        return
    }
    $launcherLogs = Join-Path $RunRoot "launcher_logs"
    New-Item -ItemType Directory -Force -Path $launcherLogs | Out-Null
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $stdoutPath = Join-Path $launcherLogs "$($Lane.Name)_watchdog_$stamp.out.log"
    $stderrPath = Join-Path $launcherLogs "$($Lane.Name)_watchdog_$stamp.err.log"
    $argList = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "scripts\run_hotpotqa_one_by_one_windows.ps1",
        "-Start", "$Start",
        "-Count", "$count",
        "-RunRoot", $laneRoot,
        "-Dataset", $Dataset,
        "-MaxRounds", "$MaxRounds",
        "-EnvFile", $Lane.EnvFile,
        "-AgentStepTimeoutSeconds", "$AgentStepTimeoutSeconds",
        "-WorkforceProcessTimeoutSeconds", "$WorkforceProcessTimeoutSeconds"
    )
    $process = Start-Process `
        -FilePath "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" `
        -ArgumentList $argList `
        -WorkingDirectory $RepoRoot `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -WindowStyle Hidden `
        -PassThru
    Write-WatchdogLog "started $($Lane.Name): pid=$($process.Id), start=$Start, count=$count, env=$($Lane.EnvFile)"
}

while ($true) {
    foreach ($lane in $lanes) {
        $laneRoot = Join-Path $RunRoot $lane.Name
        if (Test-LaneRunning $laneRoot) {
            Write-WatchdogLog "$($lane.Name) running"
            continue
        }
        $defaultStart = switch ($lane.Name) {
            "key1" { 618 }
            "key2" { 1976 }
            "key3" { 3334 }
            "key4" { 4691 }
            "key5" { 6048 }
        }
        $next = Get-NextOffset $laneRoot $defaultStart
        Write-WatchdogLog "$($lane.Name) not running; restarting from $next"
        Start-Lane $lane $next
    }
    Start-Sleep -Seconds $IntervalSeconds
}
