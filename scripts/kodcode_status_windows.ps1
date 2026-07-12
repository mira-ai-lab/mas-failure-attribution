$RunRoot = 'D:\mas_runs\kodcode_full_batches'
$OutRoot = Join-Path $RunRoot 'output'
$LogRoot = Join-Path $RunRoot 'logs'
$Dataset = 'D:\code_re\mas-failure-attribution\dataset\code\kodcode-light-rl-10k-hard.parquet'

$running = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*run_kodcode_windows_lane.ps1*' } | Select-Object ProcessId,CommandLine
Write-Output "running_lanes=$($running.Count)"
$running | ForEach-Object { Write-Output ("pid={0} {1}" -f $_.ProcessId, $_.CommandLine) }

$batchDirs = 0
$complete = 0
$finals = 0
if (Test-Path $OutRoot) {
    $batchDirs = (Get-ChildItem -Directory $OutRoot -ErrorAction SilentlyContinue | Measure-Object).Count
    $complete = (Get-ChildItem -Recurse -Filter eval_kodcode.json $OutRoot -ErrorAction SilentlyContinue | Where-Object { $_.FullName -like '*\kodcode\round_3\eval_kodcode.json' } | Measure-Object).Count
    $finals = (Get-ChildItem -Recurse -Filter *.json $OutRoot -ErrorAction SilentlyContinue | Where-Object { $_.FullName -like '*\final_results\*.json' } | Measure-Object).Count
}

$totalRows = 0
try {
    $py = 'D:\code_re\mas-failure-attribution\.venv-win\Scripts\python.exe'
    $totalRows = (& $py -c "import pandas as pd; print(len(pd.read_parquet(r'$Dataset')))" )
} catch { $totalRows = 1410 }

Write-Output "dataset_rows=$totalRows"
Write-Output "output_batch_dirs=$batchDirs"
Write-Output "complete_batches_with_round3_eval=$complete"
Write-Output "final_results_json=$finals"
Write-Output "approx_rows_complete_by_batches=unknown_mixed_batch_sizes"

if (Test-Path $LogRoot) {
    Get-ChildItem $LogRoot -Filter 'kodcode_windows_*.log' | Sort-Object Name | ForEach-Object {
        Write-Output "--- $($_.Name) tail ---"
        Get-Content $_.FullName -Tail 8 -ErrorAction SilentlyContinue
        $err = $_.FullName + '.err'
        if (Test-Path $err) {
            $errTail = Get-Content $err -Tail 4 -ErrorAction SilentlyContinue
            if ($errTail) {
                Write-Output "--- $([IO.Path]::GetFileName($err)) tail ---"
                $errTail
            }
        }
    }
}
