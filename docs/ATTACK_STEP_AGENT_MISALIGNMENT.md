# Attack 注入步 Agent 错位问题梳理

> 基于 math Captain 批次数据（6 批共 114 条 `final_results`，其中 90 条含 `attacked_content` 的 Attack 样本）的讨论总结。

---

## 1. 问题是什么

**错位**：`attack_analysis` 在 Round N-1 的 log 上选定目标 `step_id` 及对应 Expert agent，但 Round N replay 真正执行到该 step 时，log 里记录的 agent 已变成框架角色（如 `speaker_selection_agent`、`Expert_summoner`），Expert 并未在该 step 发言。

| 维度 | 说明 |
|------|------|
| 表现 | `pre_log[step].name`（分析轮）≠ `flip_log[step].name`（注入轮） |
| 规模 | **9/90** Attack 任务错位（约 10%）；**81/90** 严格对齐 |
| 典型样本 | `task_eada88b86028`：Round0 step3=`LinearEquations_Expert` → Round1 step3=`speaker_selection_agent` |
| 附带问题 | **Diagnose 标 framework**（4 任务、10 条 label）是**另一类 bug**：pre/flip 两轮 agent **一致**，但 LLM 选了不可归因的编排步；见 §6 |

注入仍按 `monitor.step == attack_step` 触发，**不校验**该 step 当前 agent 是否与 analysis 一致，因此错位时会在错误角色上 inject/replay。

---

## 2. 根本根因：Captain 只 replay content，不 replay 状态

三框架 Attack replay 策略一致：**只重放历史文本（content），不重放运行时状态**。

| 框架 | Agent 池 | Replay 含义 |
|------|----------|-------------|
| MetaGPT | 固定角色 | 每角色独立 LLM hook，step 与 agent 天然绑定 |
| MagenticOne | 固定角色 | `step >= attack_step` 起 inject，角色不变 |
| **Captain** | **动态 Expert**（`seek_experts_help` 按题建组） | 前置 step 只 replay 文本；**子群聊是否建立、speaker 顺序等状态未重放** |

Captain 虽从 Round0 复用 `build_history`（专家池配置），但 replay 链路存在关键缺口：

1. **Step1 的 `seek_experts_help`**：只 replay tool call 的**文本**，**不执行**建组副作用 → 子群聊内部调度可能与 Round0 不同。
2. **Inject 触发**：`should_inject()` 只看 step 编号；step 到时会先走 `_auto_select_speaker`（`is_select`）或 Expert `generate_reply`（`is_generate`），**谁先被 hook 到谁就吃注入**。
3. **`save_final_result` 原逻辑**：`mistake_agent` 取自 flip round log 该 step 的 agent → 错位时 label 落在 framework 上。

**一句话**：analysis 按 Round N-1 的「step → Expert」做决策；replay round 到同编号 step 时，**实际说话的可能已是选人/调度 agent**，不是当初那个 Expert。

---

## 3. 为什么只有 ~10% 错位，其它样本看起来「很顺畅」？

**不是专家组完全没建立，而是「调度路径是否与 Round0 一致」是否碰巧对齐。**

### 多数样本（~90%）为何顺畅

- `build_history` 复用后，专家池在内存里**存在**；replay 的前几步 content 往往足以让后续 **speaker 选择与 Round0 同路径**。
- 注入点若在 Expert 的 `generate_reply` 上触发，且该 step 未被 `_auto_select_speaker` 抢占 → pre/flip agent 一致，任务正常跑完。

### 少数样本（~10%）为何分叉

- Step1 seek **未真正执行** → 子群聊建立/激活时序与 Round0 有细微差别。
- 到 inject step 时，**先触发的是 `speaker_selection_agent`**，而非目标 Expert。
- 后续 trajectory 仍可能跑通（框架 agent 也能 carry 文本），eval 仍可能 flip → **任务「完成了」，但 inject step 的 agent 错了**。

**类比**：按旧剧本念了前几场对白（content replay），但演员上场顺序（runtime 调度）偏了；90% 的题顺序碰巧一样，10% 在 inject 那一步撞上了 framework 角色。

---

## 4. 与 MetaGPT / MagenticOne 的差异（为何它们少受此问题）

- **固定角色**：step N 几乎恒对应同一 agent 名，content replay 足够保证「同 step = 同 agent」。
- **Captain 动态性**：同 step 编号在不同 round 可能是 `CaptainAgent` → `speaker_selection_agent` → `X_Expert` 等不同 hook；只 replay content **无法保证** hook 顺序与 Round0 一致。

这是 **Captain replay 机制与动态专家组架构的结构性错配**，不是 attack_analysis 白名单失效（analysis 阶段 `validate_attack_suggestion` 仍有效）。

---

## 5. 当前解决方案（已落地）

**策略**：不在本轮大改 replay；对 **eval flip 成功**的样本，在写入 `final_results` 前做**硬校验**，不对齐则自动 discard。

### 校验规则（`validate_flip_attribution_alignment`）

对每个 attribution 条目（`final_info` 中每条 `step_id`）：

```
pre_log[step].agent == flip_log[step].agent
且 agent ∉ NON_INJECTABLE_AGENTS
```

- Attack flip：pre = Round N-1，flip = Round N  
- Diagnose flip：pre = Round N-1，flip = Round N  
- 不通过 → `logger.warning`，**不调用** `save_final_result`

### 代码改动

| 文件 | 改动 |
|------|------|
| `utils/expert_group_report.py` | 新增 `validate_flip_attribution_alignment()` |
| `main.py` | flip 分支在 `save_final_result` 前调用校验；Attack 路径区分 pre/flip log |
| `utils/common.py` | `save_final_result(..., agent_source_log=)`：Attack 的 `mistake_agent` 取自 analysis 轮（pre log） |
| `tests/test_flip_alignment.py` | 单元测试（对齐 / 错位 / framework 三类） |

### 预期效果

| 类型 | 处理 |
|------|------|
| Attack 错位（9 任务） | `Replay agent mismatch` → discard |
| Diagnose 标 framework（4 任务） | pre==flip 但 agent 为 framework → `Non-injectable agent` → discard |
| 对齐样本 | 正常落库；**落库数据 100% step+agent 一致** |
| 代价 | 约 10% flip 成功样本被弃（与此前手动过滤比例相当），改为自动化 |

**未改动的部分**：inject 仍按 step 编号触发；replay 仍只 replay content。当前方案保证**数据质量**，不保证**采集成功率**。

---

## 6. 相关但 distinct 的问题：Diagnose 标 framework

| | Attack 错位 | Diagnose 标 framework |
|--|-------------|------------------------|
| 任务数 | 9 | 4 |
| pre vs flip | **不一致** | **一致**（两轮都是 framework） |
| 根因 | replay 调度分叉 | `diagnose.py` 未用 `filter_injectable_step_ids`，LLM 可选编排步 |
| 当前筛选 | agent mismatch | framework 检查 |

纯 Diagnose 24 条中：19 条通过筛选，5 条被拒（4 框架 + 1 条 expert label 但 flip step agent 漂移）。

---

## 7. 若要从根本改 replay：思路参考

当前「只 replay content」与 MetaGPT/MagenticOne 对齐，对 Captain **不够**。可选方向：

### A. 状态级 replay（治本，改动大）

- Step1 **真正执行** `seek_experts_help`（或等价恢复子群聊状态），而非只 replay tool 文本。
- Inject 绑定 **`(step_id, target_agent)`**，仅在 `target_agent.generate_reply` 时 inject；禁止 `is_select` 路径 inject。
- 参考 MetaGPT：hook 挂在具体 agent/role 的 LLM 调用上，而非仅按全局 step 计数。

### B. 严格 step 对齐 + 失败 discard（当前方案）

- 不改 replay；落盘前校验 pre/flip agent 一致。
- 实现成本低，保证训练集干净；牺牲约 10% 样本。

### C. Diagnose 侧补强（独立）

- 与 Attack 共用 `filter_injectable_step_ids`，analysis 阶段禁止选 framework step。
- 不解决 Attack 错位，但减少 Diagnose framework 误标。

### 建议路径

- **短期**：B（已实施）+ 可选 C。  
- **中期**：A 的子集——至少「inject 绑定 target_agent + 禁止 select hook inject + seek 副作用恢复」，再视 discard 率决定是否 full state replay。

---

## 8. 错位 Attack 任务清单（9）

`task_eada88b86028`, `task_117e0600ec82`, `task_0b95e651f605`, `task_23f53dbd64f3`, `task_3ae30952337c`, `task_ab7701b24412`, `task_f21d8712ce20`, `task_f5c4f2e98742`, `task_f6c92ebfc1a3`

---

## 9. 结论（对外说明用）

1. **错位不是随机噪声**，而是 Captain 在「只 replay content、动态 Expert、按 step 编号 inject」组合下的**结构性偶发问题**。  
2. **大多数样本顺畅**，是因为 `build_history` 复用 + 前置 content replay 在多数轨迹上**碰巧**维持了与 Round0 相同的 speaker 顺序；少数在 inject step 被 framework hook 抢占。  
3. **当前解法**是 flip 成功后、落库前的 **pre/flip agent 硬校验 + 自动 discard**，并修正 Attack 的 `mistake_agent` 来源。  
4. **根本修复**需让 replay 恢复专家组运行时状态，或让 inject 绑定 agent 而非仅 step——参考 MetaGPT 的「按角色 hook」而非 MagenticOne 式纯 step 计数。
