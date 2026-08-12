# MAS Failure Attribution OWL Handoff / OWL 交接说明

This file is the handoff entry point for the current working tree. It documents
the runnable OWL pipeline, dataset locations, commands, outputs, and files that
should not be committed.

本文是当前工作目录的交接入口，覆盖 OWL 跑数链路、数据集位置、运行命令、输出位置，以及不应提交的本地文件。

---

## 1. Scope / 交接范围

This project extends `mas-failure-attribution` with an OWL backend and dataset
runners for:

当前项目在 `mas-failure-attribution` 上增加了 OWL backend，并整理了以下数据集的运行入口：

| Dataset | Data source | Default input |
|---|---|---|
| KodCode | `kodcode` | `dataset/code/kodcode-light-rl-10k-hard.parquet` |
| GAIA | `gaia` | `dataset/gaia/gaia_validation.parquet` |
| BrowseComp | `browsecomp` | `dataset/browsecomp/browsecomp.parquet` |
| AssistantBench | `assistantbench` | `dataset/assistantbench/assistantbench_dev.parquet` |
| HotpotQA | `hotpotqa` | `dataset/hotpotqa/hotpotqa_validation_full.parquet` |

GAIA, BrowseComp, and AssistantBench parquet files can be prepared automatically
by the new runner if the raw files are present in the expected dataset folders.

如果 GAIA、BrowseComp、AssistantBench 的 parquet 不存在，只要原始文件在对应目录，新的 runner 会自动转换。

---

## 2. Environment / 环境

Recommended runtime:

推荐运行环境：

```powershell
cd D:\code_re\mas-failure-attribution
.\.venv-win\Scripts\python.exe --version
```

OWL dependency:

OWL 依赖：

```bash
pip install "owl @ git+https://github.com/camel-ai/owl.git@98d150d29b4a1f0d362695df936dc3da2ced875a"
```

The project now targets the upstream `camel-ai/owl` package installation model
instead of requiring a sibling local `owl/` checkout. The pinned commit above
currently reports OWL package version `0.0.1` in the upstream `pyproject.toml`.
Because the upstream repository does not currently publish GitHub release tags,
we pin the git commit for reproducibility.

当前项目已改为依赖上游 `camel-ai/owl` 的包安装方式，不再要求旁边必须有一个本地 `owl/` 源码目录。上面固定的 commit 在上游 `pyproject.toml` 中声明的包版本号是 `0.0.1`。由于上游仓库暂时没有 GitHub release/tag，这里用 git commit 固定版本，便于复现。

API keys are read from OWL env files:

API key 从 OWL env 文件读取：

```text
MAS_FA_ENV_FILE=/path/to/owl.env
# or
OWL_ENV_FILE=/path/to/owl.env
```

Each file should define:

每个文件应包含：

```text
VLLM_API_URL=https://api.qingyuntop.top/v1
VLLM_MODEL_NAME=gpt-4o-mini
VLLM_API_KEY=<fill-your-key>
```

Do not commit `.env*` files with real keys.

不要提交包含真实 key 的 `.env*` 文件。

Additional runtime notes:

额外运行依赖说明：

- Web-research datasets (`gaia`, `browsecomp`, `assistantbench`, `hotpotqa`) call the OWL/CAMEL web, document, file, code, image, and spreadsheet tools.
- Coding datasets (`kodcode`) additionally require a local `sandbox_fusion`-compatible service at `http://localhost:8080/`.
- If KodCode reaches evaluation while the sandbox is down, the run will fail with `localhost:8080 /run_code connection refused`.
- Some GAIA tasks require PDF parsing. If PDF extraction falls back to `read_file`, missing `markitdown` can cause failures such as `No module named 'markitdown'`.

- Web 检索类数据集（`gaia`、`browsecomp`、`assistantbench`、`hotpotqa`）会调用 OWL/CAMEL 的网页、文档、文件、代码、图片、表格工具。
- Coding 类数据集（`kodcode`）还需要本地启动兼容 `sandbox_fusion` 的服务，地址为 `http://localhost:8080/`。
- 如果 KodCode 进入 eval 阶段但 sandbox 没启动，会报 `localhost:8080 /run_code connection refused`。
- 部分 GAIA 样本需要 PDF 解析。如果 PDF extraction 失败并退到 `read_file`，缺少 `markitdown` 会导致 `No module named 'markitdown'`。

---

## 3. Single-lane Run / 单路顺序运行

Use one command for all supported datasets:

所有数据集统一用一个入口：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\run_owl_dataset_windows.ps1 `
  -DatasetName hotpotqa `
  -Start 0 `
  -Count 1 `
  -RunRoot D:\mas_runs\hotpotqa_one_by_one `
  -EnvFile owl\owl\.env.gaia `
  -MaxRounds 3
```

Replace `hotpotqa` with:

可替换的数据集名：

```text
kodcode
gaia
browsecomp
assistantbench
hotpotqa
```

The runner executes one sample completely through `round0 -> round1 -> round2 -> round3`
before moving to the next sample.

该 runner 会让每条样本完整跑完 `round0 -> round1 -> round2 -> round3` 后再进入下一条。

Failures are recorded under `failed_samples`; no fake `final_results` are forced.

失败样本会记录到 `failed_samples`，不会强制伪造 `final_results`。

---

## 4. Multi-key Run / 多 key 并行运行

Use:

使用：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\start_owl_dataset_lanes_windows.ps1 `
  -DatasetName hotpotqa `
  -RunRoot D:\mas_runs\hotpotqa_multi_env `
  -Start 0 `
  -MaxRounds 3
```

The script reads row count from the parquet file, splits the selected range
across the env files, and starts hidden PowerShell lane processes.

脚本会读取 parquet 行数，将指定范围按 env 文件数量分片，并启动后台 PowerShell 进程。

To run a subset:

只跑子区间：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\start_owl_dataset_lanes_windows.ps1 `
  -DatasetName browsecomp `
  -RunRoot D:\mas_runs\browsecomp_multi_env `
  -Start 200 `
  -Count 100
```

---

## 5. Status / 查看进度

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\status_owl_runs_windows.ps1 `
  -RunRoot D:\mas_runs\hotpotqa_multi_env
```

It reports latest sample log, failed sample count, and `final_results` count per lane.

该命令会按 lane 汇报最新样本日志、失败样本数量和 `final_results` 数量。

To stop a run, terminate the PowerShell runner and its child Python processes whose command line contains the target `RunRoot`.

如需停止运行，终止命令行中包含目标 `RunRoot` 的 PowerShell runner 及其子 Python 进程。

---

## 6. Output Layout / 输出结构

Typical output:

典型输出：

```text
<RunRoot>/
  key1/ or single-lane root/
    workspace/
    output/
      <data_source>/
        round_0/
        round_1/
        round_2/
        round_3/
      final_results/
    process_logs/
    failed_samples/
  launcher_logs/
```

`final_results` is generated only when the attribution pipeline observes an eval
flip and validates the attribution artifact.

只有当归因流程观察到 eval flip 且归因 artifact 通过校验时，才会生成 `final_results`。

---

## 7. Important Code Paths / 关键代码位置

| File | Purpose |
|---|---|
| `adapter/OWL/core.py` | OWL backend adapter, workforce construction, artifact validation, timeouts. |
| `main.py` | CLI, round orchestration, `--sample_offset`, `--env_file`, dataset alias resolution. |
| `pipeline/coding/run.py` | Coding task execution plus OWL GAIA-style web task execution. |
| `pipeline/coding/eval.py` | Coding sandbox eval, GAIA/HotpotQA scoring, BrowseComp/AssistantBench LLM judge. |
| `pipeline/coding/attack.py` | Attack attribution artifact generation and validation. |
| `pipeline/coding/diagnose.py` | Diagnosis attribution artifact generation and validation. |
| `utils/common.py` | JSON helpers, history compaction, attribution step validation, final result saving. |
| `utils/prompts.py` | Attack/diagnosis prompt templates. |

---

## 8. No-fallback Policy / 无兜底策略

The current OWL adapter intentionally does not synthesize missing attack or
diagnosis JSON files. If OWL fails to create a required artifact, the sample
fails and is recorded. This preserves result validity.

当前 OWL adapter 不会合成缺失的 attack/diagnosis JSON 文件。如果 OWL 没有生成必要 artifact，该样本失败并记录。这是为了保证结果真实性。

This means a command can be "runnable" while a particular sample still fails.
The failure is expected to be recorded under `failed_samples`, not hidden by a
fake final result.

这意味着“命令可以运行”和“某条样本一定成功产出 final_results”不是一回事。失败样本应记录到 `failed_samples`，不应通过伪造 final result 隐藏。

---

## 9. Verified Handoff Smoke Tests / 已验证的交接测试

The generic runner was tested with one complete sample per dataset under:

通用 runner 已按每个数据集完整跑一条样本验证，输出目录为：

```text
D:\mas_runs\handoff_full_one
```

Observed results:

实测结果：

| Dataset | Command result | Artifacts | Explanation |
|---|---:|---|---|
| HotpotQA | exit 0 | 4 logs, 4 eval files, 0 final_results | Finished round0 to round3. Eval stayed `true`, so no eval flip and no final result. |
| BrowseComp | exit 0 | 2 logs, 2 eval files, 1 final_result | Round0 `false`, round1 `true`; final attribution was generated. |
| GAIA | exit 1 | round0 log/eval plus failed sample | Round0 succeeded, but round1 replay reached `round_limit=6` without `<final_answer>`. PDF parsing also reported `No module named 'markitdown'`. |
| AssistantBench | exit 1 | failed sample | Round0 found evidence but produced `Final Answer:` instead of `<final_answer>...</final_answer>`, so no successful log was written. |
| KodCode | exit 1 | round0 log and `solution.py`, failed sample | OWL generated a solution, but evaluation failed because local sandbox `localhost:8080` was not running. |

| 数据集 | 命令结果 | 产物 | 说明 |
|---|---:|---|---|
| HotpotQA | exit 0 | 4 个 log，4 个 eval，0 个 final_results | 完整跑完 round0 到 round3。eval 一直是 `true`，没有 flip，所以没有 final result。 |
| BrowseComp | exit 0 | 2 个 log，2 个 eval，1 个 final_result | round0 为 `false`，round1 为 `true`，触发 final attribution。 |
| GAIA | exit 1 | round0 log/eval 和 failed sample | round0 成功，但 round1 replay 达到 `round_limit=6` 仍没有 `<final_answer>`。同时 PDF 解析出现 `No module named 'markitdown'`。 |
| AssistantBench | exit 1 | failed sample | round0 找到了证据，但输出 `Final Answer:` 而不是 `<final_answer>...</final_answer>`，因此没有写成功 log。 |
| KodCode | exit 1 | round0 log、`solution.py` 和 failed sample | OWL 生成了代码，但本地 sandbox `localhost:8080` 未启动，eval 失败。 |

The purpose of this smoke test is to verify that the handoff commands are executable and that failures are visible and recorded. It does not claim every dataset sample will produce `final_results`.

这个 smoke test 的目的，是验证交接命令可执行，且失败会被显式记录；它不表示每个数据集的每条样本都会生成 `final_results`。

---

## 10. Troubleshooting / 常见问题

### No `final_results` / 没有 `final_results`

This is expected if eval does not flip between rounds. Check:

如果各轮 eval 没有发生 flip，这是正常情况。检查：

```text
<RunRoot>\output\<data_source>\round_*\eval_<data_source>.json
```

### Sample failed but command continued / 样本失败但命令继续

This is intentional when `ContinueOnFailure` is enabled. Check:

启用 `ContinueOnFailure` 时这是预期行为。检查：

```text
<RunRoot>\failed_samples
<RunRoot>\process_logs
```

### AssistantBench has evidence but fails / AssistantBench 找到证据但失败

The pipeline requires `<final_answer>...</final_answer>`. If OWL outputs `Final Answer:` only, the run is recorded as failed.

流程要求输出 `<final_answer>...</final_answer>`。如果 OWL 只输出 `Final Answer:`，该样本会记录为失败。

### GAIA PDF extraction failure / GAIA PDF 解析失败

Install or repair document parsing dependencies such as `markitdown`, or rerun with a sample that does not require PDF parsing.

安装或修复 `markitdown` 等文档解析依赖，或者换一条不依赖 PDF 解析的样本测试。

### KodCode sandbox failure / KodCode sandbox 失败

Start a `sandbox_fusion`-compatible server before running KodCode.

运行 KodCode 前先启动兼容 `sandbox_fusion` 的服务。

---

## 11. Files Not To Commit / 不应提交的文件

Do not commit:

不要提交：

```text
.venv-win/
.venv-gaia-lite/
.codex/
tmp/
owl/
owl/owl/.env*
D:\mas_runs\*
dataset real data files
```

Keep `.env_template` only.

只保留 `.env_template`。

---

## 12. Known Result Folders / 已整理结果

HotpotQA merged final results:

HotpotQA 合并结果：

```text
D:\mas_runs\hotpotqa_all_final_results_merged
```

GAIA merged final results:

GAIA 合并结果：

```text
D:\mas_runs\gaia_all_final_results
```

Other historical experiment outputs are under `D:\mas_runs`.

其他历史实验输出位于 `D:\mas_runs`。
