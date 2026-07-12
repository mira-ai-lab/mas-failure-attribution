param(
    [Parameter(Mandatory=$true)][string]$Lane,
    [Parameter(Mandatory=$true)][string]$EnvFile,
    [Parameter(Mandatory=$true)][string]$BatchDirName,
    [Parameter(Mandatory=$true)][string]$WorkspaceName,
    [Parameter(Mandatory=$true)][string]$Prefix,
    [ValidateSet('batch10','seq')][string]$NameMode = 'batch10',
    [int]$StartRow = 0,
    [int]$EndRow = -1,
    [int]$BatchSize = 10,
    [int]$MinBatchIndex = -1,
    [int]$MaxBatchIndex = -1,
    [string]$ProjectRoot = 'D:\code_re\mas-failure-attribution',
    [string]$RunRoot = 'D:\mas_runs\kodcode_full_batches',
    [string]$Dataset = 'D:\code_re\mas-failure-attribution\dataset\code\kodcode-light-rl-10k-hard.parquet',
    [int]$BatchTimeoutSeconds = 1200
)

$ErrorActionPreference = 'Stop'
$Python = Join-Path $ProjectRoot '.venv-win\Scripts\python.exe'
if (!(Test-Path $Python)) { throw "Python venv not found: $Python" }
if (!(Test-Path $EnvFile)) { throw "Env file not found: $EnvFile" }

Set-Location $ProjectRoot
$env:PYTHONPATH = $ProjectRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

Get-Content $EnvFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq '' -or $line.StartsWith('#')) { return }
    $idx = $line.IndexOf('=')
    if ($idx -le 0) { return }
    $key = $line.Substring(0, $idx).Trim()
    $val = $line.Substring($idx + 1).Trim().Trim('"').Trim("'")
    [Environment]::SetEnvironmentVariable($key, $val, 'Process')
}

$BatchDir = Join-Path $RunRoot $BatchDirName
$OutRoot = Join-Path $RunRoot 'output'
$WorkspaceRoot = Join-Path $RunRoot $WorkspaceName
$LogRoot = Join-Path $RunRoot 'logs'
New-Item -ItemType Directory -Force -Path $BatchDir,$OutRoot,$WorkspaceRoot,$LogRoot | Out-Null
$env:MAS_FAIL_ATTR_LOG = Join-Path $LogRoot ("mas-fail-attr_$Lane.log")

$env:KOD_DATASET = $Dataset
$env:KOD_BATCH_DIR = $BatchDir
$env:KOD_PREFIX = $Prefix
$env:KOD_NAME_MODE = $NameMode
$env:KOD_START_ROW = [string]$StartRow
$env:KOD_END_ROW = [string]$EndRow
$env:KOD_BATCH_SIZE = [string]$BatchSize

function Get-CompletedSpans {
    param([string]$Root)
    $spans = @()
    if (!(Test-Path $Root)) { return $spans }

    Get-ChildItem -Recurse -Filter *.json $Root -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -like '*\final_results\*.json' } |
        ForEach-Object {
            $batchName = $_.FullName.Substring($Root.Length + 1).Split('\')[0]
            if ($batchName -match '_(\d{4})_(\d{4})$') {
                $spans += [pscustomobject]@{
                    Batch = $batchName
                    Start = [int]$matches[1]
                    End = [int]$matches[2]
                }
            }
        }

    Get-ChildItem -Recurse -Filter eval_kodcode.json $Root -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -like '*\kodcode\round_3\eval_kodcode.json' } |
        ForEach-Object {
            $batchName = $_.FullName.Substring($Root.Length + 1).Split('\')[0]
            $batchRoot = Join-Path $Root $batchName
            $hasTrace = $false
            if (Test-Path $batchRoot) {
                $trace = Get-ChildItem -Recurse -File $batchRoot -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -eq 'log.json' -or $_.Name -eq 'monitor.json' } |
                    Select-Object -First 1
                $hasTrace = $null -ne $trace
            }
            if ($hasTrace -and $batchName -match '_(\d{4})_(\d{4})$') {
                $spans += [pscustomobject]@{
                    Batch = $batchName
                    Start = [int]$matches[1]
                    End = [int]$matches[2]
                }
            }
        }
    return $spans
}

function Test-RangeComplete {
    param(
        [object[]]$Spans,
        [int]$Start,
        [int]$End
    )
    foreach ($span in $Spans) {
        if ($span.Start -le $Start -and $span.End -ge $End) {
            return $true
        }
    }
    return $false
}

$prepare = @"
import os
from pathlib import Path
import pandas as pd

src = Path(os.environ['KOD_DATASET'])
out = Path(os.environ['KOD_BATCH_DIR'])
prefix = os.environ['KOD_PREFIX']
mode = os.environ['KOD_NAME_MODE']
start = int(os.environ['KOD_START_ROW'])
end_row = int(os.environ['KOD_END_ROW'])
batch_size = int(os.environ['KOD_BATCH_SIZE'])

out.mkdir(parents=True, exist_ok=True)
df = pd.read_parquet(src)
end_limit = len(df) if end_row < 0 else min(end_row, len(df))
seq = 0
for i in range(start, end_limit, batch_size):
    end = min(i + batch_size, end_limit)
    if mode == 'batch10':
        name = f"{prefix}_{i // batch_size:04d}_{i:04d}_{end:04d}.parquet"
    else:
        name = f"{prefix}_{seq:04d}_{i:04d}_{end:04d}.parquet"
    path = out / name
    if not path.exists():
        df.iloc[i:end].to_parquet(path, index=False)
    seq += 1
print(f"prepared_rows={len(df)} rows={start}:{end_limit} batch_size={batch_size} prepared_batches={seq}", flush=True)
"@
$prepare | & $Python -

$CompletedSpans = @(Get-CompletedSpans -Root $OutRoot)

Get-ChildItem -Path $BatchDir -Filter "$Prefix*.parquet" | Sort-Object Name | ForEach-Object {
    $batch = $_.FullName
    $name = $_.BaseName
    $rowStart = $null
    $rowEnd = $null
    if ($name -match '_(\d{4})_(\d{4})$') {
        $rowStart = [int]$matches[1]
        $rowEnd = [int]$matches[2]
    }
    if ($NameMode -eq 'batch10') {
        $parts = $name.Substring($Prefix.Length + 1).Split('_')
        $batchIndex = [int]$parts[0]
        if ($MinBatchIndex -ge 0 -and $batchIndex -lt $MinBatchIndex) { return }
        if ($MaxBatchIndex -ge 0 -and $batchIndex -ge $MaxBatchIndex) { return }
    }

    $complete = Join-Path $OutRoot "$name\kodcode\round_3\eval_kodcode.json"
    $finalResultDir = Join-Path $OutRoot "$name\final_results"
    $hasFinalResult = (Test-Path $finalResultDir) -and @(Get-ChildItem -Filter *.json $finalResultDir -ErrorAction SilentlyContinue).Count -gt 0
    $hasTrace = $false
    $outputRootForBatch = Join-Path $OutRoot $name
    if (Test-Path $outputRootForBatch) {
        $trace = Get-ChildItem -Recurse -File $outputRootForBatch -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -eq 'log.json' -or $_.Name -eq 'monitor.json' } |
            Select-Object -First 1
        $hasTrace = $null -ne $trace
    }
    if ($hasFinalResult -or ((Test-Path $complete) -and $hasTrace)) {
        Write-Output "===== $Lane SKIP COMPLETE $name $(Get-Date -Format o) ====="
        return
    }
    if ($null -ne $rowStart -and $null -ne $rowEnd -and (Test-RangeComplete -Spans $CompletedSpans -Start $rowStart -End $rowEnd)) {
        Write-Output "===== $Lane SKIP COVERED $name rows=${rowStart}:${rowEnd} $(Get-Date -Format o) ====="
        return
    }

    Write-Output "===== $Lane START $name $(Get-Date -Format o) ====="
    $workspacePath = Join-Path $WorkspaceRoot $name
    $outputPath = Join-Path $OutRoot $name
    $arguments = @(
        'main.py',
        '--dataset', $batch,
        '--backend', 'OWL',
        '--workspace', $workspacePath,
        '--output', $outputPath,
        '--max_rounds', '3',
        '--skip_existing'
    )
    $proc = Start-Process -FilePath $Python -ArgumentList $arguments -NoNewWindow -PassThru
    $lastActivity = Get-Date
    while (-not $proc.WaitForExit(60000)) {
        $latestWrite = $null
        foreach ($path in @($outputPath, $workspacePath)) {
            if (Test-Path $path) {
                $candidate = Get-ChildItem -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue |
                    Sort-Object LastWriteTime -Descending |
                    Select-Object -First 1
                if ($null -ne $candidate -and ($null -eq $latestWrite -or $candidate.LastWriteTime -gt $latestWrite)) {
                    $latestWrite = $candidate.LastWriteTime
                }
            }
        }
        if ($null -ne $latestWrite -and $latestWrite -gt $lastActivity) {
            $lastActivity = $latestWrite
        }
        $idleSeconds = ((Get-Date) - $lastActivity).TotalSeconds
        if ($idleSeconds -lt $BatchTimeoutSeconds) {
            continue
        }

        Write-Output "===== $Lane TIMEOUT $name idle ${BatchTimeoutSeconds}s last_activity=$($lastActivity.ToString('o')) $(Get-Date -Format o) ====="
        & taskkill.exe /PID $proc.Id /T /F | Write-Output
        if (Test-Path $outputPath) { Remove-Item -LiteralPath $outputPath -Recurse -Force }
        if (Test-Path $workspacePath) { Remove-Item -LiteralPath $workspacePath -Recurse -Force }
        exit 124
    }
    $status = $proc.ExitCode
    Write-Output "===== $Lane END $name status=$status $(Get-Date -Format o) ====="
}
