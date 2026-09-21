## 派发与监督协议（宿主无关）

dev 阶段的「派发」只依赖一个宿主无关原语：**宿主 agent 自动新开一个独立对话执行派发提示词**。不绑定任何具体工具名，不依赖外部 CI 或人工触发。

### 派发原语

- 派发 = 主会话通过宿主 agent 的「新开独立对话」能力，启动一个新对话执行 `/implement issue-NNN`（或 `/ai-review`）；新对话不继承、也不应依赖主会话上下文
- 派发提示词必须自包含：票文件路径、tech-spec 路径、`./dev/agents-config.md` 路径、`feature-slug`——被派发方全靠落盘文件工作
- 模型档位：被派发对话默认取当前宿主可用模型范围内**经济性最高的一档**（`agents-config.dispatch.model: economy`，比主会话低一档）；仅票被标记高风险 / 复杂时显式升级

### 宿主适配

| 宿主 | 新开独立对话的机制 | worktree 归属 |
|---|---|---|
| Claude Code | `Task` 工具（subagent） | 与主会话同一文件系统：实现对话在主工作区建 `.worktrees/<issue-id>`，主会话按清场序列删 |
| ZCode | `Agent` 工具（subagent） | 同 Claude Code |
| Codex | 宿主的新会话 / 子代理能力（以实际版本为准） | Codex 自建会话沙箱 `~/.codex/worktrees/<hash>/`，路径不可预知且不随会话正常结束保证回收：实现对话退出前按分支名自清，并把 worktree 路径写进结构化回报；主会话按清场序列以 branch 匹配兜底 |
| WorkBuddy | 宿主的任务派发 / 新对话能力 | 以实际机制为准；派发环境与主工作区不同文件系统时，实现对话退出前按分支名自清，主会话 `git worktree prune` 校验 |
| 其他通用 agent | 任何「同一宿主内新开独立对话」的等价机制 | 按 WorkBuddy 行原则处理 |

- skill frontmatter 里 `allowed-tools` 的 `Task` 是 Claude Code 的工具命名；其他宿主安装或运行时按上表映射为自身派发工具（如 ZCode 的 `Agent`），协议语义不变
- 兜底：宿主完全没有自动新开对话能力时，主会话产出自包含派发提示词、请人在新对话窗口启动执行；不得在主会话同一上下文里「顺便自己实现」充数
- 写查分离不可降级：无论宿主能力如何，**评审（`/ai-review`）必须在与实现不同的独立对话 / 独立环境中执行**；宿主连评审独立对话都无法提供时，停下找人，绝不自评

### worktree 清场序列（收口与对账共用）

按分支名发现，不按路径猜——宿主自管沙箱（如 Codex）会让真实路径偏离 `.worktrees/<issue-id>/` 约定：

1. `git worktree list --porcelain` 找 branch = `feat/<feature-slug>-<issue-id>` 的注册条目
2. `git worktree remove <path>`；有未跟踪产物被拒删时 `--force`（票已收口，产物不再需要）
3. `git worktree prune` 清悬空注册
4. 分支删除只能在 worktree 清空之后：gitlab 模式由 `glab mr merge --delete-source-branch` 带删远端源分支，local 模式核对已并入 `main-branch` 后 `git branch -D feat/<feature-slug>-<issue-id>`（存在远端同名再 `git push origin --delete`）
5. `rmdir .worktrees 2>/dev/null || true` 清空父目录

### 监督循环（主会话职责，直到完成合并）

门②（票清单确认）通过后，主会话自动进入监督循环，**派出后不停手，监督到每张票合并完成**：

1. **取票**：读 `./dev/features/<feature-slug>/issues/` 票文件，重算 frontier（blocked-by 全部 `done` 的票）
2. **派发**：按宿主适配机制为 frontier 票新开独立对话跑 `/implement issue-NNN`；默认按依赖序逐张串行（一张收口再派下一张），人明确要求并行时才同时派多张
3. **跟踪**：等待被派发对话返回；其结构化回报（分支名 / worktree 路径 / MR IID 或草案路径 / 证据与评审报告路径 / 遗留 Medium-Low 清单）只是线索，不作为成功依据
4. **收口**：主会话亲自核对落盘产物——evidence `all-passed: true` 且 `tier-executed` 达到票 `verify-tier`（或已按 `verify-policy` 升档并留记录）、评审结论 pass / pass-with-notes、MR ready——然后执行合并（gitlab：`glab mr merge <IID> --delete-source-branch`；local：主工作区 `git merge --no-ff feat/<feature-slug>-<issue-id>`），票置 `done`，按 worktree 清场序列收尾；合并冲突先在 worktree 内 rebase `main-branch`、重跑 `quality-gate.fast` 后再合
5. **推进**：每轮先做 worktree 对账——`git worktree list --porcelain` 与票状态比对，票已 `done` 但 worktree 仍在的按清场序列补删（回收中断会话的遗留），`in-review` / `needs-human` 票的保留并在总账列出路径；再重算 frontier，回到第 1 步；全部票 `done` 后回显总账（每票：合并 commit / 证据与评审报告路径 / worktree 已清或保留原因 / 遗留 Medium-Low），输出完成状态
6. **唯一暂停条件**：被派发对话回报 `needs-human` / `阻塞`、合并冲突自动 rebase 后仍无法解决、或其他无法自行裁决的问题——与人单点确认后再继续；其余情况（含评审 BLOCKER 回修、fast 质量门自修复）一律不问人
