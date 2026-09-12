# MR：<issue-id> <title>

| 项 | 内容 |
|---|---|
| feature-slug | `<feature-slug>` |
| issue | `<issue-id>`（tracker 模式附 IID / URL） |
| 源分支 | `feat/<feature-slug>-<issue-id>` → `main` |
| 状态 | ready（评审通过，由主会话合并，无需人审） |

## 变更摘要

3–5 条：做了什么、为什么。对齐票文件「要做什么」。

## 证据摘要

- 验收命令 N/N 通过，详见 `evidence-<issue-id>.md`（all-passed: true）
- 全量质量门通过
- commit：`<hash>`

## 评审结论

- 评审引擎：open-code-review；模式 `delegate` / `local` / `ci`；内联评论见 MR discussions（仅 ci 模式逐行回贴）
- 评审报告：`reviews/<文件名>`，结论 `pass` / `pass-with-notes`
- BLOCKER 处理记录：N 处 critical/high，已回修 N 轮，逐条对应关系见 impl-log「自修复轮次记录」
- 遗留 medium / low：N 条，见 impl-log「遗留」

## diff 统计

```text
<git diff --stat main...HEAD 摘要>
```

## 合并后动作（由主会话执行，无需人操作）

- 主会话合并 MR（`glab mr merge` 或本地 `git merge --no-ff`）；合并冲突先 rebase 主分支重跑 fast 质量门，仍无法解决才升级给人
- 票状态置 `done`；删除远 / 本地分支；清理 worktree `.worktrees/<issue-id>/`
- （后续阶段接 P4 验证 / P5 发布——当前版本不自动执行）
