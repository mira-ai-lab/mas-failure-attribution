$ErrorActionPreference = 'Stop'
$ProjectRoot = 'D:\code_re\mas-failure-attribution'
$RunRoot = 'D:\mas_runs\kodcode_full_batches'
$Script = Join-Path $ProjectRoot 'scripts\run_kodcode_windows_lane.ps1'
$LogRoot = Join-Path $RunRoot 'logs'
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null

$lanes = @(
    @{Name='key1'; Env='owl\owl\.env'; BatchDir='batches'; Workspace='workspace'; Prefix='kodcode_batch'; Mode='batch10'; Start=0; End=-1; Size=10; Min=-1; Max=71},
    @{Name='key2'; Env='owl\owl\.env.key2'; BatchDir='batches'; Workspace='workspace_key2'; Prefix='kodcode_batch'; Mode='batch10'; Start=0; End=-1; Size=10; Min=71; Max=90},
    @{Name='key3'; Env='owl\owl\.env.key3'; BatchDir='batches_b3_key3'; Workspace='workspace_key3'; Prefix='kodcode_b3_key3'; Mode='seq'; Start=900; End=1071; Size=3; Min=-1; Max=-1},
    @{Name='key4'; Env='owl\owl\.env.key4'; BatchDir='batches_b3_key4'; Workspace='workspace_key4'; Prefix='kodcode_b3_key4'; Mode='seq'; Start=1071; End=1242; Size=3; Min=-1; Max=-1},
    @{Name='key5'; Env='owl\owl\.env.key5'; BatchDir='batches_b3_key5'; Workspace='workspace_key5'; Prefix='kodcode_b3_key5'; Mode='seq'; Start=1242; End=1410; Size=3; Min=-1; Max=-1}
)

foreach ($lane in $lanes) {
    $log = Join-Path $LogRoot ("kodcode_windows_{0}.log" -f $lane.Name)
    $args = @(
        '-NoProfile','-ExecutionPolicy','Bypass','-File',$Script,
        '-Lane',("WINDOWS_{0}" -f $lane.Name.ToUpper()),
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
    Start-Process -FilePath 'powershell.exe' -ArgumentList $args -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError ($log + '.err')
    Write-Output "started: $($lane.Name) -> $log"
}
