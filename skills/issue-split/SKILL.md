---
name: issue-split
version: 0.6.1
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

本 skill 可额外执行一次只读文档校验：`python3 ../shared/scripts/feature_closeout.py validate-plan --feature-dir <需求目录>`。它只验证功能验收计划，不属于实现或测试执行；其他实现导向命令限制不变。

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

提问分两种形态：**拷打轮次**（需求澄清，按「拷打规则（Grilling）」，一轮可问多个相互独立的 frontier 问题）与**阻塞型单点确认**（`feature-slug` 歧义裁决、模式 / 方向批准、术语冲突裁决、是否进入下一阶段等，一次只问一个）。本节规则对两种形态都适用。

先把未决问题归类为三种之一：

- `可假设继续`：对当前判断影响较小——带默认假设继续，并在输出中显式写出假设
- `必须提问后继续`：影响核心判断、关键前提、模式选择、优先级、规则边界或最终结论，不能绕过——先发问再继续，不用「可以先假设」绕过，也不沉入「待确认项」；提问用 `AskUserQuestion`，不自行脑补答案
- `仅记录为低优先级风险`：不影响当前判断——暂记为风险或待确认项

每次提问遵循：

1. **先说人话背景**：用 2-4 句大白话讲清——在做什么（哪份需求 / 哪块改动）、进行到哪一步、现在卡在哪；不堆术语，不贴内部状态
2. **说透决策点**：要用户定的到底是什么，一句话说清楚；涉及需求 / 代码改动时讲清改什么、影响面（哪些模块 / 哪些人 / 什么行为变化）
3. **说明为什么必须问**：指出影响哪一个核心判断，不问清会导致什么判断失真或返工
4. **给推荐方向**：先给推荐与理由，显式说明仍等用户拍板，不伪装成结论
5. **分叉题再给 A / B / C**：A 推荐、B 保守或替代、C 激进 / 延后 / 不同路径，每个选项用大白话说清做法与代价；非分叉题不强行给选项
6. **告诉用户怎么回**：一句话即可（「回 A 或 B」「回 同意 / 调整」），不让用户猜格式
7. **单点确认一次只问一个**，不合并多个决策点（拷打轮次不受此限）

禁区（机械化语言）：

- 内部词不说给用户：拷打 / frontier / 门② / BLOCKER / 升档 / 状态码 / 字段名堆叠——协议词只做内部判定，对人一律换成日常说法
- 不用「回显」「输出完成状态」这类机器动作描述对人说话；报告与状态表达按「完成状态协议 · 对人表达」执行
- 不用模板腔占位符对用户说话（如直接贴 `<feature-module>/<feature-slug>`），换成中文名称与自然句

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
      issues/
        issue-001-<短名>.md
      impl/
        issue-001/
          impl-log-issue-001.md
          evidence-issue-001.md
          conformance-issue-001.md
      reviews/
        <feature-summary>-review-issue-001-YYYY-MM-DD.md
      mr/
        <feature-summary>-mr-issue-001-YYYY-MM-DD.md
      integration/                # 需求收口（feature-closeout: local-human）产物，结构见 shared/templates/feature-closeout.md
        candidate.json            #   冻结候选与计划哈希（feature_closeout.py freeze 写）
        acceptance-plan.json      #   冻结时的计划快照
        evidence.md               #   自动证据头
        business-results.json     #   每条 AC 的实际执行、AI 判定 + 人工结果
        acceptance-report.md      #   合并前功能验收报告（feature_closeout.py report 生成）
        confirmation.md           #   人确认原话 + 候选 tree
        conformance-feature.md    #   feature 级覆盖核对
        verify-log.md             #   候选变更与回修轨迹
        raw/  artifacts/
      unattended.md               # 无人值守授权边界（人离场时才产生）
      decisions.md                # 无人值守越界决策记录
      progress.md                 # 无人值守逐票进度留痕
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
- `unattended.md` / `decisions.md` / `progress.md` 是无人值守模式（人离场自动跑）的授权与留痕，仅离场时产生；语义见「监督循环」
- dev/ 产物（票文件 / impl-log / evidence / 评审报告及其引用的捕获物）一律写**主工作区**的 dev/ 树——worktree 是独立 checkout，写进它的 dev/ 清场即丢；worktree 内只放代码与 `.logs/` 等临时产物。**worktree 内不得出现 dev/ 实体副本**（每票一份完整副本 = 磁盘随票线性膨胀，清场还可能丢真源）：建 worktree 后立即把 `<worktree>/dev` 软链到主工作区 dev/（仓库根 `.worktrees/<issue-id>/` 下用相对目标 `../../dev`；宿主自管沙箱改用主工作区 dev/ 的绝对路径），此后 worktree 内对 `./dev/` 的读写自动落主工作区；软链建不成（如 Windows 权限）退回用主工作区绝对路径读写，仍不得复制。唯一例外：票 DoD 声明契约更新（或 flow-back 补记）时，`docs/ARCHITECTURE.md`「接口现行态」表在 worktree 分支内就地更新、随代码同一 MR 合入（开口规则见 GLOSSARY dev 段）
- worktree 统一放仓库根 `.worktrees/<issue-id>/` 并加入 `.gitignore`；宿主自管会话沙箱（如 Codex）时以沙箱为隔离、不嵌套开此目录，但分支名仍按 `feat/<feature-slug>-<issue-id>`——清场一律按分支名发现（见「派发协议」），不按路径猜
- 命名与版本规则沿用 `./docs/` 树约定；读取模式匹配：`*-tech-spec-*`、`issues/issue-*`、`*-review-issue-*`、`*-mr-issue-*`、`prototypes/*-verdict-*`

## 留存与体积控制（dev/ 防膨胀）

dev/ 通常不入库又无 Git 兜底，体积失控主因是迭代中间产物全量留存。规则：

- **中间轮产物只留最终轮 + 结论**：迭代快照 / 截图 / 页面捕获等，中间轮在下一轮通过后即删，过程结论以文本写进 impl-log / evidence；收口快照按相对路径引用原件，禁止机械复制整目录（如 raw/ 整份拷进 final/）
- **可重下载的缓存不进 dev/**：安装包 / wheelhouse / keyring / 依赖缓存等官网可重下的内容，临时存放用完即删；确需留存只记版本号与获取命令
- **merge 中间物即合即删**：`*.ours` / `*.theirs` / `*.orig` / merge-* 工作目录在合并提交后删除——git 历史可取回
- **删除判据（三满足其一）**：① 可重新下载；② 原件仍在（字节级 diff 验证）；③ 字节相同且文档零引用。删前后 `grep -rn` 查悬空引用；截图 / 录屏等二进制证据只留被文档引用的
- 票 `done` 收口时按本节清一次本票 impl 目录（中间轮 / 缓存 / merge 中间物），保留票文件 / impl-log / 评审报告 / 被引用证据

## Dev 配置（agents-config）

dev 阶段 skill 开始工作前，先读取 `./dev/agents-config.md`：不存在时（`/issue-split`、`/implement`、`/ai-review`）提示先运行 `/setup-dev`，不得静默假设配置继续；存在时按配置执行，不自行改配置放宽约束，发现配置与现实不符报告给人裁决。

必要字段（结构见 `shared/templates/agents-config.md`）：

- `tracker`：`local`（markdown 票文件）或 `gitlab`（glab CLI）
- `main-branch`：主分支名（worktree 与 merge request 的基准）
- `repo-level`：A / B / C 仓库等级；A 类仓（飞行软件 / 涉密）禁止进入 `/implement`，只允许只读辅助
- `quality-gate`：只配置票级 `fast` 命令（秒级，类型 / lint / 单文件测试）。每张票在实现、自修复、评审回修与 merge / close 时都只执行自己的 DoD + `quality-gate.fast`；不得按文件数、LOC、模块影响面或风险名义改跑其他票级套件，也不设置票级 scoped / full 命令
- `host-budget`：本机重测试算力预算（`total` worker 总数 / `max-runs` 套数 / `queue-timeout` 排队秒数上限），只用于 local-human 需求收口的关联选择集与单项失败归因；fast 不受限。真源是机器级文件 `~/.cache/agent-dev/slots/budget.json`（同机各仓共享一份），agents-config 里只是镜像记录，执行以机器级文件为准；CI full 使用判读机自己的预算
- `verify-policy`：票粒度、失败归因与测试职责边界。
  - `ticket-max-files: 10` 与 `ticket-max-loc: 400` 只是拆票粒度上限：拆票时预估超任一上限必须再拆；实现期实测超任一上限必须 re-split，无法自动再拆则置 `needs-human`，不得把它解释成测试范围或模型路由的升级理由
  - 实测 diff 超出 `declared-scope` 时在 impl-log「漂移记录」逐文件登记；超粒度上限按上述 re-split / needs-human 处理，不超上限且仍属本票范围时登记后继续
  - `gate-scope`：AI 交付门只保证功能实现度（DoD 行为、契约、回归）；`non-gating` 类（默认性能基准 perf、展示样式 display）只在最终 commit 执行一次并落盘，不阻塞交付、不进 `all-passed` 分母，由人工验收确认；缺外部依赖的用例按分类清单显式 skip 并在 evidence 列出，不算失败也不算通过
  - `attribution`：先在失败默认按 `quarantine` 隔离。入册须 base 侧同签名稳定失败，且每条带 owner / 去向（修复票 / 永久删除决议）/ 首录日期，无去向不入册；清单内用例以 skip（标 quarantined）执行，不进 `all-passed` 分母，清单外失败按本票引入处理并只对单项补跑 base 侧对照。未执行项不是先在失败，不得入册冒充通过；feature 收口的预算延期只写 feature 级 deferred 证据。`two-sided` 为保守档（每条失败双侧同命令对照）。隔离清单由 CI full 报告刷新，超 `quarantine-max-age-days`（默认 14）的条目在 CI 回归记录与主会话监督输出中呈现
  - **full 只有 CI 入口**：本机、票 worktree、集成 worktree 与监督循环均不得执行 full。`ci-promote` 的 feature / branch / main 放行必须执行 `process_check.py --phase promote --ci-verdict <...> --ci-run-proof <trusted-gitea-run-proof.json>`，核对 CI verdict、run proof、实际 HEAD commit 与 tree 同源；分支前进或 tree 改变即重新验证。人工重跑也只能触发同一 CI workflow，不设本机补跑出口
  - `daily-regression` 如启用，只能由 CI schedule / 手工 dispatch 触发同一个 CI full workflow；记录 CI run / verdict / tree / 合入区间 / 归因与隔离清单变化。漏跑时只能在 CI 补触发，不得创建本机 worktree 或保存可冒充 CI 判决的本地引用。pandu 不启用 nightly，push 不自动部署
- `feature-closeout`：需求收口方式。`mode: ci-promote`（默认，缺此节同）以 feature / branch / main 的同 tree CI verdict + run proof 放行；`mode: local-human` 时全部票合入每需求独立集成分支（`integration-branch`，默认 `feat/<feature-slug>`）后，本机冻结候选与 `acceptance-plan.yaml` 计划哈希 → Agent 自动证据（`scoped_select.py` 关联选择集，indirect 预算 `verify-budget-seconds`；另按计划执行 command / browser-agent 功能 AC）→ 生成正式功能验收报告 → 按报告完成人工实测 / 复核 / 豁免 → 人显式确认 → 合 main → 按 `prod` 发布并冒烟，main push 后 CI full 作安全网而非合 main 前置。`selection-map` 是可选的 dir-map 映射表路径，缺省 / `null` 使用 `dev/scoped-map.yaml`；显式配置时给集成选择器追加 `--map <路径>`。feature 级 scoped 报告与 deferred 证据保留。`verify-instance` 有则部署到测试实例，`null` 则在集成 worktree 用开发启动器；功能验收计划的 `executor`（command / browser-agent / human）与 `oracle`（auto / auto+human-review / human）分离，合并前必须有脚本生成且未过期的 `acceptance-report.md`。main 冻结后只在 `main-drift-ignore` 路径前进时候选仍有效。门禁由 `shared/scripts/feature_closeout.py` 判定，流程见「监督循环 · 需求收口」。**票的目标分支**（worktree 基准、rebase 目标、合并目标、process_check 的 diff base、账实对账与清场的「已合并」判定）一律用 `feature_closeout.py target --issue <票文件>` 解析：票 frontmatter `target-branch` 显式指定 > local-human 下代入 `integration-branch` > `main-branch`；`since`（YYYY-MM-DD）之前创建的存量票保持 `main-branch` 默认。local-human 下票只合进集成分支，main 只接收需求收口的整需求合并；目标分支不存在即停，不退回 main
- `max-fix-rounds`：自修复轮次上限，默认 99；`review-round-alert`（默认 3）：同票评审达此轮数时主会话介入判断拆票 / 缩范围 / 升级模型（预警，不是硬停）
- `conformance`：需求符合性核对（主模型校验）——主会话在合并前核对「要的有没有、偏没偏」，与 ai-review 正交（两者都使用当前宿主 `supervisor-review`；评审判代码质量，需求语义符合性由主会话或另一独立监督评审会话核对）。方法：先独立枚举需求点（不看 impl-log 覆盖矩阵），再对照矩阵打开代码逐条核语义；结论落盘 `impl/<issue-id>/conformance-<issue-id>.md`，`process_check.py --phase close` 把关。`mode: always`（默认）每票必核；`sampled` 时所有非集成票都按 `sample-rate` 抽查，未抽中写 `verdict: skipped`，集成票一律必核；`checker: main-session`（默认亲核）或 `dispatched`（派发独立监督评审对话，上下文紧张时用）。缺此节按默认执行，即默认启用
- `base-freeze`：票开工时固定 base，实现与评审期间不追集成分支，只在合入前 rebase 一次；冲突解决后重跑受影响 DoD + fast
- `mechanical-refresh`：预授权机械刷新白名单（路径 glob + 字段 / 生成块）；diff 可机器证明只动所列字段时直接刷新并登记，不停等人；白名单外的既有测试 / 夹具改动仍按红线处理——唯一例外「需求驱动更新」：票 DoD / tech-spec 明确改变的行为，其冲突旧测试随票更新并逐条登记映射（见 /implement Step 2），不按红线停
- `artifact-budget`：impl-log / evidence 正文行数与 impl 目录体积上限；正文只留结论与指针，过程放 raw，中间轮原始产物只留最终轮
- `redlines`：红线文件 glob 清单（协议文件、密钥配置、验收判定文件等），命中即停，不得绕过
- `dispatch`：执行与模型配置。`schema-version: 1` + `executor: subagent`；`hosts.<claude-code|codex|zcode|dsh>` 按宿主分条。每条必须有：`supervisor-review.model / effort`（主会话监督、符合性核对与独立 `/ai-review` 共用角色模型，但评审另开独立会话）；`development.default / escalated.model / effort`（独立 `/implement`：普通票用 default，集成票 / 高风险票及评审预警后的回修用 escalated，绝不按测试范围路由）。开工前运行 `python3 ../shared/scripts/dispatch_config.py validate --config ./dev/agents-config.md --catalog ./dev/dispatch-model-catalog.json`；用 `resolve` 取当前宿主原生参数，缺当前宿主、旧 `dispatch.model`、字段缺失或模型 / effort 不在实时目录即停，不跨宿主套用、不静默继承。原生角色文件由 `render` 从本配置生成，不手工另建第二份模型真源。`parallel-max`：frontier 中 declared-scope 互不相交的票可并行派发的上限
- `ocr.mode`：评审模式。默认 `delegate`——ocr 只做文件筛选与规则解析（不调 LLM），评审由宿主 agent 内新开的独立对话执行；`local` / `ci` 为可选增强，需配置 LLM 端点


## 测试位置

- **票级循环内验证在本机**：TDD 见红 / 变绿、每条 DoD、fast、单项失败归因、独立评审复测与主会话验收复跑都在本票 worktree 执行；merge / close 仍只核 DoD + fast。fast 不经整机槽位包装，也不新增把票级测试提交到其他机器执行的通道
- **local-human 需求级验证在本机**：需求收口的 `scoped_select.py` 关联选择集与其单项失败归因在集成 worktree 经 `shared/scripts/test_slots.py run` 包装执行，预算与 feature 级 deferred 证据按 `feature-closeout` 配置处理；`acceptance-plan.yaml` 声明的 command / browser-agent 功能 AC 也在同一候选测试环境执行并落标准证据。它们不是票级交付门，也不得扩大为 full；宿主无浏览器能力时对应 AC 记 unverified，不伪造通过
- **远端只承接 CI full**：已登记 feature / branch / main 的 push，以及同一 CI workflow 的定时 / 手工触发，是完整套件的唯一入口。CI 在判读机独立 checkout 对应提交并自行调用判读机预算封装；本机、票内、监督循环都不向其提交命令、也不补跑 full。CI 不可用时保持未验证，不降级本机补跑

## 派发协议（标准角色配置）

dev 阶段的「派发」只依赖一个宿主无关原语：**宿主 agent 自动新开一个独立对话执行派发提示词**。模型不再靠提示词口头指定，而是由同一份 `agents-config` 校验、解析并绑定到宿主原生角色。

### 配置校验与角色解析

- 主会话开始监督、实现会话启动、发起评审前都先跑：`python3 ../shared/scripts/dispatch_config.py validate --config ./dev/agents-config.md --catalog ./dev/dispatch-model-catalog.json`（目录由 `/setup-dev` 从宿主实时能力生成，结构 `{host: {model: [effort...]}}`）。旧 `dispatch.model`、缺字段、当前宿主无条目、模型或 effort 不可用 → 停下修正配置，不跨宿主套用、不自动降级。
- **主会话监督**：取 `dispatch.hosts.<当前宿主>.supervisor-review`，用 `resolve --role supervisor` 核对当前会话的实际 model / effort。不符即停下请人按配置新开或切换主会话；配置文件不会 retroactively 切换已经存在的会话，不得把「写进配置」冒充「当前已生效」。
- **开发实现**：普通票取 `development.default`，集成票 / 高风险票及评审达 `review-round-alert` 轮后的续修取 `development.escalated`；用 `resolve --role development` 得到原生参数。不得按 light / scoped / full 或任何测试范围路由模型。
- **独立评审与派发的符合性核对**：固定取 `supervisor-review`，用 `resolve --role review`；不随开发角色切换，但必须另开独立对话，写查分离不变。
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
5. 分支删除只能在 worktree 清空之后：gitlab 模式由 `glab mr merge --delete-source-branch` 带删远端源分支；local 模式核对已并入本票目标分支（`feature_closeout.py target`；local-human 下为集成分支）后 `git branch -D feat/<feature-slug>-<issue-id>`（有远端同名再 `git push origin --delete`）
6. `rmdir .worktrees 2>/dev/null || true` 清空父目录

## 监督循环（主会话职责，直到完成合并）

### 状态与事件写入契约（progress.jsonl + dispatch 块）

票的 `status` 字段与 `progress.jsonl` 事件流是面板、下钻与新会话接力的唯一机器真源，写入规则：

- **谁改状态谁写事件**：任何把票 `status` 置变的动作，同一笔追加一行 JSON 到 `./dev/features/<feature-module>/<feature-slug>/progress.jsonl`，至少含 `ts`（本地 `%Y-%m-%d %H:%M`）/ `event` / `issue`。事件词表固定 11 个，不发明新词：票级 8 个——`dispatch`（附 host / model / attempts）、`returned`（实现对话回报完成）、`verify-fail`（附 n 与失败摘要）、`mr-submitted`（附 MR 或草案路径）、`needs-human`（附原因）、`merged`（附 host 与合并 commit）、`circuit-break`、`digest`；需求级 3 个（`issue: feature`，仅 `feature-closeout.mode: local-human`，由 `feature_closeout.py` 在门禁通过时写入）——`feature-verified`（自动阶段完成且当前验收报告已生成，可能仍有明确列出的未验证 / 待人工项）、`feature-confirmed`（人工确认通过合并前核对）、`feature-merged`（附合 main 的 commit）。坏行直接跳过，不重写历史
- **dispatch 块**：首次派发时主会话在票 frontmatter 写 `dispatch: {host, model, dispatched-at, attempts: 1}`；打回重派 `attempts` +1 并更新 `dispatched-at`
- **合并动作序列（三步一笔）**：执行合并命令 → 票置 `done` → 追加 `merged` 事件（含 host 与 commit）。三步连续完成，不许只做其一——合并而票不关，票就会像僵尸一样挂在板上（实测教训：合并一个月后票仍停在评审中）
- **账实对账（每轮，随第 6 步 worktree 对账同做）**：票 `status` 仍在途（in-progress / in-review / mr-submitted）而其分支已删或合并提交已在本票目标分支（`feature_closeout.py target`；local-human 下为集成分支）→ 漏关账：补置 `done` 并补写 `merged` 事件（note 标 `backfill`）；合并既成事实且 two-sided 已留档，不重跑验收

门②（票清单确认）通过后，主会话自动进入监督循环，**派出后不停手，监督到每张票合并完成**：

1. **取票**：只读 `./dev/features/<feature-module>/<feature-slug>/issues/` 票文件的 frontmatter（`status` / `blocked-by`），重算 frontier（blocked-by 全部 `done` 的票）；票正文由被派发的实现对话自读，主会话不读
2. **派发**：`feature-closeout.mode: local-human` 时首次派发前先按「需求收口」第 1 步建好本需求集成分支，派发提示词写明目标分支（`feature_closeout.py target --issue <票文件>` 的输出）；按「派发协议」宿主适配机制为 frontier 票新开独立对话跑 `/implement issue-NNN`；派发前按「状态与事件写入契约」写票 `dispatch` 块并追加 `dispatch` 事件；frontier 中 `declared-scope` 两两不相交的票按 `dispatch.parallel-max` 并行派发、各自 worktree；有交集或判定有疑义的串行，不计算关联测试闭包来决定能否并行。并行票合入按完成先后，后合者合入前 rebase 一次
3. **跟踪**：被派发对话的结构化回报（分支名 / worktree 路径 / MR IID 或草案路径 / 证据与评审报告路径 / 遗留 Medium-Low）只是线索，不作为成功依据；回报到达即追加 `returned` 事件
4. **收口**：先跑**流程核对**：`python3 ../shared/scripts/process_check.py --issue <票文件> --repo <票 worktree> --phase merge --json <impl/<issue-id>/process-check-merge.json>`——红线登记、「漂移记录」、`ticket-max-files` / `ticket-max-loc` 粒度上限、evidence 的 `all-passed: true`、`commit` 与 HEAD 的本票文件一致、impl-log 需求覆盖矩阵一次判完，退出码非 0 即退回实现对话补齐，不进合并；实测超粒度上限只能 re-split，无法自动再拆则置 `needs-human`。主会话不再手工逐字段核对 evidence 头。再核对评审报告头部结论 `pass` / `pass-with-notes`、MR ready（评审结论只判代码，测试执行状态以本步核对为准）。然后**验收复跑**（回执只是主张，不是证据）：主会话亲自发起复跑（在本票 worktree 对 evidence 记录的 `commit` 执行，禁止引用实现对话自述）；只重跑 evidence 记录的每条 DoD 验收命令与 `quality-gate.fast`，退出码须全为 0 且输出与 evidence 记录无明显矛盾；worktree 已被实现对话自清时按分支名重建（`git worktree add .worktrees/<issue-id> feat/<feature-slug>-<issue-id>`，随后补 dev/ 软链 `ln -s ../../dev .worktrees/<issue-id>/dev`）。不得加跑票级关联套件或完整套件，也不得用人工指派抽查绕过这个边界。复跑通过后执行**需求符合性核对**（主模型校验，与 ai-review 正交——评审判「写得好不好」，本步判「要的有没有、偏没偏」，专门抓 diff 与退出码里看不见的缺席与偏差）：
   - **反向枚举先行**：先不看 impl-log 覆盖矩阵，从票「要做什么 / 不做什么」+ tech-spec 锚点节独立枚举需求点清单，防止被实现方叙事带偏
   - **逐条核映射**：对照 impl-log「需求覆盖矩阵」，打开映射指向的代码位置核对语义（不只看 evidence 退出码；至少覆盖全部 ★ 关键路径行），逐条判 `已实现 / 语义偏差 / 未实现`；只照抄矩阵不打开代码等于没核
   - **集成票加做 feature 级核对**：对 tech-spec 全节（不只票锚点）枚举 feature 级需求点，对照本 feature 各票覆盖矩阵的并集，抓票间接缝缺口（A 票以为 B 票做）
   - 结论按模板 `shared/templates/conformance.md` 落盘 `impl/<issue-id>/conformance-<issue-id>.md`（`verdict: conform / deviate / missing` + `commit`；`deviate / missing` 逐条标打回原因分类：未实现 / 语义偏差 / DoD 本身窄，供复盘归因）
   - **执行身份**：默认主会话亲核（输入有界：票全文 + spec 锚点节 + diff stat + 覆盖矩阵 + 抽查代码，不读全量代码）；其模型 / effort 必须已按派发协议核对为当前宿主 `supervisor-review`。上下文紧张时按派发协议开独立对话执行（同样取 `supervisor-review`，用 `resolve --role review` 绑定独立角色），主会话只读结论；核对产物全部落盘，上下文只留结论行
   - **抽样触发**按 `conformance.mode`：`always`（默认）每票必核；`sampled` 时所有非集成票都按 `conformance.sample-rate` 抽查（未抽中写 `verdict: skipped` 落盘留痕），集成票一律必核
   - 核对通过后跑**收口流程核对** `--phase close`（= `--phase merge` 全部项 + conformance 文件存在、`verdict: conform`、commit 与 HEAD 本票文件一致），通过才执行**合并动作序列**——合并（gitlab：`glab mr merge <IID> --delete-source-branch`；local：目标分支所在 checkout `git merge --no-ff feat/<feature-slug>-<issue-id>`——local-human 下在集成 worktree 合进集成分支，**票永不直接合进 main**）→ 票置 `done` → 追加 `merged` 事件，三步一笔（见「状态与事件写入契约」）——随后按「派发协议」清场序列收尾；合并冲突先在 worktree 内 rebase 目标分支、重跑 `quality-gate.fast` 再合
4a. **CI full 与 branch / main 放行**：票级 merge / close 永远只核本票 DoD + fast，不要求或接受任何票级 scoped / full 证据，也不得在本机任何 worktree 执行 full。`ci-promote` 下，票合入需求分支只表示代码已集成；主会话随后普通 push 已登记的 feature / 集成分支，由判读机 CI 在被 push 提交上执行 full。feature 分支或 main 的实际放行必须另核 CI run 的 ref、commit、tree、pass 与需求级验收：执行 `process_check.py --phase promote --ci-verdict <branch-ci-verdict.json> --ci-run-proof <trusted-gitea-run-proof.json>`，verdict 与 run proof 必须绑定实际 HEAD 的同一 tree，未通过不得合并 main；分支前进或 tree 改变即重新验证。`local-human` 的 main 放行改走下方「需求收口（local-human）」，同 tree CI promote 不是合 main 前置，main push 后的 CI full 是安全网。清单外失败由 CI 执行器在 base 隔离检出中自动补跑单项并归因，agent 只读 `new_failures` / `preexisting_failures` / `unattributed_failures` 与签名指针，不读整套日志。符合自动回退条件的 feature 分支由独立 CI job 二分责任提交、复跑原失败项、生成回修票并普通 push；回退后的新 CI 通过才恢复放行资格。责任不唯一、revert 冲突、复跑仍红或基线不可用时保持未验证，不自动试错，也不回退 main / query / 票分支。pandu 的 full 只有 push / 手工 dispatch CI 路径：不启用 nightly，不由 push 自动部署。每日回归与人工重跑也只走同一 CI workflow，本机不可用时不降级补跑
5. **打回**（验收复跑失败 / 符合性核对未过，共用 `verify-fails` 计数）：复跑任一命令失败或输出与 evidence 明显矛盾，或符合性核对 `deviate / missing` → 不合并：票 frontmatter `verify-fails` +1 并追加 `verify-fail` 事件（附次数与失败摘要）；未达 2 时票从 `mr-submitted` 退回 `in-progress`，失败命令与实际输出（或缺口清单）按派发协议派回实现对话修复（等同 BLOCKER 回修路径）；修复后重跑受影响 DoD 命令、聚焦缺口条目与回修 diff 重核，重新过评审并转 ready 后再置 `mr-submitted`，再跑 `--phase close` 复核；`verify-fails` 达 2 → 不再自动打回，票置 `needs-human` 按第 7 步处理
6. **推进**：每轮先做 worktree 与账实对账（`git worktree list --porcelain` 与票状态比对，票已 `done` 但 worktree 仍在的按清场序列补删；`in-review` / `needs-human` 票的保留并列入总账；并按「状态与事件写入契约」核对漏关账——在途票的分支已删或合并提交已进目标分支的，补置 `done` 补写 `merged`），再重算 frontier 回到第 1 步；全部 `done` 后：`feature-closeout.mode: local-human` 时先进入「需求收口（local-human）」，最终功能验收报告即收尾总结的主体；否则直接向用户做收尾总结（语言按「完成状态协议 · 对人表达」）：先一句话说整体结果（共几票、全合并了还是有关卡），再逐票一句大白话（这票交付了什么、合在哪个提交、有什么遗留要注意、worktree 已清或为何保留），最后列证据与评审报告路径索引（冠以「想细看：」）
6a. **每日 CI 回归结果接收**：每轮读 `daily-regression.record-dir` 最新 CI 记录，核对其 run / verdict / run proof 与 tree——有清单外新失败且已由 CI 二分到合入提交 → 对应票置 `needs-human`、按派发协议开回修票进 frontier，合入不暂停；存在进行中 feature 而最近记录早于 `max-gap-days` → 只补触发同一判读机 CI workflow，不创建本机 worktree、不在本机执行。记录中含超期隔离条目清单（超 `quarantine-max-age-days`）时，在向人的进度 / 收尾输出里逐条说清：哪条隔离测试挂了多久、登记的去向是什么、建议销记还是催修（说人话，不甩字段名）
7. **唯一暂停条件**：回报 `needs-human` / `阻塞`、`verify-fails` 达 2、合并冲突自动 rebase 后仍无法解决、其他无法自行裁决的问题——与人单点确认后再继续；其余（含 BLOCKER 回修、fast 自修复、未达标的复跑打回）一律不问人

### 需求收口（local-human：本机自动证据 + 功能验收报告 + 人工确认合 main）

`feature-closeout.mode: local-human` 时，全部票 `done` 后主会话按下列步骤收口；门禁全部由 `python3 ../shared/scripts/feature_closeout.py` 机器判定，产物写主工作区 `integration/`（结构见 `shared/templates/feature-closeout.md`），不写集成 worktree、不提交到集成分支。分工真源是需求目录 `acceptance-plan.yaml`：**`executor` 决定谁执行，`oracle` 决定谁最终验收**。浏览器 Agent 能操作页面不等于能替人判断业务合理性。

1. **集成分支每需求独立**：门②通过后、首次派发前建 `feature-closeout.integration-branch`（默认 `feat/<feature-slug>`，显式 start-point `main-branch`），worktree `.worktrees/integration-<feature-slug>/`，登记进 agents-config 集成分支表；一个集成分支只承载一个需求（共用分支上的需求只能整分支一起收口）
2. **收口前提**：全票 `done` 且有 `merged` 事件、每票 conformance `conform`、无未决 needs-human；feature 级覆盖核对落 `integration/conformance-feature.md`；PRD 的稳定 AC 已全部进入 `acceptance-plan.yaml` 且 `validate-plan` 通过
3. **同步并冻结代码与计划**：集成 worktree 内 `git merge <main-branch>`（冲突在集成分支解决）→ `feature_closeout.py freeze --feature-dir <主工作区 feature 目录> --repo <集成 worktree>`（同一 feature 目录分多轮需求各自收口时加 `--round <轮名> --issues <本轮票>`，产物落 `integration/<轮名>/`、集成分支按轮名代入，后续各命令同带 `--round`）；冻结记录候选 tree、计划哈希与计划快照。冻结后集成分支或验收计划不得变化；回修或计划换版即重新 freeze，旧业务结果、人工确认与报告失效
4. **准备候选测试环境**：有 `feature-closeout.verify-instance` 时取租约（同一时刻只给一个需求）并把候选部署上去、核部署提交 = 候选；没有时启动集成 worktree 开发实例并给出地址 / 命令。浏览器验收使用当前宿主可用的浏览器工具；宿主无此能力时对应 AC 如实记 `unverified`，不得写成通过或假装执行
5. **Agent 自动证据与首次报告**（后台，不叫人）：fast → `scoped_select.py --repo <集成 worktree> --base <candidate.json merge_base> --run --budget-seconds <verify-budget-seconds> --report <integration/raw/scoped.json>`（经 `test_slots.py run` 包装；`feature-closeout.selection-map` 显式配置时追加 `--map <selection-map>`，缺省 / `null` 由选择器使用 `dev/scoped-map.yaml`；direct 全跑、indirect 到点停发进 feature 级 deferred、不跑 full）→ 按计划执行 `command` / `browser-agent` 的自动部分，逐条写 `integration/evidence.md` 与 v2 `business-results.json`。自动 `pass` 必须有实际执行记录和预期 / 实际断言；浏览器还必须有 URL、actions、console/page error 与有效截图，截图单独不算通过。清单外失败只对单项在 merge_base 对照归因，本需求引入 → 开回修票修复后回第 3 步。然后运行 `feature_closeout.py report` 生成 `acceptance-report.md`，再 `check --phase verify --record`；报告缺失、AC 漏项 / 重复 / 额外、证据无效均不过
6. **人工验收与最终报告**：先把首次报告呈现给人——自动实际通过多少、哪些失败 / 未验证、哪些仍需人工及原因、每条操作与期望。AI 发现原计划自动判据不足时只能向 `auto+human-review / human` 严格调整并写 `reclassification`；浏览器不可用时可改由人实际测试，不得把未执行写成通过。人逐条回结果，主会话写回 `business-results.json` 的 `human`；自动未验证且未调整为人工实测的只能显式豁免。人报问题 → 回第 3 步形成新候选。**只有对话内的显式确认才算合并授权**——原话、时间、候选 tree、覆盖动作写 `confirmation.md`；随后重新运行 `report` 生成最终报告，沉默 / 超时不算
7. **合 main**：`check --phase merge --main-repo <主工作区> --record`（计划与结果逐条对账、最终报告未过期、人工项全部 confirmed / waived、未验证自动项已豁免或改为人工实测、确认绑定同一 tree、main 冻结后只在 `main-drift-ignore` 路径前进、主工作区在 `main-branch`）通过后，主工作区执行 `git merge --no-ff <集成分支>` → `feature_closeout.py record-merge --repo <主工作区> --commit HEAD`（核对落到 main 的代码 = 候选，写 `feature-merged`）
8. **发布与兜底**：确认范围含 `deploy-prod` 时，按 `feature-closeout.prod` 先 dry-run 再 sync，核部署提交 = main HEAD，跑最终报告列出的 Prod 只读冒烟、请人抽查；冒烟失败 → 提议 `prod.rollback`，等人确认再执行。push 只在人另行指示时做，main push 的 CI full 是套件级兜底；CI 新失败归因到本需求 → 开回修票，影响 Prod 时提议 revert 合并提交 + 回滚部署，人确认后执行
9. **永远人工**：合 main、发布 Prod、push 不在任何自动或无人值守授权清单内——无人值守模式停在第 6 步等人，`unattended.md` 写明也不得越过

### 无人值守模式（人离场自动跑）

监督循环默认有人值守；人显式宣布离场（如「离场自动跑」）时进入本模式：

- **授权落盘**：先把本次授权边界写进 `./dev/features/<feature-module>/<feature-slug>/unattended.md` 给人过目——可自主做的事（默认 = 监督循环全部动作：派发 / 验收复跑 / 符合性核对 / 打回修复 / 合并 / 清场）、绝不可做的事（默认 = 命中 `agents-config.redlines`、改票 DoD、绕过拆票粒度上限、执行本机 full 或越过停止条件）、**预算与停止条件**（人给的费用 / token / 时长预算与时限；额度停止阈值人未指定时默认 = 账号额度用量 ≥80% 即停，其余停止条件见下）。用大白话写，让人看完能直接说「可以」或指出哪条不行；落盘即授权记录，也是崩溃后接力的依据。**预算与停止条件未经人过目确认，不开跑**
- **队列即 DAG**：不另建队列文件——门②确认的票清单 + 阻塞边即队列，每轮按第 1 步重算 frontier 取票
- **单循环、默认串行**：同一项目同时只跑一个无人值守循环——开跑前先确认没有仍在运行的旧循环或遗留定时唤醒，有则先停掉再进入本模式；离场期间并行派发上限按 1（串行）执行，仅当人在授权里明确同意并行时才按 `dispatch.parallel-max`
- **越界决策不擅自拍板**：遇到授权边界外又必须决定的事 → 决策内容与推荐方向写进 `decisions.md`，该票置 `needs-human`，继续跑其他票
- **评审轮次预警**：同票评审达 `review-round-alert` 轮 → 主会话读最近两轮 BLOCKER，判断是范围过大（拆出后续票）、首轮漏报堆积（要求评审一次列全）还是模型能力不足（续修改用当前宿主 `development.escalated`）；判定追加为 `digest` 事件，不在此停等人
- **进度留痕**：每关一票、每打回一次、每张 `needs-human`，事件由「状态与事件写入契约」自然落 `progress.jsonl`，本模式不再另记 progress.md；`progress.jsonl` + 票 frontmatter 是新会话接力的唯一依据
- **暂停改写为记录**：第 7 步的暂停条件在本模式下不中断循环——记 `decisions.md`、票置 `needs-human` 后继续跑其他票；命中**停止条件**才停：frontier 空且全部 `done`（正常完成）/ 剩余票全部 `needs-human` 或 `阻塞` / 人给的预算或时限到 / 命中下方熔断。任何停止都做三件事：追加 `digest` 事件（总结写进 note）、撤销本次创建的定时唤醒（循环停了心跳不许还在）、按第 6 步的收尾总结格式向用户汇报
- **熔断即停，不 retry**：命中任一立即停止派发与合并、追加 `circuit-break` 事件（原因写进 note）并撤销定时唤醒，不安排「过会儿再试」，保持停止等人裁决：①账号额度用量达授权阈值（默认 80%），或已额度耗尽 / 持续限流 / 鉴权失败；②定时唤醒续跑时连续 2 次唤醒没有任何票状态推进（空转只烧额度，不产出）
- **定时唤醒（心跳）从紧**：优先在主会话内持续跑监督循环，不为离场另建高频定时任务；宿主只能靠定时唤醒续跑时才建，间隔不短于 2 小时、仅在上一轮有实质进展时续，唤醒后按「恢复」条另起新会话接力
- **离场前提醒人**：电脑不休眠、宿主权限模式不等人点确认、模型额度限制与本次预算 / 停止阈值
- **恢复**：新会话或崩溃重启后读 `unattended.md` + `decisions.md` + `progress.jsonl` + 票 frontmatter，从 frontier 重算处续跑，不从头再来；续跑一律另起新会话靠落盘文件接力，不反复唤醒同一长会话（超长历史每轮全量重发会放大 token 消耗）


## 架构图谱与经验的消费义务

- 读取现行架构或影响分析时，使用 `python3 ../shared/scripts/architecture_graph.py --repo <项目根> [--module <模块>|--interface <关键词>|--version <版本>]`；现有 `docs/ARCHITECTURE.md` 表由同一读取器兼容读取。分片存在后以 `docs/architecture/*.json` 为结构化真源，图谱查询默认排除 `needs_verification`。
- 所有开发技能按模块/分类/适用条件查询项目经验；通用经验只在适用时消费。记录命中的稳定经验 ID、适用结论和冲突，不把关键词命中当适用结论。
- `/pd-plan` 在方案中记录受影响模块及其现行架构记录 ID；模块归属按 GLOSSARY 登记，图谱不另造归属。
- `/tech-spec` 标记新增、修改、废弃或版本限定的接口/依赖关系，引用图谱 ID 与命中的项目经验；契约更新说明来源定位和验证入口。
- `/issue-split` 将图谱记录更新、替代关系、失败场景与验证义务拆到具体票及 DoD，避免只在集成票留空泛责任。
- `/implement` 按票更新图谱；只有代码或契约已生效且有可复核依据时，才能把记录标为 `current` 并填写验证提交/证据。推断或缺失证据保留 `needs_verification`。
- `/review` 在契约巡检时运行 `contract_check.py --check`。事实更正需已生效代码/发布证据；职责或语义争议交回原责任环节，不以复盘直接覆盖结论。经验消费须记录命中的经验 ID；需求、项目和通用经验维持既有分层，并分别记录最后复现与最后核实。规则级建议关联承接票、延期决定或拒绝理由。

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

- `./dev/agents-config.md` 不存在 → 提示先 `/setup-dev`，状态置 `阻塞`
- 必须先确定唯一 `feature-module` 与 `feature-slug`（沿用 `./docs/features/` 的最多两层匹配规则：模块层 / slug 层）；读取该目录下最新 `*-tech-spec-*` 文档
- tech-spec 不存在，或 frontmatter `status` ≠ `已确认（待拆解）` → 直接 `阻塞`；存量 tech-spec 缺 frontmatter：先按正文表头誊抄补全（只加 frontmatter 不动正文）再判定
- 补读：`./docs/GLOSSARY.md`、通过 `architecture_graph.py --repo <项目根> --interface <关键词>` 查询接口图谱（tech-spec 3.2 非空时作为契约 DoD 的对齐基线）、该 feature-slug 最新 PRD（意图与稳定 AC 编号）、同目录 `acceptance-plan.yaml`（需求级功能验收分工）
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
- **粒度取向取少不取多**：每票有固定执行成本（独立实现对话 + 独立评审对话 + 监督收口），票数是成本乘数；无独立 DoD、与邻票同接缝同目录的切片合并为一票——粒度红线是上限，不是目标
- **声明阻塞边**（blocked-by）：以 DAG 表达依赖，编号按依赖序
- **标注上游节锚点**：备注节写明本票涉及的 tech-spec 节（如 `#3.2 接口变更`、`#5 测试策略`，可多个）与 PRD 相关节——`/implement` 按锚点选读，不全量读 spec
- 首张票应是最细的端到端贯通（先打穿，再加厚）

例外——宽改造（大范围重构）：按 expand–contract 序列拆（加新形 → 分批迁移 → 删旧形），每批独立成票、可独立验收，不得拆出「中间态不可用」的票。

**DoD 必须写成可自动判定的验收命令**：每票 1–3 条「命令 + 期望输出」，从 tech-spec 测试策略的接缝与验收命令草案导出。禁止「功能正常」「符合预期」类不可判定表述；写不出可判定命令的验收标准，退回 `/tech-spec` 补测试策略。**DoD 只写功能实现度**：性能阈值、展示样式 / 版式类验收写进票的「人工确认项」节（可附采集命令，执行一次落盘、不阻塞交付），不进 DoD（`verify-policy.gate-scope`）。

**DoD 覆盖性自查（防 DoD 写窄——票级核对再严也只是照窄 DoD 核）**：每条 DoD 必须能映射到 tech-spec 节锚点；反过来，标注节的每个行为点至少被一条 DoD（或人工确认项）覆盖——覆盖不上的行为点，要么补 DoD，要么显式写进票的「不做什么」，不许无声漏掉。自查产物 = 「需求点 → 票 / DoD」覆盖表，门②时随票清单一并呈现（只在对话呈现，不落盘）。另做需求级 AC 对账：PRD 的每个稳定 AC 都必须在 `acceptance-plan.yaml` 恰好出现一次；票级 DoD 负责实现切片，需求级计划负责最终业务验收，二者不得相互替代。发现 AC 漏项、重复或分类理由不成立，退回 `/tech-spec`，不得在票里悄悄补一条就算闭环。

**契约 DoD（tech-spec「3.2 接口变更」非空时强制）**：3.2 的每个接口变更点，必须落在某张承接票的 DoD 里并引用稳定图谱 ID；用 `architecture_graph.py --interface <关键词> --status needs_verification` 核对迁移中记录，再检查对应分片 diff。无承接票的接口变更点，要么补票，要么在 3.2 显式标注「不进入架构图谱」（如纯内部重构），不许无声漏掉。覆盖表中接口变更点单列一类呈现。

**确定 declared-scope 与票粒度（确定性红线，不凭感觉）**：

- 从 tech-spec 的模块 / 接口 / 约束导出本票 `declared-scope`（预计触碰的生产代码路径 / glob 清单）
- **粒度红线**：预计生产代码文件数 > `verify-policy.ticket-max-files`（默认 10）、预计生产代码 LOC > `verify-policy.ticket-max-loc`（默认 400），或 scope 不可枚举（只能写出目录级 glob）→ 本票必须再拆；不得靠增加测试深度或留到集成阶段放行。实现期实测超任一红线同样停止并退回再拆
- **票级验证统一为 DoD + fast**，不为迁移 / 鉴权、公共接口、共享模块或 DAG 终点设置额外测试范围。DoD 仍须可执行且覆盖票面行为；feature / main 的 CI full 是套件级安全网，不是写窄票 DoD 的理由
- **集成票不再强制额外测试**：DAG 中无后继（没有其他票 blocked-by 它）的票标记为集成票，用于 `development.escalated` 路由与集成态核对；它仍只跑本票 DoD + fast，并始终接受 feature 级 conformance 核对，同样受上述粒度红线约束

**判定票类型 `type`**：修复既有缺陷（现象 → 根因 → 修复）的票标 `type: bug`，其余标 `feature`（缺省）。bug 票的 DoD 必须含一条复现该 bug 的回归测试命令——写不出复现测试的 bug 票，退回 `/tech-spec` 补测试策略；实现期该票须过回归有效性验证（revert 修复见红，见 `/implement` Step 5）

### Step 2：门②——清单确认（阻塞型，必须人批准）

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

- **local 模式**（默认）：按模板 `shared/templates/issue.md`（相对本 SKILL.md 为 `../shared/templates/issue.md`）在 `./dev/features/<feature-module>/<feature-slug>/issues/` 下每票一个文件，依赖序编号（issue-001 起），frontmatter `status: confirmed`
- **gitlab 模式**：本地票文件照写（本地真源），另用 `glab issue create` 逐票建 issue：标题 `[<feature-slug>] issue-NNN <标题>`，正文取票文件内容，label `ready-for-agent`；把 IID / URL 回写票文件 frontmatter。glab 命令失败：报告错误并保持 local 产物完整，不阻塞
- 修改父级 issue / tech-spec / PRD：禁止

### Step 4：收尾与派发协议（门②后由主会话自动执行）

- 向用户说清：共拆出几票、哪几票可以立即开工（没有前置依赖）、关键的先后手关系一句话带过
- 提示时附离场说明，用大白话：你可以宣布「离场自动跑」进入无人值守——我会先把「我能自主做什么、绝不做什么、预算和额度上限、什么时候停」写下来给你过目，你点头后我自动跑完全程（见「监督循环 · 无人值守模式」）
- 本 skill 的职责到此结束；**门②是执行段之前唯一的人审检查点，之后全程不再等人审核**。主会话按上方「派发协议」与「监督循环」逐票派发、监督执行直到逐票合并完成（实现动作发生在被派发的独立对话内，不违反本会话 DOC_MODE）

---

## 硬约束

- tech-spec 未确认不开拆；`acceptance-plan.yaml` 缺失或校验不过不开拆；拆解不改变方案与验收计划内容
- 门②未过不发布；发布只写票文件与 tracker issue，不碰源码
- DoD 不可自动判定的票不允许存在；tech-spec 3.2 非空而票清单无任何契约 DoD 的，不许过门②
- 票一旦 confirmed，标题 / DoD / declared-scope 不得由本 skill 单方面修改；变更须重新过门②。实现期登记实际 diff 漂移；超过 `ticket-max-files` / `ticket-max-loc` 必须退回再拆，不得以补跑测试代替
- 分工：派发、监督与合并在主会话，实现与评审各自在独立的新开对话；主会话不代写实现、不代评
- 用词遵循 GLOSSARY 标准术语
