---
name: why
version: 0.1.0
description: |
  Investigate why code, an interface, a threshold, or an architectural decision
  has its current shape. Builds a code anchor, covers seven evidence categories,
  uses independent source investigators and a citation-checking synthesizer,
  and separates Direct/Supported/Inferred/Speculative/Unknown claims. Use /how
  for current runtime mechanics.
allowed-tools:
  - Read
  - Write
  - Edit
  - Grep
  - Glob
  - Bash
  - Task
---
<!-- AUTO-GENERATED from SKILL.md.tmpl -->
<!-- do not edit directly -->

## 完成状态协议

- `已完成`：产物可进入下一阶段，不存在阻塞性交付缺口
- `已完成但有风险`：可用产物已产出，仍存在须显式记录的风险、依赖或信息缺口；可进入下一阶段，但不得隐藏问题
- `阻塞`：当前目标不能继续推进（缺必须前置文档 / 关键输入未批准 / 存在无法自行裁决的冲突）；必须指出阻塞点和解除阻塞所需条件
- `需补充上下文`：上下文不足以做出可靠产品判断；先进入单问题补充流程，不强行产出正式文档

### 对人表达（所有说给用户的话都适用）

状态名、内部标签、路径用于落盘与机器判定，**不对人说**。对人输出按以下组织：

1. **先结论后细节**：先用大白话说「发生了什么、结果如何、接下来怎样」，路径与证据清单放最后（冠以「想细看：」），不先甩一屏路径与字段名
2. **状态翻译成人话**，不单独报状态名：`阻塞` → 卡在哪、需要什么才能继续；`需补充上下文` → 缺什么、缺了会影响什么判断；`needs-human` → 这个我定不了、需要你定什么；`已完成但有风险` → 结论照给，风险逐条说人话（什么风险、什么时候会爆、怎么留意）
3. **需要人决策 / 接手时**说四件事：发生了什么、为什么我定不了、需要你定什么、每个选项的代价（改哪些模块 / 影响谁 / 什么风险）；并给推荐与理由
4. **说改动时**说：改什么、为什么改、影响面（哪些模块 / 哪些人 / 什么行为变化）、怎么验证；不用 commit hash 与文件清单代替说明
5. **内部术语不进对话**：拷打 / frontier / 门② / BLOCKER / 升档 / 回显 / 落盘 / status / worktree 等协议词只做内部判定，对人换成日常说法（必须先修的问题、需要重新拆票、记录下来、本票专用分支）

开发流程中的 `needs-human` 是**存在活动 human-decision 决策包的工作等待态**，不是完成结论，也不是子会话直接问人的提示。被派发实现 / 评审会话只返回决策请求；主会话统一问人、记录回答并完成路由确认后，才解除等待状态。无人值守只记录并继续其他票。提问组件的标题、问题、选项与说明全部遵守「提问格式与对人表达」字段契约。

对照（坏 → 好）：

- ✗「票置 needs-human。分支 feat/订单-issue-003，证据见 impl/issue-003/」→ ✓「第 3 票（订单超时自动关闭）卡在评审：支付回调重试时可能重复关单，涉及资损口径我不敢自己拍。A 按方案第 4 节加幂等键（推荐，只动订单模块）；B 先回退重试逻辑。想细看：评审与证据在 impl/issue-003/。」
- ✗「阻塞：feature brief 不存在」→ ✓「写 PRD 还差上游的需求方案：这个需求目录下没找到。要么先跑 /pd-plan 定方案，要么你指给我一份已有的。」

# /why

你负责回答“**是什么力量让代码/设计成为今天这样**”。`/how` 回答当前机制；`/why` 查历史动机、外部约束、事故、业务依据、数据阈值和取舍。

核心纪律：**代码能证明机制，不能证明自己的动机。**先读 `references/epistemics.md` 并全程执行；宁可留下具体 Unknown，也不写一个听起来完整的故事。

## 权限、模型与来源边界

- 除调用方明确要求的 `dev/features/<feature-module>/<feature-slug>/research/` 报告外，不写本地或外部状态；不提交、不评论 PR/ticket/chat、不运行写 SQL。
- investigator 和 synthesizer 使用 general-purpose **agent mode，而非会剥离 MCP 的 readonly/Ask mode**。这是为了保留证据工具和引用抽查；提示词仍禁止一切写入。宿主能同时保留 MCP 和只读权限时优先只读。
- 默认继承当前宿主模型。显式模型被拒绝时回退继承并披露；有实时列表时可选同族最近有效项。不得复制 pstack slug、跨宿主套模型或静默换族。
- 无子代理能力时，主会话逐角色顺序执行并披露；不能因为降级而减少证据类别。
- 本地无 PR 路径下，Git、本地 issue/change-request、PRD/tech-spec/review/prototype verdict 都是正式一手来源；`gh`、外部 PR 和外部 MCP 只在真实存在时使用，不是开工前提。

## Step 1：确定 Target 与 Question

target 可以是代码段、接口、模式、阈值、ARCH-CHECK 组 / 规则 ID、feature 或已命名决策。question 可以是设计动机、取舍、回归、事故、失效约束或阈值来源。

指代含糊时结合当前文件、近期改动和对话给出最短范围解释并继续，让用户可纠正。用户问题中自带的假设只是待验证 hypothesis，不是结论。

## Step 2：建立共同 Code Anchor

在启动 investigator 前，主会话内联完成：

- 目标 `path:line`、关键符号、ARCH-CHECK 组 / 规则 ID、相关测试与文档；
- `git blame -L <start>,<end> <file>`；
- `git log --follow -p -- <file>` 追踪重命名和补丁；
- `git log --oneline -20 -- <file>` 扩大近期历史；
- 对实质提交运行 `git show --stat --patch <commit>` 和 `git log -1 --format=%B <commit>`；
- 从提交和本地文档提取 ticket ID、spec/PRD 锚点、外部 PR 编号。只有真实存在且 `gh` 可用时才读取 PR body/review，不把无 PR 当作缺口。

同一 anchor 必须交给每个 investigator 和 synthesizer。最近提交只是线索，不自动拥有最终解释权。

## Step 3：建立七类来源覆盖图并并行调查

先检查实际工具/MCP 列表和本地资源，记录 `available / unavailable / ambiguous / queried / empty / failed`。本地无 PR 路径仍必须覆盖下表；不能因为缺外部 MCP 就静默缩范围。

| 类别 | 本地正式来源 | 外部来源示例 | 最擅长回答 |
| --- | --- | --- | --- |
| Source control | Git、代码注释、测试、ADR、CHANGELOG | 真实存在的 PR/review | 实现当时的讨论与变更轨迹 |
| Issue / ticket | 本地 issue、change-request、进度/决策记录 | Linear、Jira、GitHub Issues、GitLab | 产品、业务、合规 forcing function |
| Long-form documents | PRD、tech-spec、feature brief、review、prototype verdict、ADR | Notion、Confluence、Lark Doc | 设计理由、替代方案、共识 |
| Real-time team chat | 无默认本地来源 | Lark、Slack、Discord、Teams | 未落文档的实时讨论 |
| Infrastructure observability | 仓内 runbook/incident 记录 | Datadog、Grafana、New Relic、Splunk | 超时、重试、限流等运行时压力 |
| Error / exception tracking | 仓内错误报告和复盘 | Sentry、Rollbar、Bugsnag | 防御代码对应的异常轨迹 |
| Product analytics warehouse | 仓内数据报告 | Databricks、Snowflake、BigQuery、ClickHouse | 阈值、实验、使用量和迁移依据 |

执行规则：

1. Source control **始终调查**。其他类别只要存在本地来源或匹配 MCP，就启动对应 investigator。
2. 每类默认一个 investigator，且只拥有一个主来源/MCP；不得让一个 agent 混合多个来源。额外的同类来源记为覆盖项或独立后续任务。
3. 全部 investigator 在同一轮并行启动。提示词完整包含 `references/investigator-prompt.md`、共同 anchor、原始问题、对应 `references/source-playbook.md` 指向的类别 playbook。
4. 防御性代码（null guard、retry、timeout、rate limit、feature flag、egress/OOM guard）额外附 `references/sources/incident-postmortem.md`。
5. investigator 只收集证据，不作最终结论；空搜索、矛盾、查询原文和 Additional Leads 都必须返回。
6. 只有“没有任何对应来源”或“可证明无关”才能跳过，理由写入 Sources Consulted。不可访问、保留期、认证失败都记 gap，不记“没有相关事实”。
7. 单提交琐碎目标只有在确认七类可用来源均冗余时才可 inline 回答，并明确说明；这是罕见例外。

## Step 4：独立综合与引用抽查

启动一个 synthesizer，输入：

1. 全部 investigator 原始结果，包括 null、empty、failed 和 skipped reason；
2. 共同 code anchor、原始问题和覆盖图；
3. `references/epistemics.md` 与 `references/synthesizer-prompt.md`。

synthesizer 必须抽查不确定或承重引用，合并重复来源、保留矛盾、逐条校准 Direct / Supported / Inferred / Speculative / Unknown。主会话只能轻度编辑，不得重写置信度语言或把 Unknown 改成确定答案。

## Step 5：输出与规划约束

固定保留以下语义章节：

```text
The Question
The Code in Question
What We Found
What We Can Reasonably Infer
Competing Hypotheses
What We Don't Know
Sources Consulted
Confidence Summary
```

- Sources Consulted 每类一行，包含实际查询、打开对象、empty/failed/skipped 原因；不能只列有发现的来源。
- 若调查是为了修改代码，之后追加 **Preserve / Change / Avoid / Risk**：
  - Preserve：证据支持必须维持的行为/约束；
  - Change：本次允许改变且有证据覆盖的部分；
  - Avoid：历史上已证实会导致回归或违背外部约束的做法；
  - Risk：未知、来源缺口、可能已过期约束和需要进一步验证的假设。
- 每条规划约束回链证据和置信度；Unknown 不得自动升级为 Preserve，只能成为 Risk/待确认项。
- 调用方提供 feature-module/slug 或明确要求落盘时，按 `shared/templates/why-report.md` 写入 `dev/features/<feature-module>/<feature-slug>/research/<topic>-why-YYYY-MM-DD.md`。

## 常见失败

- Recency bias：只读最近提交，不追溯形成当前形状的更早决策。
- Rationalization：因为设计今天合理，就替历史作者补一个合理动机。
- Sycophancy：用户猜“为了性能”，就只搜支持性能的证据。
- Coverage laundering：外部 MCP 不存在时不搜索本地票/文档，也不披露缺口。
- Confidence laundering：把多个间接线索写成 Direct，或把未知阈值写成既定约束。
