# MR：<issue-id> <title>

| 项 | 内容 |
|---|---|
| feature-slug | `<feature-slug>` |
| issue | `<issue-id>`（tracker 模式附 IID / URL） |
| 源分支 | `feat/<module>/<feature>/<campaign>-<issue-id>` → `<ticket-context.target_branch>` |
| 状态 | ready（票级内环通过，由主会话合入集成分支） |

## 变更摘要

3–5 条：做了什么、为什么。对齐票文件「要做什么」。

## 证据摘要

- 实现会话由 `verify_issue.py run implementer` 实际执行本票 DoD 与 `quality-gate.fast`，结构化记录、完整 HEAD/tree、退出码与日志哈希见 `verification-implementer.json`；`evidence-<issue-id>.md` 只是确定性生成视图。
- 票级门禁为 `process_check.py --phase ticket`，覆盖 scope / deny / 粒度 / 见红 / 生成物 / dev 软链 / 预算 / manifest / bug regression / 活动 decision / implementer 证据；票级不做架构检查。票级不跑独立评审、supervisor 复跑或 conformance。
- 票级不执行 scoped / full。feature 冻结后由 `verify_issue.py run-candidate` 对全部票统一复跑一次，分支 / main 晋级由 `delivery.py promote` 在线核对 CI run、候选 tree 与受信来源；禁止把票级证据写成 CI full 已通过。
- commit：`<hash>`

## 评审与符合性

- 票级不发起独立评审；全部票合入集成分支后，在 feature candidate 上执行一次整需求 `/ai-review`、一次 feature conformance，以及 `feature_closeout` 的 arch-check + `ARCHITECTURE.md` 字节检查。
- feature 评审报告 / receipt：`integration/feature-review.md`、`integration/feature-review-receipt.json`；符合性：`integration/conformance-feature.md`。
- 票级遗留 medium / low：N 条，见 impl-log「遗留」。

## diff 统计

```text
<git diff --stat <ticket-context.base>...HEAD 摘要>
```

## 合并后动作（由主会话执行，无需人操作）

- 主会话通过 `delivery.py merge` 完成票级门禁复核并合入集成分支，写 `merged` 并保留 worktree / branch；目标分支前进时先 `delivery.py rebase` 并重新生成验证。
- 全部票 merged/done 后进入 feature 收口；feature-merged 后由 `feature_closeout.py finalize` 写 accepted/done 并统一清理。
- （后续 P4 验证 / P5 发布——当前 skill 不自动执行，push / Prod 需另行授权）
