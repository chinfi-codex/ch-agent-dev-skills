---
name: why
version: 0.2.0
description: |
  Investigate historical reasons for code, interfaces, thresholds, or design
  decisions. Start with targeted evidence and expand when uncertainty affects
  the decision. Separate facts, inference, and unknowns; use /how for mechanics.
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

## Campaign 身份与产物连接

- `feature-module / feature-slug` 是稳定业务归属；`campaign-id` 是该需求下的一次独立交付，不替代或改写 feature-slug。新交付从当前入口阶段按需创建 campaign；同一战役的回修、重验、重新冻结仍属于原 campaign。
- 先用用户给定路径、票、候选或上游引用确定 campaign；有多个候选且无法消歧才问人。已有明确任务范围时可据其摘要命名，不要求再确认目录。不同交付不得因属于同一需求而共用 campaign。
- 产品产物根为 `docs/features/<feature-module>/<feature-slug>/campaigns/<campaign-id>/`；开发产物根为 `dev/features/<feature-module>/<feature-slug>/campaigns/<campaign-id>/`。仅到达相应阶段时创建目录/文件，不补造未经历的 PRD、票或报告。
- 标准路径可从 brief → PRD → tech-spec → 票组 → 验收；小变更可从 change-request → tech-spec 进入。共享基线 PRD、研究与历史成果通过明确路径引用，不复制、不改其归属。
- 每份阶段文档记录 `feature_module`、`feature_slug`、`campaign_id` 与 `upstream` 的具体路径；票用 `feature-slug`、`campaign-id`、`tech-spec`。已有引用优先于目录搜索；未绑定输入时只在当前 campaign 内按类型发现，多个版本须确定明确的修订/取代关系，同日或并行文档不能靠“最新”猜。冻结消费使用路径与哈希，不能悄悄切换上游。
- `acceptance-plan.yaml` 是**每个 campaign 一份**的固定入口，`tech_spec` 指向本战役方案；计划 documents 用仓库相对路径 + SHA256 显式绑定来源。不得回退父需求目录、轮换占位或把其他 campaign 的票/计划纳入本次交付。
- 所有接受 `--feature-dir` 的开发命令，新流程传当前 **campaign 开发目录**（保留参数名兼容旧工具）。`issues/impl/integration/decisions/progress.jsonl/unattended.md` 均以该目录为根；票号只需在该 campaign 内唯一。命令返回的分支和工作目录为准，不自行按票号拼路径。
- 默认集成分支为 `feat/<module>/<feature>/<campaign>`，票分支追加 `-<issue-id>`；工作目录为 `.worktrees/<module>/<feature>/<campaign>/<issue-id>/`。配置的 integration-branch 模板中 `<feature-slug>` 在新流程展开为上述完整 `<module>/<feature>/<campaign>` 分支键，稳定票字段不变。分支键中的空格、点和 Git 特殊符号按 UTF-8 百分号编码，中文与字母数字保留，避免有空格的模块名导致非法分支；路径以命令返回值为准。
- `integration/[round/]` 只区分本 campaign 的候选执行轮次；评审 receipt 的 round 是评审次数。两者均不是 campaign 身份，均不得修改 feature-slug 或切换集成分支。作用域完整性、决策阻塞、授权、复盘与清理只消费当前 campaign。
- 兼容旧产物：无 campaign 子目录的历史 feature 根仍按旧入口读写；历史 `--round` 分支约定保持。不得自动搬迁、改写冻结 context/digest、复制成新真源或把它们算成新 campaign 的票。新战役一律使用 campaign 子目录；既有在途战役继续原路径，迁移需单独建立显式映射并核对历史引用。
- 机器字段中的 `feature_dir` / `--feature-dir` 表示交付作用域目录；candidate/receipt/progress 的 `feature` 表示可唯一寻址的作用域键 `module/feature/campaigns/campaign`（旧产物仍为 `module/feature`）。它们不是票的稳定 `feature-slug`。human-decision 保持 `scope.feature=module/feature`，新增 `scope.campaign=campaign-id`；其 CLI `--feature` 使用完整作用域键。

# /why

你负责回答“**是什么力量让代码/设计成为今天这样**”。`/how` 回答当前机制；`/why` 查历史动机、外部约束、事故、业务依据、数据阈值和取舍。

核心纪律：**代码能证明机制，不能证明自己的动机。**先读 `references/epistemics.md` 并全程执行；宁可留下具体 Unknown，也不写一个听起来完整的故事。

## 权限、模型与来源边界

- 除调用方明确要求的 `dev/features/<feature-module>/<feature-slug>/campaigns/<campaign-id>/research/`（尚未进入具体交付的调查可用需求根的 `research/`，后续显式引用） 报告外，不写本地或外部状态；不提交、不评论 PR/ticket/chat、不运行写 SQL。
- investigator 和 synthesizer 使用 general-purpose **agent mode，而非会剥离 MCP 的 readonly/Ask mode**。这是为了保留证据工具和引用抽查；提示词仍禁止一切写入。宿主能同时保留 MCP 和只读权限时优先只读。
- 默认继承当前宿主模型。显式模型被拒绝时回退继承并披露；有实时列表时可选同族最近有效项。不得复制 pstack slug、跨宿主套模型或静默换族。
- 仅在需要委派时使用子代理；无子代理能力时主会话查证，说明缺少独立核查的限制，不模拟多角色流程。
- 本地无 PR 路径下，Git、本地 issue/change-request、PRD/tech-spec/review/prototype verdict 都是正式一手来源；`gh`、外部 PR 和外部 MCP 只在真实存在时使用，不是开工前提。

## Step 1：确定 Target 与 Question

target 可以是代码段、接口、模式、阈值、ARCH-CHECK 组 / 规则 ID、feature 或已命名决策。question 可以是设计动机、取舍、回归、事故、失效约束或阈值来源。

指代含糊时结合当前文件、近期改动和对话给出最短范围解释并继续，让用户可纠正。用户问题中自带的假设只是待验证 hypothesis，不是结论。

## Step 2：按风险选择调查深度

- **轻量（默认）**：主会话定位目标代码、相关测试/注释，查询针对性的 Git 历史及直接关联的 ticket/spec。明确原因已有证据时直接回答，不必启动代理或填写七类来源表。
- **标准**：关键原因不明、涉及跨模块取舍时，沿现有证据线索扩展相关来源。一个调查者可以追踪 Git → ticket → spec 的完整证据链；可委派独立问题，不按来源机械拆分。
- **深度**：高风险公共契约、迁移、事故调查、重大历史证据冲突，或用户明确要求全面调查。评估下列七类来源的相关性，按必要角度并行调查，并独立核查承重结论；评估不等于七类都要查询。

建立与问题相称的 code anchor：目标文件/符号、相关记录和关键提交。按需使用 `git blame -L`、有范围的 `git log`、`git log -S/-G` 定位形成当前行为的提交，再用 `git show` 检查相关补丁与说明；不默认扫描文件全历史补丁。最近提交只是线索，不自动代表最初原因。无 Git 历史时披露限制，继续查询其他一手记录。

## Step 3：沿证据扩展，按问题停止

七类来源是检索菜单。优先 Git 和直接关联的本地文档；仅当来源可能回答关键未决问题时扩展，不因存在 MCP 就查询。按需读取 `references/source-playbook.md` 中对应 playbook。

| 类别 | 本地正式来源 | 外部来源示例 | 最擅长回答 |
| --- | --- | --- | --- |
| Source control | Git、代码注释、测试、ADR、CHANGELOG | 真实存在的 PR/review | 实现当时的讨论与变更轨迹 |
| Issue / ticket | 本地 issue、change-request、进度/决策记录 | Linear、Jira、GitHub Issues、GitLab | 产品、业务、合规 forcing function |
| Long-form documents | PRD、tech-spec、feature brief、review、prototype verdict、ADR | Notion、Confluence、Lark Doc | 设计理由、替代方案、共识 |
| Real-time team chat | 无默认本地来源 | Lark、Slack、Discord、Teams | 未落文档的实时讨论 |
| Infrastructure observability | 仓内 runbook/incident 记录 | Datadog、Grafana、New Relic、Splunk | 超时、重试、限流等运行时压力 |
| Error / exception tracking | 仓内错误报告和复盘 | Sentry、Rollbar、Bugsnag | 防御代码对应的异常轨迹 |
| Product analytics warehouse | 仓内数据报告 | Databricks、Snowflake、BigQuery、ClickHouse | 阈值、实验、使用量和迁移依据 |


- 防御性行为只有在事故线索或风险需要时读取 `references/sources/incident-postmortem.md`，不因存在 null guard 就开启事故调查。
- 委派时读取 `references/investigator-prompt.md`，传入原始问题、共同 anchor、负责的问题与来源范围；共享已查证结果避免重复调查。代理只读。
- 深度调查或关键证据存在争议时，使用 `references/synthesizer-prompt.md` 独立核查原始引用；其余由主会话综合。不得通过改写提升置信度或隐藏冲突。
- 记录实际查询过的来源、关键对象和影响结论的空结果/失败。未查询不等于无事实；只披露与当前问题相关的来源缺口。深度调查额外记录七类相关性及选择/跳过理由。
- **停止条件**：影响本次决策的关键问题已有证据、相关冲突已处理、剩余未知及影响已说明。答案明确后不为完成来源清单扩查；找不到历史原因可给出有搜索边界的 Unknown，不无限追查，也不将未解决的关键风险说成已解决。

## Step 4：输出与规划约束

轻量回答保留结论、证据、置信度和未知项。较大调查按需使用 The Question / The Code in Question / What We Found / What We Can Reasonably Infer / Competing Hypotheses / What We Don't Know / Sources Consulted / Confidence Summary；没有竞争假设或推断时不凑章节。

- 全程区分 Direct / Supported / Inferred / Speculative / Unknown；五级是判断纪律，不要求每次填满五栏。
- 修改前置调查按适用性提取 **Preserve / Change / Avoid / Risk**，每条回链证据和置信度。Unknown 只能进入 Risk/待验证，不自动变成 Preserve；历史动机是否仍适用也须单独判断。
- 默认对话返回或由 tech-spec 将必要结论写入 2.1。仅提供 feature-module/slug 不自动归档；调用方明确要求归档（含复杂、可复用调查）时，按 `shared/templates/why-report.md` 写入 `dev/features/<feature-module>/<feature-slug>/campaigns/<campaign-id>/research/<topic>-why-YYYY-MM-DD.md`。
- 复用相同 target/question 的历史证据，只检查相关新增记录、反证及约束是否过期；没有变化不重复搜索所有来源。

## 常见失败

- 用当前代码行为替历史作者补动机，或只找支持用户猜测的证据。
- 只看最近提交，漏掉形成当前行为的更早决策。
- 将空搜索当反证，将间接线索升为 Direct，将 Unknown 写成既定约束。
- 因工具可用扩大范围，或为了固定章节、代理数量、来源覆盖继续调查。
