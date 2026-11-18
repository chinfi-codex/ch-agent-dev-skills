## ARCH-CHECK 规范与动作

- 架构规范的唯一机器真源是 `docs/ARCH-CHECK.json`；`docs/ARCHITECT.md` 是 `arch_check.py render write` 确定性生成的人类 / agent 指南，禁止手改。
- 编码前先读 `docs/ARCHITECT.md`：分层、组与路径、允许 / 禁止依赖、放置规则、参数 / 类行数 / 注释 / 重复度阈值。AI 的上下文里没有这些规则就不会遵守。
- **票级不设架构门**：`delivery.start`、`ticket_context.check_plan`、`process_check --phase ticket` 都不做架构检查。架构检查只在 feature candidate 的 `freeze / check` 强制运行；本地 pre-push hook 只做提前预警，权威门仍是 candidate。
- 规则变更（新增、删除、放宽、改阈值）必须写进 tech-spec 3.2 和承接票 `declared-scope`，并重新 `render write`；ai-review 把“删除 / 放宽规则”当作高风险项审查。
- 旧 `docs/architecture/*.json`、`docs/ARCHITECTURE.md`、stable ID、`architecture-updates`、flow-forward/back 已废弃；不要新增或读取。

常用命令：

```bash
python3 ../shared/scripts/arch_check.py check  --repo <项目根> --head HEAD --rules docs/ARCH-CHECK.json
python3 ../shared/scripts/arch_check.py validate --repo <项目根> --worktree --rules docs/ARCH-CHECK.json
python3 ../shared/scripts/arch_check.py render write --repo <项目根>
python3 ../shared/scripts/arch_check.py render check --repo <项目根> --worktree
```

违规输出包含规则 ID、`file:line`、违规事实和 Step 1 / Step 2 修复动作。按步骤修代码或按流程改规则；不要手改 `docs/ARCHITECT.md`，也不要用改规则来绕过错误。
