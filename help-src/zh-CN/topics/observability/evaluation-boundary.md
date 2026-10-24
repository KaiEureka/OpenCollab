---
{
  "id": "evaluation-boundary",
  "title": "用 OCE 评测 OC 的 Agent、Team 和 Workflow",
  "category": "observability",
  "summary": "理解 OC 与 OCE 的分工，找到正式运行和收集评分结果的入口。",
  "aliases": [
    "OpenCollab-Eval",
    "跑题",
    "Terminal-Bench",
    "DeepSWE",
    "SWE-bench",
    "官方评分"
  ],
  "keywords": [
    "评测",
    "OCE",
    "Benchmark"
  ],
  "questions": [
    "理解 OC 与 OCE 的分工，找到正式运行和收集评分结果的入口？"
  ],
  "related": [
    "run-results",
    "native-test-evidence",
    "token-cache-cost"
  ],
  "verified_ref": "e7946a42"
}
---

# 用 OCE 评测 OC 的 Agent、Team 和 Workflow

OC 提供 Agent、Team、Workflow 的执行与协作能力，OpenCollab-Eval 负责 benchmark 数据、批量运行、候选评分和实验分析。准备评测时，先确定要比较的 OC 运行方式，再到 OCE 配置数据集与验证器。

## 一次评测包含什么

通常需要任务输入、初始仓库或镜像、模型与运行配置、生成的候选、原始验证结果以及用量记录。生成完成、验证器启动、测试通过和最终成绩采用是不同阶段，应在结果里分别辨认。

OC 的 `RunResult` 说明运行怎样结束。正式通过与否由选定 benchmark 的验证规则决定。例如运行完成后仍可能有测试失败，设施错误也可能使验证根本没有执行。

## 选择执行方式

可以评测单 Agent、团队文件或工作流。评测器通过 SDK 调用时，需要传递模型、预算、时限、环境与产物位置。自定义 Workflow 应先在普通项目上确认输入输出和交付行为，再进入批量评测。

模型提供方、角色提示、工具集合、执行环境与验证器资源都会影响结果。比较不同协作方案时，应明确这些设置，并保留可对应到每题的记录。

## 代码和工作区怎样交付

不同评测适配器可能采用补丁、最终工作树或完整容器状态。选择方式应遵循任务规则，尤其是存在未提交文件、启动服务或容器内产物的任务。候选通过哪条路径送入验证器，需要从 OCE 的对应适配器核对。

设施问题恢复时，已经保存的候选可以通过相应恢复流程重新评分。是否允许再次生成，则由实验协议决定。保留原尝试、恢复过程和最终结果有助于解释差异。

## 使用哪个文档入口

当前评测命令与数据集设置以 [OpenCollab-Eval](https://github.com/RISE-X-Lab/OpenCollab-Eval#readme) 为入口。OC 的配置运行时文档说明 SDK 的预算、取消、清理与结果接口。项目中历史实验的命令应结合当时的提交阅读，正式运行前确认你使用的两个仓库版本和对应功能。

这篇指南负责建立评测对象与材料关系，具体某个 benchmark 的安装和评分参数由其当前 OCE 指南提供。

## 源码与进一步阅读

[docs/evaluation-runtime.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/evaluation-runtime.md) · [docs/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/README.md) · [scripts/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/scripts/README.md)

[读取运行结果与判断任务是否完成](run-results.md) · [用实际测试验证 Agent 的交付](native-test-evidence.md) · [统计 token、缓存命中和费用](token-cache-cost.md)
