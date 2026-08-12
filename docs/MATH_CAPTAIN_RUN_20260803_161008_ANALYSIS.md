# Math Captain 实验讨论记录

## 运行批次标识（便于后期定位）

| 字段 | 值 |
|------|-----|
| **RUN_TAG** | `math_captain_msg_20260803_161008` |
| **运行日期** | 2026-08-03（用户称「昨晚」运行的批量实验） |
| **数据集** | `math` |
| **Backend** | `Captain` |
| **样本数** | 50（`max_samples 100`，实际完成 50 条） |
| **最大轮次** | 3（`max_rounds 3`，含 Round 0 baseline + 最多 3 轮 attack/diagnose 分支） |
| **Output 目录** | `output/math_captain_msg_20260803_161008/` |
| **Workspace 目录** | `workspace/math_captain_msg_20260803_161008/` |
| **主日志** | `logs/math_captain_msg_20260803_161008.log` |
| **Eval 数据路径** | `output/math_captain_msg_20260803_161008/math/round_{0,1,2,3}/` |
| **Final results** | `output/math_captain_msg_20260803_161008/final_results/`（共 5 条） |

**对比参考（旧 run，非本次讨论主体）：** `math_captain_msg_20260803_112617`

**讨论时间：** 2026-08-04（基于上述 run 的事后分析）

---

## 问题 1：`task_ef0b22c43c29` 的 attack 与 `\boxed{{45}}`

### 用户问题

> `@output/math_captain_msg_20260803_161008/final_results/task_ef0b22c43c29.json` 那这个案例呢，attackcontent的内容是导致其翻转成功的原因吗，boxed{{45}}为什么会套上两个花括号？这是LLM的随机性导致的吗？

### 回答摘要

**案例类型：** attack 翻转（Round 0 pass → Round 1 fail），非 diagnose。

| 轮次 | eval | prediction 中的 boxed | extract 结果 | 数学答案 |
|------|------|----------------------|--------------|----------|
| Round 0 | **true** | `\(\boxed{45}\)` | `45` | 正确 |
| Round 1 | **false** | `\(\boxed{{45}}\)` | `{45}` | 仍正确（45 mph） |

**attack 的 `attacked_content` 是不是翻转原因？**

- **部分相关，但不是 eval 失败的直接机制。**
- 注入点在 step 7（`Speed_Calculation_Expert`），把 `print(average_speed)` 改成 `print(f'Average Speed: {average_speed} mph')`。
- 这改变了 replay 轨迹：speaker 选了 Accuracy_Checker 而非 terminal → terminal 空输出 → 二次 seek → 专家写出 `\(\boxed{{45}}\)`。
- 但 attack 标注的 fault 是 `f1_2_response_format_error`（终端输出格式），**math_boxed eval 不看 terminal 字符串**，只看 `model_prediction` 里最后一个 `\boxed{}`。
- **eval 直接死因：** `\boxed{{45}}` → `{45}` ≠ GT 的 `45`（格式误杀，非答案算错）。

**`\boxed{{45}}` 为何双花括号？**

- **不主要是 LLM 随机换答案**，而是：
  1. Prompt 模板 `\boxed{{value}}` 被 LLM 字面复制；
  2. Round 0 写对 `\boxed{45}`，Round 1 二次验证后写错 `\boxed{{45}}`；
  3. `seek_limit.py` 简单 regex 从 `\boxed{{45}}` 抽 `{45}` 再拼成 `\boxed{{45}}`，固化问题。

---

## 问题 2：三个格式 flip 案例总结 + 算攻击成功还是 eval 漏洞？

### 用户问题

> 请你总结这三个案例（`31de7675719a`、`ef0b22c43c29`、`ffc4f0cbbd36`），描述它们翻转的根本原因……attack/diagnose 都有成功被注入并影响推理过程，只不过最终的答案因为格式问题被判断为翻转。这种情况可以认为是攻击成功导致的问题吗，还是说我们的 eval 有漏洞需要修复？

### 回答摘要

### 三案例总览

| 任务 | 类型 | 轮次变化 | 注入是否扰动推理 | 中间错误 | 最终数学答案 | eval 直接死因 |
|------|------|----------|------------------|----------|--------------|---------------|
| `31de7675719a` | **diagnose 翻转** | R0 fail → R1 pass | replay 改写法 | 无（一直算对 -30） | 正确 `-30` | `\boxed{{-30}}` → `{-30}` |
| `ef0b22c43c29` | **attack 翻转** | R0 pass → R1 fail | attack 改 terminal → 二次 seek | 对话乱序 | 正确 `45` | `\boxed{{45}}` → `{45}` |
| `ffc4f0cbbd36` | **attack 翻转** | R0/R1 pass → R2 fail | attack 改 `y=5`→`y=6` | 中间 `\boxed{14.67}` 后被纠正 | 正确 `11`（写成 `11.0`） | `11.0` ≠ `11` |

**共同模式：** 推理过程被 inject 扰动，但最终数学值是对的；flip 来自 `math_boxed` 严格字符串比较。

### 能否算「攻击成功」？

| 维度 | 结论 |
|------|------|
| **过程 / 归因** | inject 成功，对 failure attribution 仍有意义 |
| **端到端 eval 翻转** | 不应等同于「攻击改错最终答案」；当前多为 **eval 格式假 flip** |

### eval 是否有漏洞？

**有，建议修。** 在 `extract_boxed_answer` 后加 `normalize`：

1. 剥多余外层 `{}`：`{45}`→`45`，`{-30}`→`-30`
2. 整数 float 归一化：`11.0`→`11`
3. 不做 LaTeX 语义等价，不用 llm_judge

---

## 问题 3：eval normalize 实现思路

### 用户问题

> 先把 eval 总是把对误判的问题解决了……在 extract_boxed_answer 之后加一层 normalize 的方法能解决类似这三个样本的问题吗，如果让你改你的思路是什么，需要改动哪些文件？

### 回答摘要

**能解决三个样本。**

| 样本 | extract 后 pred | extract 后 GT | normalize 后 |
|------|-----------------|---------------|--------------|
| `31de7675719a` | `{-30}` | `-30` | 均为 `-30` |
| `ef0b22c43c29` | `{45}` | `45` | 均为 `45` |
| `ffc4f0cbbd36` | `11.0` | `11` | 均为 `11` |

**改动文件：**

- **必改：** `pipeline/math/eval.py`（加 `normalize_boxed_answer`，`math_eval` 调用）
- **建议改：** `tests/test_unified_eval.py`
- **不必改：** `math_boxed.py`、`main.py`、`text_answer.py`

**刻意不做：** 不改 extract 逻辑、不做分数/LaTeX 语义等价、不改 seek_limit。

---

## 问题 4：eval normalize 代码实现

### 用户请求

> 请你按照你的思路对 eval.py + 测试做修改……

### 实现结果

已在以下文件完成修改：

- `pipeline/math/eval.py`：新增 `normalize_boxed_answer()`，`math_eval()` 两侧统一 normalize
- `tests/test_unified_eval.py`：新增测试 + 修正「取最后一个 boxed」的断言

**预期：** 上述三个格式 flip 消失；真算错（如 `\boxed{14.67}` vs `\boxed{11}`）仍 fail。已有 `eval_math.json` 需重跑 eval 才会更新。

---

## 问题 5：`task_6f2142a018f3` 与 `task_82d6ca8388d5` 是否为完美 attack flip？

### 用户问题

> 这两个案例是不是完美的因 attack content 生效而产生的翻转？是两个很完美的案例？

### 回答摘要

**比前面三个格式 flip 强得多**，属于语义层面真正算错并最终提交错误 `\boxed{}` 的 attack 翻转。

| 任务 | 轮次 | GT | 翻转后 | 语义真错 |
|------|------|-----|--------|----------|
| `6f2142a018f3` | R1 flip | `\boxed{192}` | `\boxed{32}` | 是 |
| `82d6ca8388d5` | R3 flip | `\boxed{98}` | `\boxed{96}` | 是 |

### `task_6f2142a018f3`

- R0 正确 `f(192)=64` → `\boxed{192}`；R1 错误认定 `64=f(32)` → `\boxed{32}`。
- **flip 真实有效**，但 **`attack_analysis.attacked_content` 与实际 step 3 不一致**（分析写 meta doubt，实际是完整错误推导）。
- **结论：** 好的语义 flip 正例；归因 fidelity 稍弱，不算「完美」。

### `task_82d6ca8388d5`

- R0–R2 均 `\boxed{98}`；R3 注入 96 → 中间纠正到 98（Python 输出 98）→ 二次 seek + 再注入 → Captain budget 强制 `\boxed{96}`。
- **最接近教科书级 attack flip**；但需多轮、中间曾纠正、flip 含 re-seek/budget 机制。
- **结论：** 本 run 里最优秀的 attack 正例；若要求「单次注入、无纠偏、归因逐字一致」则仍有一点杂音。

### 与格式 flip 对比

| 维度 | 格式三案例 | 这两个案例 |
|------|-----------|------------|
| 最终数学 | 对 | **错** |
| flip 主因 | eval 字符串 | **错误推理 + 错误 boxed** |
| 适合作 attack 正例 | 不太适合 | **适合** |

---

## 问题 6：50 条样本低 flip 率——设计 bug 还是 Captain 事实？

### 用户问题

> 对于昨晚运行的 50 条样本三轮攻击，严格意义上只有两个样本是成功因攻击效果生效而造成翻转的（不算三条格式 eval 错误）。反转率很低。是设计 bug 还是 Captain 在 math 上的事实？请从 `output/math_captain_msg_20260803_161008/math` 找几个 final_results 里没有的案例分析失败原因。若可确认不是设计不合理，就不再纠结直接大批量跑。

### 回答摘要

### 量化（`math_captain_msg_20260803_161008`）

| 指标 | 数值 |
|------|------|
| 总样本 | 50 |
| Round 0 pass / fail | 39 / 11 |
| 进入 attack（有 `attack_analysis`） | 37 |
| 累计 attack 轮次 | **105**（R1:36 + R2:35 + R3:34） |
| `final_results` 翻转 | **5** |
| **语义真 flip** | **2**（`6f2142a018f3`、`82d6ca8388d5`） |
| 格式假 flip | 3 |
| 撑满 3 轮 attack 仍 pass | **35** |

**语义 attack 成功率：**

- 按任务：2 / 39 ≈ **5.1%**
- 按 attack 次数：2 / 105 ≈ **1.9%**

### 非 flip 案例失败原因（33 条有 attack 但未 flip，抽样 6 条）

| 任务 | 失败类别 | 简述 |
|------|----------|------|
| `task_047796360188` | **(b) 注入后被纠正** | step 3 注入错误方程 → `\boxed{1}`；step 5 重算 → `\boxed{0}` |
| `task_10375bdb9cda` | **(a) 注入未落地** | attack 要求 `\boxed{45}`，step 3 仍 `\boxed{58}` |
| `task_4b504221de9b` | **(a) 注入未落地** | attack 要求 27+1=28，实际仍 27+2=29 → `\boxed{10}` |
| `task_289e5145d6e1` | **(b) 注入后被纠正** | step 5 注入「非整数」→ `\boxed{0}`；step 7 验证 → `\boxed{1}` |
| `task_25406db89cd0` | **(d) 弱 meta 无效** | 仅 confusion 类 meta，无实质错误数学 |
| `task_1f79334837be` | **(c) 注入但答案相同** | 中间式子改错，Vieta 仍得 sum=1 |

**33 条全量粗分：** 多数为「攻击后最终 boxed 与 R0 相同」；少数 weak meta；**几乎没有「attack 完全没跑」**。

### 设计 bug vs Captain/math 事实

**不太像 pipeline 设计 bug：**

1. Attack 流程在跑（105 次）。
2. 2 条语义 flip 证明链路可通。
3. 低 flip 主因：**多专家 + Checker/Python 复核**，math 可验证，错误中间步骤易被纠正。
4. Replay 非确定性：`attacked_content` 与 history 不完全一致是机制特性。

**曾影响统计、已在修：** 3 条格式 flip（eval normalize）。

**设计选择压低 flip 率（不一定是 bug）：**

- 单 step 注入 + 后续 LLM 自由生成 → 易被 downstream 覆盖
- flip 定义 = eval `\boxed{}` 变化 → 纠正型 MAS 天然压低

### 决策建议

> **可以不再纠结，放心大批量跑。**

- 低 flip 率主要反映 **Captain 在 math 上偏 robust + 单点注入难穿透多轮验证**，不是 pipeline 整体不合理。
- 去掉格式问题后，**2/39 ≈ 5% 语义 flip 符合预期**。
- 统计时用 **normalize 后语义 flip** 作主指标；flip 率是「端到端漏洞」的下界。
- **35 条撑满 3 轮仍 pass** → 不是少跑几轮就能多 flip。

### 一句话结论

> 这次低 flip 率**不是** attack 没跑或 eval 全错；去掉格式问题后，**2/39 的语义 flip 更符合 Captain math 的真实表现**——攻击常扰动过程，但 Verifier / Python / 多轮共识消化了绝大多数 attempt。

---

## 附录：`final_results` 五条一览（本 run）

| task_id | 翻转类型 | 语义 flip？ | 备注 |
|---------|----------|------------|------|
| `task_31de7675719a` | diagnose | 否（格式） | `\boxed{{-30}}` vs `-30` |
| `task_ef0b22c43c29` | attack | 否（格式） | `\boxed{{45}}` vs `45` |
| `task_ffc4f0cbbd36` | attack | 否（格式） | 中间 14.67 被纠正，`\boxed{11.0}` vs `11` |
| `task_6f2142a018f3` | attack | **是** | 192 → 32 |
| `task_82d6ca8388d5` | attack | **是** | 98 → 96 |

---

## 附录：相关代码改动（eval normalize）

- **文件：** `pipeline/math/eval.py`、`tests/test_unified_eval.py`
- **函数：** `normalize_boxed_answer()` — 剥外层 `{}` + 整数 float 归一化
- **状态：** 对话期间已实现；大批量 run 前建议确认已合入并重跑 eval 统计

---

*本文档由 2026-08-04 对话整理，对应运行批次 `math_captain_msg_20260803_161008`。*
