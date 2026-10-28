# Evidence：<issue-id>

> 体积预算：正文 ≤ `artifact-budget.impl-log-max-lines` 行，只写结论、表格与 raw 文件指针；过程日志、完整输出放 `raw/`，中间轮只留最终轮。


```yaml
issue: <issue-id>
feature-slug: <feature-slug>
commit: <完成时 HEAD 的 commit hash>
branch: feat/<feature-slug>-<issue-id>
verify-tier: light|scoped|full      # 票 frontmatter 标注的档位
tier-target: light|scoped|full      # 升档后的目标档位（未升档 = verify-tier；高于票面时 impl-log「漂移与升档记录」须有对应行）
tier-executed: light|scoped|full    # 实际已执行的档位（once-at-final 下评审前 full 档票为 scoped，最终 full 落盘后改 full）
scoped-deferred: closure-incomplete  # 仅 scoped 档票选择集不可枚举、票内未跑选择集时填；套件级验证由集成票 full 与每日回归兜底
full-deferred: ci                  # 仅 verify-policy.full-execution: ci-only 时填；票级只核循环内验证，feature / main 另核分支 CI 同 tree 判决
isolation:                          # 仅集成票按「模块隔离认定」跑 module-scoped 时填 module-scoped，其余留空
full-commit: <跑 full 的 commit；须等于 commit，或与之差异仅为不改本票文件 blob 的 rebase>  # 仅 full-execution: ticket 的 full 档填；ci-only 禁用
full-ref:     # 仅 ticket 模式的集成票复用每日回归时填；ci-only 禁用，不得冒充分支 CI 判决
all-passed: true|false    # 全部验收命令与档位门退出码为 0 时才允许 true（隔离项 skip 不算失败）
quarantined: 0            # 本次执行中按隔离清单 skip 的用例数（attribution: quarantine 时必填）
generated: YYYY-MM-DD HH:MM
# 有多实例部署配置的项目必填；每个验收/复跑命令均须被远端回执覆盖
test-location: remote
remote-runs: [raw/<运行编号>/result.json]
remote-command-manifests: [raw/<运行编号>/commands.json]
```

## 验收命令执行记录

> 逐条对应票文件 DoD。命令必须在本票 worktree 内实际执行，禁止只引用 agent 自述。

### DoD-1

- 命令：`<命令>`
- 退出码：`0`
- 关键输出（截取可判定部分）：
  ```text
  <输出>
  ```

### DoD-2

（同上）

## 档位门记录

> 按票 `verify-tier`（升档后按 `tier-executed`）执行；规则见 `agents-config.verify-policy`。

- 票标注档位 / 实际执行档位：`<light|scoped|full>` / `<light|scoped|full>`
- fast 档命令：`<quality-gate.fast>` → 退出码 / 耗时
- scoped 档（scoped 及以上必填）：选择集来源（`selection: dir-map | impact-map`，数据 / 配置文件另记 dir-map 命中条目数）+ 换算自哪些 diff 文件 + 命令与退出码 / 耗时；选择集占全套 ≥ 50% 时另记占比（评审信号，不升档）；经 test_slots 包装的执行记 jobs / queue-seconds / run-seconds（包装器 stderr 末行摘要）。选择集不可枚举（full fallback）→ **不跑选择集**，改记：`scoped-deferred: closure-incomplete` + 不可枚举原因（选择器输出的 reasons）——套件级验证由集成票 full 与每日回归兜底
- full 档（`full-execution: ticket` 的 full 档票与集成票必填）：`<quality-gate.full>` → 退出码 / 耗时 + jobs / queue-seconds / run-seconds；集成票按「模块隔离认定」执行时改记：涉及模块清单 + 反向闭包为空的判定依据 + module-scoped 命令与退出码 / 耗时。`ci-only` 时不填票内 full 记录，改填 `full-deferred: ci`；分支 CI run / tree / artifact 由 feature 放行证据单独登记
- 先在失败隔离（`attribution: quarantine` 时必填）：隔离数（头部 `quarantined` 字段须一致）+ 清单版本或路径；清单外失败补跑记录（疑似本票引入的单项 + base 侧同命令对照结果）。`two-sided` 模式逐条记录 branch / base 退出码与原始日志名。清单外失败显式列出但不进 `all-passed` 分母（归因成立的先在失败同样列出）
- load-flake 清单（如有）：超时单项 + 运行期 load-peak + 串行重跑退出码；load-flake 不算失败、不进归因、不进 `all-passed` 分母
- 升档记录（如有）：触发原因（escalate-triggers 命中项及其级别 / 超 scoped-max-* 阈值；scoped+ 级另记可枚举性判定依据）+ 升档时间点

## 回归有效性（仅 `type: bug` 票必填）

> 证明回归测试真的能抓住这个 bug：revert 生产修复（保留测试）须见红，恢复后须绿。revert 后不变红 = 测试从未复现 bug，修复不算完成。

- revert 修复后重跑回归测试：命令 + 退出码（须非 0、断言失败）+ 失败输出要点
- 恢复修复后重跑：命令 + 退出码 `0`

## 人工确认项（non-gating，不进 all-passed）

- 性能 / 展示类结果：命令、原始输出路径、与参考值对比（只陈述，不判通过）
- env skip 清单：用例名 + 缺失依赖
