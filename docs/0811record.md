# 0811record（交接日记）
 
> 说明：仓库在 `fix/post-owl-merge`，8 月 11 日**没有 git commit**，当日工作多在未提交改动与实验产物中。

---

## 2026-08-11

从代码与当日对话看，工作主线是 **Captain × Math 数据质量与采集链路**，可分成四块：

### 1. 反 meta 泄漏：Prompt 迭代 + 泄漏样本重跑

- 审计既有约 800 条 / 114 条 `final_results`，定位 `history` 里自曝注入意图的样本。
- 新增 `utils/prompt_v1.py`、`utils/meta_leakage.py`、`scripts/audit_meta_leakage.py`、`scripts/rerun_leaked_attack.py`。
- 把 v1 约束**增量**合入 `utils/prompts.py`（保留 V0）：约束 `attacked_content` / replay 输出不要写 “injection / incorrectly / 按指令修改” 等 meta 话术，且不改成“完整重写回复”。
- 对泄漏样本做了两轮重跑，对比泄漏率与翻转率。

### 2. Attack 注入步 agent 错位：根因分析 + 落库硬筛选

- 厘清约 10% Attack flip 在 analysis 选 Expert，replay 同 step 却变成 `speaker_selection_agent` 等框架角色。
- 根因写进 `ATTACK_STEP_AGENT_MISALIGNMENT.md`：Captain 动态专家组 + **只 replay content 不 replay 状态**。
- 落地方案（不大改 replay）：
  - `utils/expert_group_report.py`：`validate_flip_attribution_alignment`
  - `main.py`：flip 成功后、写 `final_results` 前校验；Attack 丢弃错位；Diagnose 后来放宽为只拦 pre≠flip
  - `tests/test_flip_alignment.py`
- 跑了 `math_captain_v1_20260811_113339`（50 条），排查翻转少、筛选是否过严、prompt 副作用等问题。

### 3. `spoke_experts` 为空：修 8/7 GAIA 适配引入的回归

- 对比 8/4–8/11 日志，定位到 8/7 GAIA 适配改动导致 R1 起 `generate_reply` hook / nested workdir 异常，Expert 发言空。
- 修复集中在 `adapter/Captain/core.py`（nested `code_execution_config` / GroupChat patch 合并等）。
- 验证跑：`math_captain_verify_20260811_162338`；并准备重跑 8/7 批次（保留 round_0，重跑后续 round）。

### 4. 并发可行性分析（偏分析，未大改架构）

- 结论：任务间无依赖，但进程内 `--concurrent` 有 backend 共享状态风险；更稳的是**多进程 + 不重叠样本区间 + 独立 output/workspace**。
- 傍晚又起了新批次 `math_captain_v1_20260811_175411`。

### 备注

- 仓库里还有相对 7/18 HEAD 的**更早未入库存量**（例如 math eval、`attack_monitor` 等），**不算当天主动改动**。
- 相关文档：`ATTACK_STEP_AGENT_MISALIGNMENT.md`、`ATTACK_INJECTION_PHASE_REPORT.md`、`CAPTAIN_SYSTEM_CHANNEL_INJECTION_FIX.md`、`adapter/Captain/RETRY_DESIGN.md`。

---

## 2026-08-12

对两批 Captain Math 重跑/新跑结果做了完整核验（日志 + `final_results` + 各轮 `log.json` 交叉比对），重点：**泄漏、agent 错位、8/11 加的 flip 落库筛选是否生效**。

### 批次概览

| 批次 | output | 样本窗口 | final 数 | Attack / Diagnose | 备注 |
|------|--------|----------|----------|-------------------|------|
| 8/7 重跑 | `math_captain_msg_20260807_104217_rerun` | [720:800] 80 条 | 39 | 33 / 6 | 傍晚跑完至 R3 |
| 8/11 新跑 | `math_captain_v1_20260811_175411` | [800:900] 100 条 | 40 | 33 / 7 | 多进程 lane，独立 output |

### 1. 8/11 筛选机制（`validate_flip_attribution_alignment`）是否生效

**结论：生效。** 机制在 eval 翻转后、写 `final_results` 前按轮次校验：对每个 attribution 的 `step_id`，要求 **pre 轮 log 与 flip 轮 log 的 agent 一致**；Attack 额外拒绝 framework 角色。

| 指标 | 720–800 | 800–900 |
|------|---------|---------|
| 日志 `Flip alignment rejected` | 3 次 | 6 次 |
| 写入 final 的归因步 pre==flip==label | **51/51** | **56/56** |
| final Attack 实际注入步 agent 对齐 | **33/33** | **33/33** |
| 翻转过程中出现 pre≠flip（analysis 步） | 2 处 | 7 处 |
| 上述错位进入 final（保存步） | **0** | **0** |

**过滤情况（720–800，3 次拒绝）**

| task | 拒绝原因 | 结果 |
|------|----------|------|
| `task_31e873d575a1` | R2 attack step10：`Verification_Expert` → `speaker_selection_agent` | **未进 final** |
| `task_346d5fa74120` | R2 attack step8：不可注入框架角色 `speaker_selection_agent` | **未进 final** |
| `task_7e64e5ffe0ba` | R1 attack：flip log 缺 step7 | R1 attack **被拒**；**R2 diagnose step3 对齐后写入 final** |

**过滤情况（800–900，6 次拒绝）**

| task | 拒绝原因 | 结果 |
|------|----------|------|
| `task_454cd3f9f136` | R1 attack step6：Expert → `speaker_selection_agent` | R1 被拒；**R2 diagnose 对齐后写入** |
| `task_7871decc2577` | R1 attack step5：`Algebra_Expert` → `CaptainAgent` | R1 被拒；**R2 diagnose 写入**（见泄漏/框架问题） |
| `task_d2cb3d4a0425` | R1 attack step5：`Math_Expert` → `CaptainAgent` | 同上 |
| `task_3779986e53ed` | R2 attack step8：Expert → `speaker_selection_agent` | R2 被拒；**R3 diagnose 对齐后写入** |
| `task_b41a38370c21` | R2 diagnose step8：Expert → `speaker_selection_agent` | R2 被拒；**R3 attack 对齐后写入** |
| `task_dd6da388775c` | R3 attack step5：`Expert_summoner` → `Checker_Expert` | **完全未进 final** |

说明：同一 task 可在**不同 round** 先因错位被拒、后在另一路径对齐后入库——这是多轮 pipeline 设计，**不是筛选失效**。筛选保证的是：**最终写入 final 的那些 step，pre/flip agent 一致**。

典型错位模式（pipeline 内仍会发生，被筛掉）：Expert → `speaker_selection_agent` / `CaptainAgent` / `Expert_summoner` → 其他框架角色。

### 2. 泄漏（meta / 注入措辞）

扫描 final 翻转轮 history 中的 `injection info`、`ORIGINAL_TASK`、`ATTACKED_TASK`、`prompt injection` 等 meta 标记。

| 批次 | 泄漏样本 | 表现 | 建议 |
|------|----------|------|------|
| 720–800 | **1/39** `task_b67b49225933` | R2 step15 写出 `From the injection info, when ...` | **剔除** |
| 800–900 | **1/40** `task_e9f8b6299fe0` | step3 写出 `(per INJECTION INFO, use 4y^9 instead of 12y^9)` | **剔除** |
| 其余 | 38 + 39 条 | 未发现明显 meta 措辞 | 可保留 |

攻击语义落地（注入步 content 变化且与 `attacked_content` 方向一致 + eval 成功→失败）：720–800 **32/33**；800–900 **28/33**（另有 5 条弱匹配：注入步几乎只剩 `\boxed{}` / `TERMINATE`）。

### 3. 框架角色标成数学根因

| 批次 | 情况 |
|------|------|
| 720–800 | final 中 **0** 条 `mistake_agent` 为 framework |
| 800–900 | **2** 条 Diagnose：`task_7871decc2577`、`task_d2cb3d4a0425`，`mistake_agent=CaptainAgent` 且 `fault_code=f1_4_...`（agent 对齐但框架被标成数学根因）→ **建议剔除** |

Attack 路径的 framework 拒收（如 `task_346d5fa74120`）筛选正常工作；Diagnose 路径 `reject_framework_agents=False`，故 CaptainAgent 可入库。

### 4. 其他质量项

**Round1 专家组发言为空**

- 720–800：`task_15bb7b6c6c37` 全程无 Expert（`Expert_summoner` 500），**建议剔除**。
- 800–900：Round1 **无** spoke_experts 为空。

**整批是否「严格通过」**

- 720–800：剔除泄漏 1 条 + 可选复核弱匹配 1 条（`task_cfbf1f478c38`）后，约 **37 条**可用。
- 800–900：必剔 3 条（1 泄漏 + 2 框架误标）+ 建议复核 5 条弱匹配 + 1 条（`task_cfbf1f478c38` 同类），其余约 **32 条**较稳。

### 5. final_results 字段来源（交接备忘）

| 类型 | 主体轨迹（history / model_prediction 等） | mistake_agent |
|------|---------------------------------------------|---------------|
| **Attack** | **翻转轮** `round_current/log.json` | **上一轮** `pre_round_log` |
| **Diagnose** | **翻转轮的上一轮**（失败轮）`log.json` | 同失败轮 log |

例：`task_2fec284f000c` 为 **Attack**（非 Diagnose），R3 翻转；final 主体 = R3 log（`model_prediction` 含 `\boxed{0}`），`mistake_agent` = R2 history。

### 6. 并发（补充确认）

- 720–800 与 800–900 均为**单进程串行**（`concurrency=1`）；800–900 与 720–800 **并行**靠多终端 + 不同 output/样本区间，符合 8/11 结论。


---

## 交接速览（持续更新）

| 项 | 现状 |
|----|------|
| 当前分支 | `fix/post-owl-merge` |
| 主实验线 | Captain + math attack/diagnose 数据采集 |
| 已知风险 | Captain replay 只重放 content；动态 Expert 可能导致 step-agent 错位（已用 flip 落库筛选兜底） |
| 8/7 后数据 | 曾出现 `spoke_experts` 空；core 已修，相关批次建议按约定重跑 |
| 8/12 两批核验 | [720:800] 39 final / [800:900] 40 final；筛选生效（final 归因步 agent 100% 对齐）；泄漏各 1 条；800–900 另有 2 条 Diagnose 框架误标 |
| 建议剔除 | 泄漏：`task_b67b49225933`、`task_e9f8b6299fe0`；框架误标：`task_7871decc2577`、`task_d2cb3d4a0425` |
| 并发建议 | 勿依赖进程内高并发；用多进程分片跑 |
| 未提交改动 | 较多，交接前建议梳理并选择性 commit |
