# 归因数据自动化采集 — 学生讲解提纲

> 面向明天的讲解参考。图 1 为理论流程（与代码细节略有出入）；**图 2 为实际实现主线**，以 **Captain Agent（AG2/AutoGen）** 为例，**重点讲回放（Replay）设计**。

---

## 一、开场：我们在做什么？

**目标**：自动构造「多智能体系统（MAS）故障归因」训练/评测数据。

**核心思想**：
1. 先跑一遍 MAS，得到**成功轨迹**（Round 0）。
2. 分析轨迹，选定**攻击/修复点**（某一步、某 agent）。
3. **回放**同一任务：目标步之前**不重新调用 LLM**（直接复用历史），目标步**注入错误/修复**，之后**真实执行**。
4. 若最终答案**翻转**（对→错 或 错→对），说明该步是**关键归因点**，自动记录 ground truth。

**一句话**：不是随机造错，而是**可控注入 + 确定性回放 + 翻转验证**，把「谁错了」变成可标注的数据。

---

## 二、图 1：理论流程（简要，约 3–5 分钟）

理论方案分三阶段，与代码大体对应，但实现上做了简化/聚焦。

### 2.1 预处理阶段

| 理论步骤 | 代码对应 |
|---------|---------|
| 采集成功轨迹 | `main.py` Round 0：`_execute_task_and_capture_trace` |
| 获取交互拓扑 | `BaseMonitor.record_topology` + middleware `after` 钩子 |
| 构建故障候选池 | `utils/prompts.py` 中 `ATTACK_ANALYSIS_PROMPT` / 错误类型 taxonomy |

### 2.2 数据生成与标注阶段

| 理论步骤 | 代码对应 |
|---------|---------|
| 选目标 agent + 错误注入 | `pipeline/coding/attack.py` → `attack_analysis.json` |
| 多轮单点注入 + MAS 回放 | `main.py` Round ≥ 1 + `AttackMonitor` + Captain middleware |
| 致命影响？→ 记录真值 | eval 翻转检测：`eval_results[task_key] ^ last_eval_results[task_key]` |
| 多点故障？ | `injection_history` 累积多轮 `attack_analysis.json` |

### 2.3 对抗式难例筛选

理论上有「强模型判别器」筛难例；**当前代码主线尚未完全落地此步**，讲解时可作为「后续工作」一笔带过。

---

## 三、图 2：实际代码流程（主讲，约 15–20 分钟）

下面用图 2 的五步逻辑串起整条 pipeline。

```
┌─────────────────────────────────────────────────────────────────┐
│ Step 1  Round 0：query → MAS 执行 → pred_answer + history       │
│         文件：main.py, pipeline/runners/*, BaseMonitor          │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ Step 2  eval：pred_answer == ground_truth ?                     │
│         文件：pipeline/*/eval.py, evaluate_round                │
└──────────────┬─────────────────────────────┬────────────────────┘
               │ True（成功）                  │ False（失败）
               ▼                              ▼
┌──────────────────────────┐    ┌──────────────────────────┐
│ Step 3a  Attack 模块      │    │ Step 3b  Diagnose 模块    │
│ attack_analysis.json     │    │ diagnose_analysis.json   │
│ step_id + attacked_content│   │ step_id + suggested_fix  │
└──────────────┬───────────┘    └──────────────┬───────────┘
               └──────────────┬──────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ Step 4  回放 + 注入                                             │
│   step < step_i  →  middleware 短路，返回 history 缓存          │
│   step == step_i →  inject REPLAY_PROMPT，真实调用 LLM          │
│   step > step_i  →  正常 live 执行                              │
│         文件：AttackMonitor + adapter/Captain/middlewares/observe.py │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ Step 5  翻转检测：eval 相对上一轮是否变化？                      │
│   翻转 → 保留样本，写入归因标签                                  │
│   未翻转 → Attack/Diagnose Fail，可进入下一轮注入                │
└─────────────────────────────────────────────────────────────────┘
```

### 3.1 Step 1：首次执行与轨迹记录

**入口**：`main.py` 第 198–214 行，Round 0 并发跑所有 task。

```python
# main.py ~126-128
monitor = BaseMonitor(recovery_path, workspace, backend)
# → runner(task, workspace, output, backend, monitor=monitor)
```

**Monitor 记录什么**（`monitor/base_monitor.py`）：
- `history`：逐步 assistant 输出（step、content、name、role）
- `topology`：agent 间有向交互边
- `step`：当前步计数器（从 1 递增）

**Round 0 产出**（每个 task 目录）：
- `log.json`：`history` + `model_prediction`
- `recovery/`：monitor 序列化 checkpoint
- Captain 还会在 `round_0/<task_id>/` 保存 `build_history_*.json`（专家组建配置，供后续轮复用）

### 3.2 Step 2：正确性判定

`evaluate_round` 对比 `model_prediction` 与 `ground_truth`，得到 `eval_results[task_id]: bool`。

- **True** → 走 Attack（「怎么把对的搞错」）
- **False** → 走 Diagnose（「怎么把错的搞对」）

### 3.3 Step 3：Attack / Diagnose 分析

**调度**：`main.py` 第 265–349 行。

```python
# 成功 → attack
is_success = await attack_analysis(task=last_round_log, ...)
replay_info = get_attack_analysis(output)  # 取最后一条 suggestion

# 失败 → diagnose
is_success = await diagnose_analysis(task=last_round_log, ...)
replay_info = get_diagnose_analysis(output)
```

**分析产物示例**（`attack_analysis.json`）：

```json
{
  "step_id": 3,
  "attacked_content": "...错误推导... \\boxed{3}",
  "mistake_reason": "...",
  "fault_code": "f1_4_knowledge_or_reasoning_limitation"
}
```

- `step_id`：对应 `log.json` 里 `history[step_id - 1]`（1-based）
- `attacked_content`：注入到 REPLAY 的「修改指令」

### 3.4 Step 4：构造 AttackMonitor 并回放

```python
# main.py ~351-358
monitor = AttackMonitor(
    replay_recovery_path,   # 上一轮 recovery/
    workspace,
    backend,
    replay_info[-1],        # suggestion dict
    last_round_log,         # 上一轮完整 log（replay 数据源）
)
runner(task, workspace, output, backend, monitor=monitor)
```

**AttackMonitor 三个关键状态**（`monitor/attack_monitor.py`）：

| 成员 | 含义 |
|------|------|
| `_attack_step` | 注入发生在 monitor 的第几步 |
| `_attack_suggestion` | `attacked_content` 或 `suggested_fix` |
| `_last_round_log` | 上一轮 history，供 `get_current_reply()` 读取 |

### 3.5 Step 5：翻转检测

```python
# main.py ~403
if eval_results[task_key] ^ last_eval_results[task_key]:
    # 行为翻转 → 归因样本成立
```

- Attack 成功：**True → False**（一招致命）
- Diagnose 成功：**False → True**（修复有效）

---

## 四、Captain 系统架构（切入角度）

Captain 基于 **AG2 CaptainAgent**：协调者 Captain 通过 `seek_experts_help` 动态组建专家 GroupChat，多轮选发言人 + 生成回复。

**我们的适配层职责**（`adapter/Captain/core.py`）：

1. **跑通 backend 契约**：`run_backend(idea, workspace, monitor, ...)`
2. **在不改 AG2 源码的前提下**，用 middleware **hook** 关键方法
3. **保证 Round ≥ 1 与 Round 0 专家组成一致**（replay 可比性）
4. **把每次 LLM 调用变成可编号、可回放、可注入的 step**

### 4.1 整体调用链

```
main.py
  → CaptainAdapter.run_backend()
       → 创建 CaptainAgent + UserProxy
       → _instrument_agents()          # 给已知 agent 打补丁
       → _install_dynamic_agent_hooks() # 给动态创建的专家打补丁
       → user_proxy.initiate_chat(captain, message=idea)
            → [多次] generate_reply / _auto_select_speaker
                 → ThinkMiddleware.before()  # replay / inject 决策
                 → (可选) 真实 LLM 调用
                 → ThinkMiddleware.after()   # record_step / topology
```

---

## 五、回放设计（重点）

回放的核心问题：**如何让 MAS「看起来重新跑了一遍」，但实际上目标步之前零 LLM 成本、且输出与 Round 0 完全一致？**

### 5.1 设计原则

| 原则 | 实现 |
|------|------|
| **步对齐** | Monitor 的 `step` 与 `last_round_log.history` 下标一一对应 |
| **注入前严格 replay** | `step < attack_step`：middleware `before` 直接 `return` 缓存内容，**不调用**原方法 |
| **注入步 live** | `step == attack_step`：`should_inject()` 为真，改 prompt 后**真实调 LLM** |
| **注入后 live** | `is_injected()` 为真：后续步全部真实执行，观察错误传播 |
| **身份冻结** | 复用 `round_0` 的 `build_history`，避免重建不同专家集 |

### 5.2 Middleware 短路机制

`adapter/middleware.py` 的 `build_llm_wrapper`：

```python
result = m.before(ctx)
if result is not None:
    return result   # 短路！不执行原 generate_reply
else:
    result = await func(...)  # 正常执行
```

**ThinkMiddleware.before** 在 replay 阶段返回非 `None` → 等价于「MAS 没有真正执行这一步，只是播放录像」。

### 5.3 三类 hook 点

Captain 只 hook 两类与「一步」强相关的方法（`observe.py` 第 252–258 行）：

1. **`generate_reply` / `a_generate_reply`**：专家/Captain 生成内容 → 记入 history 的「内容步」
2. **`_auto_select_speaker` / `a_auto_select_speaker`**：选下一个发言人 → 记入 history 的「选人步」

不 hook 其他内部调用，避免一步被记两次。

### 5.4 Replay 数据源

```python
# monitor/attack_monitor.py:82-83
def get_current_reply(self) -> str:
    return self._last_round_log['history'][self.step - 1]['content']
```

- `self.step` 是当前即将记录的步号（1-based）
- 返回上一轮**同一步**的 content，作为本次「假回复」

选发言人时特殊处理（`observe.py` 304–313 行）：返回的是 agent 名字字符串，再 `agent_by_name` 转成 agent 对象。

### 5.5 注入：REPLAY_PROMPT

```python
# monitor/attack_monitor.py:61-79
def inject_content(self, default_value: str) -> str:
    if self.should_inject():
        self._injected = True
        return REPLAY_PROMPT.format(
            original_task=default_value,      # 通常是 agent persona
            injection_info=self._attack_suggestion,
        )
    return default_value
```

`REPLAY_PROMPT` 定义见 `utils/prompts.py` 第 243–263 行：要求 LLM 在 persona（ORIGINAL TASK）基础上，**按 INJECTION INFO 做最小修改**。

### 5.6 注入通道：System vs Messages（重要设计点）

Captain 的 Expert 有两层 prompt：
- **`_oai_system_message`**：角色 persona（如「用 Python 解题」）
- **`messages` / `_oai_messages[sender]`**：群聊历史 + 题目

**当前实现**（`_inject_replay_prompt_system`，`observe.py` 103–145 行）：
- **generate_reply 路径**：覆盖 `_oai_system_message[0]` 为整段 REPLAY_PROMPT，**不动** messages 里的群聊历史
- **speaker_selection 路径**：无 persona，走 `_inject_replay_prompt` 改 messages

注入后在 `after` 里**恢复**原 system prompt（336–359 行），避免污染 agent 对象。

**`_resolve_oai_messages`**（147–167 行）：AG2 GroupChat 调用 `generate_reply(sender=self)` 时不传 messages，需从 `agent._oai_messages[sender]` 拉回历史，否则注入步专家处于「上下文真空」。

### 5.5 Round ≥ 1 专家身份复用

```python
# core.py:91-93, 146-174
saved_build_history = self._load_saved_build_history(workspace, agent_library_path)
captain.executor.build_history.update(saved_build_history)
```

- `agent_library_path` 固定指向 `round_0/<task_id>/`（110–143 行）
- `_force_replay_group_name`（234–259 行）：强制使用已保存的 group_name，避免重建新专家组

---

## 六、代码精读：`core.py` 重点行

| 行号 | 函数/逻辑 | 讲解要点 |
|------|-----------|----------|
| **59–107** | `run_backend` | Captain 单次任务入口；创建 agent → 打补丁 → `initiate_chat` |
| **91–93** | 加载 `build_history` | Replay 轮复用 Round 0 专家库，保证拓扑一致 |
| **100–102** | `_instrument_agents` + `_install_dynamic_agent_hooks` | 静态 + 动态 agent 都挂上 ThinkMiddleware |
| **110–143** | `_resolve_agent_library_path` | 库路径永远锚定 `round_0/<task_id>` |
| **146–174** | `_load_saved_build_history` | 非 round_0 时 merge 所有 `build_history_*.json` |
| **192–231** | `_install_dynamic_agent_hooks` | 包装 `executor._run_autobuild`，在专家创建时临时 patch Builder/GroupChat |
| **234–259** | `_force_replay_group_name` | `CAPTAIN_FREEZE_GROUP_IDENTITY=1` 时锁定 group 名 |
| **281–302** | `_patch_groupchat_methods` | 给 `_auto_select_speaker` 等挂 ThinkMiddleware（含 topology + 动态 instrument） |
| **316–353** | `_instrument_agents` | 每个 agent 的 `generate_reply` / `generate_oai_reply` 挂 middleware |
| **383–438** | `_wire_patched_oai_reply` | AG2 通过 `_reply_func_list` 调 reply，需显式替换才能 hook 到 |
| **356–364** | `_collect_participants` | Captain 顶层参与者列表（replay 控制范围） |

**讲解时可强调**：`core.py` 解决的是「**在哪 hook**」和「**replay 环境一致性**」；真正的 replay/inject 决策在 `observe.py`。

---

## 七、代码精读：`observe.py` 重点行

### 7.1 辅助函数

| 行号 | 函数 | 讲解要点 |
|------|------|----------|
| **39–52** | `_decode_replay_payload` | Round 0 部分 step 存的是 dict 字符串（tool_calls），replay 时需解析 |
| **76–90** | `_extract_messages` | 从不同方法签名里统一取出 messages 参数 |
| **147–167** | `_resolve_oai_messages` | GroupChat 无 messages 参数时的历史回退 |

### 7.2 注入逻辑

| 行号 | 函数 | 讲解要点 |
|------|------|----------|
| **103–145** | `_inject_replay_prompt_system` | **主注入路径**：persona → REPLAY_PROMPT，写 `_oai_system_message` |
| **169–247** | `_inject_replay_prompt` | speaker selection / fallback 路径；prepend 或替换首条 message |
| **206–218** | `fell_back` 分支 | 有群聊历史时 **prepend** REPLAY，不覆盖历史 |

### 7.3 核心决策：`before`（249–334 行）

建议按以下顺序讲解：

```
before(ctx):
  │
  ├─ 非 select/generate → 放行（257-258）
  │
  ├─ should_inject() == True  【注入步】
  │     ├─ generate → _inject_replay_prompt_system
  │     └─ select    → _inject_replay_prompt
  │     return None  → 继续真实调 LLM
  │
  ├─ not is_injected()  【注入前的 replay 段】
  │     ├─ generate 且非 can_replay → 放行（内部 agent 不 replay）
  │     ├─ replayed = get_current_reply()
  │     └─ replayed 非空 → return replayed  【短路！】
  │
  └─ is_injected() 之后 【live 段】→ 放行，正常执行
```

**`can_replay` 过滤**（277–280 行）—— 避免重复/错乱：

```python
can_replay = (
    sender_name == "chat_manager"
    or (src_name == "CaptainAgent" and sender_name == "Expert_summoner")
)
```

只有「群聊 manager → 专家」或「Captain ← Expert_summoner 汇总」的 generate_reply 才参与 replay；`speaker_selection_agent` 等内部调用走自己的 hook 路径。

### 7.4 记录与恢复：`after`（336–402 行）

| 行号 | 逻辑 | 讲解要点 |
|------|------|----------|
| **336–359** | 恢复 `_oai_system_message` | 注入步结束后还原 persona |
| **361–370** | `_create_internal_agents` | 动态创建的 selector agent 补挂 instrument |
| **372–378** | speaker selection 记录 | `record_step` + `record_topology` |
| **388–391** | 跳过 selector 的 generate_reply | 避免一步记两次 |
| **393–401** | 正常 generate 记录 | topology 边 + history content |

---

## 八、完整小例子（建议演示用）

**任务**：`task_c573a016401d` — 多项式次数（Round 0 答对 `\boxed{4}`）

**Round 0 history 节选**（`log.json`）：

| step | name | 内容摘要 |
|------|------|----------|
| 1 | CaptainAgent | 调用 seek_experts_help |
| 2 | speaker_selection_agent | 选 Python_Expert |
| 3 | Python_Expert | 正确推导，`\boxed{4}` |
| 4 | speaker_selection_agent | 选 Data_Analysis_Expert |
| 5 | Data_Analysis_Expert | 验证，仍 `\boxed{4}` |
| … | … | … |

**Round 1 Attack**（`attack_analysis.json`）：

- `step_id: 3` → 打在 **Python_Expert 首次解题**
- `attacked_content`：错误合并项，声称次数为 3，`\boxed{3}`

**回放时 middleware 行为**：

| monitor.step | 行为 |
|--------------|------|
| 1–2 | `get_current_reply()` → 返回 Round 0 step 1–2 内容，**不调 LLM** |
| 3 | `should_inject()` → system 通道注入 REPLAY，**真实调 LLM**，应产出错误推导 |
| 4+ | `is_injected()` → 后续专家 **live** 运行，可能纠正或放大错误 |

**Step 5 判定**：若最终 eval 从 True 变 False → 样本标注为「step 3 / Python_Expert / 推理错误」。

---

## 九、与图 1 的差异（诚实说明，防学生追问）

| 图 1 理论 | 当前实现 |
|-----------|----------|
| 显式「故障候选池」匹配 agent 角色 | 由 Attack LLM + fault_code 隐式完成 |
| 多点故障循环在同一轮叠加 | 多轮 Round 1/2/3 各注入一次，`injection_history` 串联 |
| 强模型难例筛选 | 尚未接入主 pipeline |
| 归因子图自动抽取 | 有 topology + step 级 history，子图后处理可扩展 |

---

## 十、讲解节奏建议（约 30 分钟）

| 时间 | 内容 |
|------|------|
| 0–5 min | 图 1 三阶段 + 项目目标 |
| 5–10 min | 图 2 五步 + `main.py` 调度 |
| 10–22 min | **回放设计**：Middleware 短路 + AttackMonitor 状态机 + 注入通道 |
| 22–27 min | 走读 `observe.py` 的 `before` 分支（可投屏代码） |
| 27–30 min | 小例子 + Q&A |

---

## 十一、常见问题预案

**Q1：replay 会不会和 Round 0 步数对不齐？**  
A：靠 `BaseMonitor.step` 单调递增 + `get_current_reply` 用 `step-1` 索引；selector 与 generate 交替出现，与 Round 0 记录顺序一致。

**Q2：为什么注入步还要调 LLM，不能也缓存？**  
A：注入步的目的就是**产生与 attacked_content 一致的新行为**；之前步缓存是为了「复现相同上下文」，注入步必须 live。

**Q3：Diagnose 和 Attack replay 机制一样吗？**  
A：一样，共用 `AttackMonitor` + `ThinkMiddleware`；区别只在 suggestion 字段名（`attacked_content` vs `suggested_fix`）和 eval 翻转方向。

**Q4：Captain 为何专门处理 `_oai_system_message`？**  
A：Expert 角色 system prompt 优先级高于 messages 里的注入；只改 messages 时 injection 常被 persona 压制（详见 `CAPTAIN_SYSTEM_CHANNEL_INJECTION_FIX.md`）。

**Q5：`build_history` 不复用会怎样？**  
A：Round 1 可能组出不同名字/数量的专家，history 步序与 topology 无法与 Round 0 对齐，replay 语义崩溃。

---

## 十二、相关文件索引

| 文件 | 职责 |
|------|------|
| `main.py` | 多轮调度、Attack/Diagnose 分支、翻转检测 |
| `monitor/base_monitor.py` | history / topology / step 记录 |
| `monitor/attack_monitor.py` | 注入步判定、`inject_content`、`get_current_reply` |
| `adapter/Captain/core.py` | Captain 适配、hook 安装、build_history 复用 |
| `adapter/Captain/middlewares/observe.py` | **Replay + Inject 核心** |
| `adapter/middleware.py` | Middleware 短路框架 |
| `utils/prompts.py` | `REPLAY_PROMPT`、`ATTACK_ANALYSIS_PROMPT` |
| `pipeline/coding/attack.py` | 攻击分析 LLM 调用 |
| `CAPTAIN_SYSTEM_CHANNEL_INJECTION_FIX.md` | 注入通道设计说明（深入阅读） |
| `ATTACK_INJECTION_PHASE_REPORT.md` | 实验现象与翻转率分析 |

---

*文档生成自当前代码库；行号以 `adapter/Captain/core.py` 与 `adapter/Captain/middlewares/observe.py` 为准。*
