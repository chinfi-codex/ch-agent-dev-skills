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
isolation:                          # 仅集成票按「模块隔离认定」跑 module-scoped 时填 module-scoped，其余留空
full-commit: <跑 full 的 commit；须等于 commit，或与之差异仅为不改本票文件 blob 的 rebase>  # full 档才填
all-passed: true|false    # 全部验收命令与档位门退出码为 0 时才允许 true
generated: YYYY-MM-DD HH:MM
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
- scoped 档（scoped 及以上必填）：选择集来源（`selection: dir-map | impact-map`）+ 换算自哪些 diff 文件 + 命令与退出码 / 耗时；选择集占全套 ≥ 50% 时另记占比（评审信号，不升档）
- full 档（full 档票与集成票必填）：`<quality-gate.full>` → 退出码 / 耗时；集成票按「模块隔离认定」执行时改记：涉及模块清单 + 反向闭包为空的判定依据 + module-scoped 命令与退出码 / 耗时
- 先在失败归因（套件存在与本票无关的既有失败时必填）：归因方式（`known-failures` / `two-sided`）；known-failures 模式下记录清单版本或路径、命中清单的先在失败项（测试名 + 签名一致判定）、清单外补跑 base 侧的单项清单与对照结果；two-sided 模式逐条记录 branch / base 退出码与原始日志名。先在失败显式列出但不进 `all-passed` 分母
- 升档记录（如有）：触发原因（escalate-triggers 命中项及其级别 / 超 scoped-max-* 阈值；scoped+ 级另记可枚举性判定依据）+ 升档时间点

## 人工确认项（non-gating，不进 all-passed）

- 性能 / 展示类结果：命令、原始输出路径、与参考值对比（只陈述，不判通过）
- env skip 清单：用例名 + 缺失依赖
