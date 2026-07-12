# Dataset Layout / 数据集目录说明

The code expects datasets to be placed under this directory. Large raw datasets
and generated parquet files are data assets; they are not part of the clean code
commit unless explicitly requested.

代码默认从本目录读取数据集。大型原始数据和生成的 parquet 属于数据资产；除非明确要求，否则不放进干净代码提交。

## Expected Files / 预期文件

| Dataset | Required by runner | Can be generated from |
|---|---|---|
| KodCode | `code/kodcode-light-rl-10k-hard.parquet` | external prepared parquet |
| GAIA | `gaia/gaia_validation.parquet` | `gaia/2023/validation/metadata.parquet` |
| BrowseComp | `browsecomp/browsecomp.parquet` | `browsecomp/browse_comp_test_set.csv` |
| AssistantBench | `assistantbench/assistantbench_dev.parquet` | `assistantbench/assistant_bench_v1.0_dev.jsonl` |
| HotpotQA | `hotpotqa/hotpotqa_validation_full.parquet` | external prepared parquet |

## Preparation Commands / 转换命令

```powershell
.\.venv-win\Scripts\python.exe scripts\prepare_gaia_failure_dataset.py `
  --input dataset\gaia\2023\validation\metadata.parquet `
  --output dataset\gaia\gaia_validation.parquet

.\.venv-win\Scripts\python.exe scripts\prepare_browsecomp_dataset.py `
  --input dataset\browsecomp\browse_comp_test_set.csv `
  --output dataset\browsecomp\browsecomp.parquet

.\.venv-win\Scripts\python.exe scripts\prepare_assistantbench_dataset.py `
  --input dataset\assistantbench\assistant_bench_v1.0_dev.jsonl `
  --output dataset\assistantbench\assistantbench_dev.parquet
```

The generic runner `scripts/run_owl_dataset_windows.ps1` automatically runs the
needed preparation step for GAIA, BrowseComp, and AssistantBench if the default
parquet file is missing and the raw file exists.

通用 runner `scripts/run_owl_dataset_windows.ps1` 会在默认 parquet 缺失且原始文件存在时，自动为 GAIA、BrowseComp、AssistantBench 执行转换。
