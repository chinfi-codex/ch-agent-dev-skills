## 派发协议（标准角色配置）

dev 阶段的「派发」只依赖一个宿主无关原语：**宿主 agent 自动新开一个独立对话执行派发提示词**。模型不再靠提示词口头指定，而是由同一份 `agents-config` 校验、解析并绑定到宿主原生角色。

### 配置校验与角色解析

- 主会话开始监督、实现会话启动、发起评审前都先跑：`python3 ../shared/scripts/dispatch_config.py validate --config ./dev/agents-config.md --catalog ./dev/dispatch-model-catalog.json`（目录由 `/setup-dev` 从宿主实时能力生成，结构 `{host: {model: [effort...]}}`）。旧 `dispatch.model`、缺字段、当前宿主无条目、模型或 effort 不可用 → 停下修正配置，不跨宿主套用、不自动降级。
- **主会话监督**：取 `dispatch.hosts.<当前宿主>.supervisor-review`，用 `resolve --role supervisor` 核对当前会话的实际 model / effort。不符即停下请人按配置新开或切换主会话；配置文件不会 retroactively 切换已经存在的会话，不得把「写进配置」冒充「当前已生效」。
- **开发实现**：light / scoped 取 `development.default`，full / 集成 / 高风险及评审达 `review-round-alert` 轮后的续修取 `development.escalated`；用 `resolve --role development --tier <档>` 得到原生参数。
- **独立评审与派发的符合性核对**：固定取 `supervisor-review`，用 `resolve --role review`；不随开发票档切换，但必须另开独立对话，写查分离不变。
- 原生角色文件只从配置生成：`python3 ../shared/scripts/dispatch_config.py render --config ./dev/agents-config.md --host <宿主> --output <宿主配置根>`。渲染器拒绝覆盖已有不同文件；确需更新时先核对差异并备份，再由 `/setup-dev` 明确重生，不手改第二份模型真源。DSH 的角色路由合并进目标 profile，不覆盖其他 profile。

### 派发原语

- 派发 = 主会话通过宿主 agent 的「新开独立对话」能力，启动一个新对话执行 `/implement issue-NNN`（或 `/ai-review`）；新对话不继承、也不应依赖主会话上下文。
- 派发提示词必须自包含：票文件路径、tech-spec 路径、`./dev/agents-config.md` 路径、`feature-module` 与 `feature-slug`——被派发方全靠落盘文件工作。
- 派发前置写入：启动实现对话前，主会话按「监督循环 · 状态与事件写入契约」在票 frontmatter 写 `dispatch` 块（host / model / dispatched-at / attempts）并追加 `dispatch` 事件——归属在派发瞬间落盘，不靠事后补登；这里记录的是经 `resolve` 确定并随原生角色绑定的模型。

### 宿主适配

| 宿主 | 主会话监督 | 独立对话与原生角色参数 | worktree 归属 |
|---|---|---|---|
| Claude Code | 项目 settings 的 `model` / `effortLevel` | `Task`；`subagent_type = supervisor-review / development / development-escalated`，角色 frontmatter 绑定 `model` / `effort`；`CLAUDE_CODE_SUBAGENT_MODEL_FORCE` 或 `CLAUDE_CODE_EFFORT_LEVEL` 覆盖配置时即停 | 同主会话文件系统：实现对话在主工作区建 `.worktrees/<issue-id>`，主会话按清场序列删 |
| ZCode | 当前会话须核对为 `supervisor-review.model` / `thoughtLevel` | `Agent`；`subagent_type` 同上，角色 frontmatter 绑定 `model` / `thoughtLevel` | 同 Claude Code |
| Codex | 项目 config 的 `model` / `model_reasoning_effort` | 原生多代理工具；`agent_type` 同上，角色 TOML 绑定 `model` / `model_reasoning_effort` | 自建沙箱 `~/.codex/worktrees/<hash>/`，路径不可预知且不保证回收：实现对话退出前按分支名自清并把路径写进结构化回报；主会话按清场序列以 branch 匹配兜底 |
| DSH | profile `agent-default-model` 的 `provider` / `model` / `reasoningEffort` | `subagent`；每次调用显式传 `provider` / `model` / `reasoning_effort`，只取 `allowedModels` 中由 `resolve` 选出的路由 | 以实际机制为准；派发环境与主工作区不同文件系统时，实现对话退出前按分支名自清，主会话 `git worktree prune` 校验 |

- 未登记的宿主没有可套用配置：先扩展 schema、实时目录与原生 adapter，再进入开发；不得拿其他宿主模型名凑合。
- frontmatter `allowed-tools` 里的 `Task` 是 Claude Code 命名；其他宿主按上表映射为自身派发工具（如 ZCode 的 `Agent`），协议语义不变。
- 兜底：宿主完全没有独立对话能力时，主会话产出自包含派发提示词与 `resolve` 结果，请人在对应模型的新对话启动；无法确认实际模型时不得开工，也不得在主会话同一上下文里「顺便自己实现」充数。
- 写查分离不可降级：**评审（`/ai-review`）必须在与实现不同的独立对话 / 独立环境执行**；宿主连评审独立对话都无法提供时，停下找人，绝不自评。
- 同票复审续接：宿主支持子对话续接（如 ZCode `SendMessage` 按 agentId 续接已完成对话）时，BLOCKER 回修后的复审发回**同一评审对话**（喂回修 commit 范围 + 上轮报告路径），不每轮新开实例、不重载评审上下文；写查分离不变式不受影响（评审实例始终独立于实现），跨票仍一律新开；宿主不支持续接时退回现状：每轮新开独立对话全量复审。续接只延续会话，不得改变已绑定的 `supervisor-review` 模型 / effort。

### worktree 清场序列（收口与对账共用）

按分支名发现，不按路径猜——宿主自管沙箱（如 Codex）会让真实路径偏离约定：

1. `git worktree list --porcelain` 找 branch = `feat/<feature-slug>-<issue-id>` 的注册条目
2. **salvage 前置（任何会话删 worktree 前必做，巡检外会话同样适用）**：`git -C <path> status --porcelain --ignored` 连 ignored 一起摸——ignored 目录（dev/、reports/、.logs/ 等）是本地真源，随 worktree 删除即永久丢失，git 历史救不回。**先辨软链**：`<path>/dev` 是指向主工作区的软链（约定形态）时，其内容本就在主工作区，**不得跟随软链打包**（否则把主 dev/ 整树复制进 .salvage，二次膨胀），软链本身随 worktree 删除即可；实体目录才按下述判定。不可再生且未收录进主工作区的内容（票文档 / 证据捕获 / 测试产物）→ 先打包到主工作区 `./dev/.salvage/<issue-id>-<日期>/` 并附 `_manifest.json`（来源路径 / 内容清单 / 判定理由）；可再生的（依赖目录 / 构建产物 / 下载缓存）不留
3. `git worktree remove <path>`；未跟踪产物拒删时 `--force`（salvage 完成后剩余产物可弃）
4. `git worktree prune` 清悬空注册
5. 分支删除只能在 worktree 清空之后：gitlab 模式由 `glab mr merge --delete-source-branch` 带删远端源分支；local 模式核对已并入 `main-branch` 后 `git branch -D feat/<feature-slug>-<issue-id>`（有远端同名再 `git push origin --delete`）
6. `rmdir .worktrees 2>/dev/null || true` 清空父目录
