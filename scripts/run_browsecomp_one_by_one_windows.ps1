param(
    [int]$Start = 0,
    [int]$Count = 5,
    [string]$RunRoot = "D:\mas_runs\browsecomp_one_by_one_test",
    [string]$Dataset = "dataset\browsecomp\browsecomp.parquet",
    [int]$MaxRounds = 3,
    [string]$EnvFile = ""
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$Workspace = Join-Path $RunRoot "workspace"
$Output = Join-Path $RunRoot "output"
$Logs = Join-Path $RunRoot "process_logs"
New-Item -ItemType Directory -Force -Path $Workspace, $Output, $Logs | Out-Null

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
    if ($EnvFile -ne "") {
        $argsList += @("--env_file", $EnvFile)
    }

    "[$(Get-Date -Format s)] starting sample offset $i" | Tee-Object -FilePath $logPath
    if ($EnvFile -ne "") {
        "[$(Get-Date -Format s)] env file $EnvFile" | Tee-Object -FilePath $logPath -Append
    }
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & .\.venv-win\Scripts\python.exe @argsList *>&1 | Tee-Object -FilePath $logPath -Append
    $ErrorActionPreference = $previousErrorAction
    $exitCode = $LASTEXITCODE
    "[$(Get-Date -Format s)] sample offset $i exited with code $exitCode" | Tee-Object -FilePath $logPath -Append
    if ($exitCode -ne 0) {
        throw "sample offset $i failed with exit code $exitCode"
    }
}
