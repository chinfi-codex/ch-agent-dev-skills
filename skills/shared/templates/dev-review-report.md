# Dev Review Report：<issue-id>

| 项 | 内容 |
|---|---|
| feature-slug | `<feature-slug>` |
| issue | `<issue-id>` |
| 固定点 | `<fixed-point>`（merge-base `main...HEAD` 的 commit hash） |
| 评审引擎 | open-code-review（ocr），模式 `ci` / `local` / `delegate` |
| 引擎调用 | `<实际命令 或 CI job 链接>` |
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

## 红线复核

对照 `agents-config.redlines` 的可解释清单：命中项一律列 Critical BLOCKER（无论引擎是否报告）；无命中写「无」。

## BLOCKER 清单（回修依据）

| # | 来源 | 位置 | 问题 | 修复方向 |
|---|---|---|---|---|
| 1 | ocr-critical / ocr-high / 红线复核 | | | |

（BLOCKER = critical / high + 红线命中，必须回修后重新评审；medium / low 记入 impl-log 遗留，不阻塞。）

## 评审员意见（如有）

对引擎结论的补充判断、上下文信息、疑似误报标注——与 findings 分开，不混淆。
