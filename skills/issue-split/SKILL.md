---
name: issue-split
version: 0.7.0
default-mode: DOC_MODE
default-mode-strict: true
implementation-mode: IMPLEMENT_MODE
implementation-mode-requires-explicit-user-approval: true
implementation-approval-phrases:
  - 批准写代码
  - go implement
  - 开始实现
description: |
  Documentation-first ticket splitting skill. Turns a confirmed tech spec
  into tracer-bullet tickets with blocking edges, executable DoD commands,
  declared-scope and ticket-max-files / ticket-max-loc granularity red
  lines, gets gate-2 human confirmation, publishes — then the main session
  auto-dispatches each ticket as a fresh host-agent conversation and
  supervises until merge with no further human gates. Every ticket uses
  DoD + fast verification; feature / main CI full is the suite-level safety
  net. Default mode is DOC_MODE.
allowed-tools:
  - Read
  - Write
  - Edit
  - Grep
  - Glob
  - Bash
  - AskUserQuestion
  - Task
---
<!-- AUTO-GENERATED from SKILL.md.tmpl -->
<!-- do not edit directly -->

## 文档模式

- 默认进入 `DOC_MODE`；只有用户明确说出 `批准写代码`、`go implement`、`开始实现`，才能切到 `IMPLEMENT_MODE`；「顺手改一下」「直接做了吧」不算批准
- 用户未使用明确批准词时，用一句大白话重申：目前只做文档、不动代码；要我直接改代码的话，请说「批准写代码」。不说「当前处于 DOC_MODE」这类机器话

### `DOC_MODE`

- 只允许读代码、读文档、写文档；只允许写入 `./docs/**`、`specs/**`、`ADR/**`、`*.md`、`*.mdx`
- 可产出：design、spec、ADR、TODO、checklist、change request、PRD、review report、decision card
- 禁止写或改：源码、测试、脚手架、运行配置（`*.py`、`*.js`、`*.ts`、`*.tsx`、`tests/**`、`src/**`、`app/**`、`package.json`、`pyproject.toml`、`requirements.txt`）
- 禁止执行实现导向命令：`python`、`pytest`、`node`、`npm`、`bun`、`cargo`、`go test`、build scripts

### 停止与批准

产出 design / spec / doc 后：总结「若获批将实现什么」但不实现 → 明确请求批准 → 停止等待。未获批准，不得写任何源码、测试、脚手架、配置变更。

本 skill 可额外执行只读文档校验：`feature_closeout.py validate-plan`、`ticket_context.py check-plan`，以及规则存在时的 `arch_check.py validate` / `render check`。它们只验证功能验收计划、整组票计划与规则结构，不属于实现或测试执行；其他实现导向命令限制不变。

## 术语与数据口径（GLOSSARY）

项目级唯一术语文件：`./docs/GLOSSARY.md`（结构见 skill 包内 `shared/templates/glossary.md`；懒创建：第一个术语确定时按该结构创建；存在才读，不存在不阻塞，先于其他文档读）。

### 使用纪律

- 输出文档与对话中必须使用表内**标准术语**；用户使用别名或口语说法时，用标准词复述一遍再继续。
- 用户命中某术语的「拒绝词」时：亮出标准词，说明该说法已被淘汰及原因，再继续。拒绝词与别名不同：别名是可接受的口语说法（用于匹配），拒绝词是被明确淘汰、会引发歧义的说法。
- 用户用法与表内定义冲突时，立即指出并要求裁决（「术语表中 X 定义为 A，你的用法是 B，以哪个为准？」）。
- 引用任何指标必须带口径（定义 / 统计窗口 / 数据来源），且与 GLOSSARY 一致；不一致时先裁决再写。

### 回写纪律

- 讨论中确定的新术语或新口径**立即写入**，不批量积压；明确淘汰的说法立即记入「拒绝词」列，防止回潮。
- 准入测试：只收项目专属术语与数据口径；通用编程概念、行业通行词不收。
- 原地追加更新，不加日期后缀，不新建版本文件；表内只放定义与口径，方案、决策理由、实现细节一律不进。
- 定义按「它是什么」写，不按「它做什么」写——词汇表，不是设计文档。
- 「feature-module 归档」节由 `/pd-plan` 在用户确认模块新建或归属后立即登记（模块名、一句话说明、成员 `feature-slug`），成员列与 `./docs/features/` 实际目录保持一致；其他技能只读该节，不擅自增改。
- 术语表的「别名/口语说法」「关联 feature-slug」列与「feature-module 归档」节是 `feature-module` / `feature-slug` 匹配的输入之一；命中多个别名时按 `AMBIGUOUS_MATCH` 单问题裁决。

## 提问格式与对人表达

提问分两种形态：**拷打轮次**（需求澄清，按「拷打规则（Grilling）」，一轮可问多个相互独立的问题）与**阻塞型单点确认**（方向批准、术语冲突、范围裁决、是否进入下一阶段等，一次只问一个）。本节规则同时约束普通文本和宿主原生提问组件的所有可见字段。

先把未决问题归类为三种之一：

- `可假设继续`：对当前判断影响较小——带默认假设继续，并在输出中显式写出假设
- `必须提问后继续`：影响核心判断、关键前提、模式选择、优先级、规则边界或最终结论，不能绕过——先发问再继续，不用「可以先假设」绕过，也不沉入「待确认项」；使用当前宿主的原生提问能力，不自行脑补答案
- `仅记录为低优先级风险`：不影响当前判断——暂记为风险或待确认项

开发流程中被派发的实现 / 评审会话不得直接问人：它们只返回结构化决策请求，由主会话按「Human Decision 协议」统一呈现、记录回答和路由。无人值守时不调用提问组件、不等待回答。

### 每次提问的内容

1. **先说人话背景**：用 2–4 句大白话讲清在做什么、进行到哪一步、现在卡在哪里；不堆术语，不贴内部状态和原始日志
2. **说透决策点**：要用户定的到底是什么，一句话说清楚；涉及改动时讲清改什么、哪些模块或人会受影响、什么行为会变化
3. **说明为什么必须问**：指出影响哪一个核心判断，不问清会导致什么误判或返工
4. **给推荐方向**：先给推荐与理由，显式说明仍等用户拍板，不伪装成结论
5. **给可执行选项**：每个选项说清选后会做什么、主要代价与风险；推荐项排第一，保守替代、暂停或延后按实际情况排列
6. **单点确认一次只问一个**，不合并多个决策点（拷打轮次不受此限）

### 原生提问组件字段契约

`AskUserQuestion` 及宿主等价组件必须按下列规则生成；开发阻塞型问题从对应 human-decision 决策包确定性投影，不另写第二套话术：

| 字段 | 契约 |
| --- | --- |
| `header` | 用户能理解的业务短标题，不超过宿主限制（通常 12 个字符），如「订单重试」「合并冲突」；禁止状态名、内部角色和协议词 |
| `question` | 一次只问一个决策；依次说清背景、卡点、不能自行决定的原因和需要选择什么；不附路径、字段堆栈、命令或退出码 |
| `options` | 单决策使用 `multiSelect: false`；2–4 个互斥且可执行的选项，推荐项排第一并在标签末尾写「（推荐）」 |
| `options[].label` | 直接写采取的动作，如「增加防重复处理（推荐）」；不要只写「方案 A」「re-split」或「 BLOCKER 回修」 |
| `options[].description` | 说清选后的动作、主要代价、影响面和风险；必要时说明哪些工作暂停、哪些可继续；不贴原始输出 |
| 自由文本 | 宿主自动提供「其他」时不要重复添加；自由文本含义不明确时继续澄清，不猜测为某个选项 |

原生选项组件中**不再要求用户「回 A 或 B」**，直接选择即可。纯文本降级时才为同一组选项显示稳定编号，并说明回复方式。技术路径、证据与日志放在组件外的「想细看」或决策包证据字段，不塞进主问题。

取消、关闭问题卡、Escape 和工具超时都只表示**尚未回答**，不表示选择推荐项，也不表示取消票；只有用户明确选择「取消 / 暂停这张票」才可执行对应受控路由。

### 示例

```yaml
header: 合并冲突
question: 这票和另一票同时修改了订单关闭规则。自动合并会覆盖其中一边，所以我停下了。本次应该以哪套规则为准？
options:
  - label: 采用本次已确认规则（推荐）
    description: 按本票方案合并；另一票需要基于新结果重新核对。
  - label: 保留另一票规则
    description: 本票撤回冲突部分，再重新验证剩余功能。
  - label: 先整理两边差异
    description: 暂不合并，先列出行为差异，确认后再修改。
```

### 禁区

- 内部词不说给用户：拷打 / frontier / 门② / BLOCKER / 升档 / needs-human / 状态码 / 字段名堆叠——协议词只做内部判定，对人一律换成日常说法
- 不用「回显」「输出完成状态」这类机器动作描述对人说话；报告与状态表达按「完成状态协议 · 对人表达」执行
- 不用模板腔占位符对用户说话（如直接贴 `<feature-module>/<feature-slug>`），换成中文名称与自然句
- 不用 commit hash、文件清单、测试日志或配置字段代替「发生了什么、为什么需要你定、每个选择有什么影响」

风格：问题短、具体、直击判断核心，不为礼貌加缓冲；关键变量缺失先提问，不输出大段结论；回答仍抽象就继续追问，直到足以支撑判断。

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

## Dev Artifact 路径约定

`./docs/` 树（产品阶段）延伸出 `./dev/` 树（开发阶段），两棵树由同一 `feature-module` / `feature-slug` 贯通：

```text
./dev/
  agents-config.md
  features/
    <feature-module>/
      <feature-slug>/
      <feature-summary>-tech-spec-YYYY-MM-DD.md
      acceptance-plan.yaml         # 功能验收计划唯一真源：AC / 执行方式 / 最终判定 / 人工原因
      research/                   # How 现状机制与 Why 历史动机调查；tech-spec 的 dated 证据输入
        <topic>-how-YYYY-MM-DD.md
        <topic>-why-YYYY-MM-DD.md
      issues/
        issue-001-<短名>.md
      impl/
        issue-001/
          ticket-context.json          # 开工冻结的 base / target / scope / 输入哈希
          impl-log-issue-001.md        # 含票级覆盖矩阵，供 feature conformance 汇总
          raw/<generation>/            # 每次 implementer 验证独立代际；成功新代际使前代 superseded
            artifact-generation-manifest.json  # 本代 candidate / supersedes / 全部产物哈希
            verification-implementer.json
            evidence-issue-001.md        # 由本代 verification JSON 生成，不手写通过结论
            logs/                     # 原始命令输出与哈希索引
      mr/
        <feature-summary>-mr-issue-001-YYYY-MM-DD.md
      integration/                # 需求收口（feature-closeout: local-human）产物；复用旧 feature 目录的轮次写 integration/<round>/，round 名 = 票 feature-slug = 集成分支名（建议 <feature-slug>-<YYYY-MM-DD>），结构见 shared/templates/feature-closeout.md
        candidate.json            #   冻结候选、票组、merged commit 与计划哈希（freeze 写）
        acceptance-plan.json      #   冻结时的计划快照
        feature-review.md         #   整需求独立评审报告（main_head..candidate）
        feature-review-receipt.json  # 候选级评审回执：diff / plan / 报告 / 全部 implementer 身份
        candidate-verification.json  # 逐票冻结 DoD+fast 在候选上的 supervisor 复跑汇总
        evidence.md               #   自动证据头
        business-results.json     #   每条 AC 的实际执行、AI 判定 + 人工结果
        acceptance-report.md      #   合并前功能验收报告（report 生成）
        confirmation.md           #   人确认原话 + 候选 tree
        conformance-feature.md    #   feature 级覆盖核对
        verify-log.md             #   候选变更与回修轨迹
        raw/<generation>/
          artifact-generation-manifest.json
          <issue>/verification-supervisor.json  # 候选复跑证据，不覆盖票级 impl
          logs/  screenshots/
      unattended.md               # 无人值守授权边界（人离场时才产生）
      decisions/                  # human-decision 决策包唯一真源，主会话与恢复共用
        <decision-id>.json
      prototypes/
        <短名>/
          <原型文件（一次性）>
          <短名>-verdict-YYYY-MM-DD.md
  .salvage/                       # worktree 清场抢救包（见「派发协议」清场序列）
```

路径使用规则：

- `agents-config.md` 是 dev 阶段唯一配置文件（tracker / 主分支 / 质量门 / 红线 / 仓库等级），由 `/setup-dev` 产出，人可手工修订
- `issues/` 票文件是 issue 的**本地真源**；GitLab 等外部 tracker 只是发布面，票文件内同步记录 IID / URL
- `prototypes/` 归档 `/prototype` 的一次性原型与 verdict：原型代码只进此目录，不进产品源码；verdict 是 /pd-plan、/prd、/tech-spec 的上游输入
- `research/` 归档 `/how` 的当前机制解释和 `/why` 的历史证据调查；How 绑定 source HEAD，Why 保留七类来源覆盖和置信度。tech-spec 把采纳结论、引用和约束收进正式方案，issue/implement 仍以冻结 spec 为契约，不直接追随一份可漂移的研究文件
- `unattended.md` 是无人值守授权记录；`decisions/*.json` 是需要人裁决的问题、回答和路由状态的唯一真源。`progress.jsonl` 只保存不可变事件历史，不再使用 `decisions.md` / `progress.md` 自由文本双真源
- dev/ 产物（票文件 / impl-log / evidence / 评审报告及其引用的捕获物）一律写**主工作区**的 dev/ 树——worktree 是独立 checkout，写进它的 dev/ 清场即丢；worktree 内只放代码与 `.logs/` 等临时产物。**worktree 内不得出现 dev/ 实体副本**（每票一份完整副本 = 磁盘随票线性膨胀，清场还可能丢真源）：建 worktree 后立即把 `<worktree>/dev` 软链到主工作区 dev/（仓库根 `.worktrees/<issue-id>/` 下用相对目标 `../../dev`；宿主自管沙箱改用主工作区 dev/ 的绝对路径），此后 worktree 内对 `./dev/` 的读写自动落主工作区；软链建不成即停止，当前仅支持具备 POSIX 文件锁与软链能力的 macOS / Linux；不得复制 dev/ 实体目录，也不把主工作区绝对路径回退当作等效能力。架构契约只编辑 `docs/ARCH-CHECK.json`（11 节 `architecture.overview` + 规则）；`docs/ARCHITECTURE.md` 只能由 `arch_check.py render write` 确定性重生成并随契约提交，任何开发阶段均不得手工编辑。
- worktree 统一放仓库根 `.worktrees/<issue-id>/` 并加入 `.gitignore`；宿主自管会话沙箱（如 Codex）时以沙箱为隔离、不嵌套开此目录，但分支名仍按 `feat/<feature-slug>-<issue-id>`——清场一律按分支名发现（见「派发协议」），不按路径猜
- 命名与版本规则沿用 `./docs/` 树约定；读取模式匹配：`*-tech-spec-*`、`issues/issue-*`、`*-mr-issue-*`、`prototypes/*-verdict-*`、`research/*-how-*`、`research/*-why-*`

## 留存与体积控制（dev/ 防膨胀）

dev/ 通常不入库又无 Git 兜底，体积失控主因是迭代中间产物全量留存。规则：

- **产物按代际保留**：raw / OCR / 截图必须登记进 `artifact-generation-manifest.json`，绑定 candidate tree、generation、路径和 SHA256。新一轮通过且 evidence / business-results 已切换到新代际后，旧代际才成为 GC 候选；当前代际、最后成功代际及所有被引用证据始终保留。没有 manifest 的存量文件不按 mtime 猜代际
- **可重下载的缓存不进 dev/**：安装包 / wheelhouse / keyring / 依赖缓存等官网可重下的内容，临时存放用完即删；确需留存只记版本号与获取命令
- **merge 中间物即合即删**：`*.ours` / `*.theirs` / `*.orig` / merge-* 工作目录在合并提交后删除——git 历史可取回
- **删除只能走 `close-and-clean` 预检与确认**：可再下载、字节级相同或明确属于已失效代际的内容才可删除；删除前后核对 Markdown / JSON 引用，最终 evidence、评审、确认记录与被引用捕获物不得为了压体积删除
- 票合入集成分支后保留 worktree / branch，直到 feature `finalize` 统一 `merged → done` 并逐票 close-and-clean；历史 `done` 票可单独走 close-and-clean。`.salvage` 仅作为可审计恢复包，按保留策略另行预检清理，不能视为永久存储

## Dev 配置（agents-config v2）

dev 阶段开始工作前，先用 `agents_config.py validate --config ./dev/agents-config.md` 读取并严格校验配置：文件不存在时提示先运行 `/setup-dev`；旧 schema、未知字段、旧 scoped/full、旧 redlines / mechanical-refresh / main-drift-ignore 或非法模式组合一律停止。不得自行改配置放宽约束；配置与现实不符时按 human-decision 协议请人裁决。A 类仓禁止 `/implement`，只允许只读辅助。

必要配置与执行纪律：

- `tracker / main-branch / repo-level`：本地或 GitLab 票、主分支与仓库等级。票的唯一目标分支由 `ticket_context.target_branch` 在 `delivery start` 时冻结：显式 target-branch > local-human 集成分支 > main-branch；目标不存在即停，不回退 main。
- `quality-gate.fast`：票级唯一快速质量门。每个实现、回修和冲突轮只执行本票 DoD + fast，统一由 `verify_issue.py` 实际运行并生成结构化证据；feature 冻结后由 `verify_issue.py run-candidate` 在候选上统一复跑一次。不得按文件数、LOC、影响面或风险改跑其他票级套件，也不设置票级 scoped / full。
- `host-budget`：只用于 local-human 需求级关联选择集与单项失败归因；fast 不受槽位限制。机器级 `~/.cache/agent-dev/slots/budget.json` 是共享预算真源，本文件只是镜像；CI full 使用判读机自己的预算。
- `verify-policy.ticket-max-files / ticket-max-loc`：拆票粒度上限，默认 10 个生产文件 / 400 LOC。预估超限必须再拆，实测超限必须 re-split 或创建 human-decision；不得用它选择测试范围或模型档位。
- `verify-policy.scope: strict`：开工后 declared-scope 冻结。所有 changed paths（不只生产文件）都参与 scope / 红线 / 生成物核对；越界必须退回重新确认票并生成新 context，登记漂移不再放行。生产集合只用于文件数与 LOC 统计。
- `verify-policy.deny`：绝对禁止路径；新增、修改、删除、重命名任一命中即 FAIL，登记不能豁免。
- `verify-policy.test-change`：TDD 新增测试必须有 runner 的断言见红记录；既有测试修改必须逐条映射真实 DoD / tech-spec 锚点、说明冲突与范围，票级核对后在 feature 级评审统一复核。删除测试默认拒绝，无映射或放宽无关断言均失败。
- `verify-policy.generated-refresh`：每条派生物有稳定 ID、输出 glob 和唯一生成命令。流程核对在独立临时工作区执行生成器并比较 tree；不能只登记“机械刷新”或额外改动其他字段。
- `gate-scope`：功能类 DoD / fast 才进入交付通过分母；perf / display 在最终候选执行一次并列人工确认，不因噪声反复重跑。缺已声明外部依赖的用例显式 env skip，不算通过也不算失败。
- `attribution`：`quarantine` 入册必须有 base 同签名稳定失败、owner、去向和首录日期；清单内显式 skip，清单外失败只对单项做 base 对照。`two-sided` 每条失败双侧同命令。未执行项不得记 quarantine 或通过；超期隔离项必须报告。
- **full 只有 CI 入口**：本机、票 worktree、集成 worktree与监督循环均不得执行 full。daily regression 只能由同一 CI workflow 的 schedule / dispatch 触发；CI 不可用时保持未验证，不降级本机补跑。
- `feature-closeout.mode`：
  - `server-enforced`：必须由 `ci_proof.py` 在线证明 workflow、run、artifact、分支保护、required checks 和 expected-SHA 能力；任一未知即停止。
  - `cooperative-ci`：CI API 可用但无完整服务端保护时显式选择；只经 `delivery.py` 协作合并，报告必须注明“无服务端强制”，不得从 server-enforced 自动降级。
  - `local-human`：每需求独立集成分支；票只做内环并合入集成分支（状态 `merged`）。复用旧 feature 目录（已有收口过的需求）必须换新 round：票 feature-slug、`freeze --round`、集成分支三者同名（建议 `<feature-slug>-<YYYY-MM-DD>`），`delivery start` 拒绝携带非 main 提交的集成分支。全部票 merged/done 后 freeze 候选与验收计划，在候选上各执行一次整需求独立评审、`run-candidate` 复跑和 feature conformance，再执行 scoped 关联选择集及 command / browser-agent / human AC，生成正式报告，完成人工实测 / 复核 / 豁免，并取得明确 `merge-main` 确认后才由 `delivery.promote` 合 main，随后 finalize 转 done 并清理（多轮目录 finalize/close-and-clean 带本轮 `--round`，旧轮结论产物不动）。push / Prod 始终另行授权。
- `feature-closeout.ci`：server / cooperative 必填 `{provider, base-url, repository, workflow, artifact, token-env}`；local-human 必须为 null。配置只存环境变量名，不存 token、verdict 或 proof。main 或候选前进后必须重新同步、freeze 和验证，没有 drift-ignore 豁免。
- `conformance`：只在 feature 冻结候选上执行一次，不再每票必做。主会话或独立监督会话先反向枚举需求，再打开代码按 tech-spec 全节与全部票覆盖矩阵并集逐条核对；`integration/conformance-feature.md` 必须绑定 candidate/tree、精确覆盖本轮票与 spec hash，空正文、缺来源、缺代码 / 测试 / evidence 指针均不能通过。票级覆盖矩阵保留在 impl-log 供 feature 核对。
- `base-freeze`：由 ticket context 冻结 base 与 target；实现和评审期间不追目标分支。目标前进只能走 `delivery.rebase`，之后旧验证、评审和符合性结论全部失效并重新生成。
- `artifact-budget / retention`：限制 impl-log / evidence 行数及 impl / integration 体积；raw / OCR / 截图按 generation manifest 管理。当前、最后成功和被引用证据始终保留，失效代际与 salvage 只能经 `close-and-clean` 预检确认后清理。
- `integration-runtime`：显式配置开发服务与 verify-instance 的启动、就绪、停止和租约；运行记录绑定 candidate。停止服务、释放租约失败时不得清集成 worktree。
- `dispatch`：`supervisor-review` 用于主会话监督、feature 独立评审与 feature conformance；`development.default / escalated` 分别用于普通票和集成 / 高风险 / feature 评审 BLOCKER 后的修复票。先运行 `agents_config.py validate`，再运行 `dispatch_config.py validate --catalog` 和 `resolve`；模型 / effort 不在实时目录、缺当前宿主或角色文件过期即停。原生角色只由配置生成，`render --check` 只读核对，不手改第二份真源。
- `enforcement`：声明评审信任级别与宿主 / 分支 / SHA 能力要求。宿主不能证明独立评审身份时不得冒充 host-verified；非 server-enforced 不得宣称具有服务端分支保护。
- `ocr.mode`：默认 `delegate`，OCR 只筛文件与规则，由独立宿主对话评审；`local / ci` 为可选增强，必须配置对应端点和 CI。

## 测试位置

- **票级验证在本票 worktree**：TDD 见红 / 变绿、每条 DoD、fast 和单项失败归因；票级只跑内环，不跑独立评审、supervisor 复跑或 conformance。fast 不经整机槽位包装。
- **feature 候选复跑在集成 worktree**：`verify_issue.py run-candidate` 对每票冻结 DoD + fast 真实执行一次，证据写 `integration/raw/<generation>/`，不覆盖票级 implementer manifest。
- **local-human 需求级验证在本机候选环境**：`scoped_select.py` 关联选择集与单项归因经 `test_slots.py run` 包装；command / browser-agent AC 复用真实执行器和 generation 证据。direct 全跑，indirect 到预算停发并记 feature 级 deferred，不扩大为 full；宿主无浏览器能力时对应 AC 记 unverified 或转人工，不伪造通过。
- **远端只承接 CI full**：已登记分支的 push 与同一 CI workflow 的定时 / 手工触发是唯一入口。`delivery.promote` 在线核对实际 run 与候选 tree；本机、票内和监督循环不得保存或伪造本地 full 判决。

## 派发协议（标准角色配置）

dev 阶段的「派发」只依赖一个宿主无关原语：**主会话通过宿主 agent 新开独立对话执行自包含提示词**。模型、角色、会话句柄、候选和路由回执必须来自同一份 agents-config 与宿主真实返回，不靠提示词口头指定。

### 配置校验与角色解析

主会话开始监督、实现会话启动、发起评审前都先执行：

```bash
python3 ../shared/scripts/agents_config.py validate --config ./dev/agents-config.md
python3 ../shared/scripts/dispatch_config.py validate --config ./dev/agents-config.md --catalog ./dev/dispatch-model-catalog.json
```

- **主会话监督**：取当前宿主 `supervisor-review`，用 `resolve --role supervisor` 核对实际 model / effort。配置写入不会 retroactively 切换现有会话，不符即停。
- **开发实现**：普通票取 `development.default`；集成 / 高风险及评审预警后的续修取 `development.escalated`，用 `resolve --role development`。不得按测试范围路由。
- **独立评审与派发的符合性核对**：固定 `supervisor-review`，用 `resolve --role review`；必须另开独立对话，写查分离不可降级。
- 原生角色只从配置生成。`render --check` 只读检查缺失或过期；确需更新由 `/setup-dev` 明确重生，不手改第二份模型真源，也不静默覆盖项目配置。
- 旧 dispatch.model、缺字段、当前宿主无条目、模型 / effort 不在实时目录或角色过期即停，不跨宿主套用、不自动降级。

### 派发原语与结构化回报

- 派发 = 主会话启动独立对话执行 `/implement issue-NNN` 或 `/ai-review`；新对话不继承、也不依赖主会话上下文。
- 提示词必须包含：票、tech-spec、agents-config、feature-module / slug、ticket context（开工后）、execution context（有人 / 无人值守）、dispatch_id、parent_session_id，以及必要的 worktree / 分支和报告路径。
- 真实派发前，主会话按 Progress Events 在票 frontmatter 写 `dispatch` 块（host / model / dispatched-at / attempts / dispatch_id / session_id / parent_session_id）并追加 `dispatch` 事件。session ID 尚未分配时可暂空，宿主返回后立即补齐。
- 成功回报包含分支、worktree、MR、ticket context、verification、process-check-ticket、impl-log 和遗留；它只是线索，不是验收证据。
- 需要人裁决时，实现与评审子会话只返回未持久化 human-decision envelope，不直接问人、不写 returned / mr-submitted。主会话统一持久化、提问和路由。
- resume 沿用 dispatch/session，attempts 不增；redispatch 新开独立对话，主会话更新 dispatch 块、attempts +1 并写新 dispatch 事件。路由没有真实 ack 前票保持 needs-human，结果不明不得重派。

### 宿主适配

| 宿主 | 主会话监督 | 独立对话与原生角色参数 | worktree / 能力边界 |
| --- | --- | --- | --- |
| Claude Code | 项目 settings 的 `model` / `effortLevel` | `Task`；`subagent_type = supervisor-review / development / development-escalated`；环境变量覆盖模型 / effort 时即停 | 同机注册 worktree；session / worktree 活性未知时保留 |
| ZCode | 当前会话须核对 `supervisor-review.model` / `thoughtLevel` | `Agent`；角色 frontmatter 绑定 model / thoughtLevel；复审和回答后可按 agentId 用 `SendMessage` 续接 | 同 Claude Code |
| Codex | 项目 config 的 `model` / `model_reasoning_effort` | 原生多代理；`agent_type` 同上，角色 TOML 绑定模型 / effort | 自管沙箱必须由 adapter 回报路径和可达性；异文件系统不可达时保留唯一分支并报 external-pending |
| DSH | profile `agent-default-model` 的 provider / model / reasoningEffort | `subagent` 显式传 provider / model / reasoning_effort，只取 resolve 选出的 allowedModels | 以真实 adapter 能力为准；主会话 `git worktree prune` 不能冒充异文件系统清理 |

- 未登记宿主先扩展 schema、实时目录与 adapter，不得拿其他宿主模型名凑合。
- frontmatter 中的 `Task` 是 Claude Code 命名；其他宿主映射为自身派发工具，协议语义不变。
- 宿主完全没有独立对话能力时，主会话只能产出自包含提示词与 resolve 结果，请人在对应模型的新对话启动；无法确认实际模型不得开工，也不得在主会话顺便实现。评审没有独立环境时同样停止，绝不自评。
- feature 复审优先续接原评审对话，输入为新 candidate 范围、上轮报告与 receipt；续接不得改变 supervisor-review 模型 / effort。跨 feature 一律新开。票级不再发起独立评审。
- 宿主能力由 `host_adapters.py` 区分 available / unavailable / unknown。配置里写了能力不等于实际可用；评审定稿先 `write_review_binding` 通过宿主原生接口附着本次 issue / round / head / tree / report 绑定，再 `verify_review_session` 实时重读。`host-probes.json` 只保存写回执供对账，单独文件或 capability bool 不构成证明。浏览器不可用时 AC 记 unverified 或转人工，session 活性 unknown 时禁止删除其 worktree。

### 清场触发与统一入口

清场只在**合并与关账事实已确认之后、收尾总结之前**发生；MR ready、子会话退出、Stop hook 或面板轮询都不触发删除。

```bash
# 默认只预检，返回 operation_id 与 confirm
python3 ../shared/scripts/progress.py close-and-clean --repo <主工作区> --issue <issue-id>

# 只有预检内容与真实授权一致才执行
python3 ../shared/scripts/progress.py close-and-clean --repo <主工作区> --issue <issue-id> \
  --apply --operation-id <预检ID> --confirm <完整指纹>

# 每轮监督先只读对账；只恢复已有授权 journal 或已证明 merged 的残留
python3 ../shared/scripts/progress.py reconcile --repo <主工作区>

# 恢复必须显式分类，不能用 cleanup 恢复其他类型事务
python3 ../shared/scripts/progress.py reconcile --repo <主工作区> --kind cleanup --operation-id <ID> --apply
python3 ../shared/scripts/progress.py reconcile --repo <主工作区> --kind delivery --context <ticket-context.json> --apply
python3 ../shared/scripts/progress.py reconcile --repo <主工作区> --kind delivery --candidate-path <candidate.json> --candidate-repo <集成worktree> --apply
python3 ../shared/scripts/progress.py reconcile --repo <主工作区> --kind human_decision [--feature <module/slug>] --apply
python3 ../shared/scripts/progress.py reconcile --repo <主工作区> --kind runtime --operation-id <service/lease ID> --candidate <完整OID> --apply
```

- 清理器**永不合并代码**，没有 action=merge 或可任意切换的 dev-only。中途搁置只能显式 `--intent discard` 重新预检，先 salvage 再关闭为 deferred，不解锁依赖。
- 目标分支只取 ticket context，不能回退 query / main。worktree、detached、locked、prunable、branchless、remote-only、本地 / 远端 ref、dev、session、实例和服务都由同一份计划覆盖。
- confirm 绑定票 / config、refs、完整 ignored 状态、软链、session / runtime 和 dev GC 项；apply 锁内重验，强制删除前再扫描。活跃会话、日志增长、错误软链或外部能力未知都停止。
- `.logs/**`、未收录证据与报告默认 salvage；dev 软链只有解析后等于主工作区 dev 才不跟随打包。依赖和构建缓存可按计划删除，但最终证据、最后成功代际和被引用捕获物不得删除。
- journal 逐步记录 expected / observed；崩溃后 `reconcile` 按真实状态幂等恢复。部分完成、prune 失败、远端 OID 变化或剩余路径非空都必须报告，不能显示“清理完成”。
- 票合入集成分支后只写 `merged`，worktree / branch 保留；不在票级 close-and-clean。
- local-human 的 integration worktree / 分支、逐票 worktree / branch 与 dev 过程物，统一在 feature-merged 后由 `feature_closeout.py finalize` 清理：逐票 close-and-clean，再停服务 / 释放租约，salvage 并清 Git 资源。finalize 失败保持 needs-attention，可重跑；不能用分支消失反推验收完成。
- 单独处理已完成历史票时仍可用 `close-and-clean --issue`；它只接受真实 `done` 票，不把 `merged` 当作已验收。

## 票状态、派发字段与进度事件统一契约

机器真源分为三处：票 frontmatter 表示当前交付状态，`dev/features/<feature-module>/<feature-slug>/progress.jsonl` 表示追加事实流，`decisions/<decision-id>.json` 表示人工作决策及其回答 / 路由。三者必须能对账；面板、下钻或新会话接力不得另造第四份状态。

### 1. 票状态

| 状态 | 含义 | 是否视为下游依赖已完成 |
| --- | --- | --- |
| `proposed` | 票清单尚未确认 | 否 |
| `confirmed` | 可进入派发候选，仍需核对 `blocked-by` | 否 |
| `in-progress` | 实现或回修进行中 | 否 |
| `in-review` | 独立评审进行中 | 否 |
| `mr-submitted` | 等待主会话收口 | 否 |
| `merged` | 已合入目标集成分支（local-human 即 feature 集成分支）；worktree / branch 保留到 feature finalize | **是**；下游可开工，但本票不计入需求验收完成 |
| `needs-human` | 有同票活动 human-decision 包，等待人回答或路由收尾 | 否 |
| `done` | feature candidate 已验收并完成验收关账 | **是** |
| `deferred` | 明确延期，可另行重开 | 否 |
| `cancelled` | 明确取消，不代表需求已交付 | 否 |

`merged` 与 `done` 都能解锁 `blocked-by`（否则下游会在集成分支可用后死锁），但只有 `done` 计入需求验收完成。`merged → done` 只能由 feature finalize 在验收通过后完成：写 `accepted` 事件并把票状态改为 `done`；票级 delivery 不写 `done`。历史展示层可以把 `deferred / cancelled` 归入“已关闭”泳道，但不能把它们计入 `done` 集合。Human Decision 的 `defer / cancel` 只产生上述关闭状态，不删除依赖边，不允许 `next_status=done`；`merged` 也只能由真实合并事务写入。

### 2. `dispatch` 块

主会话在真实派发前写入并维护：

```yaml
dispatch:
  host: zcode
  model: <由 agents-config 解析并绑定的实际模型>
  dispatched-at: 'YYYY-MM-DD HH:MM'
  attempts: 1
  dispatch_id: dispatch-<本次派发的稳定 ID>
  session_id: <宿主会话 ID；新会话尚未分配时可暂空>
  parent_session_id: <发起派发的主会话 ID>
```

- `attempts` 计算**新会话派发次数**，不计算原会话续接。首次派发为 1；打回后新开独立会话、`redispatch` 为 +1；`resume` 续接同一 `session_id` 不变。
- `dispatch_id` 标识一次新派发，用于和 route / 事件 / 宿主回执对账。`resume` 沿用旧 `dispatch_id` 和 `session_id`；`redispatch` 使用新 `dispatch_id`，新 `session_id` 由宿主返回后补齐。
- `parent_session_id` 表示谁发起本次派发，不用旧子会话冒充新子会话的父会话。
- `model` 不能从 decision prompt 猜测，仍由 agents-config 校验、角色解析与宿主原生绑定决定。
- `route_begin` 只给出 `attempts_before / attempts_after` 和 ID 计划；集成层实际派发时才改 `dispatch` 块并写 `dispatch` 事件。路由重放不得重复增加。

### 3. 事件信封与幂等

每行至少包含：

```json
{
  "ts": "YYYY-MM-DD HH:MM",
  "event": "human-decision",
  "issue": "issue-001",
  "event_id": "human-decision:dec-example:resolved:route-...",
  "decision_id": "dec-example",
  "state": "resolved",
  "route_id": "route-..."
}
```

- `ts` 使用本地 `%Y-%m-%d %H:%M` 或带时区 RFC3339；所有 replay / hosts / 对账读取器必须同时正确解析，不能把 RFC3339 当作 epoch 0。包内时间戳统一为带时区的 RFC3339。
- 所有新事件通过 `append_event_once(path, event, event_id)` 写入。同一 ID、完全相同内容重放返回已写；同一 ID 内容不同即冲突，不能覆盖历史。
- `event_id` 由写者根据稳定事实构造。例如每次呈现使用其 `presented_at` 派生，每次路由使用 `route_id`，所以取消后再次提问和明确失败后的重试不会互相覆盖，也不会因重放重复追加。
- 旧事件流若缺少 `event_id` 或含坏行，严格存储会拒绝继续追加。集成层迁移 / 回填时必须先按明确规则补稳定 ID；Human Decision 和对账不得猜测 ID、重写业务含义或跳过损坏后伪装成功。

### 4. 固定事件词表（原 11 个 + accepted）

不得另造同义事件词：

| 范围 | 事件 | 必须表达的事实 |
| --- | --- | --- |
| 票 | `dispatch` | 新会话派发；附 `host / model / attempts`，并带 `dispatch_id / session_id / parent_session_id`（已分配时） |
| 票 | `returned` | 实现 / 评审子会话结构化回报到达；回报本身不是验收证据 |
| 票 | `verify-fail` | 验收复跑或符合性核对打回；附 `n` 和短摘要 |
| 票 | `mr-submitted` | MR 或本地草案已提交；附 IID / URL 或路径 |
| 票 | `needs-human` | 票进入人工决策；附原因和 `decision_id` |
| 票 | `merged` | 已合入目标集成分支，但尚未验收；附 `host`、合并 `commit`、精确 `branch` / `target` 与 `source_head`，供 finalize 与后续清理复核。写此事件不等于 `done` |
| 票 | `accepted` | feature finalize 验收通过后把 `merged` 票转为 `done`；附 `commit` / `target` 与验收依据，作为唯一 `merged → done` 的状态事件 |
| 票 | `circuit-break` | 无人值守熔断及原因 |
| 票 / 需求 | `digest` | 阶段摘要、无人值守心跳或关闭说明；不冒充具体状态迁移 |
| 需求，`issue: feature` | `feature-verified` | local-human 自动阶段完成且报告已生成；未验证项仍明确保留 |
| 需求，`issue: feature` | `feature-confirmed` | 人已完成合并前确认 |
| 需求，`issue: feature` | `feature-merged` | 需求已合入 main；附 commit |
| 票 | `human-decision` | 决策包迁移；必须附 `decision_id / state / route_id`，无路由时 `route_id: null` |

`human-decision.state` 与包状态一致：`pending / waiting / answered / routing / resolved / expired / cancelled / superseded`。`routing` 和 `resolved` 事件附当前 `route_id`；明确失败回到 `answered` 的事件也附失败路由 ID，以区别于首次回答。`resolved` 事件附 `next_status / strategy / attempts`，可作为“ack 后允许并执行票状态迁移”的对应事件。

### 5. 谁改状态，谁写事件

| 动作 | 票 / dispatch 写入者 | 同事务序列中的事件 |
| --- | --- | --- |
| 首次派发、打回后新开会话、decision `redispatch` | 主会话 / 宿主集成层写 `dispatch` 块，`attempts + 1`（首次从 0 到 1） | `dispatch` |
| decision `resume` | 主会话续接原会话，不增加 attempts，不伪造新会话字段 | 不写新的 `dispatch`；只写 `human-decision: routing` |
| 子会话回报 | 主会话接收，不直接据此改验收结论 | `returned` |
| 验收复跑 / 符合性打回 | 主会话更新 `verify-fails` 和票状态 | `verify-fail` |
| MR 提交 | 主会话写 MR 信息和票状态 | `mr-submitted` |
| 新建 human decision | 主会话先写 pending 包，再把票置 `needs-human` | `needs-human` + `human-decision: pending` |
| 呈现 / 未回答中断 / 回答 / 开始路由 / 到期 | 主会话只迁移决策包；票保持 `needs-human` | 对应 `human-decision` 状态 |
| 路由明确失败 | 主会话把包退回 `answered`，票继续 `needs-human` | `human-decision: answered`（带失败 route_id） |
| 收到真实 ack | 主会话先持久化 resolved 包，再把票改为计划 `next_status` | `human-decision: resolved`；若实际发生新派发，另有 `dispatch` |
| 明确延期 / 取消 | 主会话按受控计划写 `deferred / cancelled` | `human-decision: resolved` 或显式 `cancelled`；不得写 `merged` |
| 票级合并 | 主会话执行合并后把票置 `merged`；worktree / branch 保留 | `merged`（`target` 为集成分支） |
| feature finalize 验收 | feature 验收通过后把 `merged` 票置 `done`；票级 delivery 不再改状态 | `accepted` |
| feature 收尾清理 | 验收完成后由 feature finalize / close-and-clean 清理保留的 worktree / branch | 可写 `close-clean` 或 `digest`，不重复写 `merged` / `accepted` |
| 对账补写 | 主会话只根据已经持久化的事实修包 / 票 | 用原稳定 `event_id` 补缺失事件，不创造新事实 |

事件不是“谁最后看见谁补一句”的自由日志。写票状态的职责方必须同时写对应事件；崩溃后对账可以按同一稳定 ID 补齐，但不能用摘要事件替代必须存在的状态事件。

### 6. Human Decision 对账口径

- 活动包包括 `pending / waiting / answered / routing / expired`，都应有同票 `needs-human`。`expired` 只是停止接受旧回答，不等于票已解锁。
- `resolved` 包必须含真实 ack；只有它能支持把孤立 `needs-human` 修复为计划的 `next_status`。
- `routing` 没有 ack 时结果未知：保留包和票，向宿主对账，不得重派、增加 attempts 或补 `dispatch`。
- `superseded` 必须由新包的显式 `supersedes` 链接支持；旧答案不迁移。
- `deferred / cancelled / superseded / resolved` 都不是活动包；若票仍为 `needs-human` 且没有另一活动包，对账只能依据真实 ack / 取消记录修复，否则报告冲突。
- 同一票出现多个无关联活动包、孤立 `needs-human`、损坏事件流或无法解释的包路径时，停止自动推进并报告，不猜一个“看起来合理”的状态。

## Human Decision v1（主会话唯一写者）

本契约处理“必须由一个具体的人选择后才能继续”的单点决策。它不复述监督循环；票的 frontier、验收、合并和清场仍按原流程执行。

### 1. 唯一真源与角色边界

决策包唯一路径：

```text
dev/features/<feature-module>/<feature-slug>/decisions/<decision-id>.json
```

包的 `schema` 固定为 `ch-agent.human-decision/v1`，结构与跨字段约束由 `shared/references/human-decision.schema.json` 和 `shared/scripts/human_decision.py` 共同校验。`scope.feature` 是 `<feature-module>/<feature-slug>`，`scope.issue` 是稳定票号；`scope.repo` 只能是 `.` 或仓库标签，不能代替 API 的仓库根参数，也不能存绝对路径。

| 角色 | 能做什么 | 不能做什么 |
| --- | --- | --- |
| 实现 / 评审子会话 | 构造请求，调用 `validate_request(envelope)`，把未持久化 envelope 结构化返回主会话 | 写决策包、改票、写事件、调用提问组件、记录回答、开始 / 确认路由 |
| 主会话 | 校验并 `request`，统一 `present` / `answer`，生成受控路由，接收宿主回执，查询与对账 | 代替人选择、猜测模糊自由文本、伪造宿主回执、直接置票 `done` |
| 宿主集成层 | 执行真实的原生提问、会话续接 / 新会话派发、已登记 supervisor operation，提供真实回执 | 把 decision prompt 当 shell 执行、让子会话自行问人、在结果不明时重派 |

所有 Python 写操作都必须显式传 `main_session=True`；缺少该标志即失败，不能只凭 `source.role` 自称主会话。`validate_request` 不写任何文件。CLI 对应标志为 `--main-session`。

无人值守时，主会话可以 `request(..., unattended=True)` 留下包、票和事件，但不得调用原生提问、等待回答或选择推荐项；`present(..., unattended=True)` 也原样返回 `pending`。循环是否继续其他票由监督契约决定，本模块不复制那部分逻辑。

### 2. 请求包与不可变依据

请求包含：

- `id`：小写稳定 ID，只允许字母数字和连字符；路径由模块构造，调用方不能提供文件路径。
- `scope`：`repo / feature / issue`。
- `source`：`host / skill / role / dispatch_id / session_id`，可带 `parent_session_id`；它们只用于续接和关联，不出现在对人的问题里。
- `basis`：`issue_sha256 / head_commit / report_sha256`。回答和开始路由必须传入主会话刚核对的 `current_basis`，三项必须完全一致。变化时旧答案不能套用，必须 `supersede` 创建新包；新包不复制旧答案或路由。
- `question`：`context / decision / why / recommendation / options`。`recommendation` 是一个选项 ID，不是自动答案。
- `evidence`：最多 12 条 `{path, sha256, summary}`。路径必须是仓内相对 POSIX 路径；创建时核对文件摘要。原始日志、内容快照和秘密不进入包。
- `presentation.header`：不超过 12 个字符的业务短标题。
- `expires_at`、`supersedes` 与完整时间戳。

每题 2–4 个互斥选项。选项有稳定 `id`、动作型 `label`、说明代价与风险的 `description`，以及受控 `route`：

| `action` | 允许的计划 | 允许的结果票状态 |
| --- | --- | --- |
| `continue` | `strategy=resume/redispatch`，受控 `role=development/review`、`tier=default/escalated`，以及自包含 `prompt` | `confirmed / in-progress / in-review / mr-submitted` |
| `supervisor` | 仅 `operation`，不得带自由 prompt 或模型路由 | 同上；operation 只可以是 `reconcile-routing / re-split / request-review / collect-evidence`；`redesign` 例外只能去 `proposed` |
| `defer` | 无 prompt、无会话计划 | `deferred` |
| `cancel` | 无 prompt、无会话计划 | `cancelled` |

任何位置都禁止 shell / command / args / env 等执行字段、任意未登记 operation、`next_status=done`、路径逃逸、秘密和大型日志。请求整体不得超过 128 KiB。模块只生成计划，永远不执行 prompt 或 operation。

### 3. 状态机与票状态不变式

```text
pending → waiting → answered → routing → resolved
    ↑        │
    └────────┘  问题卡取消 / Escape / 工具超时（未回答）

pending / waiting / answered --显式且到期 expire--> expired
pending / waiting / answered / expired --显式 cancel--> cancelled
活动包（routing 除外）--supersede--> superseded + 新 pending
```

- 活动状态 = `pending / waiting / answered / routing / expired`。`expired` 不再接受回答，但仍是活动包，继续要求原票保持 `needs-human`，直到显式取消或用新包替代。
- 同一票最多一个活动包。`request` 重放相同不可变内容返回原包；相同 ID 改 basis 或其他请求内容即冲突。新 ID 遇到同票活动包也会被拒绝，除非它明确 `supersedes` 旧包。
- `needs-human` 必须能定位到同票活动包。`request` 先写包，再把票置为 `needs-human` 并补事件。对账发现孤立 `needs-human` 时只报告，不猜之前状态，也不伪造一个包。
- 回答只接受 `waiting`，必须同时记录 `option_id` 和用户原文 `text`。原生组件的标签映射由主会话确定；模糊自由文本应继续澄清，不能猜成选项。
- 只有持久化宿主 ack 后才能进入 `resolved`，随后才允许把票从 `needs-human` 改为计划的 `next_status`。本模块在 `route_ack` 中按此顺序写入；集成层不得提前改票。
- `defer / cancelled` 都不是 `done`，不得删除或改写任何 `blocked-by`，也不得让下游按完成解锁。Human Decision 永远没有直接 `done` 路由。

### 4. 提问投影与未回答语义

`to_question_component(packet)` 返回宿主 `AskUserQuestion` 形状：

```json
{
  "questions": [{
    "header": "订单重试",
    "question": "……2–4 句……",
    "multiSelect": false,
    "options": [
      {"label": "增加防重复处理（推荐）", "description": "……"},
      {"label": "暂缓这张票", "description": "……"}
    ]
  }]
}
```

投影恰好一个问题；推荐项始终第一，标签由模块追加 `（推荐）`。可见字段只有 `header / question / multiSelect / options`，选项只有 `label / description`。不得暴露 decision ID、route ID、会话、内部角色、状态词、哈希、路径或原始日志，也不要求用户“回 A / B”。宿主自动提供的“其他”不重复添加。

只有纯文本降级使用 `to_text_fallback(packet)`：它为同一组选项显示 `1. 2. ...` 并提示“请回复选项编号，或直接说明你的选择”。编号不是持久化选项 ID；记录回答时仍保存稳定 option ID 与原文。

`cancel_presentation` 和 `timeout_presentation` 只把 `waiting` 退回 `pending`，保留“未回答”事实；它们不选择推荐、不取消票、不写 `expired`。迟到的取消 / 超时不能覆盖已存在的回答或路由。只有显式 `expire` 且已到 `expires_at` 才能写 `expired`。`cancel` 是另一项明确的管理操作，表示人要取消本票，绝不能由问题卡关闭动作触发。

### 5. 路由、计数与回执

`route_begin` 需要与回答时相同的 basis，生成并持久化唯一 `route_id` 和计划：

- `resume`：沿用原 `dispatch_id / session_id`，`attempts_after = attempts_before`，供主会话续接同一子会话；不新增一次派发。
- `redispatch`：生成新 `dispatch_id`，`session_id=null` 等宿主分配，`attempts_after = attempts_before + 1`，供主会话新开独立子会话。重放 `route_begin` 返回原 route，不再次计数。
- `supervisor`：只返回已登记 operation，不生成模型 / 会话升级指令。`redesign` 是承重设计被证伪时的受控路由：ack 后票回 `proposed`，不增加 dispatch attempts；主会话局部重跑 `/how`、`/why` 和 tech-spec 候选，新 spec 经 supersedes 接管，受影响票重新过门②并冻结新 context 后才能转 `confirmed`。旧验证、评审、conformance 和 candidate 不得沿用。
- `defer / cancel`：返回受控关闭计划，不增加派发次数。

集成层在实际新派发前，按计划更新票的 `dispatch` 块并追加 `dispatch` 事件；模型 / effort 仍由 agents-config 和宿主原生角色解析，本模块不猜模型。续接不新增 `dispatch` 事件，重派新增。无论哪种路由，ack 前票都保持 `needs-human`。

`route_ack(route_id, ack)` 只接受当前路由的真实宿主回执，先保存 ack 和 `resolved`，再改票及补事件。相同路由重放不能换一份不同回执。

只有宿主明确报告“未执行或已安全失败”时，才能 `route_fail(..., outcome_known=True)`：包回到 `answered`，保留原回答和失败事实，票继续 `needs-human`。之后人可以维持选择并生成新 route，也可以因 basis 变化重开新包。结果不明不得调用 `route_fail`，不得新建 route 或重派。

### 6. 恢复与分步写入对账

新会话接管或发现 `routing` 时，先运行 `reconcile`，再向宿主核对原 `route_id / dispatch_id / session_id`。模块恢复路由时只返回原计划，不会生成第二次派发。

写入顺序保证可修复性：

- 创建：新包 → 旧包替代关系（如有）→ 票 → 幂等事件。
- 回答 / 呈现 / 路由：包 → 幂等事件。
- ack：包含真实 ack 的 resolved 包 → 票 → 幂等事件。
- 替代采用“新包先写”：崩溃可能短暂留下两个有明确 `supersedes` 链接的包，对账可以完成旧包替代；不能反过来让票失去活动包。

`reconcile` 可以：

- 根据活动包补回票 `needs-human` 和缺失事件；
- 根据新包的显式 `supersedes` 链接完成旧包替代；
- 根据已持久化的真实 ack 补写计划中的票状态和 resolved 事件；
- 根据显式取消记录补写取消结果；
- 报告同票多个无关联活动包、孤立 `needs-human`、损坏包和未知路由。

它不能把 `routing` 猜成成功或失败，不能伪造用户回答 / 宿主 ack，不能复活旧 basis，不能替孤立 `needs-human` 选择先前状态。`routing` 且没有 ack 的包会列入 `unknown_routes`，保持不动。

### 7. API 与 CLI 入口

Python：

```text
validate_request                       request / supersede
present / cancel_presentation / timeout_presentation
answer                                 route_begin / route_ack / route_fail
expire / cancel                        show / list
active_for_issue / active_for_feature reconcile
to_question_component / to_text_fallback
```

CLI 子命令与上面同名，路由函数使用连字符：

```bash
python3 human_decision.py validate-request --request envelope.json --repo REPO
python3 human_decision.py request --main-session --repo REPO --request envelope.json [--unattended]
python3 human_decision.py present --main-session --repo REPO --feature M/S --id DECISION [--channel text]
python3 human_decision.py answer --main-session --repo REPO --feature M/S --id DECISION --option-id OPTION --text '用户原话' --current-basis basis.json
python3 human_decision.py route-begin --main-session --repo REPO --feature M/S --id DECISION --current-basis basis.json
python3 human_decision.py route-ack --main-session --repo REPO --feature M/S --id DECISION --route-id ROUTE [--ack ack.json]
python3 human_decision.py route-fail --main-session --repo REPO --feature M/S --id DECISION --route-id ROUTE --reason '宿主明确未执行' --outcome-known
python3 human_decision.py reconcile --main-session --repo REPO [--feature M/S]
```

另有 `expire / cancel / supersede / show / list / active / cancel-presentation / timeout-presentation`。`--request -` 可从 stdin 读取 envelope；`--current-basis` 和 `--ack` 接收 JSON 文件。

## 监督循环（主会话职责，直到 feature candidate 合入与 finalize 完成）

票状态、dispatch 块、progress 事件和 human-decision 包统一遵守「票状态、派发字段与进度事件统一契约」。主会话不能用实现回报代替机器证据，也不能把分支删除单独当成已合并证明。

门②通过后，主会话自动进入监督循环，**派出后不停手，直到每票合入集成分支、feature candidate 收口和清理都有确定结果**：

1. **取票**：只读票 frontmatter 的 status / blocked-by，重算 frontier。依赖为 `merged` 或 `done` 才算满足；`merged` 表示依赖代码已在集成分支上，`done` 表示已通过 feature 验收。deferred / cancelled / needs-human 不解锁。
2. **派发**：local-human 首次派发前建立本需求独立集成分支（从当前 main 建，`feat/<feature-slug>`）和集成 worktree。**复用旧 feature 目录（该目录已有收口过的需求）必须换新 round**：票 feature-slug、`freeze --round`、集成分支三者同名（建议 `<feature-slug>-<YYYY-MM-DD>`）；`delivery start` 会拒绝携带非 main 提交的集成分支（旧需求残留），不换 round 无法开工。按 Dispatch Protocol 为 frontier 票新开独立实现对话；先写 dispatch 块和事件。declared-scope 两两不相交的票可按 parallel-max 并行，有交集或疑义串行，不用关联测试闭包决定并行。
3. **跟踪**：成功结构化回报到达即写 returned。回报只提供分支、worktree、ticket context、verification、process-check-ticket、impl-log 和遗留线索，不作为成功依据。needs-human 不是成功回报。
4. **票级收口**：
   - 实现会话已执行 `process_check.py --phase ticket`；主会话复核其 JSON、implementer verification、票 scope 和活动 decision，不重复发起独立评审、supervisor 复跑或 conformance。
   - 只执行 `python3 ../shared/scripts/delivery.py merge --context <ticket-context.json>`。delivery 统一复核 target 未前进、票级门禁、implementer 证据与活动 decision，执行 no-ff 合并，写 `merged` 事件并**保留** worktree/branch。
   - `merged` 解锁下游，但不计入需求验收完成；只有 feature finalize 写 `accepted` 后才把票置 `done`。
   - 不得另跑裸 git / glab merge、公开 record-merge，也不得在票级调用 close-and-clean。
5. **CI 与晋级**：票级 merge 永远只核 DoD + fast，本机任何 worktree 不执行 full。server-enforced / cooperative-ci 由 `delivery.py promote` 经 CI API 核 run、artifact、ref、commit/tree、归因与外部能力；分支或 tree 前进即重新验证。server-enforced 能力未知即停，cooperative 输出必须注明无服务端强制。CI 不可用时保持未验证，不降级本机补跑。
6. **打回**：票级 process-check、implementer runner 失败、输出矛盾或活动 decision → 不合并，verify-fails +1 并写 verify-fail。未达 2 时退回实现会话，修复后刷新受影响 DoD 并重跑票级门禁；达 2 时创建 human-decision，不再自动打回。feature 级评审 BLOCKER 另按需求收口处理。
7. **推进与对账**：每轮先运行只读 `progress.py reconcile`，聚合 cleanup、delivery、human_decision 和 runtime 的真实状态。恢复时必须显式分类：`--kind cleanup --operation-id ...`、`--kind delivery --context ...` 或 feature 的 `--kind delivery --candidate-path ... --candidate-repo ...`、`--kind human_decision [--feature ...]` 或 `--kind runtime --operation-id ... --candidate ...`，并只在已有授权时加 `--apply`。完成票但仍有 worktree / ref / dev 残留时，在 feature finalize 统一处理；不从分支消失反推 merged。全部票 merged/done 后进入需求收口。
8. **每日 CI 记录（启用时）**：只读最新 run / verdict / proof 与 tree；清单外新失败已由 CI 二分到合入提交时开回修票并创建 human-decision，其他合入不暂停。漏跑只补触发同一 CI workflow；超期隔离项必须向人说明挂起时长、去向和建议。
9. **唯一问人路径**：needs-human、阻塞、verify-fails 达 2、无法解决的冲突或其他越界问题，统一由主会话按 Human Decision 协议 present → answer → route-begin → 原生调用 → route-ack。子会话不直接问人；ack 前不改票，路由失败不盲目重派。其余自修复、复跑打回和未达标的验证不叫人。

### 需求收口（local-human）

全部票 `merged` 或 `done` 后按以下顺序执行；所有产物写主工作区 `integration/[round/]`，不写集成 worktree、不提交到集成分支。`executor` 决定谁执行，`oracle` 决定谁最终验收，浏览器操作不能替代人的业务判断。

1. **前提**：全票 merged/done + 每票 merged commit 完整且为候选祖先、无活动 human-decision；`freeze` 只冻结候选与票组，**不再要求每票 conformance**。PRD 或 change-request delta + retained baseline AC 全部进入 acceptance-plan.yaml 且 validate-plan 通过。
2. **同步并冻结**：集成 worktree 先同步 main，冲突在集成分支解决；`feature_closeout.py freeze` 绑定 candidate / tree / 票组与计划哈希（复用旧 feature 目录的轮次加 `--round <round名> --issues <本轮票>`，round 名与票 feature-slug 一致，产物写 `integration/<round>/`）。main 或候选之后任何前进都必须重新同步、freeze 并重跑 feature 门。
3. **整需求独立评审**：在冻结候选上发起**一次** `/ai-review`，评审 `main_head..candidate` 全 diff，按票 merge commit 分段并整体判断跨票契约。评审者必须独立于主会话和全部票 implementer；产出 `integration/feature-review.md` 与 `integration/feature-review-receipt.json`（`review_receipt.py create-feature`）。critical/high 未闭合或 verdict=blocked 即阻塞。
4. **候选 supervisor 复跑**：在集成 worktree 执行：
   ```bash
   python3 ../shared/scripts/verify_issue.py run-candidate \
     --candidate <integration/candidate.json> --repo <集成 worktree> \
     --identity-json <supervisor 身份>
   ```
   对每票冻结 DoD + fast 真实执行一次，写 `integration/raw/<generation>/<issue>/verification-supervisor.json` 与 `integration/candidate-verification.json`；不覆盖票级 implementer manifest。任一失败阻塞。
5. **Feature conformance**：完成 `integration/conformance-feature.md`，绑定 candidate/tree；按 tech-spec 全节与全部票覆盖矩阵并集逐条核对，coverage.issues / specs 精确覆盖本轮票与方案，每票至少一行有效证据。票级 conformance 不再必做，票级矩阵保留在 impl-log 供此处核对。
6. **准备候选环境与自动证据**：配置了 verify-instance 时由 integration_runtime 获取租约、部署并核 candidate；否则按显式 dev-service 配置启动集成 worktree 实例。后台执行 fast → `scoped_select.py`（经 test_slots 包装，direct 全跑、indirect 到预算停发并记 feature deferred，不跑 full）→ command / browser-agent AC 的真实执行与 generation 证据。自动 pass 必须有实际断言；浏览器还需 URL、actions、console/page error 和当前候选截图。清单外失败只做单项 base 归因，本需求引入则开修复票并重新 freeze。
7. **机器门禁与首次报告**：`feature_closeout.py report` 生成 acceptance-report.md，再 `check --phase verify --record`。该门同时核 feature review receipt、candidate-verification、conformance-feature、票状态 / merged 祖先、AC 漏项 / 重复 / 额外和证据环境；任一项失败不过。
8. **人工验收与最终报告**：先向人展示实际通过、失败、未验证、待人工原因及每条操作与预期。判据不足只能向 auto+human-review / human 严格调整并写 reclassification；未执行只能真人实测或显式豁免。人报问题回第 2 步重新 freeze、评审与复跑。明确 `merge-main` 原话、时间、候选 tree 和无附加条件写入 confirmation.md，再重新 report；沉默、取消或超时不算确认。
9. **合 main**：主会话只执行：
   ```bash
   python3 ../shared/scripts/delivery.py promote \
     --candidate <integration/candidate.json> --candidate-repo <集成 worktree> --repo <主工作区>
   ```
   delivery 在同一授权进程内复核 review receipt / candidate verification / conformance / 报告 / 确认哈希，合并、内部 record，并把候选 `merged` 票写成 `done` + `accepted`。不接受公开 record-merge 或任意 `--commit`。最终 tree 必须等于候选；push / Prod 不在本授权内。
10. **finalize 与集成清场**：feature-merged 后运行：
    ```bash
    python3 ../shared/scripts/feature_closeout.py finalize \
      --feature-dir <主工作区 feature 目录> --repo <主工作区> [--round <round名>]
    ```
    确认候选、feature-merged 事件和主 HEAD 一致；逐票 close-and-clean，再清理 integration worktree / 精确 upstream / 失效代际（多轮复用目录必须带本轮 `--round`，finalize 只清本轮候选与分支，不动旧轮结论产物）。任何一步失败都保持 needs-attention，不能用总结掩盖残留。
11. **发布与兜底**：push、Prod dry-run/sync、只读冒烟和 rollback 均需人另行明确授权；main push 后 CI full 是安全网。新失败归因到本需求则开修复票，影响 Prod 时提议 revert / 回滚，人确认后执行。

### 无人值守模式（人离场自动跑）

- **授权落盘**：`unattended.md` 写明可自主事项、禁止事项、预算和停止条件，经人过目后开跑。默认禁止 deny 命中、改 DoD、绕过 scope / 粒度、执行本机 full、合 main、Prod、push 和越过停止条件；额度阈值未指定时默认 80%。
- **队列即 DAG**：不另建队列文件；同一项目只跑一个循环，离场默认串行，只有授权明确允许才按 parallel-max。
- **无人值守不问人**：越界问题只创建 human-decision 包并保持 needs-human，写清业务问题与推荐方向，继续其他无依赖票；不调用 AskUserQuestion、不等待回答。
- **评审预警**：feature 评审多轮 blocked 时主会话判断拆票、首轮漏报或模型能力，写 digest，必要时续修改用 escalated，不停等人。
- **进度与恢复**：只信票 frontmatter + progress.jsonl + decisions/*.json + journal。新会话先 reconcile，再从 frontier 续跑；routing 无 ack 不得重派，旧 needs-human 缺包必须报告契约错误并重建问题。
- **停止**：frontier 空且全部票 merged/done、feature candidate 已 finalize，或剩余票全部等待 / 阻塞、预算 / 时限到或熔断时停止；写 digest、撤销本次心跳并汇总。feature 收口需要人确认 merge-main 时按授权停在该门，不自行越过。
- **熔断即停**：额度达阈值、耗尽、持续限流、鉴权失败，或连续 2 次心跳无实质推进，立即停止派发与合并、写 circuit-break、撤销心跳，不安排自动 retry。
- **心跳从紧**：优先主会话持续监督；只能定时唤醒时间隔不短于 2 小时且上一轮必须有实质进展，续跑另起新会话，不反复唤醒同一长会话。
- **离场前提醒**：电脑不休眠、宿主权限不等人点确认、模型额度与本次预算 / 停止阈值。合 main、Prod、push 永远不因 unattended.md 获得授权。


## ARCH-CHECK 规范与动作

- 架构契约的唯一机器真源是 `docs/ARCH-CHECK.json`；`docs/ARCHITECTURE.md` 是 `arch_check.py render write` 确定性生成的 11 节架构总览 + 规则附录，禁止手改。
- 编码前先读 `docs/ARCHITECTURE.md`：1–10 节的系统结构、组件、存储、集成、部署、安全、测试与演进，以及附录 A–E 的分层、组、依赖、放置和阈值。AI 的上下文里没有这些事实就不会遵守。
- `init` 只生成空规则与 11 节骨架；首次出现架构影响时由 `/tech-spec` 3.2 按代码与 How/Why 证据补全总览基线，不能留空跳过。后续技术方案只更新受影响节，不重写无关节。
- **票级不设架构门**：`delivery.start`、`ticket_context.check_plan`、`process_check --phase ticket` 都不做架构检查。架构检查只在 feature candidate 的 `freeze / check` 强制运行；本地 pre-push hook 只做提前预警，权威门仍是 candidate。
- 规则变更（新增、删除、放宽、改阈值）必须写进 tech-spec 3.2 和承接票 `declared-scope`，并重新 `render write`；ai-review 把“删除 / 放宽规则”当作高风险项审查。
- 总览与规则冲突时以代码事实修正总览、以 tech-spec 讨论规则；不得为了绕过错误而改总览或放宽规则。
- 旧 `docs/architecture/*.json`、stable ID、`architecture-updates`、flow-forward/back 已废弃；不要新增或读取。

常用命令：

```bash
python3 ../shared/scripts/arch_check.py check  --repo <项目根> --head HEAD --rules docs/ARCH-CHECK.json
python3 ../shared/scripts/arch_check.py validate --repo <项目根> --worktree --rules docs/ARCH-CHECK.json
python3 ../shared/scripts/arch_check.py render write --repo <项目根>
python3 ../shared/scripts/arch_check.py render check --repo <项目根> --worktree
```

违规输出包含规则 ID、`file:line`、违规事实和 Step 1 / Step 2 修复动作。按步骤修代码或按流程改规则；不要手改 `docs/ARCHITECTURE.md`，也不要用改规则来绕过错误。

# /issue-split

你是技术项目经理。你负责：**把一份已确认的 tech-spec，拆成一组带阻塞关系、验收标准可自动判定的 tracer-bullet 票，经人确认（门②）后发布**。

你不负责：

- 技术方案设计（`/tech-spec` 的事）
- 实现任何一张票（`/implement` 的事）
- 修改 tech-spec 的方案内容；发现方案问题退回 `/tech-spec`

你的位置是：

**Tech Spec（已确认）→ 票 + 阻塞边（你）→ 门②人审清单 → 主会话按 DAG 逐票派发（宿主新开独立对话跑 `/implement`）→ 主会话监督到逐票合并、推进，全程不再人审**

---

## 上游读取规则

- `./dev/agents-config.md` 不存在 → 提示先 `/setup-dev`，状态置 `阻塞`；存在时先运行 `python3 ../shared/scripts/agents_config.py validate --config ./dev/agents-config.md`，旧版本或非法配置不得继续
- 必须先确定唯一 `feature-module` 与 `feature-slug`（沿用 `./docs/features/` 的最多两层匹配规则：模块层 / slug 层）；读取该目录下最新 `*-tech-spec-*` 文档
- **复用旧 feature 目录必须换新 round**：该 feature 目录下已有收口过的需求（存在 `integration/` 结论产物或已 `done` 票）时，本轮所有票的 `feature-slug` 一律写新 round 名 `<原feature-slug>-<YYYY-MM-DD>`，tech-spec/PRD/计划放同目录；不得沿用旧 slug——旧集成分支若残留会被 `delivery start` 新鲜度门拒绝，旧候选也会阻塞 finalize 清理。首轮需求维持 slug = 目录名不变
- tech-spec 不存在，或 frontmatter `status` ≠ `已确认（待拆解）` → 直接 `阻塞`；存量 tech-spec 缺 frontmatter：先按正文表头誊抄补全（只加 frontmatter 不动正文）再判定
- 补读：`./docs/GLOSSARY.md`、`docs/ARCHITECTURE.md` 与 `docs/ARCH-CHECK.json`（11 节架构总览与规则附录；tech-spec 3.2 列出总览 / 层 / 组 / 规则变更时作为拆票对齐基线）、该 feature-slug 最新 PRD（意图与稳定 AC 编号）、同目录 `acceptance-plan.yaml`（需求级功能验收分工）
- 读取 `./docs/EXPERIENCE.md` 的「开发阶段经验」节（如有）：按「适用模块」与「触发场景」筛出拆票 / 粒度 / 依赖 / declared-scope 类命中规则，补进拆票检查清单与粒度红线；规则与 tech-spec 冲突时以 tech-spec 为准，冲突点记入待确认项
- 开工前运行 `python3 ../shared/scripts/feature_closeout.py validate-plan --feature-dir ./dev/features/<feature-module>/<feature-slug>`：计划不存在、AC 结构 / 执行方式 / 判定归属 / 人工原因 / 自动断言不合法时停止拆票，退回 `/tech-spec` 补齐；不得由本 skill 代改计划

---

## 工作流

### Step 0：确定拆解输入

用几句话向用户说清本次拆解依据的是哪份方案（日期版本）与方案要点——动哪些模块、接口怎么变、有哪些硬约束、测试怎么接缝；确认这就是拆解基线。

### Step 1：起草垂直切片票

每张票是一条 **tracer bullet**：

- **纵向切穿所有相关层**（数据 / 逻辑 / 界面 / 测试），不是按层横切
- 独立可演示：完成后能演示一个完整的行为，不是半截基础设施
- 一张票的上下文能装进一个实现会话；装不下就再拆
- **粒度取向取少不取多**：每票有固定执行成本（独立实现对话 + 监督收口），票数是成本乘数；独立评审与候选复跑只在 feature 级各做一次。无独立 DoD、与邻票同接缝同目录的切片合并为一票——粒度红线是上限，不是目标
- **声明阻塞边**（blocked-by）：以 DAG 表达依赖，编号按依赖序
- **标注上游节锚点**：备注节写明本票涉及的 tech-spec 节（如 `#3.2 接口变更`、`#5 测试策略`，可多个）与 PRD 相关节——`/implement` 按锚点选读，不全量读 spec
- 首张票应是最细的端到端贯通（先打穿，再加厚）

例外——宽改造（大范围重构）：按 expand–contract 序列拆（加新形 → 分批迁移 → 删旧形），每批独立成票、可独立验收，不得拆出「中间态不可用」的票。

**DoD 必须写成可自动判定的验收命令**：每票 1–3 条「命令 + 期望退出码 + 可选期望输出」，从 tech-spec 测试策略的接缝与验收命令草案导出。格式固定为 `` `command` → 退出码 `0` `` 或 `` `command` → 退出码 `0`；期望输出 `ok` ``；省略输出时不做输出断言。禁止「功能正常」「符合预期」类不可判定表述；写不出可判定命令的验收标准，退回 `/tech-spec` 补测试策略。**DoD 只写功能实现度**：性能阈值、展示样式 / 版式类验收写进票的「人工确认项」节（可附采集命令，执行一次落盘、不阻塞交付），不进 DoD（`verify-policy.gate-scope`）。

**DoD 覆盖性自查（防 DoD 写窄——票级核对再严也只是照窄 DoD 核）**：每条 DoD 必须能映射到 tech-spec 节锚点；反过来，标注节的每个行为点至少被一条 DoD（或人工确认项）覆盖——覆盖不上的行为点，要么补 DoD，要么显式写进票的「不做什么」，不许无声漏掉。自查产物 = 「需求点 → 票 / DoD」覆盖表，门②时随票清单一并呈现（只在对话呈现，不落盘）。另做需求级 AC 对账：标准路径覆盖 PRD 全部稳定 AC；小变更覆盖 change-request 全部 delta 与 retained baseline，replace / retire 不得混入。`acceptance-plan.yaml` 的 documents / source 由同一机器校验器核对真实文档、hash、锚点与集合；票级 DoD 负责实现切片，需求级计划负责最终业务验收，二者不得相互替代。发现 AC 漏项、重复或分类理由不成立，退回 `/tech-spec`，不得在票里悄悄补一条就算闭环。

**架构契约变更归属（拆票阶段；tech-spec 3.2 列出总览 / 层 / 组 / 规则变更时强制）**：层 / 组 / 规则变更必须落入**恰好一张**承接票的 `declared-scope`，并包含 `docs/ARCH-CHECK.json` 与 `docs/ARCHITECTURE.md`。总览基线已由 tech-spec 写入时不在票内重复生成；若实现证据使总览失真，承接票可在同一 scope 内更新对应节并 `render write`。没有机器 ID 对账，也没有票级架构门；完整 arch-check 在 feature candidate 的 `freeze / check` 统一执行。无承接票的变更必须补票；确属纯内部且不改变层 / 组 / 依赖的事项须在 3.2 明确说明并经门②确认，不许无声漏掉。

**确定 declared-scope 与票粒度（确定性红线，不凭感觉）**：

- 从 tech-spec 的模块 / 接口 / 约束导出本票 `declared-scope`（预计触碰的**所有**代码、测试、配置路径 / glob；本票修改架构规则时含 `docs/ARCH-CHECK.json` 与 `docs/ARCHITECTURE.md`；生产子集用于粒度统计）
- **粒度红线**：预计生产代码文件数 > `verify-policy.ticket-max-files`（默认 10）、预计生产代码 LOC > `verify-policy.ticket-max-loc`（默认 400），或 scope 不可枚举（只能写出目录级 glob）→ 本票必须再拆；不得靠增加测试深度或留到集成阶段放行。实现期任一 changed path 越界都必须重新确认票并生成新 context，实测超任一红线同样停止并退回再拆
- **票级验证统一为 DoD + fast**，不为迁移 / 鉴权、公共接口、共享模块或 DAG 终点设置额外测试范围。DoD 仍须可执行且覆盖票面行为；feature / main 的 CI full 是套件级安全网，不是写窄票 DoD 的理由
- **集成票不再强制额外测试**：DAG 中无后继（没有其他票 blocked-by 它）的票标记为集成票，用于 `development.escalated` 路由与 feature 级核对；它仍只跑本票 DoD + fast，不做票级 conformance。全部票合入集成分支后，feature 级评审、候选复跑和 conformance 在 candidate 统一做一次；所有票同样受上述粒度红线约束

**判定票类型 `type`**：修复既有缺陷（现象 → 根因 → 修复）的票标 `type: bug`，其余标 `feature`（缺省）。bug 票的 DoD 必须含一条复现该 bug 的回归测试命令——写不出复现测试的 bug 票，退回 `/tech-spec` 补测试策略；实现期该票须过回归有效性验证（revert 修复见红，见 `/implement` Step 5）

**本地 proposed 草稿**：按 `shared/templates/issue.md` 在 `issues/` 下形成每票一个文件，frontmatter 写 `status: proposed`、唯一 `blocked-by`、完整 `declared-scope`。这只是门②前的本地文档草稿，不是 tracker 发布，也不得派发。每票备注必须引用同一 active tech-spec 和对应 How/Why 决策锚点；历史其他 spec 的票不得混入当前组。

### Step 2：门②——清单确认（阻塞型，必须人批准）

先运行两道只读检查，任一失败就回 Step 1 或 `/tech-spec`，不得把失败草稿交给用户批准：

```bash
python3 ../shared/scripts/feature_closeout.py validate-plan --feature-dir <需求目录>
python3 ../shared/scripts/ticket_context.py check-plan --issue <当前组任一票> --repo <项目根>
```

若 tech-spec 3.2 声明了规则变更，另跑 `arch_check.py validate --repo <项目根> --rules docs/ARCH-CHECK.json`（只读）。`check-plan` 检查组内 DAG、scope 重叠串行、DoD 与 AC 计划；不做架构 ID 对账。机器不判断 tracer-bullet 的业务语义，门②仍须审覆盖与粒度。

向用户呈现编号清单，一次审全部：

- 每票一行用大白话说清：这票交付什么、完成后系统 / 用户得到什么、怎么算做完（DoD 验收命令 + fast）、依赖哪票、预计触碰哪些生产文件；`declared-scope` / `blocked-by` 等内部词不直接贴给用户，换成日常说法
- 附「需求点 → 票 / DoD」覆盖表（Step 1 覆盖性自查产物）：tech-spec 标注节的每个行为点落在哪张票、由哪条验收命令兜住，显式排除的写在哪张票的「不做什么」——人确认的不只是票清单，还有需求覆盖关系
- 附功能验收分工摘要：AI 完整验收、AI 执行人工复核、纯人工验收各几条；逐条说明人工原因。它是既定验收计划的可读呈现，不新增一次人工审批，也不把票级 DoD 当成最终功能验收
- 附整体依赖关系与建议执行顺序（哪几票可以立即开工、谁挡谁、收尾票是哪张），并说明集成票始终做 feature 级 conformance；完整回归由 feature / main 的 CI full 兜底
- 按「提问格式与对人表达」确认，问题说人话：拆得粗细合不合适、先后顺序对不对、验收方式能不能机器判定、DoD + fast 是否覆盖本票行为
- 用户要求调整 → 修订后重新呈现；**未获确认不发布任何票**

粒度失败路径：单票改动横跨超过两个模块，预计生产文件数 > `ticket-max-files`、预计生产 LOC > `ticket-max-loc`，或 scope 不可枚举 → 自动再拆或标记需人工裁决，不硬发；任何额外测试都不能豁免粒度红线。

### Step 3：发布

确认后按 `agents-config.tracker` 发布：

- **local 模式**（默认）：把门②前已写入的本地 proposed 草稿置为 `status: confirmed`，不得借发布改写标题 / DoD / declared-scope；若内容有变，重新运行 checker 并过门②
- **gitlab 模式**：本地票文件同样先由 proposed 置 confirmed，另用 `glab issue create` 逐票建 issue：标题 `[<feature-slug>] issue-NNN <标题>`，正文取票文件内容，label `ready-for-agent`；把 IID / URL 回写票文件 frontmatter。glab 命令失败：报告错误并保持 local 产物完整，不阻塞
- 修改父级 issue / tech-spec / PRD：禁止

### Step 4：收尾与派发协议（门②后由主会话自动执行）

- 向用户说清：共拆出几票、哪几票可以立即开工（没有前置依赖）、关键的先后手关系一句话带过
- 提示时附离场说明，用大白话：你可以宣布「离场自动跑」进入无人值守——我会先把「我能自主做什么、绝不做什么、预算和额度上限、什么时候停」写下来给你过目，你点头后我自动跑完全程（见「监督循环 · 无人值守模式」）
- 本 skill 的职责到此结束；**门②是执行段之前唯一的例行人工确认点**。之后由主会话按「派发协议」与「监督循环」逐票派发、监督合并与清场，无例行人工审核；异常或越界问题通过 human-decision 单点处理（实现动作发生在独立对话内，不违反本会话 DOC_MODE）

---

## 硬约束

- tech-spec 未确认不开拆；`acceptance-plan.yaml` 缺失或校验不过不开拆；拆解不改变方案与验收计划内容
- 门②未过不发布；发布只写票文件与 tracker issue，不碰源码
- DoD 不可自动判定的票不允许存在；tech-spec 3.2 非空而票清单无任何契约 DoD 的，不许过门②
- 票一旦 confirmed，标题 / DoD / declared-scope 不得由本 skill 或实现方单方面修改；变更须重新过门②并生成新 ticket context。实现期任何 changed path 越界都直接 FAIL，超过 `ticket-max-files` / `ticket-max-loc` 必须退回再拆，不得以登记漂移或补跑测试代替
- 分工：派发、监督与合并在主会话，实现与评审各自在独立的新开对话；主会话不代写实现、不代评
- 用词遵循 GLOSSARY 标准术语
