param(
    [int]$IntervalSeconds = 600,
    [string]$ProjectRoot = 'D:\code_re\mas-failure-attribution',
    [string]$RunRoot = 'D:\mas_runs\kodcode_full_batches'
)

$ErrorActionPreference = 'Continue'
$Script = Join-Path $ProjectRoot 'scripts\run_kodcode_windows_lane.ps1'
$LogRoot = Join-Path $RunRoot 'logs'
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
$WatchdogLog = Join-Path $LogRoot 'kodcode_watchdog_windows.log'

function Write-WatchdogLog([string]$Message) {
    $line = "$(Get-Date -Format o) $Message"
    Add-Content -Encoding UTF8 -Path $WatchdogLog -Value $line
    Write-Output $line
}

$lanes = @(
    @{Name='key1'; Lane='WINDOWS_KEY1'; Env='owl\owl\.env'; BatchDir='batches_strong_key1_0000_0250'; Workspace='workspace_strong_key1'; Prefix='kodcode_strong_key1'; Mode='seq'; Start=0; End=250; Size=1; Min=-1; Max=-1},
    @{Name='key2'; Lane='WINDOWS_KEY2'; Env='owl\owl\.env.key2'; BatchDir='batches_strong_key2_0250_0550'; Workspace='workspace_strong_key2'; Prefix='kodcode_strong_key2'; Mode='seq'; Start=250; End=550; Size=1; Min=-1; Max=-1},
    @{Name='key3'; Lane='WINDOWS_KEY3'; Env='owl\owl\.env.key3'; BatchDir='batches_strong_key3_0550_0900'; Workspace='workspace_strong_key3'; Prefix='kodcode_strong_key3'; Mode='seq'; Start=550; End=900; Size=1; Min=-1; Max=-1},
    @{Name='key4'; Lane='WINDOWS_KEY4'; Env='owl\owl\.env.key4'; BatchDir='batches_strong_key4_0900_1185'; Workspace='workspace_strong_key4'; Prefix='kodcode_strong_key4'; Mode='seq'; Start=900; End=1185; Size=1; Min=-1; Max=-1},
    @{Name='key5'; Lane='WINDOWS_KEY5'; Env='owl\owl\.env.key5'; BatchDir='batches_strong_key5_1185_1410'; Workspace='workspace_strong_key5'; Prefix='kodcode_strong_key5'; Mode='seq'; Start=1185; End=1410; Size=1; Min=-1; Max=-1}
)

function Get-LaneProcesses($laneName) {
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -like '*run_kodcode_windows_lane.ps1*' -and $_.CommandLine -like "*-Lane $laneName*"
    })
}

function Start-Lane($lane) {
    $log = Join-Path $LogRoot ("kodcode_windows_{0}.log" -f $lane.Name)
    $err = $log + '.err'
    $args = @(
        '-NoProfile','-ExecutionPolicy','Bypass','-File',$Script,
        '-Lane',$lane.Lane,
        '-EnvFile',(Join-Path $ProjectRoot $lane.Env),
        '-BatchDirName',$lane.BatchDir,
        '-WorkspaceName',$lane.Workspace,
        '-Prefix',$lane.Prefix,
        '-NameMode',$lane.Mode,
        '-StartRow',[string]$lane.Start,
        '-EndRow',[string]$lane.End,
        '-BatchSize',[string]$lane.Size,
        '-MinBatchIndex',[string]$lane.Min,
        '-MaxBatchIndex',[string]$lane.Max,
        '-ProjectRoot',$ProjectRoot,
        '-RunRoot',$RunRoot
    )
    Start-Process -FilePath 'powershell.exe' -ArgumentList $args -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError $err
    Write-WatchdogLog "started missing lane $($lane.Lane) -> $log"
}

function Test-StrongCompletedBatch([string]$BatchRoot) {
    $finalDir = Join-Path $BatchRoot 'final_results'
    if ((Test-Path $finalDir) -and @(Get-ChildItem -Filter *.json $finalDir -ErrorAction SilentlyContinue).Count -gt 0) {
        return $true
    }
    $round3 = Join-Path $BatchRoot 'kodcode\round_3\eval_kodcode.json'
    if (!(Test-Path $round3)) {
        return $false
    }
    $trace = Get-ChildItem -Recurse -File $BatchRoot -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -eq 'log.json' -or $_.Name -eq 'monitor.json' } |
        Select-Object -First 1
    return $null -ne $trace
}

function Check-Once {
    $runningTotal = 0
    foreach ($lane in $lanes) {
        $procs = Get-LaneProcesses $lane.Lane
        $runningTotal += $procs.Count
        if ($procs.Count -eq 0) {
            Write-WatchdogLog "lane missing: $($lane.Lane)"
            Start-Lane $lane
        } elseif ($procs.Count -gt 1) {
            $ids = ($procs | Select-Object -ExpandProperty ProcessId) -join ','
            Write-WatchdogLog "warning duplicate lane processes for $($lane.Lane): $ids"
        } else {
            Write-WatchdogLog "lane alive: $($lane.Lane) pid=$($procs[0].ProcessId)"
        }
    }

    $complete = 0
    $finals = 0
    $outRoot = Join-Path $RunRoot 'output'
    if (Test-Path $outRoot) {
        $complete = (Get-ChildItem -Directory $outRoot -ErrorAction SilentlyContinue | Where-Object { Test-StrongCompletedBatch $_.FullName } | Measure-Object).Count
        $finals = (Get-ChildItem -Recurse -Filter *.json $outRoot -ErrorAction SilentlyContinue | Where-Object { $_.FullName -like '*\final_results\*.json' } | Measure-Object).Count
    }
    Write-WatchdogLog "summary lane_processes=$runningTotal strong_complete_batches=$complete final_results=$finals"
}

Write-WatchdogLog "watchdog started interval=${IntervalSeconds}s project=$ProjectRoot runroot=$RunRoot"
while ($true) {
    try {
        Check-Once
    } catch {
        Write-WatchdogLog "watchdog error: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds $IntervalSeconds
}
