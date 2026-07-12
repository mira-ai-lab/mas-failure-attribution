param(
    [int]$Start = 618,
    [int]$Count = 1,
    [string]$RunRoot = "D:\mas_runs\hotpotqa_validation_from_0618_fixed\key1",
    [string]$Dataset = "dataset\hotpotqa\hotpotqa_validation_full.parquet",
    [int]$MaxRounds = 3,
    [string]$EnvFile = "",
    [int]$AgentStepTimeoutSeconds = 360,
    [int]$WorkforceProcessTimeoutSeconds = 600
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$ResolvedEnvFile = ""
if ($EnvFile -ne "") {
    if ([System.IO.Path]::IsPathRooted($EnvFile)) {
        $ResolvedEnvFile = $EnvFile
    } else {
        $ResolvedEnvFile = Join-Path $RepoRoot $EnvFile
    }
    $ResolvedEnvFile = (Resolve-Path $ResolvedEnvFile).Path
}

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
        "--dataset", $Dataset,
        "--backend", "OWL",
        "--workspace", $Workspace,
        "--output", $Output,
        "--max_rounds", "$MaxRounds",
        "--max_samples", "1",
        "--sample_offset", "$i",
        "--per_task_rounds"
    )
    if ($ResolvedEnvFile -ne "") {
        $argsList += @("--env_file", $ResolvedEnvFile)
    }

    "[$(Get-Date -Format s)] starting sample offset $i" | Tee-Object -FilePath $logPath
    if ($ResolvedEnvFile -ne "") {
        "[$(Get-Date -Format s)] env file $ResolvedEnvFile" | Tee-Object -FilePath $logPath -Append
    }

    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & .\.venv-win\Scripts\python.exe @argsList *>&1 | Tee-Object -FilePath $logPath -Append
    $ErrorActionPreference = $previousErrorAction

    $exitCode = $LASTEXITCODE
    "[$(Get-Date -Format s)] sample offset $i exited with code $exitCode" | Tee-Object -FilePath $logPath -Append
    if ($exitCode -ne 0) {
        $failedPath = Join-Path $Failed ("sample_{0:D4}.failed.txt" -f $i)
        @(
            "sample_offset=$i",
            "exit_code=$exitCode",
            "log_path=$logPath",
            "failed_at=$(Get-Date -Format s)"
        ) | Set-Content -Path $failedPath -Encoding UTF8
        "[$(Get-Date -Format s)] sample offset $i failed; recorded $failedPath and continuing" |
            Tee-Object -FilePath $logPath -Append
    }
}
