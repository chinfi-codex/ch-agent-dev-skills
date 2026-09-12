# Evidence：<issue-id>

```yaml
issue: <issue-id>
feature-slug: <feature-slug>
commit: <完成时 HEAD 的 commit hash>
branch: feat/<feature-slug>-<issue-id>
all-passed: true|false    # 全部验收命令退出码为 0 时才允许 true
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

## 质量门记录

- 全量档命令：`<quality-gate.full>`
- 退出码：`0` / 关键摘要
