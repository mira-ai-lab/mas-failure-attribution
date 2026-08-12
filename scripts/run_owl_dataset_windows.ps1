param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("kodcode", "gaia", "browsecomp", "assistantbench", "hotpotqa")]
    [string]$DatasetName,
    [int]$Start = 0,
    [int]$Count = 1,
    [string]$RunRoot = "",
    [string]$Dataset = "",
    [int]$MaxRounds = 3,
    [string]$EnvFile = "owl\owl\.env.gaia",
    [int]$AgentStepTimeoutSeconds = 360,
    [int]$WorkforceProcessTimeoutSeconds = 600,
    [bool]$ContinueOnFailure = $true
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$Python = Join-Path $RepoRoot ".venv-win\Scripts\python.exe"
if (!(Test-Path $Python)) {
    throw "Python venv not found: $Python"
}

function Resolve-RepoPath {
    param([string]$PathValue)
    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return (Resolve-Path $PathValue).Path
    }
    return (Resolve-Path (Join-Path $RepoRoot $PathValue)).Path
}

function Ensure-PreparedDataset {
    param([string]$Name, [string]$PathValue)
    if (Test-Path (Join-Path $RepoRoot $PathValue)) {
        return
    }
    if (Test-Path $PathValue) {
        return
    }

    if ($Name -eq "browsecomp") {
        & $Python scripts\prepare_browsecomp_dataset.py `
            --input dataset\browsecomp\browse_comp_test_set.csv `
            --output $PathValue
        return
    }
    if ($Name -eq "gaia") {
        & $Python scripts\prepare_gaia_failure_dataset.py `
            --input dataset\gaia\2023\validation\metadata.parquet `
            --output $PathValue
        return
    }
    if ($Name -eq "assistantbench") {
        & $Python scripts\prepare_assistantbench_dataset.py `
            --input dataset\assistantbench\assistant_bench_v1.0_dev.jsonl `
            --output $PathValue
        return
    }
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
Ensure-PreparedDataset -Name $DatasetName -PathValue $Dataset
$DatasetPath = Resolve-RepoPath $Dataset

if ($RunRoot -eq "") {
    $RunRoot = Join-Path "D:\mas_runs" "${DatasetName}_one_by_one"
}

$ResolvedEnvFile = ""
if ($EnvFile -ne "") {
    $ResolvedEnvFile = Resolve-RepoPath $EnvFile
}

$env:PYTHONPATH = $RepoRoot
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:OWL_AGENT_STEP_TIMEOUT_SECONDS = "$AgentStepTimeoutSeconds"
$env:OWL_WORKFORCE_PROCESS_TIMEOUT_SECONDS = "$WorkforceProcessTimeoutSeconds"

$Workspace = Join-Path $RunRoot "workspace"
$Output = Join-Path $RunRoot "output"
$Logs = Join-Path $RunRoot "process_logs"
$Failed = Join-Path $RunRoot "failed_samples"
New-Item -ItemType Directory -Force -Path $Workspace, $Output, $Logs, $Failed | Out-Null

for ($i = $Start; $i -lt ($Start + $Count); $i++) {
    $logPath = Join-Path $Logs ("sample_{0:D4}.log" -f $i)
    $argsList = @(
        "main.py",
        "--dataset", $DatasetPath,
        "--backend", "OWL",
        "--workspace", $Workspace,
        "--output", $Output,
        "--max_rounds", "$MaxRounds",
        "--max_samples", "1",
        "--sample_offset", "$i"
    )
    if ($ResolvedEnvFile -ne "") {
        $argsList += @("--env_file", $ResolvedEnvFile)
    }

    "[$(Get-Date -Format s)] starting $DatasetName sample offset $i" | Tee-Object -FilePath $logPath
    "[$(Get-Date -Format s)] dataset $DatasetPath" | Tee-Object -FilePath $logPath -Append
    if ($ResolvedEnvFile -ne "") {
        "[$(Get-Date -Format s)] env file $ResolvedEnvFile" | Tee-Object -FilePath $logPath -Append
    }

    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $Python @argsList *>&1 | Tee-Object -FilePath $logPath -Append
    $ErrorActionPreference = $previousErrorAction

    $exitCode = $LASTEXITCODE
    "[$(Get-Date -Format s)] sample offset $i exited with code $exitCode" | Tee-Object -FilePath $logPath -Append
    if ($exitCode -ne 0) {
        $failedPath = Join-Path $Failed ("sample_{0:D4}.failed.txt" -f $i)
        @(
            "dataset=$DatasetName",
            "sample_offset=$i",
            "exit_code=$exitCode",
            "log_path=$logPath",
            "failed_at=$(Get-Date -Format s)"
        ) | Set-Content -Path $failedPath -Encoding UTF8
        if (-not $ContinueOnFailure) {
            throw "sample offset $i failed with exit code $exitCode"
        }
    }
}
