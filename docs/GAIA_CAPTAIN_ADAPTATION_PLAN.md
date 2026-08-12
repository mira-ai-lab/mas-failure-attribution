---
name: GAIA接入Captain系统适配计划
overview: 让 GAIA 数据集能在 Captain backend 上通过 main.py 入口完整跑通 Round 0 和 Round 1，复用 MATH 已验证的多轮 attack/diagnose 框架。第一版用推理+代码执行（不接 web 工具），先验证 pipeline 连通性。
todos:
  - id: fix-data-source
    content: 修复 _infer_data_source() 让 gaia_dataset_raw 路径识别为 gaia
    status: pending
  - id: add-fallback
    content: web_research runner 增加 fallback 答案提取逻辑（从 result/history 提取并写 final_answer.txt）
    status: pending
  - id: fix-attachment-path
    content: 增强 _build_web_research_idea 附件路径提示（传绝对路径 + 存在性检查）
    status: pending
  - id: smoke-test
    content: 用 Level 1 纯文本题做 smoke test 验证 Round 0 + Round 1 连通性
    status: pending
  - id: verify-multi-round
    content: 确认多轮 attack/diagnose/replay/inject 在 GAIA 上自动复用
    status: pending
isProject: false
---

# GAIA 接入 Captain 系统适配计划

## 背景

当前 `python main.py --dataset GAIA --backend Captain` 直接运行会在 Round 0 大面积失败，存在 4 个关键缺口。本计划分阶段修复，先让 pipeline 跑通，再考虑多轮注入。

## 当前问题诊断

| # | 问题 | 影响 | 根因位置 |
|---|------|------|----------|
| 1 | `data_source` 被识别为 `"metadata"` 而非 `"gaia"` | 走错 runner（code_generation）+ 错 eval（llm_judge） | `dataset/dataset.py` `_infer_data_source()` |
| 2 | Captain 不产出 `final_answer.txt` | `run_web_research_task` 返回 False，不写 log.json | `pipeline/runners/web_research.py` 无 fallback |
| 3 | 附件路径只传 basename | Captain 找不到附件文件 | `pipeline/runners/web_research.py` `_build_web_research_idea()` |
| 4 | Captain 无 web 搜索工具 | 需要 web browsing 的题答不对 | 第一版暂不处理 |

## 阶段一：修复 data_source 识别（P0 阻塞项）

**文件**: `dataset/dataset.py`

`_infer_data_source()` 当前逻辑检查 `"gaia" in parts`，但路径 `gaia_dataset_raw/.../metadata.parquet` 的目录名是 `gaia_dataset_raw`，不等于 `gaia`，所以返回 `"metadata"`。

**修复方案**: 在 `_infer_data_source()` 中增加子串匹配，将 `gaia_dataset_raw` 也识别为 `gaia`：

```python
# 当前（约第 75 行）:
if "gaia" in parts or "gaia" in stem or "gaia" in name:
    return "gaia"

# 改为:
if (
    "gaia" in parts
    or "gaia" in stem
    or "gaia" in name
    or any("gaia" in part for part in parts)  # 匹配 gaia_dataset_raw
):
    return "gaia"
```

同样处理 `assistantbench`、`browsecomp` 等已有别名（它们当前用 `in stem` 已能匹配，但 `gaia_dataset_raw` 在 parts 里不在 stem 里）。

**验证**: `resolve_dataset_argument("gaia")` → 加载后 `normalize_parquet_task_row` 返回 `data_source == "gaia"`。

## 阶段二：web_research runner 增加 fallback 提取（P0 阻塞项）

**文件**: `pipeline/runners/web_research.py`

当前 `run_web_research_task` 在第 129-134 行硬性要求 `final_answer.txt`，不存在直接返回 False。MATH 的 `text_answer` runner 有 `\boxed{}` 回填逻辑，web_research 没有。

**修复方案**: 参考 `pipeline/runners/text_answer.py` 的 `_resolve_model_prediction_for_math()`，在 `final_answer.txt` 不存在时从 backend 返回值和 monitor history 中提取答案：

```python
# 在 run_web_research_task 中，第 128 行之后:
answer_path = workspace / "final_answer.txt"
if answer_path.exists():
    model_prediction = answer_path.read_text(encoding="utf-8").strip()
else:
    # Fallback: extract from backend result and chat history
    model_prediction = _extract_web_research_prediction(result, monitor, task_id)
    if not model_prediction:
        logger.warning(f"final_answer.txt not found and no prediction extracted for {data_source}/{task_id}")
        return False
    # Write extracted answer to final_answer.txt for consistency
    answer_path.write_text(model_prediction, encoding="utf-8")
    logger.info(f"Extracted prediction for {data_source}/{task_id}: {model_prediction[:100]}")
```

新增辅助函数 `_extract_web_research_prediction(result, monitor, task_id)`：
1. 优先从 `result`（backend 返回的 summary）提取：找 `FINAL ANSWER:` 标记后的内容，或最后一段非空文本
2. 若 summary 无明确答案，从 `monitor.history` 中按时间倒序找最后一条 expert/assistant 消息，提取其中的答案候选
3. 对 GAIA 格式做简单清洗（去 markdown、去 `FINAL ANSWER:` 前缀）

**注意**: `run_web_research_task` 当前没有保存 `result` 变量（第 115-123 行执行 backend 但未存返回值），需要改为 `result = backend.run_backend(...)` 并处理 awaitable。

## 阶段三：传递附件绝对路径（P1）

**文件**: `pipeline/runners/web_research.py`

当前 `_build_web_research_idea()` 第 75-76 行只传 basename：

```python
if task.get("file_name"):
    file_hint = f"\nNecessary file: {task['file_name']}"
```

`normalize_parquet_task_row` 在 `dataset/dataset.py` 第 221-224 行已将 `file_name` 展开为绝对路径（当 `inferred_source == "gaia"` 时）。但阶段一修复前 `inferred_source` 是 `"metadata"`，展开不生效。阶段一修复后，`file_name` 已是绝对路径。

**额外增强**: 在 `_build_web_research_idea()` 中对 GAIA 增加更明确的附件说明：

```python
if task.get("file_name"):
    file_path = task["file_name"]
    file_hint = f"\nAttached file (absolute path): {file_path}"
    if Path(file_path).exists():
        file_hint += f"\nFile exists on disk. Use code execution to read it."
    else:
        file_hint += f"\nWarning: file not found on disk."
```

## 阶段四：验证 Round 0 + Round 1 连通性

修复阶段一至三后，执行 smoke test：

```bash
RUN_TAG="gaia_captain_smoke_$(date +%Y%m%d_%H%M%S)"
nohup env \
  MAS_FAIL_ATTR_LOG="logs/${RUN_TAG}.log" \
  python main.py \
  --dataset gaia \
  --backend Captain \
  --workspace "./workspace/${RUN_TAG}" \
  --output "./output/${RUN_TAG}" \
  --max_samples 1 \
  --max_rounds 3 \
  --env_file /data/sdb/liuhui36/mas-failure-attribution/config/env \
  > /dev/null 2> "logs/${RUN_TAG}.stderr.log" &
```

**验证清单**:
- [ ] `output/gaia/round_0/task_*/log.json` 存在且 `model_prediction` 非空
- [ ] `output/gaia/round_0/eval_gaia.json` 存在且值为 bool
- [ ] `output/gaia/round_1/task_*/log.json` 存在（attack 或 diagnose 分析后 replay）
- [ ] `output/gaia/round_1/eval_gaia.json` 存在
- [ ] 若 eval 翻转，`output/final_results/task_*.json` 生成
- [ ] `logs/*.log` 无 KeyError、无未捕获异常

**建议**: 第一版先用 Level 1 纯文本题（不需附件、不需上网）做 smoke test，确认 pipeline 连通后再扩大范围。可在 `load_tasks` 后手动筛选 `level == "1"` 且 `file_name` 为空的题。

## 阶段五：多轮注入与攻击（复用现有框架）

MATH 的多轮 attack/diagnose/replay/inject 机制在 `main.py` 中与 data_source 无关，GAIA 修复后自动复用。关键复用点：

| 机制 | 位置 | GAIA 是否自动生效 |
|------|------|-------------------|
| Round 0 执行 | `main.py` 第 198-222 行 | ✅（runner 切换后自动） |
| Attack 分析 | `pipeline/coding/attack.py` | ✅（读 log.json，与 data_source 无关） |
| Diagnose 分析 | `pipeline/coding/attack.py` | ✅ |
| Replay + Inject | `AttackMonitor` + `ThinkMiddleware` | ✅（Captain middleware 已实现） |
| Eval 翻转检测 | `main.py` 第 398-443 行 XOR 逻辑 | ✅ |
| Final results | `save_final_result()` | ✅ |

**需关注**: `attack.py` 中 `get_attack_analysis` 生成的注入建议只允许注入专家步骤（排除 Captain、speaker_selection、Computer_terminal）。GAIA 任务若 Captain 组建的专家角色不同（如 Web_Researcher、File_Analyst），注入逻辑应自动适配，但需在 smoke test 中确认 `attack_analysis.json` 的 `step_id` 能正确映射到 history 中的步骤。

## 阶段六（后续）：Web 搜索工具接入

第一版不接 web 工具。后续若需提升 GAIA 准确率，可参考 OWL 的 toolkit 配置，在 Captain 的 AutoBuild 中注入搜索/文件工具。这属于独立增强，不影响当前 pipeline 连通性。

## 改动文件清单

| 文件 | 改动 | 阶段 |
|------|------|------|
| `dataset/dataset.py` | `_infer_data_source()` 增加 `gaia_dataset_raw` 子串匹配 | 一 |
| `pipeline/runners/web_research.py` | 增加 fallback 提取 + 保存 result 变量 + 附件路径增强 | 二、三 |

**不改动**: `main.py`、`adapter/Captain/`、`pipeline/eval/`、`pipeline/coding/attack.py`。多轮框架无需改动，自动复用。

## 风险与注意事项

1. **Captain 代码执行目录是 `groupchat/`**（相对路径），不是任务 workspace。若 Agent 尝试用代码写 `final_answer.txt`，会写到 `groupchat/final_answer.txt` 而非 workspace 根。fallback 提取逻辑（阶段二）可绕过此问题。
2. **GAIA 答案格式多样**（数字、字符串、逗号分隔列表），fallback 提取需处理 `FINAL ANSWER:` 前缀和格式清洗，否则 `gaia_score` 会误判。
3. **Level 3 题需要多步 web 搜索**，第一版无 web 工具会全错。建议 smoke test 用 Level 1。
4. **`_infer_data_source` 修复后需回归测试**：确保 kodcode、hotpotqa 等其他别名不受影响。`tests/test_load_tasks.py` 已有相关测试可跑。

---

## 附录 A：2026-08-07 进度更新（相对原 plan 的补充，不修改上文）

> 本节基于 Kipchoge 单样本（`e1fc63a2-da7a-432f-be78-7c4a95598703`，`sample_offset=4`，ground truth=`17`）多轮 smoke 与 AG2 源码排查结论。**目标已从「先不接 web 工具验证连通」调整为「GAIA 在 Captain 上跑通且能正常调用工具，再大批量采集」。**

### A.1 原 plan 各阶段实现状态

| 原 plan 项 | 状态 | 说明 |
|-----------|------|------|
| 阶段一 `data_source` 识别 | **已实现** | `dataset/dataset.py` 已增加 `any("gaia" in part for part in parts)`，可识别 `gaia_dataset_raw` |
| 阶段二 fallback 提取 | **部分实现** | `web_research.py` 已有 `_extract_web_research_prediction` + Backfill；但会误提取长 summary /「未完成」类句子，**未**可靠提取纯数字 `17` |
| 阶段三 附件绝对路径 | **待验证** | 依赖 data_source=gaia 后 `file_name` 展开；Level 1 纯文本 smoke 未覆盖 |
| 阶段四 smoke test | **进行中** | 多轮 RUN 已跑，Round 0 eval 仍 false；Round 1 diagnose/replay 部分连通 |
| 阶段五 多轮注入 | **部分验证** | diagnose 可触发；replay inject 与 eval 翻转仍不稳定 |
| 阶段六 web 工具 | **进行中** | 见 A.3；非「后续独立增强」，现为 **P0 阻塞** |

### A.2 相对原 plan 已新增的 Captain 适配（原 plan「不改动 adapter/Captain」已突破）

| 改动 | 文件 | 作用 |
|------|------|------|
| nested `Computer_terminal` work_dir 绑定 task workspace | `adapter/Captain/core.py` | 避免写 `groupchat/final_answer.txt`；replay 时 patch `AgentBuilder.load` |
| `CAPTAIN_TOOL_LIB` + DuckDuckGo | `adapter/Captain/tool_lib.py`, `core.py`, `config/env` | 对齐 AG2 `tool_lib` 参数；MATH 默认 `none` 不受影响 |
| pyproject `captain` extra 含 `ddgs` 等 | `pyproject.toml` | GAIA 开 duckduckgo 时的依赖声明 |

**尚未做（当前最大缺口）**：`agent_lib=captainagent_expert_library.json`（官方 crosstool 与 bind 的前提）。

### A.3 单样本 smoke 历程与现状（Kipchoge 题）

固定测试题：`e1fc63a2-da7a-432f-be78-7c4a95598703`（Level 1 纯文本，答案 `17` = **17 thousand hours**）。

| RUN_TAG | Round 0 主要现象 | eval | 说明 |
|---------|------------------|------|------|
| `151945` | 写 `groupchat/`，答案 17000/17 | false | work_dir 未绑 workspace（已修） |
| `093621` | nested 仍写 groupchat；R1 未完成 | false | 仅顶层 work_dir（已修 nested） |
| `101415` | 无 exitcode，seek 预算用尽 | false | `--max_rounds 1` |
| `104453` | 手算 17，无 exitcode，无写文件 | false | Computer_terminal 协作失败 + fallback 长句 |
| **`140932`** | tool_lib=duckduckgo；wikipedia 缺包；re-seek 崩溃 | false | **见 A.4** |

**最新一次（140932）Round 0 失败链（已对齐 AG2 源码）**：

1. `[captain-tool-lib] mode=duckduckgo tools=['duckduckgo_search']` — 配置写入成功  
2. 第一次 seek 走 **`builder.build()` 从零建群**（无 `agent_lib`）→ AG2 **未执行 tool bind**  
3. Physics_Expert step5 口头查网无代码 → step7 `no code`  
4. step9 用 `import wikipedia`（非 `duckduckgo_search`）→ step11 `exitcode:1` ModuleNotFoundError  
5. 第二次 seek：`Error: 'CaptainUserProxyAgent' object has no attribute 'tool_root_dir'`（reload 分支假设第一次已 bind）  
6. Captain 手算 **17000**（单位错：应为 17 thousand hours）且未写盘  
7. 最终 summary 称「task incomplete」→ fallback 写入该句 → `gaia_score` false  

**140932 Round 1**：diagnose 完成，replay ~14s 结束，`Eval Result remains the same, Attack/Diagnose Fail`（inject 未翻转 eval）。

### A.4 根因分层（当前认知）

```
Layer 0  Pipeline 路由/评测
  ├─ data_source / runner / eval          → 已通
  └─ fallback 提取质量                    → 会写文件但内容常错（长 summary / incomplete）

Layer 1  Captain 嵌套专家执行
  ├─ work_dir → workspace                 → 已修
  ├─ Computer_terminal 代码未执行/空转   → 仍存在（speaker 路由、group_max_round、seek 预算）
  └─ 单位/thousand-hours 理解             → Agent 常输出 17000 而非 17

Layer 2  Web 工具（大批量 GAIA 前置条件）
  ├─ 仅传 tool_lib                        → 不够；bind 未跑
  ├─ AG2：bind 仅在 build_from_library 后  → 需 agent_lib（官方 crosstool）
  ├─ 无 agent_lib → builder.build()       → MATH 当前路径；GAIA 开 tool 时必须改
  └─ re-seek + tool_lib 无 tool_root_dir  → AG2 0.14.0 bug/遗漏，agent_lib 可一并解决
```

**`build_from_library` vs `builder.build()` 关系（排查用）**：

- 二者是 `_run_autobuild` 内**互斥分支**，由是否传入 **`agent_lib`**（专家模板库 JSON）决定。  
- **`builder.build()`**：LLM 现场生成专家（**MATH / 当前 GAIA 默认**）。  
- **`build_from_library()`**：从预置专家库检索 + 选配（**官方 crosstool + tool_lib 路径**）。  
- **`tool_lib` 的 bind**（专家 prompt 挂函数 + Computer_terminal 换 `LocalExecutorWithTools`）在 AG2 0.14 中写在 **`build_from_library` 分支内**；仅 `tool_lib` 无 `agent_lib` 时 bind **不会发生**。

### A.5 后续目标（相对原 plan 的调整）

1. **单样本 Round 0 eval=true**：Kipchoge 题稳定产出 `final_answer.txt`=`17` 且 `eval_gaia.json`=true。  
2. **工具可观测**：log/history 中可见 `duckduckgo_search` 相关 Python + `Computer_terminal` `exitcode:0`。  
3. **Round 1 连通**：diagnose → replay/inject → eval 可翻转或至少 inject 命中专家 step。  
4. **大批量采集前置**：上述稳定后，再扩 `max_samples` / Level 2–3 / 附件题。

### A.6 排查思路与顺序（建议严格执行）

#### Phase 0 — 工具链路与官方对齐（P0，阻塞大批量）

| 步骤 | 动作 | 通过标准 |
|------|------|----------|
| 0.1 | 新增 `adapter/Captain/captainagent_expert_library.json`（从 AG2 notebook 样例 vendoring） | 文件存在、格式合法 |
| 0.2 | `CAPTAIN_TOOL_LIB=duckduckgo` 时 **`CaptainAgent(agent_lib=..., tool_lib=...)`** 同 crosstool | log 出现 `==> Looking for suitable agents in the library...` 或等价 library 路径 |
| 0.3 | 单样本重跑 Kipchoge | 专家 system prompt 含 `## Functions` / `duckduckgo_search`；**非** `import wikipedia` |
| 0.4 | 确认 re-seek 不崩溃 | 无 `tool_root_dir` AttributeError；第二次 seek 可完成 |

依赖：`pip install "ag2[interop-langchain,duckduckgo]"` + `ddgs`（xjtu 已装）。

#### Phase 1 — 答案落盘与评测（P0）

| 步骤 | 动作 | 通过标准 |
|------|------|----------|
| 1.1 | 收紧 `_extract_web_research_prediction`：优先 `file.write("17")`、短数字、`<final_answer>`；**禁止**「incomplete / no final answer」类 backfill | backfill 内容为 `17` 或可 normalize 为 `17` |
| 1.2 | prompt（可选 P1）：thousand-hours 单位 + 必须写纯数字到 `final_answer.txt` | 减少 17000 vs 17 |
| 1.3 | 验证 `workspace/.../final_answer.txt` **非 backfill 误写** |  ideally Agent 自己写入；backfill 仅兜底 |

#### Phase 2 — Computer_terminal 协作（P1）

| 步骤 | 动作 | 通过标准 |
|------|------|----------|
| 2.1 | 查 history 中 Python_Expert 出码后是否轮到 Computer_terminal | 有 `exitcode:0` 写文件记录 |
| 2.2 | 必要时调 `CAPTAIN_GROUP_MAX_ROUND` / seek 预算 | 写文件代码不被 Expert_summoner 提前截断 |
| 2.3 | 区分「搜索代码 exitcode」与「写文件 exitcode」 | 两步均可成功 |

#### Phase 3 — 多轮 attack/diagnose/replay（P1，单样本通过后）

| 步骤 | 动作 | 通过标准 |
|------|------|----------|
| 3.1 | Round 0 false → diagnose 产出合理 `step_id` | 非 Computer_terminal / framework 角色 |
| 3.2 | replay inject 后 Round 1 eval 变化或 final_results 生成 | 不再 14s 空转结束 |
| 3.3 | `CAPTAIN_TOOL_LIB` 全 RUN 一致 | Round 0/1 build_history 与 tool bind 一致 |

#### Phase 4 — 扩面（P2，大批量）

- Level 1 多样本 → Level 2 附件 → BrowseComp 式多跳  
- 附件路径增强（原 plan 阶段三）  
- 监控：tool 调用率、exitcode 成功率、eval 准确率、fallback 占比  

### A.7 单样本验收命令（与现网一致）

```bash
# 项目根、conda activate xjtu
RUN_TAG="gaia_captain_smoke_$(date +%Y%m%d_%H%M%S)"
CAPTAIN_TOOL_LIB=duckduckgo   # config/env 已设则可省略

nohup env CAPTAIN_TOOL_LIB=duckduckgo \
  MAS_FAIL_ATTR_LOG="logs/${RUN_TAG}.log" \
  python main.py \
  --dataset gaia --backend Captain \
  --workspace "./workspace/${RUN_TAG}" \
  --output "./output/${RUN_TAG}" \
  --sample_offset 4 --max_samples 1 --max_rounds 3 \
  --env_file /data/sdb/liuhui36/mas-failure-attribution/config/env \
  > /dev/null 2> "logs/${RUN_TAG}.stderr.log" &
```

**Round 0 必查**：

```bash
grep -E '\[captain-tool-lib\]|Looking for suitable agents|exitcode|duckduckgo' logs/${RUN_TAG}.log logs/${RUN_TAG}.stderr.log
cat output/${RUN_TAG}/gaia/round_0/e1fc63a2-*/final_answer.txt
cat output/${RUN_TAG}/gaia/round_0/eval_gaia.json
```

**通过定义**：`eval_gaia.json` 为 `true`，且 history 中有成功搜索 +（ ideally）成功写文件。

### A.8 与原 plan todos 的对照（便于跟踪）

| todo id | 原 plan 状态 | 2026-08-07 实际 |
|---------|-------------|----------------|
| fix-data-source | pending | **done** |
| add-fallback | pending | **partial**（有 backfill，质量不足） |
| fix-attachment-path | pending | **pending** |
| smoke-test | pending | **in progress**（多次 smoke，Round 0 未 true） |
| verify-multi-round | pending | **partial**（R1 diagnose 有，inject/eval 翻转未稳） |

**新增 todo（建议写入下一轮实施，不替换上文 YAML）**：

- `add-agent-lib`：`agent_lib` + vendored `captainagent_expert_library.json`，GAIA 开 tool 时启用  
- `fix-fallback-quality`：fallback 只提取可评测短答案  
- `fix-thousand-hours-prompt`：GAIA 单位说明（可选）  
- `verify-tool-bind`：log 中确认 bind + duckduckgo 调用  
- `verify-reseek-tool-lib`：第二次 seek 不崩溃  

### A.9 MATH 回归约束

- `CAPTAIN_TOOL_LIB=none`（默认）：不传 `agent_lib`、不传 `tool_lib`，仍走 `builder.build()`，**与现 MATH 行为一致**。  
- 仅当 `CAPTAIN_TOOL_LIB=duckduckgo`（或 `default`）时启用 `agent_lib` + bind 路径。  
- 同一 RUN_TAG 各 round 保持相同 `CAPTAIN_TOOL_LIB`（replay 一致性）。
