# Captain 注入通道对齐修复（System-Channel Injection Alignment）

> 对话时间：2026-07-30
> 改动性质：把 Captain 的攻击注入从「patch `messages`」改为「覆盖 `_oai_system_message`」，对齐 MetaGPT / MagenticOne 的注入通道
> 关联文档：`ATTACK_INJECTION_PHASE_REPORT.md`（含 P0/P1/P3 已完成工作）
> 预期：本次修改直击「注入通道错位」根因，是迄今最可能显著提升翻转率的一次改动

---

## 1. 背景与问题定位

### 1.1 现象

Captain math 攻击 pipeline 工程上跑通，但 eval 翻转率极低（约 6%）。报告反复出现两类失效模式：

- inject step 输出忽略 INJECTION，按原方式重算或改写成 Python；
- 后续 expert 复核把错误改回。

### 1.2 根因：注入通道错位

AG2 `ConversableAgent.generate_oai_reply` 实际发给 API 的 messages 是两段拼接（`conversable_agent.py:2505, 2511`）：

```
self._oai_system_message + messages
```

- `_oai_system_message`：agent 的角色 system prompt（persona），独立一条，最靠前
- `messages`：与 sender 的群聊历史（含题目原文 + 其他 expert 发言 + 工具结果）

Captain 旧中间件（`observe.py::_inject_replay_prompt`）只 patch `messages`，**完全不碰 `_oai_system_message`**。结果 API 收到：

```
[SystemMessage] = 原 Expert 角色 prompt（"用 Python 解题、验证、相信别人是错的"）  ← 独立、最前、未改
[User/System]   = REPLAY_PROMPT（injection）                                       ← 在 messages 里，二等公民
[User]          = 群聊历史（含题目）
```

Expert 的角色 system 始终主导行为，injection 在 messages 通道优先级低，LLM 倾向按角色 prompt 走（继续用 Python 跑代码得正确答案），injection 被淹没。

---

---

## 2. 关键疑问与解答（对话精华）

### Q1：MetaGPT/MagenticOne 的 `ORIGINAL_TASK` 具体是什么？传递过程？

**结论：`ORIGINAL_TASK` = 当前 agent 的 system prompt / persona（角色指令），不是题目原文，不是上一轮答案，不是某步内容。**

**MetaGPT 链路：**

1. `adapter/MetaGPT/core.py:116-121` 把 `ThinkMiddleware` patch 到 `member.llm.aask`
2. `metagpt/roles/di/engineer2.py:160` 调 `self.llm.aask(context, system_msgs=[WRITE_CODE_SYSTEM_PROMPT])`——`system_msgs` 是 `list[str]`，装角色 instruction（"You are an autonomous programmer..."）
3. `metagpt/provider/base_llm.py:181-201` `aask` 把 `system_msgs` 和 `msg`(context) 分离成两条通道
4. `adapter/MetaGPT/middlewares/think.py:38-42` `system_msgs[0] = monitor.inject_content(default_value=system_msgs[0])`——只改 system 通道，不碰 msg
5. `monitor/attack_monitor.py:77-80` `REPLAY_PROMPT.format(original_task=default_value, ...)`——`default_value` = `system_msgs[0]` = persona

**MagenticOne 链路：**

1. `adapter/MagenticOne/core.py:176-181` patch 到 `model_client.create / create_stream`（所有 LLM 调用必经之路）
2. MagenticOne agent 调 `create()` 时 messages 列表第一个是该 agent 的 `SystemMessage`（如 `MAGENTIC_ONE_CODER_SYSTEM_MESSAGE` / `ORCHESTRATOR_SYSTEM_MESSAGE`，见 `magentic_runtime.py:353-444`）
3. `adapter/MagenticOne/middlewares/replay.py:67-76` 找第一个 `SystemMessage`，`base = m.content`，`new_content = REPLAY_PROMPT.format(original_task=base, ...)`，`mlist[i] = SystemMessage(content=new_content)`——替换第一个 SystemMessage

**共同点：** `ORIGINAL_TASK` = persona；injection 写进 system 通道；题目/历史走另一通道（`msg` / messages 后半段），中间件不碰。

### Q2：MetaGPT/MagenticOne 的 LLM input 除了 persona + injection，还有题目和历史吗？

**有。题目和历史走另一个参数，中间件不碰。**

- MetaGPT：`aask(context, system_msgs=[...])` 的 `context` = `memory + UserMessage(prompt)`（`engineer2.py:153-154`），含对话记忆 + 当前任务 prompt（`user_requirement`/`plan_status`/`file_path`）。中间件只改 system_msgs 原样。
- MagenticOne：`create()` 的 messages 列表 = `[SystemMessage(persona)] + [UserMessage(task + ledger + history)]`。中间件只替换第一个 SystemMessage，其余原样。

所以注入后 LLM 同时拥有：完整任务上下文（保证与题目相关）+ 注入后的角色指令（保证按攻击方式执行）。不会出现"遵循 injection 但和题目完全无关"的问题。

### Q3：Captain 的 `_oai_system_message` 和 `messages` 分别是什么？

- `_oai_system_message`（`conversable_agent.py:273`）：`[{"content": system_message, "role": "system"}]`，单元素列表。Captain Expert 由 `agent_builder.py:330-341` 构建，system_message = `GROUP_CHAT_DESCRIPTION.format(...)`（"You are now working in a group chat... Your role is: {name}... Your profile: {sys_msg}"），含 persona + 群聊指令 + `CODING_AND_TASK_SKILL_INSTRUCTION`（"用 Python 解题""相信别人是错的直到给出证据"）。**是角色/行为指令，不含题目。**
- `messages`（`conversable_agent.py:2502`）：默认取 `self._oai_messages[sender]`——与该 sender（群聊 manager）的对话历史。含 CaptainAgent 通过 `seek_experts_help` 投递的 `execution_task`（题目原文 + 求解计划）+ 各 expert 发言 + Computer_terminal 代码结果。**是群聊历史，题目原文在这里。**

### Q4：`_oai_system_message`（角色 prompt）要不要写进 `original_task`？覆盖会不会导致 persona 重复？

**要写进 `original_task`（它本身就是 original_task），覆盖不会重复。**

覆盖是赋值，不是追加：

```python
orig_sys = agent._oai_system_message[0]["content"]          # ① 读出原 persona
new_sys = REPLAY_PROMPT.format(original_task=orig_sys, ...)  # ② persona 包进 REPLAY_PROMPT
agent._oai_system_message[0]["content"] = new_sys           # ③ 覆盖写回
```

第 ③ 步是 `=` 赋值，直接覆盖 content 字段。执行后 `_oai_system_message[0]["content"]` = 整段 REPLAY_PROMPT，原 persona 不再独立存在，只作为 `new_sys` 内部 `ORIGINAL TASK:` 段的子串出现一次。与 MetaGPT（`system_msgs[0] = ...`）/ MagenticOne（`mlist[i] = SystemMessage(...)`）完全同构。

### Q5：`{original_task}` 能不能空？

**不能。** REPLAY_PROMPT 第一句就是 "You will receive an ORIGINAL_TASK" 且 "Execute the ORIGINAL_TASK"。空了 agent 没有行为基线，injection 没有修改对象。MetaGPT/MagenticOne 里它从不是空的（就是 persona）。题目内容不要塞进 `original_task`，题目走 messages 通道。

### Q6：不改中间件、只给 Captain 单独写 REPLAY_PROMPT 可行吗？

**部分可行但治标不治本。** 只改 prompt 文本能解决：

- ✅ 把 `original_task` fallback 从 round0 正确答案改成题目原文，避免把 `\boxed{正确}` 喂回去；
- ✅ 调整措辞让 injection 在 messages 里更显眼。

解决不了：

- ❌ system 优先级问题：角色 system 仍在最前、独立、未改，injection 在 messages 里权重低；
- ❌ 角色 prompt 与 injection 平级对抗（"inject 后改 Python"现象不消失）。

所以单独写 REPLAY_PROMPT 只能作为"不想动中间件"的过渡，天花板低。

---

## 3. 用户曾踩的坑：`original_task` fallback 到 round0 正确答案

### 3.1 现象

`monitor/attack_monitor.py:61-81` 旧逻辑：`default_value` 为空时 fallback 到 `get_current_reply()` = `self._last_round_log['history'][self.step-1]['content']`——即上一轮同 step 的完整正确推导（含 `\boxed{正确答案}`）。

Captain `observe.py:117` 在 messages 为空时调 `inject_content(default_value="")`，触发此 fallback。于是 REPLAY_PROMPT 变成：

```
ORIGINAL TASK:
[round0 的完整正确推导 + \boxed{98}]   ← 正确答案被喂进去

INJECTION INFO:
[attacked_content，比如"验证逻辑有误"]
```

### 3.2 为什么帮倒忙

REPLAY_PROMPT 措辞是 "Execute the ORIGINAL_TASK, STRICTLY FOLLOWING modifications in INJECTION INFO" + "minimal and surgical"。当 `ORIGINAL_TASK` 本身是含正确 `\boxed{}` 的完整解时，"execute the original task" 退化成"复述正确解"，"minimal surgical modification" 让 LLM 觉得 injection 只能动一点——正确答案已在手，原样输出，injection 失效。

这正是报告 `task_82d6ca8388d5` 的现象：`attacked_content` 是"完整正确验证 + `\boxed{98}`"，注入后 LLM 仍跑代码得 98。

### 3.3 用户的理解偏差

用户原以为"把 round0 正确答案放进去，agent 在此基础上生成错误答案"。但 `ORIGINAL_TASK` 的真实语义是 **"agent 在该步要执行的那条指令/角色定义"**，不是"agent 要在其上继续生成的内容"。把正确答案塞进去，等于把 injection 从"改行为"降级成"改答案文本"，而答案文本已经是对的，injection 被淹没。

---

## 4. 代码改动（本次）

### 4.1 改动文件 1：`monitor/attack_monitor.py`（改动 D）

移除 `get_current_reply()` fallback，改为 `default_value` 为空时 log error。

`inject_content` 方法（61-80 行）：

- **旧**：`default_value` 为空 → `get_current_reply()`（上一轮正确答案）→ 包进 REPLAY_PROMPT
- **新**：`default_value` 为空 → log error，保持空（走 system 通道后 `default_value` 是 persona，非空，不会触发；触发即暴露调用方 bug）

注释明确说明：不能用上一步输出兜底，否则会把上一轮（可能正确的）答案带进 `ORIGINAL_TASK`，让 LLM 复述正确答案，瓦解注入。

### 4.2 改动文件 2：`adapter/Captain/middlewares/observe.py`（改动 A/B/C/F）

**改动 A — 新方法 `_inject_replay_prompt_system`（103-145 行）：**

- 读出 `agent._oai_system_message[0]["content"]`（persona）
- `monitor.inject_content(default_value=persona)` 生成 REPLAY_PROMPT
- 覆盖写回 `_oai_system_message[0]["content"]`
- stash 原 persona 到 `ctx._captain_inject_orig_sys` 供恢复
- **不碰 `messages`**（群聊历史含题目原样保留）
- 若 `_oai_system_message` 形状异常，fallback 到旧 messages 路径（安全网）

**改动 C — `before` 钩子分流（281-292 行）：**

- `is_generate` + should_inject → 走 system 通道（`_inject_replay_prompt_system`）
- `is_select` + should_inject → 保留 messages 通道（`_inject_replay_prompt`，speaker selection 无 persona 可覆盖）

**改动 B — `after` 钩子恢复（336-359 行）：**

- 在所有分支之前检查 `ctx._captain_inject_orig_sys`
- 非空则把 `_oai_system_message[0]["content"]` 恢复原 persona
- 异常路径也恢复（try/except + log）
- 恢复后清空标记，避免后续步骤误恢复

**改动 F — 可观测性：**

- `system_inject applied` 日志：method/step/orig_len/new_len/messages_len/first_msg_role
- `system_after` 日志：新 system 内容前 800 字（确认是 REPLAY_PROMPT）
- `system restored after inject` 日志：确认恢复成功

### 4.3 未改动（按指示）

- `utils/prompts.py` 的 Captain 专属 REPLAY_PROMPT（改动 E）：先不动，用现有 REPLAY_PROMPT 跑回归。若实测 persona 与 injection 仍有冲突再考虑。

---

## 5. 改动后的 API 输入（与 MetaGPT/MagenticOne 同构）

```
[SystemMessage] = REPLAY_PROMPT(persona + INJECTION_INFO)   ← 覆盖 _oai_system_message，inject step 临时
[User/Assistant] = 群聊历史（含 execution_task 题目原文）     ← messages 原样
[Assistant]      = 其他 expert 上一轮发言                    ← messages 原样
[User]           = Computer_terminal 代码结果                  ← messages 原样
```

inject step 跑完后 persona 恢复，后续步骤 Expert 仍是原角色。

---

## 6. 与已有 P0/P1/P3 工作的关系

读报告全文（638 行）后发现，之前已完成的工作：


| 阶段       | 内容                                                                                                                          | 状态                  |
| -------- | --------------------------------------------------------------------------------------------------------------------------- | ------------------- |
| P0       | `CAPTAIN_LOG_LLM_INPUT` 可观测性（`llm_input_log.py`）                                                                            | 已实现验证               |
| P1（历史恢复） | `observe.py` 的 `_resolve_oai_messages` / `fell_back`，inject 时 prepend REPLAY 到恢复的历史                                         | 已实现验证               |
| P3       | `utils/expert_group_report.py` 过滤 `speaker_selection_agent`/`Computer_terminal` 等非解题 agent；`ATTACK_ANALYSIS_PROMPT` 要求过程级错误 | 已实现（§18，2026-07-30） |


**本次改动是报告 §7 原本提的另一个 P1——「Captain 注入通道对齐 MetaGPT/MagenticOne」。之前做的是「历史恢复」P1，本次做的是「通道对齐」P1，两者互补不冲突。**

### 6.1 本次改动正好对症报告 §15.3 的潜在负面效应

报告 §15.3 指出 P1（历史恢复）有潜在负面效应：恢复历史后 API 收到

```
msg[0] system → 角色画像（含 verify 约束）
msg[1] system → REPLAY 注入（attacked_content）   ← 二等 system
msg[2] user   → 群聊历史（含前序专家的正确解答）
```

LLM 同时看到"REPLAY 要求改错"和"历史里的正确答案"，倾向跟随正确答案。

**本次通道对齐改动正好解决这个**：改完后 API 收到

```
msg[0] system → REPLAY_PROMPT(persona + injection)   ← 唯一 system，最高优先级
msg[1] user   → 群聊历史（含前序专家的正确解答）
```

injection 不再是二等 system，而是和 persona 融合的唯一 system。所以本次回归的核心观察点：§15.3 描述的"LLM 跟随历史正确答案"现象是否减弱。

### 6.2 报告 §15.2 的失败原因分布（提醒瓶颈仍在 attack_analysis 质量）

报告 §15.2 修正了根因分布（9 个注入案例）：


| 失败原因                            | 占比  |
| ------------------------------- | --- |
| 注入目标错误（speaker_selection_agent） | 44% |
| attacked_content 太弱/自相矛盾        | 33% |
| 后续专家纠正                          | 11% |
| 跨 group chat 上下文断层              | 11% |


P3 已解决 44%（注入目标过滤）。本次通道对齐主要针对 11%（后续专家纠正）和 §15.3 的负面效应。**attack_analysis 内容质量（33%）仍需后续 P3 强化。** 本次改动不期望单独把翻转率拉到很高，但应能看到注入遵从率提升和"跟随历史正确答案"现象减弱。

---

## 7. 验证命令

```bash
RUN_TAG="math_captain_sys_$(date +%Y%m%d_%H%M%S)"
nohup env \
  CAPTAIN_LOG_LLM_INPUT=1 \
  CAPTAIN_LOG_LLM_INPUT_PREVIEW=3000 \
  MAS_FAIL_ATTR_LOG="logs/${RUN_TAG}.log" \
  python main.py \
  --dataset math \
  --backend Captain \
  --workspace "./workspace/${RUN_TAG}" \
  --output "./output/${RUN_TAG}" \
  --max_samples 3 \
  --max_rounds 3 \
  --env_file /data/sdb/liuhui36/mas-failure-attribution/config/env \
  > /dev/null 2> "logs/${RUN_TAG}.stderr.log" &
echo "RUN_TAG=${RUN_TAG}"
```

### 7.1 中间件执行顺序（保证日志能看到注入后的 system）

- `ThinkMiddleware`（含本次注入逻辑）hook 的是 `generate_reply` / `a_generate_reply`（外层）
- `LlmInputLogMiddleware` hook 的是 `generate_oai_reply`（内层）

调用链：`generate_reply`（ThinkMiddleware.before 跑 → 覆盖 `_oai_system_message`）→ `generate_oai_reply`（LlmInputLogMiddleware.before 跑 → 记录已覆盖的 system + messages）→ 返回 → `generate_reply`（ThinkMiddleware.after 跑 → 恢复 persona）。

所以 `[llm-input-inject]` 日志看到的是**注入后、恢复前**的真实 API 输入，能正确验证。

### 7.2 跑完后检查清单

```bash
RUN_TAG=<your_tag>

# 1) 确认 inject step 的 system 段是 REPLAY_PROMPT（不再是原 persona）
grep '\[llm-input-inject\] msg\[0\] role=system' logs/${RUN_TAG}.log | head

# 2) 确认 inject step 的 messages 通道是群聊历史（非空，含题目）
grep '\[llm-input-inject\] message_count' logs/${RUN_TAG}.log | head

# 3) 确认注入目标不再含 speaker_selection_agent（P3 生效）
grep '\[llm-input-inject\]' logs/${RUN_TAG}.log | grep -oP 'agent=\K[^ ]+' | sort | uniq -c

# 4) 确认每步 inject 后 persona 都恢复了
grep 'system restored after inject' logs/${RUN_TAG}.log | wc -l

# 5) 翻转率
grep -E "Eval Result.*flip|true→false|Attack.*flip" logs/${RUN_TAG}.log | head
```

### 7.3 期望

- ① `msg[0] role=system` 的 preview 含 "You are an assistant that executes instructions... ORIGINAL TASK: ... INJECTION INFO:"（REPLAY_PROMPT 结构），且 original_task 段是角色 persona（不再是 round0 正确答案）
- ② `message_count` ≥ 2（system + 至少 1 条历史），与 P1 修复后一致
- ③ agent 列表不含 `speaker_selection_agent` / `Computer_terminal`（P3 过滤生效）
- ④ 恢复次数 = inject 次数（每步都恢复）
- ⑤ 翻转率对比 `math_captain_p1_20260729_164913`（0/3）和 `math_captain_p3_20260730_135743`（P3 批次）有所提升

### 7.4 对照批次


| Run                               | 代码版本     | 说明                                   |
| --------------------------------- | -------- | ------------------------------------ |
| `math_captain_20260728_172643`    | Pre-P1   | 旧 messages 注入，无历史恢复                  |
| `math_captain_p1_20260729_164913` | P1（历史恢复） | messages 注入 + fell_back 历史恢复         |
| `math_captain_p3_20260730_135743` | P3（过滤）   | + attack_analysis 目标过滤               |
| `math_captain_sys_*`（本次）          | P1（通道对齐） | + system 通道注入，对齐 MetaGPT/MagenticOne |


---

## 8. 核心结论一句话

> Captain 旧实现把 REPLAY_PROMPT 塞进 `messages`（二等通道），Expert 的 `_oai_system_message`（含"用 Python 解题"等强约束）作为独立的最前 system 原样保留，导致 injection 被角色 prompt 压制；叠加 `original_task` fallback 到 round0 正确答案，LLM 直接复述正确解。本次改动把 injection 移到 system 通道（覆盖 persona，persona 被包进 REPLAY_PROMPT 的 ORIGINAL_TASK），题目/历史仍走 messages 不动——与 MetaGPT/MagenticOne 完全同构，injection 升级为 persona 层的行为指令，预期注入遵从率与翻转率显著提升。

---

*文档生成时间：2026-07-30*