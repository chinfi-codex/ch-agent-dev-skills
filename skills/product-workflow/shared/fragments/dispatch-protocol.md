## 派发与监督协议（宿主无关）

dev 阶段的「派发」只依赖一个宿主无关原语：**宿主 agent 自动新开一个独立对话执行派发提示词**。不绑定任何具体工具名，不依赖外部 CI 或人工触发。

### 派发原语

- 派发 = 主会话通过宿主 agent 的「新开独立对话」能力，启动一个新对话执行 `/implement issue-NNN`（或 `/ai-review`）；新对话不继承、也不应依赖主会话上下文
- 派发提示词必须自包含：票文件路径、tech-spec 路径、`./dev/agents-config.md` 路径、`feature-slug`——被派发方全靠落盘文件工作
- 模型档位：被派发对话默认取当前宿主可用模型范围内**经济性最高的一档**（`agents-config.dispatch.model: economy`，比主会话低一档）；仅票被标记高风险 / 复杂时显式升级

### 宿主适配

| 宿主 | 新开独立对话的机制 |
|---|---|
| Claude Code | `Task` 工具（subagent） |
| ZCode | `Agent` 工具（subagent） |
| Codex | 宿主的新会话 / 子代理能力（以实际版本为准） |
| WorkBuddy | 宿主的任务派发 / 新对话能力 |
| 其他通用 agent | 任何「同一宿主内新开独立对话」的等价机制 |

- skill frontmatter 里 `allowed-tools` 的 `Task` 是 Claude Code 的工具命名；其他宿主安装或运行时按上表映射为自身派发工具（如 ZCode 的 `Agent`），协议语义不变
- 兜底：宿主完全没有自动新开对话能力时，主会话产出自包含派发提示词、请人在新对话窗口启动执行；不得在主会话同一上下文里「顺便自己实现」充数
- 写查分离不可降级：无论宿主能力如何，**评审（`/ai-review`）必须在与实现不同的独立对话 / 独立环境中执行**；宿主连评审独立对话都无法提供时，停下找人，绝不自评

### 监督循环（主会话职责，直到完成合并）

门②（票清单确认）通过后，主会话自动进入监督循环，**派出后不停手，监督到每张票合并完成**：

1. **取票**：读 `./dev/features/<feature-slug>/issues/` 票文件，重算 frontier（blocked-by 全部 `done` 的票）
2. **派发**：按宿主适配机制为 frontier 票新开独立对话跑 `/implement issue-NNN`；默认按依赖序逐张串行（一张收口再派下一张），人明确要求并行时才同时派多张
3. **跟踪**：等待被派发对话返回；其结构化回报（分支名 / MR IID 或草案路径 / 证据与评审报告路径 / 遗留 Medium-Low 清单）只是线索，不作为成功依据
4. **收口**：主会话亲自核对落盘产物——evidence `all-passed: true`、评审结论 pass / pass-with-notes、MR ready——然后执行合并（gitlab：`glab mr merge <IID>`；local：主工作区 `git merge --no-ff feat/<feature-slug>-<issue-id>`），票置 `done`，删远 / 本地分支并清理 worktree；合并冲突先在 worktree 内 rebase `main-branch`、重跑 `quality-gate.fast` 后再合
5. **推进**：重算 frontier，回到第 1 步；全部票 `done` 后回显总账（每票：合并 commit / 证据与评审报告路径 / 遗留 Medium-Low），输出完成状态
6. **唯一暂停条件**：被派发对话回报 `needs-human` / `阻塞`、合并冲突自动 rebase 后仍无法解决、或其他无法自行裁决的问题——与人单点确认后再继续；其余情况（含评审 BLOCKER 回修、fast 质量门自修复）一律不问人
