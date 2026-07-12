param(
    [Parameter(Mandatory=$true)]
    [string]$RunRoot
)

$ErrorActionPreference = "Stop"

if (!(Test-Path $RunRoot)) {
    throw "RunRoot not found: $RunRoot"
}

function Get-LaneRows {
    param([string]$Root)
    if (Test-Path (Join-Path $Root "process_logs")) {
        return @([pscustomobject]@{ Name = (Split-Path -Leaf $Root); FullName = $Root })
    }
    return @(Get-ChildItem $Root -Directory -ErrorAction SilentlyContinue | Where-Object {
        Test-Path (Join-Path $_.FullName "process_logs")
    } | Sort-Object Name)
}

$totalLogs = 0
$totalFinal = 0
$totalFailed = 0

Get-LaneRows -Root $RunRoot | ForEach-Object {
    $laneRoot = $_.FullName
    $logs = Join-Path $laneRoot "process_logs"
    $failed = Join-Path $laneRoot "failed_samples"
    $finals = Join-Path $laneRoot "output\final_results"

    $lastLog = Get-ChildItem $logs -Filter "sample_*.log" -File -ErrorAction SilentlyContinue |
        Sort-Object Name |
        Select-Object -Last 1
    $logCount = (Get-ChildItem $logs -Filter "sample_*.log" -File -ErrorAction SilentlyContinue | Measure-Object).Count
    $failedCount = (Get-ChildItem $failed -Filter "*.failed.txt" -File -ErrorAction SilentlyContinue | Measure-Object).Count
    $finalCount = (Get-ChildItem $finals -Filter "*.json" -File -ErrorAction SilentlyContinue | Measure-Object).Count

    $totalLogs += $logCount
    $totalFailed += $failedCount
    $totalFinal += $finalCount

    $latest = "none"
    $status = "none"
    if ($lastLog) {
        $latest = $lastLog.BaseName
        $content = Get-Content -Raw $lastLog.FullName -ErrorAction SilentlyContinue
        if ($content -match "exited with code\s+\d+") {
            $status = "finished"
        } else {
            $status = "running_or_interrupted"
        }
    }

    [pscustomobject]@{
        Lane = $_.Name
        LatestLog = $latest
        LatestStatus = $status
        SampleLogs = $logCount
        Failed = $failedCount
        FinalResults = $finalCount
    }
} | Format-Table -AutoSize

"TOTAL sample_logs=$totalLogs failed=$totalFailed final_results=$totalFinal"
