# Captain「429」问题排查复盘

> 记录时间：2026-07-24  
> 任务：`math` + `Captain` backend，单题 `task_54a72d31d0d3`  
> 结论：**HTTP 429 是表象；根因是 OpenAI 消息链格式错误（400 invalid_request_error），被 qingyuntop 代理误标为 429。**

---

## 一、问题现象（起点）

| 项目 | 描述 |
|------|------|
| 任务 | Captain 跑 math 单题，`run_mode=all` |
| 表现 | Round 0 / Round 1 均在 **Captain 第 2 次** `POST /chat/completions` 失败 |
| HTTP 状态 | **429 Too Many Requests** |
| 连带后果 | 无 `solution.txt`，`history` 停在第 1 步 `seek_experts_help`，后续 diagnose 在残缺输入上继续 |

### 固定失败模式

| 次序 | 调用方 | 典型结果 |
|------|--------|---------|
| 第 1 次 | Captain（同一次 `initiate_chat` 内） | 200 |
| 第 2 次 | Captain（建 expert 组 / AutoBuild） | 「429」 |
| 第 3 次及以后 | Judge / Diagnose（换阶段或新会话第 1 枪） | 200 |

Captain 第 1 次 LLM 返回 `tool_calls: seek_experts_help`；第 2 次 LLM 在工具执行链上失败，任务中断。

---

## 二、排查过程（按 Step）

| Step | 假设 / 动作 | 代码 / 配置改动 | 运行 / 日志 | 结果 | 结论 |
|------|------------|----------------|------------|------|------|
| **0** | 初始分析 | 无（读早期日志） | `153221` / `161249` | 第 1 次 200 → ~1s 后第 2 次 429；SDK 默认重试 2 次 | 疑似 **短间隔 burst + SDK 重试**；Expert_summoner **重复注入**存在但非主因 |
| **1** | 关 SDK 重试 + middleware sleep | `CAPTAIN_MAX_RETRIES=0`；`observe.py` 里 `time.sleep` | `172204`(1.5s)、`172606`(2.5s) | Captain 仍 429；**无** SDK `Retrying...` 日志 | SDK 重试已关；**sleep 未稳定拦住第 2 次请求** |
| **2A+2B** | 全局 Rate Limiter + 诊断日志 | 新增 `utils/llm_rate_limit.py`、`utils/llm_client_hooks.py`；patch `OpenAIWrapper` / `Completions.create` | `174334` | Judge/Diagnose 有 `[rate-limit]`；Captain 第 2 次 **无**对应日志，仍 429 | 部分路径 **绕过** SDK 层 hook |
| **3** | httpx 层兜底 | 改为只 patch `httpx.Client.send`；仍用 **start→start** 计时 | `181329`(4s)、`181556`(4s) | 每次 POST 前都有 `[rate-limit]`；第 2 次仍 429 | **hook 全覆盖**；间隔 4s **仍不够** |
| **3b** | 修正计时锚点 | limiter 改为 **finish→start** | `182213`(4s)、`182748`(15s) | 间隔精确 4s/15s，第 2 次 Captain **仍 429** | **排除「间隔太短」**；不是简单 RPM |
| **4** | 分离 API Key | `JUDGE_API_KEY` ≠ `CAPTAIN_API_KEY` | `094259`（15s 间隔） | Judge 200；Captain 第 2 次仍 429 | **排除 Judge 与 Captain 抢 key** |
| **5** | 429 响应日志 + 单次重试 | 429 时 log body；`Retry-After` 或 30s 后重试 1 次 | `095212`（`LLM_MIN_INTERVAL_SEC=0`） | 日志 + 30s 重试 **均生效**；重试后 body **相同** | **突破：根因不是限流** |
| **5 结论** | 读 429 body | — | `095212` L13/L16/L69/L72 | body 为 `invalid_request_error`：tool_call 后缺 tool 回复；代理用 **429 包装 400** | **真正根因：消息链格式错误** |

---

## 三、关键日志对照表

| 日志文件 | 主要配置 | Captain 第 1 次 | Captain 第 2 次 | 特别说明 |
|---------|---------|----------------|----------------|---------|
| `153221` / `161249` | 改前 | 200 | 429 + SDK 重试 2 次 | 有 Expert_summoner 重复注入 |
| `172204` | sleep 1.5s + max_retries=0 | 200 | 429 | 无 SDK 重试 |
| `172606` | sleep 2.5s | 200 | 429 | 同上 |
| `174334` | SDK 层 limiter 2.5s | 200 | 429 | 第 2 次几乎无 limiter 日志 |
| `181329` / `181556` | httpx + 4s start→start | 200 | 429 | hook 全覆盖 |
| `182213` / `182748` | httpx + finish→start 4s/15s | 200 | 429 | **15s 仍 429** |
| `094259` | 15s + Judge 独立 key | 200 | 429 | 排除 key 共享 |
| **`095212`** | interval=0 + 429 log/retry | 200 | 「429」→ 实为 **400** | **根因定位** |
| **`105412`** | `CAPTAIN_NESTED_MAX_TURNS=3` | 200（~26 次） | 200（reflection 摘要） | **P0 修复验证通过** |

日志目录：`logs/math_captain_*.log`

---

## 四、被排除的假设

| 假设 | 排除依据 |
|------|---------|
| Expert_summoner 重复注入 | 去重后 429 模式不变 |
| SDK 重试导致 burst | 关重试后仍 429（只是少了几条重试请求） |
| middleware sleep 未执行 | httpx hook 后每次 POST 都有日志 |
| 全局间隔太短（4s/15s） | finish→start 精确等待后仍失败 |
| Captain / Judge 共用 key | Judge 换 key 后 Captain 第 2 次仍失败 |
| 代理 RPM / TPM 限流 | body 为 `invalid_request_error`，无 `Retry-After`；30s 重试无效 |
| 必须保持 `LLM_MIN_INTERVAL_SEC=15` | 15s 无效；根因非间隔 |

---

## 五、已确认根因（当前）

### 5.1 调用链

```
Captain 第 1 次 LLM
  → assistant 返回 tool_calls (seek_experts_help)  ✓ HTTP 200

Captain 第 2 次 LLM
  → messages 里 tool_call 之后缺少 role=tool 的回复
  → OpenAI 规范下应为 400 invalid_request_error
  → qingyuntop 返回 HTTP 429（误导排查方向）
```

### 5.2 典型错误 body（`095212` 日志）

```json
{
  "error": {
    "message": "An assistant message with 'tool_calls' must be followed by tool messages responding to each 'tool_call_id'. The following tool_call_ids did not have response messages: call_xxx",
    "type": "invalid_request_error",
    "param": "messages.[2].role",
    "code": null
  }
}
```

要点：

- `type` 是 **`invalid_request_error`**，不是 `rate_limit_exceeded`。
- 无 `Retry-After` 响应头。
- 30s 后重试发送 **相同错误 messages**，重试仍「429」——符合格式错误而非限流。

### 5.3 为何 Judge / Diagnose 正常

Judge、Diagnose 使用简单 user 消息，无 `tool_calls` → tool 回复链，故不受影响。

---

## 六、代码 / 配置变更清单

| 文件 | 作用 | 对排查的价值 |
|------|------|-------------|
| `adapter/Captain/core.py` | `CAPTAIN_MAX_RETRIES=0`；**P0：`CAPTAIN_NESTED_MAX_TURNS` 默认 `1`→`3`** | 消除 SDK 重试干扰；**保证 nested chat 执行 `seek_experts_help` 后再做 reflection 摘要** |
| `config/env` | `CAPTAIN_NESTED_MAX_TURNS=3`；`LLM_MIN_INTERVAL_SEC`、`LLM_429_*`、独立 Judge key 等 | P0 修复显式配置 + 实验配置 |
| `adapter/Captain/middlewares/observe.py` | 曾加 sleep，**已移除** | Step 1；后证明不够 |
| `utils/llm_rate_limit.py` | 全局 limiter（finish→start） | Step 2~3 |
| `utils/llm_client_hooks.py` | httpx hook + 429 body 日志 + 单次重试 | **Step 5，最终定位根因** |
| `adapter/runtime_bootstrap.py` | 启动时 `install_llm_rate_limit_hooks()` | 统一安装 hook |
| `pipeline/eval/scorers/llm_judge.py` | `JUDGE_MAX_RETRIES=0` | 统一关 SDK 重试 |

### 当前推荐 env（排查阶段）

```env
CAPTAIN_NESTED_MAX_TURNS=3      # P0 修复：nested chat 至少 2 轮才能执行 seek_experts_help
CAPTAIN_MAX_RETRIES=0
LLM_MIN_INTERVAL_SEC=0          # 根因非间隔，不必 15s
LLM_429_MAX_RETRIES=1           # 真限流时兜底；格式错误时会白等 30s
LLM_429_RETRY_SEC=30
JUDGE_API_KEY=...               # 可与 CAPTAIN_API_KEY 分离
```

---

## 七、排查路径示意

```
429 表象
  ├─ Step 1: 关重试 + middleware sleep     → 仍 429
  ├─ Step 2~3: SDK / httpx limiter         → 仍 429（4s → 15s 均无效）
  ├─ Step 4: 分离 JUDGE_API_KEY             → 仍 429
  └─ Step 5: 429 body 日志 + 30s 重试       → invalid_request_error
                                               → 根因：tool_call 消息链不完整
                                               → 代理误标 429
```

---

## 八、Round 0 为何有两次 Captain 调用

不是 pipeline 强制两次，而是 **Captain 架构**：

1. **第 1 次**：CaptainAgent 规划 → 返回 `seek_experts_help` tool_call  
2. **第 2 次**：执行工具 → AutoBuild 建 expert 组 → 需再打 API  

Round 1 注入回放里「第 1 次 200」是 **新一次 Captain 运行的第一枪**，不是 Round 0 第二次的重试。

---

## 九、待办（下一步）

| 优先级 | 事项 |
|--------|------|
| ~~**P0**~~ | ~~查 Captain/AutoGen 第 2 次 API 的 `messages` 组装~~ → **已修复**，见 §十一 |
| **P1** | httpx hook：若 body 含 `invalid_request_error`，**不要**当 429 重试（避免白等 30s） |
| **P2** | 可选：向 qingyuntop 反馈「400 错返回 429」 |
| **P3** | 根因修复后：`LLM_MIN_INTERVAL_SEC` 保持 0 或 2~4；429 retry 保留作真限流兜底 |

---

## 十、复盘一句话

用了 **5 轮由表及里的实验**，从「限流、重试、注入、key 共享」逐步收窄；最终在 **429 响应体日志** 中发现：**不是 rate limit，而是 Captain nested chat 在 `CAPTAIN_NESTED_MAX_TURNS=1` 下未执行 `seek_experts_help`，`reflection_with_llm` 摘要把含 `tool_calls` 但缺 `role=tool` 的 history 送 API，触发 400 却被 qingyuntop 误标为 429**。此前「加 sleep / 加间隔」对 **该根因** 无效，但对 **排除假设、验证 hook、看清代理行为** 仍有价值。

---

## 十一、P0 修复与验证（2026-07-24 收尾）

**改动：** 将 nested chat 轮数下限从 1 提到 3——在 `adapter/Captain/core.py` 把 `CAPTAIN_NESTED_MAX_TURNS` 默认值改为 `3`，并在 `config/env` 显式设置 `CAPTAIN_NESTED_MAX_TURNS=3`，使 Expert_summoner 在 `max_turns≥2` 时执行 `seek_experts_help`、写入 `role=tool` 回传后，再触发 `reflection_with_llm` 摘要。

**验证：** `logs/math_captain_20260724_105412.log` — Round 0 全部 LLM 调用 200，无 `invalid_request_error`；history 含 step 1 `tool_call` → step 12 工具回传 → step 16 摘要；生成 `solution.txt`（`a+b=0`），eval 通过；Round 1 attack replay 同样无 429。

**结论：工具调用消息链问题已解决**；后续待办以 P1（429 hook 区分格式错误）及 attack 注入上下文优化为主。
