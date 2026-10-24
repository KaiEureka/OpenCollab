---
{
  "id": "token-cache-cost",
  "title": "统计 token、缓存命中和费用",
  "category": "observability",
  "summary": "从实际用量记录计算开销，理解推理、缓存和重试对总量的影响。",
  "aliases": [
    "usage",
    "成本",
    "reasoning_tokens",
    "cached_tokens",
    "API_USAGE_LOG",
    "输入输出token"
  ],
  "keywords": [
    "token统计",
    "缓存命中",
    "费用"
  ],
  "questions": [
    "从实际用量记录计算开销，理解推理、缓存和重试对总量的影响？"
  ],
  "related": [],
  "verified_ref": "e7946a42"
}
---

# 统计 token、缓存命中和费用

token 预算控制是否继续执行，用量记录描述已经发生的调用开销。统计费用时应从提供方返回的输入、输出和缓存字段出发，并保留估算与缺失标志。

## 这些计数怎样相加

`total_tokens` 是输入加输出。缓存命中的输入已经包含在输入总量里，推理 token 通常作为输出细分保留。将缓存或推理再加一遍会高估总量。提供方可能只返回部分字段，缺失值需要保留其含义。

缓存命中率可以用累计 `cached_input_tokens` 除以累计 `input_tokens`。对多个任务求平均开销时，还应写明分母包含哪些任务与尝试。

## 开启提供方用量账本

在启动进程前配置写入位置。

```bash
export OPENCOLLAB_API_USAGE_LOG=/absolute/path/to/usage.jsonl
```

该文件按提供方调用追加记录，包含模型、协议、用量、`estimated` 以及可用的 `raw_usage`。账本与会话指标分别保存不同层面的信息，汇总前先确定选用哪一层，避免把一次请求同时从两处累计。

账本每行的 `usage` 保存 `input_tokens`、`output_tokens`、`total_tokens`、`cached_input_tokens`、`cache_creation_tokens`、`reasoning_tokens`、`cost_usd` 和 `pricing`。trace 的对应缓存读取字段名为 `cache_read_tokens`，解析时按各自结构读取。`None` 表示该项信息缺失，`estimated=True` 表示记录包含估算。

命令行批次可以通过 `OPENCOLLAB_RUN_ID` 或 `OPENCOLLAB_USAGE_RUN_ID` 标记账本运行，通过 `OPENCOLLAB_USAGE_LABEL` 追加实验标签。这些字段来自进程环境。并发服务可使用每次 SDK 调用的 `run_id` 和 `RunControl.on_event` 关联业务请求，回调会在原运行的迟到用量结算期间继续接收事件，使用方式见 [查看 Agent 做了什么](trace-and-events.md)。

## 离线汇总一份账本

下面脚本读取指定文件的全部记录，报告记录数、累计 token 和缺失标志。分析某个模型或时间段时，可以先按每行的 `model`、`timestamp`、`run_id` 或 `label` 筛选。

```python
import json
from pathlib import Path

usage_path = Path("artifacts/usage.jsonl")
with usage_path.open(encoding="utf-8") as stream:
    records = [json.loads(line) for line in stream if line.strip()]
usages = [record["usage"] for record in records]
print("records", len(usages))
print("input_tokens", sum(usage["input_tokens"] for usage in usages))
print("output_tokens", sum(usage["output_tokens"] for usage in usages))
print("total_tokens", sum(usage["total_tokens"] for usage in usages))
print("estimated_records", sum(usage["estimated"] for usage in usages))
print("missing_cache_records", sum(usage["cached_input_tokens"] is None for usage in usages))
print("missing_cost_records", sum(usage["cost_usd"] is None for usage in usages))
```

检查每条记录的 `pricing` 后，再汇总具有完整价格和用量的费用。通用价格默认值为零，零费用需要结合实际配置解释。缓存读取或写入字段缺失时，费用可能为 `None`。保留缺失记录数，便于判断汇总覆盖了多少调用。

## 重试与摘要

内部传输重试可能合并为外层调用的累计用量。上下文压力关注最终请求的输入大小，累计花费关注全部已返回尝试。摘要也会调用模型，因此需要计入真实开销。

取消发生后，后台请求可能才返回最后用量。应结合 late 与清理完成记录确定最终累计值。未返回实际用量的调用，不能由“未看到数字”推导为免费。

## 配置自己的价格

账本费用估算使用 `OPENCOLLAB_INPUT_USD_PER_MTOK`、`OPENCOLLAB_CACHED_INPUT_USD_PER_MTOK`、`OPENCOLLAB_CACHE_CREATION_USD_PER_MTOK` 与 `OPENCOLLAB_OUTPUT_USD_PER_MTOK`。单价单位为每百万 token 美元，填写你实际使用服务的价格。特定模型分支可能有单独的价格配置，应看账本中的 `pricing`。

如果服务将输入分为普通、缓存读取与缓存写入，分别乘以各自价格，再加输出费用。准确性取决于字段是否齐全与提供方的计费语义。估算值与实际账单要按相同时间范围核对。

## 查找异常增长

先区分请求次数增加、单次输入变长和输出变长，再看缓存比例、重试和摘要次数。长密文的字符数属于传输表示，不能直接作为已计费 token。相关机制见上下文压缩与深度思考指南。

## 源码与进一步阅读

[configs/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/configs/README.md) · [opencollab/adapters/llm/usage_ledger.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/llm/usage_ledger.py) · [opencollab/adapters/llm/types.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/llm/types.py) · [docs/evaluation-runtime.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/evaluation-runtime.md)
