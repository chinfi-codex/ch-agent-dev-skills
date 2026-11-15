# Dev Review Report：<issue-id>

| 项 | 内容 |
|---|---|
| feature-slug | `<feature-slug>` |
| issue | `<issue-id>` |
| 固定 base | `<ticket-context.base>` |
| 评审 HEAD / tree | `<完整 commit>` / `<完整 tree>` |
| diff / process-check 哈希 | `<SHA256>` / `<process-check-review.json SHA256>` |
| 评审引擎 | open-code-review（ocr），模式 `ci` / `local` / `delegate` |
| 引擎调用 | `<实际命令 或 CI job 链接>` |
| 实现者 / 评审者 | `<host:session/agent>` / `<host:session/agent>`（必须不同） |
| 信任级别 | `host-verified` / `operator-attested`；回执 `review-receipt.json` |
| 结论 | `pass` / `pass-with-notes` / `blocked` |

## 覆盖统计

```yaml
total_files: N
reviewed_files: N
skipped_files: N     # 每个附原因
coverage_rate: N%
```

## 评审发现（ocr findings 原样映射）

| # | 严重度 | 类别 | 位置 | 内容 |
|---|---|---|---|---|
| 1 | critical/high/medium/low | bug/security/performance/maintainability/test/style/... | `path:start-end` | `<finding 原文>` |

> findings 原样入表：不改写严重度、不删减条目。对引擎结论有异议，另起「评审员意见」节说明，不覆盖原文。

## 流程核对与语义复核

- 流程核对结果：`<process-check JSON 路径>` → `PASS` / `FAIL`（只引用，不重做；FAIL 行原样列入 BLOCKER）
- 语义复核（测试更新映射、生成器 tree、严格 scope、票粒度、旧实现清理、架构逐 ID / flow-back）：结论与依据；无则写「无」
- 评审只判代码、需求一致性与测试质量，不运行或等待 CI full；候选 HEAD/tree、报告和回执必须同源

找不到唯一票或关键输入时，不伪造代码评审结论或报告路径：返回 human-decision 请求，由主会话统一问人。

## BLOCKER 清单（回修依据）

| # | 来源 | 位置 | 问题 | 修复方向 |
|---|---|---|---|---|
| 1 | ocr-critical / ocr-high / 流程核对 / 语义复核 | | | |

（BLOCKER = critical / high + 流程核对 FAIL + 语义复核不成立项，必须回修后重新评审；medium / low 记入 impl-log 遗留，不阻塞。）

## 评审员意见（如有）

对引擎结论的补充判断、上下文信息、疑似误报标注——与 findings 分开，不混淆。

## 复审记录（复审轮填，首轮省略）

| 轮 | 回修 commit | 复审结论 | 残留 BLOCKER |
|---|---|---|---|

每轮生成独立短报告 `...-review-<issue-id>-rN-YYYY-MM-DD.md`，原报告头部更新为最新结论并链接各轮。复审聚焦回修增量、上轮 BLOCKER 闭合和新增回归；回执同时记录 round 与前轮回执哈希，最终 HEAD/tree 变化后旧回执失效。
