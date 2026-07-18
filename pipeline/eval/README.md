# Unified Evaluation Design

`pipeline/eval/` 是统一的 round-level 评估入口，目标是把原来分散在不同模块里的数据集分流和 backend 分流收敛到一套接口里。

## 设计目标
- 让 `main.py` 只负责编排，不再知道具体评估细节。
- 按评估策略组织代码，而不是在调用侧写大量 `data_source` / `backend` 分支。
- 保持原有产物契约不变：
  - `eval_<data_source>.json`
  - `eval_msg_<data_source>.json`
  - `dict[str, bool]` 形式的状态映射

## 入口
统一入口在 `core.py`：

```python
async def evaluate_round(
    eval_path: Path,
    data_source: str,
    *,
    backend: str | None = None,
    semaphore: asyncio.Semaphore | None = None,
    skip_existing: bool = True,
) -> tuple[dict[str, bool], dict[str, str]]:
```

它负责：
- 读取 round 目录下各任务的 `log.json`
- 根据 `backend` 和 `data_source` 选择 evaluator
- 聚合每个任务的评估结果
- 写回 round 级别的 eval 结果文件

## 模块划分
- `core.py`：统一入口和结果加载
- `registry.py`：策略分发
- `io.py`：任务日志读取、eval 文件读写
- `types.py`：统一结果结构
- `scorers/`：具体评估实现

## 当前策略
目前的 evaluator 分成四类：

- `sandbox`
  - 用于显式标记为代码执行评估的数据集
  - 当前 `kodcode` 走这个策略
- `rule_based`
  - 用于 `gaia`、`hotpotqa`
  - 走规则化字符串 / 数字匹配
- `llm_judge`
  - 默认策略
  - 用于 `browsecomp`、`assistantbench`
  - 使用 judge prompt 判定答案是否正确
- `universal`
  - 专供 `MagenticOne`
  - 若任务有 `test`，走 sandbox；否则走 semantic LLM eval

## 数据流
整体流程可以概括为：

1. `main.py` 调用 `evaluate_round(...)`
2. `io.py` 读取 `{round_dir}/*/log.json`
3. `registry.py` 解析应使用的 evaluator
4. `scorers/*` 对每个任务返回 `EvalOutcome`
5. `types.py` 聚合成 round 级别的 `status` 和 `messages`
6. `io.py` 写入 `eval_<data_source>.json` 和 `eval_msg_<data_source>.json`

## 结果结构
单任务结果使用 `EvalOutcome`：

```python
EvalOutcome(task_id: str, passed: bool, message: str = "")
```

round 级别结果使用 `EvalBatchResult`：

```python
EvalBatchResult(
    status: dict[str, bool],
    messages: dict[str, str],
)
```

其中 `message` 主要给 sandbox 类 evaluator 提供执行输出，其他 evaluator 可以返回空字符串，但接口保持统一。

## 扩展方式
如果后续要新增数据集或评估方式，推荐按下面做：

1. 在 `scorers/` 下新增 evaluator
2. 让它返回统一的 `EvalOutcome`
3. 在 `registry.py` 里注册新的策略映射
4. 保持 `log.json` 输入字段和 eval 文件输出格式不变

这样可以在不改 `main.py` 编排逻辑的前提下扩展新的评估能力。
