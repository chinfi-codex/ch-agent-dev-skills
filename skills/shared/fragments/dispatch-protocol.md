## 派发协议（宿主无关）

dev 阶段的「派发」只依赖一个宿主无关原语：**宿主 agent 自动新开一个独立对话执行派发提示词**。不绑定任何具体工具名，不依赖外部 CI 或人工触发。

### 派发原语

- 派发 = 主会话通过宿主 agent 的「新开独立对话」能力，启动一个新对话执行 `/implement issue-NNN`（或 `/ai-review`）；新对话不继承、也不应依赖主会话上下文
- 派发提示词必须自包含：票文件路径、tech-spec 路径、`./dev/agents-config.md` 路径、`feature-module` 与 `feature-slug`——被派发方全靠落盘文件工作
- 模型选择：取 `agents-config.dispatch.model.<当前宿主>`——light / scoped 票用 `default` 清单，full 档票 / 集成票 / 高风险票及评审达 `review-round-alert` 轮后用 `escalated` 清单，各取第一个当前可用模型；评审对话同档。当前宿主无条目 → 停下请人确认后写入配置，不跨宿主套用、不自行挑选

### 宿主适配

| 宿主 | 新开独立对话的机制 | worktree 归属 |
|---|---|---|
| Claude Code | `Task` 工具（subagent） | 同主会话文件系统：实现对话在主工作区建 `.worktrees/<issue-id>`，主会话按清场序列删 |
| ZCode | `Agent` 工具（subagent） | 同 Claude Code |
| Codex | 宿主的新会话 / 子代理能力（以实际版本为准） | 自建沙箱 `~/.codex/worktrees/<hash>/`，路径不可预知且不保证回收：实现对话退出前按分支名自清并把路径写进结构化回报；主会话按清场序列以 branch 匹配兜底 |
| WorkBuddy | 宿主的任务派发 / 新对话能力 | 以实际机制为准；派发环境与主工作区不同文件系统时，实现对话退出前按分支名自清，主会话 `git worktree prune` 校验 |
| 其他通用 agent | 「同一宿主内新开独立对话」的等价机制 | 按 WorkBuddy 行原则处理 |

- frontmatter `allowed-tools` 里的 `Task` 是 Claude Code 命名；其他宿主按上表映射为自身派发工具（如 ZCode 的 `Agent`），协议语义不变
- 兜底：宿主完全没有该能力时，主会话产出自包含派发提示词请人在新对话启动；不得在主会话同一上下文里「顺便自己实现」充数
- 写查分离不可降级：**评审（`/ai-review`）必须在与实现不同的独立对话 / 独立环境执行**；宿主连评审独立对话都无法提供时，停下找人，绝不自评
- 同票复审续接：宿主支持子对话续接（如 ZCode `SendMessage` 按 agentId 续接已完成对话）时，BLOCKER 回修后的复审发回**同一评审对话**（喂回修 commit 范围 + 上轮报告路径），不每轮新开实例、不重载评审上下文；写查分离不变式不受影响（评审实例始终独立于实现），跨票仍一律新开；宿主不支持续接时退回现状：每轮新开独立对话全量复审

### worktree 清场序列（收口与对账共用）

按分支名发现，不按路径猜——宿主自管沙箱（如 Codex）会让真实路径偏离约定：

1. `git worktree list --porcelain` 找 branch = `feat/<feature-slug>-<issue-id>` 的注册条目
2. **salvage 前置（任何会话删 worktree 前必做，巡检外会话同样适用）**：`git -C <path> status --porcelain --ignored` 连 ignored 一起摸——ignored 目录（dev/、reports/、.logs/ 等）是本地真源，随 worktree 删除即永久丢失，git 历史救不回。**先辨软链**：`<path>/dev` 是指向主工作区的软链（约定形态）时，其内容本就在主工作区，**不得跟随软链打包**（否则把主 dev/ 整树复制进 .salvage，二次膨胀），软链本身随 worktree 删除即可；实体目录才按下述判定。不可再生且未收录进主工作区的内容（票文档 / 证据捕获 / 测试产物）→ 先打包到主工作区 `./dev/.salvage/<issue-id>-<日期>/` 并附 `_manifest.json`（来源路径 / 内容清单 / 判定理由）；可再生的（依赖目录 / 构建产物 / 下载缓存）不留
3. `git worktree remove <path>`；未跟踪产物拒删时 `--force`（salvage 完成后剩余产物可弃）
4. `git worktree prune` 清悬空注册
5. 分支删除只能在 worktree 清空之后：gitlab 模式由 `glab mr merge --delete-source-branch` 带删远端源分支；local 模式核对已并入 `main-branch` 后 `git branch -D feat/<feature-slug>-<issue-id>`（有远端同名再 `git push origin --delete`）
6. `rmdir .worktrees 2>/dev/null || true` 清空父目录
