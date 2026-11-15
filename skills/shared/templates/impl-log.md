# Impl Log：<issue-id> <title>

> 体积预算：正文 ≤ `artifact-budget.impl-log-max-lines` 行，只写结论、表格与 raw 文件指针；过程日志、完整输出放 `raw/`，中间轮只留最终轮。


| 项 | 内容 |
|---|---|
| feature-slug | `<feature-slug>` |
| issue | `<issue-id>` |
| 分支 | `feat/<feature-slug>-<issue-id>` |
| ticket context | `impl/<issue-id>/ticket-context.json` |
| 冻结 base / target | `<完整 base commit>` / `<target branch>@<完整 target commit>` |
| worktree | `.worktrees/<issue-id>/` |
| 状态 | `in-progress` / `mr-submitted` / `needs-human` |

## 做了什么

按提交顺序列出：commit hash + 一句话。TDD 新增的测试文件在此登记，并逐条附**见红证据**：测试名 + 首次单独运行的失败输出要点（须为断言失败且指向预期行为差异；error 不算见红；第一次跑就通过的测试不算数，重写后再登记）。

## 放弃了什么

尝试过但放弃的路径，及放弃原因（沉淀给下一次）。

## 假设了什么

实现过程中做出的、tech-spec 未覆盖的假设。会被 `/ai-review` 的 Spec 轴核对。

## 需求覆盖矩阵

票 DoD 每条 / 「要做什么」每个行为点 → 实现位置（文件:符号）+ 覆盖测试 + evidence 行。主会话合并前的需求符合性核对逐条核此表；某行为点没有对应实现即如实写「无对应实现」（自认未实现，主会话核对时照样会判出，隐瞒只会多一轮打回）。需求行为主干实现（非边角分支）在「关键」列标 ★，主会话至少抽查这些行。

| 需求点（DoD 条目 / 行为点） | 实现位置（文件:符号） | 覆盖测试 | evidence 行 | 关键 |
|---|---|---|---|---|

## 红线接触记录

`verify-policy.deny` 命中即停止，不能登记放行；此处记录事实与 human-decision。`test-change` 逐条登记：测试文件 → 票 DoD 条目 / tech-spec 节锚点 + 冲突说明 + 改动范围声明（仅限相关断言 / 夹具）+ runner 见红 / 独立评审闭合。`generated-refresh` 逐条登记稳定 ID、生成命令与临时工作区 tree 比对结果；没有这些证据时不得继续。

## 范围变更记录

`declared-scope` 在 ticket context 中冻结，所有 changed paths 都参与对账。越界不能靠登记继续：退回 `/issue-split` 重新确认并生成新 context；生产文件数 / LOC 超上限同样必须再拆或创建 human-decision。

| 检查时点 | 越界文件 | 处理（退回重新确认 / 再拆 / human-decision） | 新 context（若有） |
| --- | --- | --- | --- |

（无越界写「无越界」。此处只作审计说明，不是放行依据；process_check 按冻结 scope 和完整 diff 判定。）

## 自修复轮次记录

| 轮 | 触发原因（构建失败 / 评审 BLOCKER） | 处理 | 结果 |
|---|---|---|---|

## 遗留

Medium / Low 级评审意见（不阻塞合并）、待人裁决事项。
