# Impl Log：<issue-id> <title>

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

命中 `agents-config.redlines` 的情况与处理（正常应为「无」；TDD 新增测试除外，逐条说明）。

## 自修复轮次记录

| 轮 | 触发原因（构建失败 / 评审 BLOCKER） | 处理 | 结果 |
|---|---|---|---|

## 遗留

Medium / Low 级评审意见（不阻塞合并）、待人裁决事项。
