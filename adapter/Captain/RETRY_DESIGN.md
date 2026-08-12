# Captain Run-Level Transient Retry 设计

> **范围**：仅 `adapter/Captain/` 下新增/修改代码。  
> **禁止改动**：`adapter/MagenticOne/**`、`main.py`、`pipeline/**`、`monitor/**`（共用）、`utils/**`（共用）等跨 backend 逻辑。  
> **参考**：MagenticOne `core.py::_run_team` 的 retry 形态；transient 判定只读复用 `magentic_runtime.is_transient_run_stream_failure`。

---

## 1. 目标与非目标

### 目标

- 在 **单次 `run_backend` 调用**（即 pipeline 的某一 Round 的一次任务执行）内，对 **Connection error / DNS / timeout / rate limit** 等 transient 失败做 run-level 重试。
- 典型场景：Round 2 inject 步 LLM 因 DNS 闪断失败（A 类）；retry 应在 **同一 Round 2 语义** 下重打 inject，而非回到 Round 0。
- 保证 **inject 之前** 的每一步仍 strict replay 自 `last_round_log`（与上一轮 log 一致），attribution 样本有效。

### 非目标（本设计不做）

- 不修改 MagenticOne、MetaGPT、共用 pipeline / eval / flip 判定。
- 不替代 SDK 级 `CAPTAIN_MAX_RETRIES`（仍建议保持 `0`，避免与 run-level retry 叠加难排查）。
- 不在 retry 内做 history backfill 或空 prediction flip guard（可另开 Captain 或 pipeline 议题；本设计不碰共用代码）。

---

## 2. 语义：retry 发生在哪一层？

```
Pipeline Round N  (main.py 调度，不改)
    └── AttackMonitor(last_round_log, suggestion)   ← 由 pipeline 创建，不改
            └── CaptainAdapter.run_backend()        ← retry 落在这里
                    attempt 1: replay 1..k-1 → inject k → [DNS fail]
                    reset monitor + 重建 agents
                    attempt 2: replay 1..k-1 → inject k → [success] → live
```

| 问题 | 答案 |
|------|------|
| 会从 Round 0 重跑吗？ | **不会**。仍是 Round N 的 `run_backend`，同一 `idea`、同一 `monitor` 配置（reset 后恢复 replay 状态）。 |
| `last_round_log` 会变吗？ | **不会**。始终指向 Round N-1 的 `log.json`（AttackMonitor 构造时注入）。 |
| `attack_step` / injection 内容会变吗？ | **不会**。仅 reset `_injected` 与 step/history，不碰 `_attack_step`、`_attack_suggestion`、`_last_round_log`。 |
| Round 0（无 inject）需要 retry 吗？ | **需要**。Round 0 用 `BaseMonitor`，同样受益于 DNS 闪断恢复；无 `_injected` 字段。 |

---

## 3. 文件布局（仅 Captain 目录）

```
adapter/Captain/
├── core.py                 # 修改：run_backend 委托给 retry 模块
├── run_retry.py            # 新增：retry loop、monitor reset、attempt 日志
├── retry_config.py         # 新增：环境变量读取（Captain 专用前缀）
└── RETRY_DESIGN.md         # 本文档
```

**只读复用（import，不改源文件）**：

```python
from adapter.MagenticOne.magentic_runtime import is_transient_run_stream_failure
```

若团队希望 Captain 与 MagenticOne 零 import 依赖，可在 `adapter/Captain/transient_errors.py` 中 **复制** 该函数并注明「与 magentic_runtime 保持同步」——仍不修改 MagenticOne 目录。

---

## 4. 配置（Captain 专用环境变量）

与 MagenticOne 对齐语义，但使用 **独立前缀**，避免误改 Magentic 实验：

| 变量 | 默认 | 含义 |
|------|------|------|
| `TE_CAPTAIN_RUN_MAX_ATTEMPTS` | `5` | 单次 `run_backend` 最多 attempt 次数 |
| `TE_CAPTAIN_RUN_RETRY_BASE_SEC` | `10` | 指数退避基数（秒） |
| `TE_CAPTAIN_RUN_RETRY_ENABLED` | `1` | `0` 关闭 run-level retry（便于 A/B） |
| `TE_CAPTAIN_REPLAY_VERIFY` | `0` | `1` 时在每次 attempt 结束后校验 pre-inject 与 `last_round_log` 一致 |

退避公式（与 MagenticOne 相同）：

```text
delay = TE_CAPTAIN_RUN_RETRY_BASE_SEC * (2 ** attempt_index)   # attempt_index 从 0 起
# 第 1 次失败后等 10s，第 2 次 20s，第 3 次 40s，第 4 次 80s；第 5 次失败则 raise
```

---

## 5. 核心流程

### 5.1 `core.py`  refactor 思路

将现有 `run_backend` 主体提取为 **`_run_once(...)`**（单次 attempt：建 agent、instrument、`initiate_chat`、收集 prompt_map），`run_backend` 仅负责：

1. 解析 `agent_library_path` / `library_info`（**跨 attempt 不变**，与 today 相同）
2. 调用 `run_retry.run_with_transient_retry(adapter=self, ...)`
3. 返回最后一次成功 attempt 的 result

**`run_backend` 对外签名与返回值不变**，pipeline 无感知。

### 5.2 每次 attempt 必须做的事（顺序固定）

```text
1. reset_monitor_for_attempt(monitor)          # 见 §6
2. self._dynamic_prompt_map = {}
3. llm_config = self._build_llm_config()       # 可复用同一份 config
4. 新建 CaptainAgent + UserProxyAgent          # 禁止复用上一轮 crash 的实例
5. captain.executor.build_history.update(...)  # 与 today 相同，reuse round_0 library
6. _collect_participants → _instrument_agents
7. _install_dynamic_agent_hooks(captain, monitor)   # 新 captain → hook 可重新安装
8. result = user_proxy.initiate_chat(...)
9. （可选）verify_pre_inject_replay(monitor, last_round_log, attack_step)
10. 成功 → break；transient 失败 → sleep → 下一 attempt
```

### 5.3 为何必须「重建 agent + reset monitor」

| 风险 | 不 reset / 不重建的后果 |
|------|-------------------------|
| `_injected=True` 残留 | 第 2 次 attempt **跳过 inject**，样本作废 |
| `monitor.step` / `history` 残留 | `get_current_reply()` 索引错位，replay 内容错误 |
| nested chat 脏状态 | `_oai_messages`、半恢复 system message；Expert_summoner 报错路径不可控 |
| `executor._captain_dynamic_hook_installed` | 旧 captain 上已安装；**新 captain 必须新装**（重建即解决） |

**Pre-inject 一致性不依赖 agent 内存**，而依赖：

- `AttackMonitor._last_round_log`（不变）
- `get_current_reply()` 按 `step-1` 索引（`observe.py`，不改）
- Round 0 `build_history` + `_force_replay_group_name`（已有，不改）

重建 team 是为了 **清 infra 失败脏状态**，不是换专家或换历史。

---

## 6. Monitor reset 规范（Captain 内实现）

在 `run_retry.py` 实现 `reset_monitor_for_attempt(monitor)`：

### `BaseMonitor`（Round 0 等）

```python
monitor.history.clear()
monitor.step = 1
monitor.topology = Topology()   # 与 base_monitor 同模型的新实例
```

### `AttackMonitor`（Round ≥ 1 replay/inject）

在 BaseMonitor reset 之上：

```python
monitor._injected = False
# 禁止修改：_attack_step, _attack_suggestion, _last_round_log
```

**禁止**：在 retry 之间替换 `AttackMonitor` 实例（pipeline 传入同一对象）；只 reset 字段。

---

## 7. Transient 判定与异常处理

### 判定

```python
from adapter.MagenticOne.magentic_runtime import is_transient_run_stream_failure

if is_transient_run_stream_failure(exc) and attempt + 1 < max_attempts:
    await asyncio.sleep(delay)
    continue
raise
```

### Captain 特有问题：异常可能包在 AG2 多层 cause 里

- 顶层：`Connection error.`（OpenAI SDK）
- 内层：`httpx.ConnectError: [Errno -2] Name or service not known`

`is_transient_run_stream_failure` 已遍历 `__cause__` 链；**直接复用**。

### 非 transient（不重试）

- 业务逻辑错误、invalid_request（非 DNS 导致的 400）、明确 auth 失败等 → 立即 raise，与 today 相同。

### 5 次全失败

- 行为与 **today 无 retry** 一致：`run_backend` raise → `text_answer.py` catch → `model_prediction=""`，log 写入 **最后一次 attempt** 的 monitor.history（残缺轨迹）。
- 额外：Captain 专用 `captain_retry_trace.json` 写入 workspace（可选），记录每次 attempt 的 error repr、step 数、是否 transient；**不影响** pipeline 读的 `log.json`  schema。

---

## 8. Replay 完整性校验（可选，Captain 内）

当 `TE_CAPTAIN_REPLAY_VERIFY=1` 且 `monitor` 为 `AttackMonitor` 时，在 attempt **成功结束**后：

```text
对每个 step s in 1 .. (attack_step - 1):
    比较 monitor.history[s-1].content 与 last_round_log.history[s-1].content
    （name 字段也应一致，speaker_selection 与 expert 步都要对）
```

- 全部一致 → DEBUG log `[captain-retry] replay verify ok`
- 任一不一致 → WARNING `[captain-retry] replay verify FAILED attempt=N`  
  - 仍写入 log.json（pipeline 不改）  
  - 在 `captain_retry_trace.json` 标记 `replay_integrity: failed`  
  - 实施者可选：在 `last_agent_library_info` 增加 `replay_verify_failed: true` 供下游人工筛样本

**不在 retry 循环内自动 discard 样本**（避免改 pipeline）；只打标 + 日志。

---

## 9. 与 A / B 类失败的关系

| 类型 | retry 前 | retry 后（DNS 恢复） | 5 次全 fail |
|------|----------|----------------------|-------------|
| **A 类** inject 即死 | ~5 步，Expert_summoner 报错 | 完整 inject + live，有效 attack 样本 | 同 today，多等 ~150s |
| **B 类** 后期 summary 死 | 多步 + Captain 强制 TERMINATE | 可能得到完整 prediction | 同 today |

retry **不保证** B 类从残缺 history 恢复 prediction；只提高 transient 窗口内成功的概率。

---

## 10. 代码改动清单（实施时）

### 新增 `adapter/Captain/retry_config.py`

- `captain_run_max_attempts() -> int`
- `captain_run_retry_base_sec() -> float`
- `captain_run_retry_enabled() -> bool`
- `captain_replay_verify_enabled() -> bool`

### 新增 `adapter/Captain/run_retry.py`

| 函数 | 职责 |
|------|------|
| `reset_monitor_for_attempt(monitor)` | §6 |
| `verify_pre_inject_replay(monitor, attack_step) -> bool` | §8 |
| `write_retry_trace(workspace, trace: dict)` | 写 `captain_retry_trace.json` |
| `async run_with_transient_retry(...)` | retry loop；调用 `adapter._run_once` |

### 修改 `adapter/Captain/core.py`

| 变更 | 说明 |
|------|------|
| 提取 `_run_once(...)` | 从现有 `run_backend` 59–107 行抽出，逻辑不变 |
| `run_backend` | 包一层 `run_with_transient_retry` |
| 不修改 | `_install_dynamic_agent_hooks` 及其他 instrument 逻辑 |

### 明确不修改

- `adapter/Captain/middlewares/observe.py`
- `monitor/attack_monitor.py`
- `pipeline/runners/text_answer.py`
- `main.py`
- `adapter/MagenticOne/**`

---

## 11. 伪代码（`run_retry.py`）

```python
async def run_with_transient_retry(
    *,
    adapter: CaptainAdapter,
    idea: str,
    workspace: Path,
    monitor: BaseMonitor | None,
    task_id: str | None,
    agent_library_path: Path,
    saved_build_history: dict,
) -> Any:
    cfg = retry_config.load()
    if not cfg.enabled:
        return adapter._run_once(...)

    trace = {"task_id": task_id, "attempts": []}
    max_attempts = cfg.max_attempts
    retry_base = cfg.retry_base_sec

    last_exc = None
    for attempt in range(max_attempts):
        rec = {"index": attempt + 1, "error": None, "history_steps": 0}
        reset_monitor_for_attempt(monitor)
        try:
            result = adapter._run_once(
                idea=idea,
                monitor=monitor,
                agent_library_path=agent_library_path,
                saved_build_history=saved_build_history,
                ...
            )
            rec["history_steps"] = len(monitor.history) if monitor else 0
            if isinstance(monitor, AttackMonitor) and cfg.replay_verify:
                rec["replay_verify"] = verify_pre_inject_replay(monitor, monitor.attack_step)
            trace["attempts"].append(rec)
            write_retry_trace(workspace, trace)
            return result
        except Exception as exc:
            last_exc = exc
            rec["error"] = repr(exc)
            rec["history_steps"] = len(monitor.history) if monitor else 0
            trace["attempts"].append(rec)
            if is_transient_run_stream_failure(exc) and attempt + 1 < max_attempts:
                delay = retry_base * (2 ** attempt)
                logger.warning(
                    "[captain-retry] attempt %s/%s transient: %s; retry in %.1fs",
                    attempt + 1, max_attempts, exc, delay,
                )
                await asyncio.sleep(delay)
                continue
            write_retry_trace(workspace, trace)
            raise

    write_retry_trace(workspace, trace)
    raise last_exc
```

---

## 12. 测试建议（Captain 本地，不依赖改 pipeline）

1. **单元**：`reset_monitor_for_attempt` 后 `AttackMonitor.should_inject()` 在 step==attack_step 时为 True。
2. **mock transient**：临时 patch `initiate_chat` 前 2 次 raise `APIConnectionError`，第 3 次成功；断言 history pre-inject 与 `last_round_log` 一致。
3. **inject 失败**：mock 仅在 `monitor.step==5` 的 `generate_oai_reply` fail；确认第 2 attempt 仍出现 `[llm-input-inject] step=5`。
4. **5 次全 fail**：断言 raise、history 步数与无 retry 同量级；trace 文件有 5 条 attempt。
5. **Round 0**：无 AttackMonitor，retry 仍工作，history 从空重启。

---

## 13. 实施检查清单（防写坏）

- [ ] retry 仅包在 `CaptainAdapter.run_backend`，未动共用 runner
- [ ] 每次 attempt 前 `reset_monitor_for_attempt`（含 `_injected=False`）
- [ ] 每次 attempt **新建** CaptainAgent / UserProxyAgent
- [ ] 每次 attempt 重新 `build_history.update(saved_build_history)`
- [ ] 未改 `_last_round_log` / `_attack_step` / suggestion
- [ ] transient 判定用 `is_transient_run_stream_failure`（只读 import）
- [ ] MagenticOne / MetaGPT / pipeline 目录 git diff 为空
- [ ] `CAPTAIN_MAX_RETRIES` 保持 `0`（文档说明，config 可选）
- [ ] 成功 attempt 的 pre-inject history 与 fail attempt 的 pre-inject 在 verify 模式下逐字一致

---

## 14. 与 MagenticOne 的差异摘要（ intentional ）

| 项 | MagenticOne | Captain（本设计） |
|----|-------------|-------------------|
| monitor  between attempts | 不 reset（Magentic 无 nested replay） | **必须 reset** |
| agent rebuild | 每次 `create_magentic_team` | 每次新建 CaptainAgent |
| replay 合约 | N/A | strict replay via `last_round_log` |
| trace 文件 | `magentic_trace.json` | `captain_retry_trace.json` |
| 配置前缀 | `TE_MG2_*` | `TE_CAPTAIN_*` |

---

*文档版本：与 2026-07-30 math_captain DNS A 类讨论对齐。*
