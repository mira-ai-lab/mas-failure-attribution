param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("kodcode", "gaia", "browsecomp", "assistantbench", "hotpotqa")]
    [string]$DatasetName,
    [string]$RunRoot = "",
    [string]$Dataset = "",
    [int]$Start = 0,
    [int]$Count = -1,
    [int]$MaxRounds = 3,
    [string[]]$EnvFiles = @(
        "owl\owl\.env.gaia",
        "owl\owl\.env.key2",
        "owl\owl\.env.key3",
        "owl\owl\.env.key4",
        "owl\owl\.env.key5"
    ),
    [int]$AgentStepTimeoutSeconds = 360,
    [int]$WorkforceProcessTimeoutSeconds = 600
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$Python = Join-Path $RepoRoot ".venv-win\Scripts\python.exe"
if (!(Test-Path $Python)) {
    throw "Python venv not found: $Python"
}

if ($Dataset -eq "") {
    $Dataset = switch ($DatasetName) {
        "kodcode" { "dataset\code\kodcode-light-rl-10k-hard.parquet" }
        "gaia" { "dataset\gaia\gaia_validation.parquet" }
        "browsecomp" { "dataset\browsecomp\browsecomp.parquet" }
        "assistantbench" { "dataset\assistantbench\assistantbench_dev.parquet" }
        "hotpotqa" { "dataset\hotpotqa\hotpotqa_validation_full.parquet" }
    }
}

# Reuse the single-lane runner once to auto-prepare datasets such as GAIA,
# BrowseComp, or AssistantBench when their parquet file is not present yet.
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\run_owl_dataset_windows.ps1 `
    -DatasetName $DatasetName `
    -Start 0 `
    -Count 0 `
    -RunRoot (Join-Path "D:\mas_runs" "${DatasetName}_prepare_probe") `
    -Dataset $Dataset `
    -MaxRounds $MaxRounds `
    -EnvFile $EnvFiles[0]

$DatasetPath = if ([System.IO.Path]::IsPathRooted($Dataset)) {
    (Resolve-Path $Dataset).Path
} else {
    (Resolve-Path (Join-Path $RepoRoot $Dataset)).Path
}
$rowCount = [int](& $Python -c "import sys, pandas as pd; print(len(pd.read_parquet(sys.argv[1])))" $DatasetPath)
if ($Count -lt 0) {
    $Count = $rowCount - $Start
}
if ($Count -lt 0) {
    throw "Invalid Start/Count: start=$Start count=$Count row_count=$rowCount"
}

if ($RunRoot -eq "") {
    $RunRoot = Join-Path "D:\mas_runs" "${DatasetName}_multi_env"
}

$launcherLogs = Join-Path $RunRoot "launcher_logs"
New-Item -ItemType Directory -Force -Path $launcherLogs | Out-Null

$laneCount = $EnvFiles.Count
$chunk = [Math]::Ceiling($Count / [double]$laneCount)
for ($laneIndex = 0; $laneIndex -lt $laneCount; $laneIndex++) {
    $laneStart = $Start + [int]($laneIndex * $chunk)
    $remaining = $Start + $Count - $laneStart
    if ($remaining -le 0) {
        continue
    }
    $laneCountItems = [Math]::Min([int]$chunk, $remaining)
    $laneName = "key$($laneIndex + 1)"
    $laneRoot = Join-Path $RunRoot $laneName
    $stdoutPath = Join-Path $launcherLogs ($laneName + ".launcher.out.log")
    $stderrPath = Join-Path $launcherLogs ($laneName + ".launcher.err.log")
    $argList = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "scripts\run_owl_dataset_windows.ps1",
        "-DatasetName", $DatasetName,
        "-Start", "$laneStart",
        "-Count", "$laneCountItems",
        "-RunRoot", $laneRoot,
        "-Dataset", $DatasetPath,
        "-MaxRounds", "$MaxRounds",
        "-EnvFile", $EnvFiles[$laneIndex],
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
    "started $laneName pid=$($process.Id) dataset=$DatasetName start=$laneStart count=$laneCountItems env=$($EnvFiles[$laneIndex]) root=$laneRoot"
}
