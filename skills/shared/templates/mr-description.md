# MR：<issue-id> <title>

| 项 | 内容 |
|---|---|
| feature-slug | `<feature-slug>` |
| issue | `<issue-id>`（tracker 模式附 IID / URL） |
| 源分支 | `feat/<feature-slug>-<issue-id>` → `<ticket-context.target_branch>` |
| 状态 | ready（评审通过，由主会话合并，无需人审） |

## 变更摘要

3–5 条：做了什么、为什么。对齐票文件「要做什么」。

## 证据摘要

- 实现会话与主会话均由 `verify_issue.py` 实际执行 DoD 验收命令与 `quality-gate.fast`，结构化记录、完整 HEAD/tree、退出码与日志哈希见 `verification-implementer.json`、`verification-supervisor.json`；`evidence-<issue-id>.md` 只是确定性生成视图
- 票级不执行 scoped / full。分支 / main 晋级由 `delivery.py promote` 在线核对 CI run、候选 tree 与受信来源；禁止把票级证据写成 CI full 已通过
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

- 主会话通过 `delivery.py merge` 完成门禁复核与合并；目标分支前进时先 `delivery.py rebase` 并重新生成验证、评审和符合性结论
- 合并登记成功后、总结前调用 `close-and-clean`，按 journal 清理 worktree、精确 upstream 与失效过程产物；任何未完成步骤必须报告，不能声称已全部清理
- （后续 P4 验证 / P5 发布——当前 skill 不自动执行，push / Prod 需另行授权）
