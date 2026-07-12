# Script Index / 脚本索引

Use the generic scripts first. Dataset-specific historical scripts are kept for
reproducibility.

优先使用通用脚本。历史数据集专用脚本保留用于复现实验。

## Generic OWL runners / 通用 OWL 入口

| Script | Purpose / 用途 |
|---|---|
| `run_owl_dataset_windows.ps1` | Run one dataset lane sequentially, one sample through all rounds before the next sample. / 单路顺序运行，一个样本完整跑完所有 round 后再跑下一条。 |
| `start_owl_dataset_lanes_windows.ps1` | Split one dataset across multiple env files and start parallel lanes. / 按多个 env 文件分片并行启动。 |
| `status_owl_runs_windows.ps1` | Report latest sample log, failed count, and final result count. / 汇报最新样本、失败数量和 final_results 数量。 |

These scripts are the preferred handoff interface. Older dataset-specific
scripts are retained only to reproduce previous experiments.

这些脚本是优先使用的交接入口。旧的数据集专用脚本仅用于复现历史实验。

Example:

示例：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\run_owl_dataset_windows.ps1 `
  -DatasetName hotpotqa `
  -Start 0 `
  -Count 1 `
  -RunRoot D:\mas_runs\hotpotqa_one_by_one `
  -EnvFile owl\owl\.env.gaia
```

## Dataset preparation / 数据集转换

| Dataset | Script |
|---|---|
| GAIA | `prepare_gaia_failure_dataset.py` |
| BrowseComp | `prepare_browsecomp_dataset.py` |
| AssistantBench | `prepare_assistantbench_dataset.py` |

The generic runner automatically calls these preparation scripts when the
default parquet file is missing and raw files are present.

当默认 parquet 缺失但原始文件存在时，通用 runner 会自动调用这些转换脚本。

## Verified smoke-test root / 已验证测试目录

One complete sample per dataset was tested under:

每个数据集各完整跑过一条样本，测试目录为：

```text
D:\mas_runs\handoff_full_one
```

See `README_HANDOFF_ZH_EN.md` for the exact success/failure interpretation.

具体成功/失败解释见 `README_HANDOFF_ZH_EN.md`。

## Historical dataset scripts / 历史专用脚本

### KodCode

```text
kodcode_start_persistent.sh
kodcode_start_watchdog_windows.ps1
kodcode_start_windows.ps1
kodcode_status.sh
kodcode_status_windows.ps1
kodcode_watchdog_windows.ps1
run_kodcode_windows_lane.ps1
run_kodcode_*.sh
```

### BrowseComp

```text
run_browsecomp_one_by_one_windows.ps1
start_browsecomp_multi_env_windows.ps1
```

### GAIA

```text
run_gaia_owl.py
```

### HotpotQA

```text
run_hotpotqa_one_by_one_windows.ps1
start_hotpotqa_multi_env_windows.ps1
watch_hotpotqa_windows.ps1
```
