# Impl Log：<issue-id> <title>

> 体积预算：正文 ≤ `artifact-budget.impl-log-max-lines` 行，只写结论、表格与 raw 文件指针；过程日志、完整输出放 `raw/`，中间轮只留最终轮。


| 项 | 内容 |
|---|---|
| feature-slug | `<feature-slug>` |
| issue | `<issue-id>` |
| 分支 | `feat/<feature-slug>-<issue-id>` |
| worktree | `.worktrees/<issue-id>/` |
| 状态 | `in-progress` / `mr-submitted` / `needs-human` |

## 做了什么

按提交顺序列出：commit hash + 一句话。TDD 新增的测试文件在此登记。

## 放弃了什么

尝试过但放弃的路径，及放弃原因（沉淀给下一次）。

## 假设了什么

实现过程中做出的、tech-spec 未覆盖的假设。会被 `/ai-review` 的 Spec 轴核对。

## 红线接触记录

命中 `agents-config.redlines` 的情况与处理（正常应为「无」；TDD 新增测试与「需求驱动更新」除外，逐条说明）。需求驱动更新逐条登记：测试文件 → 票 DoD 条目 / tech-spec 节锚点 + 冲突说明 + 改动范围声明（仅限被变更行为覆盖的断言 / 夹具）。

## 漂移与升档记录

实测 diff（merge-base..HEAD）超出票 `declared-scope` 的登记处，规则见 `agents-config.verify-policy`：

| 检查时点 | 漂移文件清单 | 是否触发升档 | 处理（登记即可 / 升档原因与补验证记录） |
|---|---|---|---|

（正常应至少每自修复轮次一行；无漂移也要留一行「无漂移」。`process_check.py` 按文件路径核对漂移登记，升档行须写明升至的档位名。）

## 自修复轮次记录

| 轮 | 触发原因（构建失败 / 评审 BLOCKER） | 处理 | 结果 |
|---|---|---|---|

## 遗留

Medium / Low 级评审意见（不阻塞合并）、待人裁决事项。
