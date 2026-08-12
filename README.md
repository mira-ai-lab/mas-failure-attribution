# mas-failure-attribution

For the current OWL handoff, start from `README_HANDOFF_ZH_EN.md`.

当前 OWL 交接请优先阅读 `README_HANDOFF_ZH_EN.md`。

A research-oriented framework to **build multi-agent system (MAS) failure datasets** and run **round-based attack / diagnosis workflows** for **failure attribution**. The core loop executes coding tasks, evaluates correctness in a **sandbox**, optionally designs **stealthy fault injections** (attack) or **root-cause fixes** (diagnosis), and **replays** execution from **recovery snapshots** to align attribution labels.

The design is **backend-agnostic**: you plug in a MAS by implementing `BaseAdapter` under `adapter/<BackendName>/` and selecting it with `--backend`.

---

## What it does (high level)

1. **Round 0 – baseline run**  
   For each task, a `BaseMonitor` records role history, **topology** (agent interaction graph), and per-step **recovery** checkpoints. Results are written under your output path as `log.json` (see below).

2. **Evaluation**  
   Solutions are run against tests via **`sandbox_fusion`** (remote sandbox). Per-round pass/fail maps are stored as `eval_<data_source>.json` under each round directory.

3. **Rounds ≥ 1 – branch by last eval**  
   - If the last round was **pass** → **attack** analysis: the backend proposes a **fault injection plan** (JSON) consistent with a **fault candidate pool** (`utils/fault_library.py`).  
   - If the last round was **fail** → **diagnosis** analysis: the backend proposes a **suggested fix** and fault code.  

4. **Replay**  
   The pipeline loads the **recovery state** at the chosen step, and re-runs the MAS to validate the attack/diagnosis trace.

5. **Final attribution**  
   When evaluation **flips** between consecutive rounds, the run may merge attack/diagnosis outputs and call `save_final_result` into `output/final_results/`, enriching the log with `mistake_information` and a small `attribution_subgraph`.

---

## Architecture

| Part | Role |
|------|------|
| `adapter/base_adapter.py` | Abstract MAS entry: `run_backend`, `save_current_state`, `get_prompt_map`. |
| `adapter/<Name>/core.py` | Concrete backend, e.g. `MetaGPTAdapter` (`adapter/MetaGPT/` + middlewares that hook `llm` / `observe` / `terminal` / etc.). |
| `monitor/` | `BaseMonitor` persists history, topology, and recovery; `AttackMonitor` injects a planned edit at a target step during replay. |
| `pipeline/coding/` | `run` (execute + log), `eval` (sandbox tests), `attack` / `diagnose` (LLM-driven analysis from prompts in `utils/prompts.py`). |
| `model/schema.py` | Pydantic models for **topology** and per-step **history**. |
| `utils/` | JSON I/O, **fault library** for prompts, logging (`mas-fail-attr.log` + console), optional `config.ini` for log level. |

New MAS backends should mirror `adapter/MetaGPT/core.py` and register under `adapter/<Backend>/core.py` with a class named `{Backend}Adapter`.

---

## Requirements

- **Python** ≥ 3.10 (see `pyproject.toml`).
- **Core Python deps** (installed with the project): `datasets`, `pydantic`, `tqdm`.
- **Backend-specific deps**: optional extras such as **`[metagpt]`** and **`[owl]`** (see `pyproject.toml`). Add new extras when you add adapters.
- **`sandbox_fusion`**: used by `main.py` and `pipeline/coding/eval.py` for code execution. It is **not** part of the default `dependencies` in `pyproject.toml`—install it from your own wheel, index, or VCS.
- **Sandbox / judge service**: evaluation expects a running sandbox; `main.py` sets:
  - `set_sandbox_endpoint("http://localhost:8080/")`
  - `set_dataset_endpoint("http://localhost:8080/online_judge/")`  
  Change these in code or extend the CLI if you deploy elsewhere.
- **LLM / MAS config**: e.g. MetaGPT reads its own config (`metagpt`); follow that project’s environment variables and API keys.

---

## Installation

```bash
# Editable install (core dependencies only)
pip install -e .

# With MetaGPT backend
pip install -e ".[metagpt]"

# Or install MetaGPT fork directly
pip install "metagpt @ git+https://github.com/yuyueryuyu/MetaGPT.git"

# With OWL backend
# Pinned to camel-ai/owl commit 98d150d..., whose upstream package version is 0.0.1.
pip install -e ".[owl]"

# Development (lint, tests, types)
pip install -e ".[all-with-dev]"
```

Then install **`sandbox_fusion`** and configure your MAS runtime per your environment.

For OWL, the runtime now assumes the package is importable from the active Python
environment; it no longer requires a sibling local `owl/` source checkout.
Provide model/API settings either directly through environment variables or via
`MAS_FA_ENV_FILE` / `OWL_ENV_FILE`.

**Optional log level**  
If `config.ini` is missing, `utils/config.py` will create a default with `log_level` (INFO). Adjust as needed.

---

## Sandbox (example)

The evaluation path assumes a **sandbox_fusion**-compatible server reachable at the URL configured in `main.py`. One possible deployment (adapt image/tag to your stack):

```bash
docker run -it --rm -p 8080:8080 volcengine/sandbox-fusion:server-20250609
```

---

## Data format (Parquet)

`main.py` loads a Parquet file via HuggingFace `datasets` as split `train`. Each row is one task. Fields used in code include (non-exhaustive):

| Field | Usage |
|-------|--------|
| `task_id` | Unique id (slashes are normalized to `_` in paths). |
| `data_source` | Namespace for output layout, e.g. `eval_<data_source>.json`. |
| `question` | User problem statement. |
| `reference_solution` | Reference code (written into `log.json` as `ground_truth` context). |
| `test` | Test code passed to the sandbox (pytest). |

After round 0, `log.json` also contains `model_prediction` (from `solution.py` if present), `history`, `topology`, and `system_prompts`.

---

## Command-line interface

```text
python main.py \
  --dataset /path/to/tasks.parquet \
  --backend MetaGPT \
  --workspace ./workspace \
  --output ./output \
  --max_rounds 3 \
  --max_samples 10 \
  --skip_existing
```

| Argument | Description |
|----------|-------------|
| `--dataset` | Path to Parquet file. |
| `--backend` | MAS name; must match `adapter.<name>.core` and class `{name}Adapter`. |
| `--workspace` | Root for per-task working directories. |
| `--output` | Root for logs, eval JSON, `final_results`, and recovery. |
| `--max_rounds` | Rounds after round 0 (default `3` in code). |
| `--max_samples` | Optional cap on number of tasks. |
| `--skip_existing` / `-s` | Skip if outputs already exist. |
| `--mode` | `full` \| `attack` \| `diagnose` (parsed in code; **not yet wired to branching**). |
| `--concurrent` | Reserved (TODO in code). |
| `--rollout` | Rollout mode (Run only round 0). |
---

## Output layout (typical)

```text
<output>/
  <data_source>/
    round_0/
      <task_id>/
        log.json
        recovery/              # per-step monitor + workspace snapshots
        eval_<data_source>.json  # at round level (see eval path in code)
    round_1/ ...
  final_results/
    <question_ID>.json         # when attribution is finalized
```

Per-round **aggregated** eval results: `eval_<data_source>.json` under `<output>/<data_source>/round_k/` (see `run_eval_tasks` in `pipeline/coding/eval.py`).

---

## Project layout

```text
mas-failure-attribution/
  main.py                 # CLI entry, multi-round orchestration
  pyproject.toml
  adapter/                # MAS adapters (e.g. MetaGPT)
  monitor/                # Execution + injection monitors
  model/                  # Shared schema (topology, history)
  pipeline/coding/        # run, eval, attack, diagnose
  utils/                  # prompts, fault library, logging, I/O
```

