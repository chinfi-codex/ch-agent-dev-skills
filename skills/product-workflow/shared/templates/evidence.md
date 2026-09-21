# Evidence：<issue-id>

```yaml
issue: <issue-id>
feature-slug: <feature-slug>
commit: <完成时 HEAD 的 commit hash>
branch: feat/<feature-slug>-<issue-id>
verify-tier: light|scoped|full      # 票 frontmatter 标注的档位
tier-executed: light|scoped|full    # 实际执行的档位（发生升档时高于票标注值，须附升档原因）
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
- scoped 档（scoped 及以上必填）：选择集来源（`selection: dir-map | impact-map`）+ 换算自哪些 diff 文件 + 命令与退出码 / 耗时
- full 档（full 档票与集成票必填）：`<quality-gate.full>` → 退出码 / 耗时
- 升档记录（如有）：触发原因（escalate-triggers 命中项 / 超 scoped-max-* 阈值）+ 升档时间点
