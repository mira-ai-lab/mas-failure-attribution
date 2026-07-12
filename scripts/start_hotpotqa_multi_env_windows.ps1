param(
    [string]$RunRoot = "D:\mas_runs\hotpotqa_validation_from_0618_fixed",
    [string]$Dataset = "dataset\hotpotqa\hotpotqa_validation_full.parquet",
    [int]$MaxRounds = 3,
    [int]$AgentStepTimeoutSeconds = 360,
    [int]$WorkforceProcessTimeoutSeconds = 600
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$lanes = @(
    @{Name="key1"; EnvFile="owl\owl\.env.gaia"; Start=618; Count=1358},
    @{Name="key2"; EnvFile="owl\owl\.env.key2"; Start=1976; Count=1358},
    @{Name="key3"; EnvFile="owl\owl\.env.key3"; Start=3334; Count=1357},
    @{Name="key4"; EnvFile="owl\owl\.env.key4"; Start=4691; Count=1357},
    @{Name="key5"; EnvFile="owl\owl\.env.key5"; Start=6048; Count=1357}
)

$launcherLogs = Join-Path $RunRoot "launcher_logs"
New-Item -ItemType Directory -Force -Path $launcherLogs | Out-Null

foreach ($lane in $lanes) {
    $laneRoot = Join-Path $RunRoot $lane.Name
    $stdoutPath = Join-Path $launcherLogs ($lane.Name + ".launcher.out.log")
    $stderrPath = Join-Path $launcherLogs ($lane.Name + ".launcher.err.log")
    $argList = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "scripts\run_hotpotqa_one_by_one_windows.ps1",
        "-Start", "$($lane.Start)",
        "-Count", "$($lane.Count)",
        "-RunRoot", $laneRoot,
        "-Dataset", $Dataset,
        "-MaxRounds", "$MaxRounds",
        "-EnvFile", $lane.EnvFile,
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
    "started $($lane.Name): pid=$($process.Id), start=$($lane.Start), count=$($lane.Count), env=$($lane.EnvFile), root=$laneRoot"
}
