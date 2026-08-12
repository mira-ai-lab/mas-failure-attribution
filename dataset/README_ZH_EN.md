# Dataset Layout / 数据集目录说明

The code expects datasets to be placed under this directory. Large raw datasets
and generated parquet files are data assets; they are not part of the clean code
commit unless explicitly requested.

代码默认从本目录读取数据集。大型原始数据和生成的 parquet 属于数据资产；除非明确要求，否则不放进干净代码提交。

## Default Dataset Resolution / 默认数据集解析

`main.py --dataset` now accepts either:

`main.py --dataset` 现在既可以接收：

- a concrete file path / 一个明确的文件路径
- a dataset alias such as `gaia` or `kodcode` / 一个数据集别名，例如 `gaia`、`kodcode`

Current built-in aliases resolve to these default files:

当前内置别名会优先解析到这些默认文件：

| Dataset | Required by runner | Can be generated from |
|---|---|---|
| KodCode | `code/kodcode-light-rl-10k-hard.parquet` | external prepared parquet |
| GAIA | `gaia/gaia_validation.parquet` if present, otherwise `gaia_dataset_raw/2023/validation/metadata.parquet` | raw GAIA validation metadata |
| HotpotQA | `hotpotqa.parquet` | external prepared parquet |
| HumanEval | `humaneval.parquet` | external prepared parquet |
| MBPP | `mbpp.parquet` | external prepared parquet |
| SWE-bench | `swebench.parquet` | external prepared parquet |

Notes:

说明：

- `gaia` intentionally maps to the validation split, not the test split.
- If you pass an explicit path, it is used as-is and bypasses alias resolution.

- `gaia` 会刻意映射到 validation，而不是 test。
- 如果你传的是显式路径，则直接使用该路径，不走别名解析。

## Optional Prepared Files / 可选预处理文件

Some datasets also have prepared files or raw sources used by scripts, even if
they are not part of the current built-in alias table.

有些数据集还存在脚本会使用到的预处理文件或原始文件，即使它们不在当前内置别名表中。

| Dataset | Prepared file / 预处理文件 | Raw source / 原始来源 |
|---|---|---|
| BrowseComp | `browsecomp/browsecomp.parquet` | `browsecomp/browse_comp_test_set.csv` |
| AssistantBench | `assistantbench/assistantbench_dev.parquet` | `assistantbench/assistant_bench_v1.0_dev.jsonl` |

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
