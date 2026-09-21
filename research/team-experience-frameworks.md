# 团队级 AI 经验框架调研：从腾讯 teamai-cli 看通用方法论与工具链

> 调研日期：2026-09-13。Star 数与活跃度均为当日 GitHub 实时数据。
> 调研方式：4 路并行深挖（teamai-cli 本体 / Spec 驱动流派 / 多智能体团队流派 / 国内生态与上下文工程），全部结论来自官方仓库、官方文档与可抓取的文章，来源 URL 附于各处。

---

## 1. 概念界定：什么是"团队级经验框架"

teamai-cli 官方问题陈述（[usage-guide.zh-CN.md](https://github.com/Tencent/teamai-cli/blob/main/docs/usage-guide.zh-CN.md)）：

> "Agent 作为个人工具已经很强，但学到的东西留在个人手里：昨天某位成员的 Agent 摸索出来的结论，今天到不了其他人的 Agent 面前。"

综合国内社区讨论（掘金多篇团队治理文章、Qoder/高德案例）与开源项目实践，业界对这类框架形成的共识定义是：

> **把团队的工程知识（规范、流程、踩坑经验、工具用法）从"个人 prompt 技巧"转化为可版本化、可审查、可分发、可强制执行的配置资产，注入到每个 AI 工具的上下文中，使一致性从"概率问题"变成"配置问题"。**

公认的构件是**五件套 + 一个分发层**：

| 构件 | 作用 | 典型载体 |
|---|---|---|
| Rules（规则） | 规范性约束，告诉 AI 该守什么 | AGENTS.md / CLAUDE.md / `.cursor/rules/*.mdc` |
| Skills / Commands（可复用流程） | 把 SOP 封装成可调用的工作流单元 | `SKILL.md`、斜杠命令 |
| Subagents（角色分工） | 把团队岗位映射为独立上下文的 agent | `.claude/agents/*.md` |
| Memory / Wiki（沉淀的知识） | 自动积累的经验与代码知识 | memory 文件、向量库、代码知识图谱 |
| Hooks（机械强制） | 规则管不住的用代码拦 | SessionStart / PreToolUse / Stop 钩子 |
| **分发层** | 让全团队所有工具同步同一份资产 | git 仓库 / marketplace / teamai-cli / rulesync / 企业控制台 |

两句最具代表性的口号：腾讯 teamai-cli 的 **"One Team. One Harness. Every Agent."**，以及 Qoder 高德客户案例的 **"Harness 治理 + SDD，告别 Vibe Coding"**（[Qoder 高德案例](https://docs.qoder.com/customer-cases/qoder-case-amap.md)）。

一个关键区分：掘金文章对传统知识库的评价是**"管人看"，teamai-cli 这类框架是"管 AI 用"**（[掘金拆解](https://juejin.cn/post/7677865401422921743)）。

---

## 2. teamai-cli 深度解析

### 2.1 基本档案

- 仓库：[Tencent/teamai-cli](https://github.com/Tencent/teamai-cli)，MIT 协议，TypeScript CLI（Node ≥ 20），npm 包 `teamai-cli`
- 时间线：2026-03-03 腾讯内网版 v0.1.0（git.woa.com）→ 2026-04-27 GitHub 建仓 → 2026-05-01 npm 首发 → 当前 0.23.0，**约每周一版，极高频迭代**
- 数据：约 4.3k stars / 290 forks；npm 月下载约 6.4k
- 支持工具矩阵（一等公民）：Claude Code、Codex、Cursor、CodeBuddy、Qoder、ZCode、OpenCode 等 10+ 种

### 2.2 定位：分发与治理层，而非内容本身

teamai-cli 解决的不是"单个 Agent 聪不聪明"，而是团队层的四类割裂：规范如何同步给每个 AI、已沉淀资产如何持续分发、多工具混用导致的工作方式割裂、个人会话中的纠正/失败/经验如何变成团队资产。

官方产品闭环：**Execute → Understand → Learn → Self-Improve**，对应三层架构：

| 层 | 要解决的问题 | 载体 |
|---|---|---|
| Team Execution | 让每个 Agent 按团队的方式工作 | init/pull/push，skills、rules、agents、hooks、MCP、env |
| Team Context (beta) | 让每个 Agent 理解整个团队 | recall 检索、learnings、代码知识图谱 teamwiki |
| Team Improvement (beta) | 让每次执行都成为团队能力积累 | 摩擦信号评分、sessions、digest 周报、dashboard |

核心名词：**Team Repo**（集中存放团队 Harness 与知识的 Git 仓）、**Harness**（官方对 skills+rules+agents+hooks+MCP+env 这套 Agent 运行环境的统称）、**Culture**（culture.md 团队文化，注入每个 Agent 的 CLAUDE.md/AGENTS.md）。

### 2.3 分发机制：经验像代码一样被维护

```
成员 A: teamai push → 自动建分支 + 创建 MR/PR
        → teamai.yaml 中 reviewers: 指定的评审人审批合并
成员 B/C/D: SessionStart hook 自动触发 teamai pull
        → 同一份资源被翻译成各 AI 工具的原生格式落盘
          （Cursor 的 .mdc、Codex 的 config.toml、OpenCode 的 plugin……）
```

分发控制维度：Projects（逻辑项目）、Roles（角色→namespace 映射）、Tags（标签订阅）、Sources（订阅其他团队/公共仓库）。"同一份资产、多工具原生格式"是手工维护和 git submodule 做不到的核心价值。

### 2.4 三处审批门

1. **资源分发门**：push → MR → reviewers 审批合并（治理靠 Git 原生机制）
2. **CI 知识提炼门**：`teamai ci extract-mr --mode comment` 把知识建议发为 MR 评论 → reviewer 用表情标记拒绝 → 合并后 `--mode write` 只把未被拒绝的建议写入知识库
3. **经验晋升门**：learning 晋升正式知识需满足成熟度阈值——置信度 ≥ 0.90、≥5 次 upvote、≥2 个不同贡献者、存活 ≥14 天（`teamai recall promote`）

### 2.5 经验自沉淀闭环（最具差异化的设计）

- **摩擦信号采集**：Stop hook 按"打断 AI、拒绝工具调用、AI 重试失败工具、纠偏词"等信号给 session 评分，达标则提示 `/teamai-share-learnings` 自动总结推到团队仓 `learnings/`（每 session 最多提示一次）
- **检索**：`teamai recall` 用 BM25 + 图谱增强；被查阅的知识自动 upvote，pull 时聚合投票
- **自净化**：`recall maintenance` 做剪枝/归档/置信度回写；dashboard 内置 KB Health 报告（覆盖率、高频召回、沉默条目）
- **代码知识图谱**：`teamai codebase` 用 tree-sitter AST 解析源码到 `teamwiki/`（组件、接口、跨仓依赖边），recall 命中时附源文件路径
- 设计思想来源：官方设计文档明示借鉴 Hindsight 的 retain/recall/reflect 三层记忆模型（[docs/designs/git-native-memory.md](https://github.com/Tencent/teamai-cli/blob/main/docs/designs/git-native-memory.md)）
- 隐私边界：上报只含计数/工具名，prompt 原文不出本机

注意：teamai-cli **本身不内置"需求→交付"的 SDLC 流程**——那由模板仓承载（[teamai-hub/template-backend](https://github.com/teamai-hub/template-backend) 的 `rules/common/development-workflow.md` 定义了 Research & Reuse → Plan First → TDD → Code Review → Pre-Review Checks 阶段门，内容改编自 everything-claude-code 和 mattpocock/skills，均 MIT）。

### 2.6 社区评价与已知风险

**正面**（[掘金](https://juejin.cn/post/7677865401422921743)、[CSDN](https://blog.csdn.net/lonelymanontheway/article/details/164339144)、[腾讯云官方长文](https://cloud.tencent.com/developer/article/2741235)）：一次配置多工具同步；Git 托管天然获得版本、评审、回滚；git 操作在隔离 worktree，不污染工作区；新人快速对齐。

**核心质疑**（掘金作者）：**"Git 只回答谁改了什么，不能证明规则有效"**——质量无保障，自动共享的错误经验会"全队一起错得更快"，分发效率越高错误扩散越快；建议"把分发和质量分开"，MR 不等于质量验证。GitHub Issues 另有真实痛点：#532 `teamai pull` 误删非其安装的本地 skill 目录（数据丢失 bug，仍 open）、#517 非 Git 用户上手难等。

**与 CodeBuddy 的关系**：无隶属声明。CodeBuddy/WorkBuddy 是它的下游被注入工具，二者是"分发层 ↔ 被分发 Agent"关系；项目深度适配腾讯生态（TGit/CNB、内部 CLI 变体）。

---

## 3. 同类框架全景：四大流派

### 3.1 Spec 驱动流派——规定"做什么 artifact、过什么门"

| 项目 | Stars | 一句话定位 | 核心机制 |
|---|---|---|---|
| [github/spec-kit](https://github.com/github/spec-kit) | **136k** | GitHub 官方 SDD 工具包，"代码服务于规格" | **Constitution（章程，九条不可变条款 + 项目自定义位）**；`/speckit.constitution → specify → plan → tasks → implement → converge`；模板四级覆盖链（overrides > presets > extensions > 核心），团队规范 = 章程 + 模板覆盖 + preset 强制门 |
| [Fission-AI/OpenSpec](https://github.com/Fission-AI/OpenSpec) | 68k | 轻量 SDD，写代码前先达成一致 | `specs/` = 当前事实源，`changes/` = 在途变更（proposal+design+tasks+**delta spec**：`## ADDED Requirements` / `#### Scenario:` WHEN...THEN 句式）；完成后 archive 合并回 specs；Stores（beta）支持跨仓共享 |
| [AWS Kiro](https://kiro.dev/docs/specs/) | IDE/CLI | Prompt → Requirements → Design → Tasks → Code | **三件套** requirements.md（**EARS 记法**：WHEN [事件] THE SYSTEM SHALL [行为]）/ design.md / tasks.md；每阶段人工批准门；steering 文件**四种注入模式**（always/fileMatch/manual/auto）；hooks 可阻断；**SMT 形式化规格分析**（把 spec 编码为逻辑式找矛盾，把问题浓缩成少量二选一抛给人） |
| [gotalab/cc-sdd](https://github.com/gotalab/cc-sdd) | 3.7k | Kiro 工作流的工具无关移植 | 17 个 Agent Skills 装到 8 种工具；阶段门批准契约后 `/kiro-impl` 自主长跑（TDD + 独立 reviewer） |
| [buildermethods/agent-os](https://github.com/buildermethods/agent-os) | 5.4k | 先沉淀 standards，再让 spec 对齐 | `/discover-standards` 从代码库**提炼"部落知识"成标准文件**；`standards/<domain>/` 按域拆分 + `index.yml` 索引；`/inject-standards` 按需注入；"Standards guide, not dictate" |
| [snarktank/ai-dev-tasks](https://github.com/snarktank/ai-dev-tasks) | 7.8k | 最小方案：两份 prompt 模板 | create-prd.md（先追问澄清）→ generate-tasks.md → 人工逐条驱动，每步一门；已约 10 个月未更新 |
| [Pimzino/spec-workflow-mcp](https://github.com/Pimzino/spec-workflow-mcp) | 4.3k | 把 SDD 流程做成 MCP 服务 | 最完整显式审批流（pending/approved/rejected/changes-requested，落盘 `approvals/`）+ 实时 Dashboard；作者已公告暂停维护 |

AWS/Kiro 官方的两句方法论定调（[Kiro 博客](https://kiro.dev/blog/deep-spec-analysis/)）：**"the prompt you give to the agent is the de-facto requirement now"**（含糊 prompt 会让 Pass@1 掉 20–40%）；**"规格正确性唯一的 oracle 是用户"**——工具的职责是自动化机械检查，把判断浓缩成具体问题抛回给人。

### 3.2 多智能体团队流派——规定"谁来做、谁来审"

| 项目 | Stars | 一句话定位 | 核心机制 |
|---|---|---|---|
| [bmad-code-org/BMAD-METHOD](https://github.com/bmad-code-org/BMAD-METHOD) | **53k** | 敏捷团队全流程映射为 AI agent 协作（头部项目） | V4 十角色（analyst/pm/architect/dev/qa/sm/po/ux + 2 个元角色）→ **v6 精简为 5 个 agent skill（QA/SM 砍掉并入 dev 技能）**；角色菜单码；**shard-doc 文档分片**解决上下文窗口；检查清单体系（pm/po/story-dod/architect-checklist）；QA gate 输出 PASS/CONCERNS/FAIL/WAIVED；QA agent YAML 写死"只允许改 story 文件的 QA Results 一节"——用 prompt 模拟组织权限；greenfield/brownfield 双路径 |
| [SuperClaude-Org/SuperClaude_Framework](https://github.com/SuperClaude-Org/SuperClaude_Framework) | 24k | Claude Code 的元编程配置框架 | 30 命令 × 20 persona × 7 行为模式 × 8 MCP 的组合矩阵；PLANNING.md/TASK.md/KNOWLEDGE.md 三约定文件；质量评分门槛（最低 0.6 / 目标 0.8） |
| [ruvnet/ruflo](https://github.com/ruvnet/ruflo)（原 claude-flow） | **72k** | 跨 harness 的 agent meta-harness | Queen-led 蜂群拓扑（Raft/Byzantine 共识）、GOAP A* 目标规划、100+ agent 类型；**SPARC 方法论**（Specification→Pseudocode→Architecture→Refinement→Completion，独立仓做成代码强制的证据门禁 + Ed25519 签名）；AgentDB 向量记忆 + ReasoningBank。**注意：宣传指标均为自述，社区对其营销口径有争议** |
| [eyaltoledano/claude-task-master](https://github.com/eyaltoledano/claude-task-master) | 28k | PRD → 任务依赖图 → 逐任务实现 | `parse-prd` 生成 tasks.json DAG；`next` 按依赖拓扑推荐；research 模式注入联网调研；模型分 main/research/fallback 三角色。**近 5 个月未更新** |
| [wshobson/agents](https://github.com/wshobson/agents) | 40k | 多 harness agentic 组件市场 | 94 插件/202 agents/183 skills/105 commands；**分层模型策略（Opus 给架构/安全/评审，Haiku 给轻量任务）= 用模型分层模拟组织资历分层**；插件隔离可组合 |
| [contains-studio/agents](https://github.com/contains-studio/agents) | 12k | 工作室自用 agent 集快照 | 按公司部门分 8 目录（含营销/增长等非研发角色），证明"软件团队"隐喻可扩展到整个公司 |
| [BloopAI/vibe-kanban](https://github.com/BloopAI/vibe-kanban) | 28k | 多编码 agent 的看板管理台 | issue → 派发 → 独立 worktree 执行 → 人审 diff（行内评论回喂 agent）→ AI 生成 PR 描述；支持 10+ agent 后端。**已宣布 sunsetting** |
| [gemini-cli-extensions/conductor](https://github.com/gemini-cli-extensions/conductor) | 3.7k | "主动型 PM"：强制 Context → Spec & Plan → Implement | Track = spec.md + plan.md + metadata.json；全部 context 文件在仓库 `conductor/` 目录当**受管制品**；conductor-revert 基于 git 历史回滚。证明"context 文件 + 阶段门禁"可以独立于多 agent 存在 |

### 3.3 分发/同步层——teamai-cli 所在的层

| 项目 | Stars | 机制 |
|---|---|---|
| **Tencent/teamai-cli** | 4.3k | 见第 2 节：Team Repo + push/pull + 多工具格式归一化 + 经验闭环 |
| [dyoshikawa/rulesync](https://github.com/dyoshikawa/rulesync) | 1.4k | `.rulesync/` 单一规则源，`rulesync generate` 生成 40+ 种工具原生配置（rules/mcp/commands/subagents/skills/hooks/permissions）；支持反向 import 与工具间 convert |
| [AGENTS.md 标准](https://agents.md/) | — | "给 agent 的 README"；Linux 基金会 Agentic AI Foundation 托管；超 6 万开源项目采用；Codex/Jules/Cursor/Windsurf/Copilot 等支持；Claude Code 不原生读，用 `@AGENTS.md` 导入共存 |
| IDE 企业控制台 | — | Cursor Team Rules（管理员创建、可 enforce、成员不可关闭，优先级 Team > Project > User）；通义灵码/百度 Comate 走"控制台统一分发 + 指标追踪 + 审计"路线 |

### 3.4 国内大厂产品形态（商业 IDE 侧）

- **阿里 Qoder**：`.qoder/rules/`（四种激活方式，兼容 AGENTS.md 且冲突时规则优先）；**Repo Wiki** 把仓库转化为结构化项目知识；Memory 明确"团队共享的规则应放项目本身，Memory 不承担团队共享"；客户案例（高德、UU 跑腿）是阿里侧最直接的方法论文档
- **腾讯 CodeBuddy**：三层 settings（个人 → 入库团队共享 → 本地 gitignore），子代理带 `memory: user/project/local` 作用域，机制与 Claude Code 高度对齐
- **字节 Trae**：SOLO 模式全流程自主；`.trae/rules/` 四态激活（Always/glob/智能判断/手动）
- **阿里心流 iFlow CLI**（[iflow-ai/iflow-cli](https://github.com/iflow-ai/iflow-cli)，5.1k stars）：IFLOW.md 分层记忆 + Sub Agents + **Open Market 一键安装 MCP/SubAgents/指令/workflow（"构建自己的 AI 团队"）**。**已于 2026-04-17 官宣停服**，仅作设计参考
- **[upstash/context7](https://github.com/upstash/context7)**（62k stars）：实时拉取版本化官方文档注入 prompt，解决 API 幻觉；可把"查最新文档"这条经验固化进 rules/skill

---

## 4. 通用方法论提炼（核心章节）

### M1. 四层漏斗工作流 + 常驻的"第 0 层"

所有框架的阶段划分收敛到同一条漏斗：**意图/需求 → 设计/计划 → 任务分解 → 自主实现**。spec-kit（specify/plan/tasks/implement）、Kiro（requirements/design/tasks/code）、OpenSpec（propose→apply）、cc-sdd、spec-workflow-mcp 完全一致；BMAD 四阶段（Analysis→Planning→Solutioning→Implementation）是同构的。

在此之上有一个**跨切面、常驻、不随 feature 消亡的"第 0 层"**，它才是"团队经验"的主载体：spec-kit 的 constitution、Kiro 的 steering、Agent OS 的 standards+product、teamai-cli 的 culture.md + rules、cc-sdd 的 settings/rules。差异只在粒度与刚性——spec-kit 与 OpenSpec 之争本质是"重流程多门 vs 轻流程自由迭代"。

### M2. 角色五件套 + 元角色，趋势是"少角色多技能"

几乎所有多 agent 框架收敛到五个核心角色：**Analyst（调研）→ PM（PRD/分解）→ Architect（方案）→ Developer（实现）→ QA/Reviewer（质量门）**。前端加 UX，元层加 Orchestrator/SM（BMAD orchestrator、ruflo Queen、contains-studio 的 studio-coach）——**元角色负责分派与切换，是多 agent 系统的标配**。

值得注意的演化方向：BMAD v6 把独立 QA/SM 角色砍掉、并入 dev 的技能集——实践上**"少角色 + 多技能"比"一人一岗"更适配 LLM 上下文经济学**。wshobson/agents 的模型分层策略（Opus 做架构与评审、Haiku 做轻任务）则证明**模型选择可以模拟组织内的资历分层**。

### M3. 工件落盘即交接，git 仓库是单一事实源

三种交接介质，且都落在仓库里：

1. **结构化文档链**（主流）：PRD → architecture → story/spec/plan 的 Markdown 链，下游角色读上游产出，**不依赖对话记忆**（"files persist beyond the conversation"）
2. **任务卡/图**：task-master 的 tasks.json（依赖 DAG + 状态机）、看板 issue 的状态流转
3. **共享内存**：仅 ruflo 的向量记忆重投入，其余框架的"记忆"本质还是文件

配套精巧设计：BMAD 的 shard-doc（大文档切片，dev 只加载相关片段）与"QA 只能写 story 的 QA Results 一节"（prompt 级权限边界）；vibe-kanban 与各类实现框架的 **worktree 隔离**；Conductor 基于 git 历史的 revert。

### M4. 阶段门设计：人只做"规格正确性的 oracle"

- **前置审批 + 实现无人值守**：Kiro/cc-sdd/spec-workflow-mcp 在 requirements/design/tasks 三处设硬性人工批准门，之后 `/kiro-impl` 自主长跑；teamai-cli 把审批嫁接到 Git 原生 MR 与 CI 评论
- **自动化机械检查**：Kiro 的 SMT 形式化找 spec 矛盾、spec-kit 的 `/speckit.analyze`、hooks 的 PreToolUse 硬阻断（exit code 2）、SPARC 的 Ed25519 证据签名
- 哲学共识（Kiro 官方）：**人不做机械检查，只做判断**；工具把歧义浓缩成少量具体问题抛给人。社区版表达：**"把一致性从概率问题变成配置问题"**（掘金《团队使用 Claude Code / Codex 的规范治理》）

### M5. 经验沉淀双通道：人工贡献 + 自动蒸馏

- **人工通道**：模板、检查清单、角色文件、rules——入库走 PR 评审（"团队宪法"模式：Issue 讨论 + Tech Lead 审核 + ✅必须/⚠️推荐/❌禁止 + P0-P2 分级）
- **自动通道**（teamai-cli 最完整）：摩擦信号 → session 评分 → 自动总结为 learning → **成熟度阈值晋升**（置信度/upvote/贡献者数/存活时间四重门槛）→ 检索被查阅自动 upvote → 定期 maintenance 剪枝
- **反向提炼**：Agent OS 的 `/discover-standards` 从现有代码库提炼"部落知识"；Qoder Repo Wiki / teamai teamwiki 用 AST 构建代码知识图谱
- 社区实践佐证：得物的"AI 错误案例库"、SuperClaude 的 ReflexionMemory

### M6. 质量机制：检查清单文化 + 独立评审 + 机械强制

- **检查清单把评审经验固化为勾选动作**：BMAD 的 pm-checklist/story-dod-checklist、spec-kit 的 `/speckit.checklist`、teamai-hub 模板的 Pre-Review Checks（CI 全绿才许请求评审）
- **独立评审（写查分离）**：cc-sdd 的独立 reviewer、teamai-hub 的 code-reviewer/database-reviewer/security-reviewer 三子代理、BMAD 的 QA gate 四态裁决
- **两层约束的分工**（Claude Code 官方与社区共识）：**规则引导行为，hooks 强制执行**——"指令文件是规范性约束，hooks 是机械性约束"
- 最弱一环：质量评分与证据链。多数框架的"质量"仍靠 prompt 约束，SPARC 的代码强制门禁 + 签名证据是最激进的尝试

### M7. 上下文经济学：分层注入与按条件激活

- **激活方式"四态"已成事实标准**（Cursor、Qoder、Trae、Windsurf 四家完全一致）：**Always（每次注入）/ Glob（按文件路径）/ Agent 判断（按 description）/ Manual（@引用）**
- **分层**（从稳到变、从强制到引导）：企业管理端/managed policy（强制）→ 全局个人层 → **项目层（入库共享，核心层）** → 路径/任务级 → 技能包 → 自动沉淀记忆层 → hooks 机械层
- **克制的教训**：得物把 CLAUDE.md 从 5000 字"百科全书"改为 200 字内"护栏"，两周迭代一次；Cursor 官方建议单规则 ≤500 行；索引 + 条件注入（Agent OS 的 index.yml、Kiro steering 的 frontmatter）解决"规范太多塞爆上下文"
- teamai-cli 的 recall（BM25 检索而非全量注入）代表另一条路：**经验按需召回，而非全部常驻**

---

## 5. 通用工具链提炼

| # | 工具链要素 | 事实标准/代表实现 | 说明 |
|---|---|---|---|
| T1 | **分发与安装** | git 仓库（teamai-cli/rulesync）、`/plugin marketplace add`、`npx skills add`、`uv/npm install` CLI | plugin marketplace 与 skills 格式正成为跨 harness 标准安装路径（BMAD、wshobson、Conductor 殊途同归） |
| T2 | **规则载体** | 纯 Markdown + YAML frontmatter；AGENTS.md 收敛为开放标准 | 全部工件入库、随 git 版本化；monorepo 嵌套、就近优先 |
| T3 | **可执行资产** | slash commands、Agent Skills（agentskills.io 开放标准）、workflow YAML、task 文件 | "task 是工作流不是参考资料"（BMAD）；斜杠命令已并入 skills（Claude Code） |
| T4 | **子代理格式** | `.claude/agents/*.md`（Markdown + frontmatter）为事实标准 | 独立上下文 + 工具/权限隔离，提交 git 共享 |
| T5 | **能力扩展** | MCP server 标配 | 进阶形态是"流程即 MCP"（spec-workflow-mcp 把审批流做成 MCP 工具）与"文档即 MCP"（context7） |
| T6 | **自动化钩子** | SessionStart（teamai 自动 pull）、PreToolUse（硬阻断）、PostToolUse（保存后 lint）、Stop（触发经验沉淀） | hooks 是把"规范"升级为"强制"的唯一手段 |
| T7 | **记忆与知识** | 文件记忆为主（KNOWLEDGE.md/ADR/learnings/）；向量+图谱为新（AgentDB、teamai recall+teamwiki）；KB 健康度量（dashboard） | 记忆设计参照 Hindsight retain/recall/reflect 模型 |
| T8 | **执行隔离与协作界面** | git worktree 隔离、看板（vibe-kanban）、Dashboard（spec-workflow-mcp/teamai）、MR 讨论区（extract-mr） | 编排层与管理界面是"平台"，方法论留给用户 |
| T9 | **CI/CD 与 Git 平台** | GitHub Actions / Coding CI 模板、GitLab（含自托管）、腾讯系 TGit/CNB | 知识提炼进 MR 流程（comment → 表情拒绝 → write）是新颖模式 |
| T10 | **模型分层** | main/research/fallback（task-master）、Opus 评审 / Haiku 轻任务（wshobson） | 成本与"资历"的双重映射；调研框架普遍把 research 模型单独配置 |

---

## 6. 总对比矩阵

| 项目 | 流派 | 核心抽象 | 团队经验载体 | 审批门 | 分发方式 | Stars / 状态 |
|---|---|---|---|---|---|---|
| teamai-cli | 分发治理层 | Team Repo / Harness / Learnings | skills+rules+agents+culture+learnings | MR 评审、CI 评论门、晋升阈值 | push/pull + 多工具归一化 | 4.3k / 周更，极活跃 |
| spec-kit | Spec 驱动 | Constitution / spec→plan→tasks | 章程 + 模板覆盖链 + preset | 软门（clarify/analyze/checklist） | CLI init + 模板 | 136k / 极活跃 |
| OpenSpec | Spec 驱动 | current truth + delta changes | specs/ + changes/archive/ | propose 后审 plan | npm CLI | 68k / 活跃 |
| Kiro | Spec 驱动（IDE） | 三件套 + EARS + steering | steering 四态注入 + hooks | 三阶段硬批准门 | IDE 内置 + MDM 推送 | 商业产品 |
| cc-sdd | Spec 驱动（移植） | Kiro 三件套 skills 化 | `.kiro/` settings/templates/rules | 阶段门批准契约 | npx 装 17 个 skills | 3.7k / 4 个月未更新 |
| Agent OS | Spec 驱动 + 规范注入 | standards 先行 + inject | `standards/<domain>/` + index.yml | plan mode 批准 | 脚本安装命令目录 | 5.4k / 活跃 |
| BMAD-METHOD | 多智能体团队 | 角色 + 流程 + 检查清单 | 角色 MD + workflow/skill + checklist | QA gate 四态 + 任务 elicit | plugin marketplace / npx skills | 53k / 极活跃 |
| SuperClaude | 多智能体（单 agent 增强） | 命令×人格×模式×MCP | PLANNING/TASK/KNOWLEDGE 三文件 | 质量评分门槛 | pipx install | 24k / 活跃 |
| ruflo | 蜂群编排 | swarm/GOAP/SPARC | AgentDB + ADR + ReasoningBank | SPARC 证据签名门禁 | npx + MCP | 72k / 极活跃（宣传口径存疑） |
| task-master | 任务编排 | PRD→tasks.json DAG | PRD + 任务图 | 无内建评审 | npm CLI / MCP | 28k / 近 5 月未更新 |
| wshobson/agents | 角色库 | 插件化角色市场 | 角色 MD + frontmatter | plugin-eval 打分 | plugin marketplace | 40k / 活跃 |
| vibe-kanban | 编排平台 | 看板 + worktree | 任务描述 | 人审 diff | npx | 28k / **sunsetting** |
| Conductor | Spec+角色中间态 | Track=spec+plan+metadata | `conductor/` 受管制 context | 人审 plan + review 阶段 | Gemini CLI 扩展 | 3.7k / 活跃 |
| rulesync | 分发层 | 单一规则源 → 40+ 工具 | `.rulesync/` | — | 生成器 | 1.4k / 活跃 |
| spec-workflow-mcp | 流程即 MCP | approval 状态机 | `.spec-workflow/` approvals | 最完整显式审批流 | npx MCP | 4.3k / **暂停维护** |

---

## 7. 共同软肋与选型风险

1. **质量无保障是最大质疑**：Git/MR 回答"谁改了什么"，不证明规则有效；自动共享的错误经验会"全队一起错得更快"。缓解：晋升阈值（teamai）、plugin-eval（wshobson）、独立复核关键规则、"把分发和质量分开"
2. **项目生命周期风险显著**：iFlow CLI 停服（2026-04）、vibe-kanban 宣布 sunsetting、task-master 近 5 月未更新、spec-workflow-mcp 暂停维护——编排层/平台类项目死亡率高于方法论类
3. **token 成本**：Conductor 官方明示代价；分层注入/按需召回是通用对策
4. **宣传指标缺第三方验证**：ruflo 最明显（72k stars 与营销口径争议并存）
5. **团队锁定**：teamai-cli 要求全团队同 Git 平台；多工具混用团队反而更需要分发层，形成依赖

---

## 8. 对本仓库（ch-pd-workflow）的对标启示

ch-pd-workflow 已有设计与主流方法论高度对齐的部分：角色五件套（ceo-office/pd-plan/prd/implement/ai-review）、阶段门（门②人审票清单）、写查分离（评审必须独立实例）、证据落盘（只认 evidence + commit hash）、worktree 隔离、模型分层（派发用低一档经济模型）、术语表 GLOSSARY。

从本次调研可借鉴的增量：

1. **第 0 层显性化**：GLOSSARY 已是雏形，可参考 spec-kit constitution / teamai culture.md，把"不可变条款 + 团队文化 + 红线"收敛为一份每个 skill 都注入的常驻文件
2. **经验自沉淀闭环**：现框架缺"执行后反哺"环节。teamai-cli 的 friction 信号 → learnings/ → 成熟度阈值晋升是最小可移植设计；至少可以加一个 `/retrospect` 类 skill（BMAD 的 bmad-retrospective 同构）
3. **EARS 记法强化 DoD**：issue-split 的「命令 + 期望输出」可叠加 Kiro 的 WHEN/SHALL 句式，让验收标准机器可查性更强
4. **hooks 补 prompt 约束之不足**：质量门命令目前靠 skill 自觉执行，可用 PreToolUse/Stop hook 做机械强制（如未跑 full 档不许标记 MR ready）
5. **归档机制**：spec/prd 完成后 archive 归档（OpenSpec 的 changes/archive/），让历史决策可被后续需求引用
6. **跨工具分发**（若有多工具需求）：rulesync 或 teamai-cli 可作分发层，当前 skills 结构改造成本低
7. **谨慎引入的部分**：蜂群并行编排（ruflo 式）证据链弱；看板平台类（vibe-kanban）生命周期风险高——流水线 + 阶段门禁已是被验证最充分的形态，与本仓库现状一致

---

## 附：主要信息来源

- teamai-cli：https://github.com/Tencent/teamai-cli 及其 docs/、https://github.com/teamai-hub/template-backend 、https://juejin.cn/post/7677865401422921743 、https://blog.csdn.net/lonelymanontheway/article/details/164339144 、https://cloud.tencent.com/developer/article/2741235
- Spec 驱动：https://github.com/github/spec-kit 、https://github.com/Fission-AI/OpenSpec 、https://kiro.dev/docs/specs/ 、https://kiro.dev/blog/deep-spec-analysis/ 、https://github.com/gotalab/cc-sdd 、https://github.com/buildermethods/agent-os 、https://github.com/snarktank/ai-dev-tasks 、https://github.com/Pimzino/spec-workflow-mcp
- 多智能体团队：https://github.com/bmad-code-org/BMAD-METHOD 、https://github.com/SuperClaude-Org/SuperClaude_Framework 、https://github.com/ruvnet/ruflo 、https://github.com/ruvnet/sparc 、https://github.com/eyaltoledano/claude-task-master 、https://github.com/wshobson/agents 、https://github.com/contains-studio/agents 、https://github.com/BloopAI/vibe-kanban 、https://github.com/gemini-cli-extensions/conductor
- 分发与上下文工程：https://agents.md/ 、https://github.com/dyoshikawa/rulesync 、https://cursor.com/docs/context/rules 、https://code.claude.com/docs/en/memory 、https://github.com/upstash/context7
- 国内：https://github.com/iflow-ai/iflow-cli 、https://docs.qoder.com/llms.txt 、https://cnb.cool/codebuddy/codebuddy-code/-/tree/main/docs 、https://docs.trae.ai 、https://help.aliyun.com/zh/lingma/extensions-management 、https://comate.baidu.com/zh/enterprise 、掘金三篇（7651056986951712819 / 7599828354514567208 / 7602454700503646249）
