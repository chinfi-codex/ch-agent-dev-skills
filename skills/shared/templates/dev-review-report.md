# Feature Review Report：<feature-slug>

> 写作成本：保留本模板全部必需内容；事实用稳定 ID 与来源引用承接，不复述上游整段正文。完整行为、边界与断言不得省略。修订只编辑变化处，独立判断不可复用他人结论。

| 项 | 内容 |
|---|---|
| feature | `<feature-module>/<feature-slug>` |
| 固定 base | `<candidate.main_head>` |
| 候选 HEAD / tree | `<candidate.candidate>` / `<candidate.candidate_tree>` |
| merge base | `<candidate.merge_base>` |
| diff / plan 哈希 | `<diff_sha256>` / `<candidate.acceptance_plan.sha256>` |
| 评审引擎 | open-code-review（ocr），模式 `ci` / `local` / `delegate` |
| 引擎调用 | `<实际命令 或 CI job 链接>` |
| 全部实现者 | `<issue → host:session/agent>` |
| 评审者 | `<host:session/agent>`（必须独立于主会话和全部实现者） |
| 信任级别 | `host-verified` / `operator-attested`；回执 `integration/feature-review-receipt.json` |
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

> findings 原样入表：不改写严重度、不删减条目。critical / high 必须有闭合证据；对引擎结论有异议，另起「评审员意见」节说明，不覆盖原文。

## 票级分区与跨票复核

| 票 | merged commit | 主要改动 | 跨票接口 / 共享状态风险 | 结论 |
|---|---|---|---|---|
| issue-001 | `<完整 commit>` | `<一句话>` | `<接缝或 none>` | pass / finding |

## 语义复核

- 需求覆盖：PRD / change-request 全部 AC 与 tech-spec 行为点是否有实现和可执行验收；跨票主路径 / 失败路径是否闭合。
- 架构总览：`docs/ARCHITECTURE.md` 的 1–10 节现行事实与真实代码、部署和测试配置是否一致；项目结构树必须展开到 2–3 层子目录与关键文件，只列顶层即不合格；首次架构影响后不得仍是骨架。
- 接口与契约：附录 A–E 的层 / 组 / 依赖规则与真实代码是否一致；ownership、错误语义、并发 / 异步契约是否被票级切分破坏。
- 测试更新映射、生成器 tree、旧实现清理、arch-check / 总览字节检查：结论与依据；无则写「无」。
- 评审只判代码、需求一致性与测试质量，不运行或等待 CI full；候选复跑由 `verify_issue.py run-candidate` 独立完成。候选 HEAD/tree、报告和回执必须同源。

找不到唯一 feature、候选或关键输入时，不伪造代码评审结论或报告路径：返回 human-decision 请求，由主会话统一问人。

## BLOCKER 清单（修复票依据）

| # | 来源 | 位置 | 问题 | 修复方向 |
|---|---|---|---|---|
| 1 | ocr-critical / ocr-high / 语义复核 | | | |

（BLOCKER = critical / high + 语义复核不成立项，必须开修复票、合入同一集成分支后重新 freeze 和评审；medium / low 记入遗留，不阻塞。）

## 评审员意见（如有）

对引擎结论的补充判断、上下文信息、疑似误报标注——与 findings 分开，不混淆。

## 复审记录（新 candidate 填，首轮省略）

| 轮 | candidate | 相对上轮增量 | 复审结论 | 残留 BLOCKER |
|---|---|---|---|---|

每轮生成独立报告，回执 `round` 递增并绑定 `previous_receipt_sha256`。复审聚焦新 candidate 增量、上轮 BLOCKER 闭合和新增回归；candidate / tree 变化后旧回执失效。
