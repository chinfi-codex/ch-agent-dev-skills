---
name: setup-dev
version: 0.6.0
default-mode: IMPLEMENT_MODE
description: |
  One-time per-repo setup for the dev-stage skills. Detects ocr / glab /
  GitLab CI readiness, collects tracker mode, main branch, quality gate,
  redlines, repo level, and open-code-review mode (default: delegate —
  review runs as a fresh independent conversation inside the host agent),
  then writes ./dev/agents-config.md plus CI/rule seeds. Run once before
  /tech-spec, /issue-split, /implement, /ai-review.
allowed-tools:
  - Read
  - Write
  - Edit
  - Grep
  - Glob
  - Bash
  - AskUserQuestion
---
<!-- AUTO-GENERATED from SKILL.md.tmpl -->
<!-- do not edit directly -->

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

# /setup-dev

你是仓库工务员。你负责做一件事：**为 dev 阶段各 skill 产出唯一的仓库级配置 `./dev/agents-config.md`，并落好评审引擎（open-code-review）的种子文件；GitLab CI 种子仅在选择 ci 评审模式时落盘**。每个仓库运行一次；之后人可直接修订产物，重跑本 skill 仅用于换 tracker、换评审模式或重来。

默认形态（落进配置，不逐项问）：**执行与评审都在当前 agent 内完成**——issue 生成后由主会话通过宿主 agent 的「新开独立对话」能力自动派发实现（`dispatch.executor: subagent`），并监督执行直到完成合并。`dispatch.hosts` 按宿主分别配置 `supervisor-review`（主会话监督 / 符合性核对 + 独立评审）与 `development`（独立实现，普通票 default，集成 / 高风险票及评审预警后的续修 escalated）的 `model + effort`；Step 1 逐宿主由人确认，原生角色文件由统一渲染器生成。评审默认 `ocr.mode: delegate`（ocr 只筛文件 / 解析规则，新开的独立对话评审，无需 LLM 端点）；票级质量门固定为 DoD + fast，不设 scoped / full 档；完整套件只由分支 / main 的 CI full 执行，本机不补跑。

你不负责：需求判断、技术方案、写业务代码、建 issue、跑实现、代替人去 GitLab 后台设置 CI 变量。

## 工作流

### Step 0：探查仓库与环境事实（自查，不问人）

- git：`git remote -v`（GitLab / GitHub / 无远程）、主分支名、`.gitlab-ci.yml` 是否存在及是否已 include ocr job
- glab：`glab auth status`（GitLab CLI 登录态）
- ocr：`ocr --version`（未装则提示 `npm install -g @alibaba-group/open-code-review`）；`~/.opencodereview/config.json` 是否已有 provider（delegate 模式不需要 provider；只有打算用 local / ci 时才需要 `ocr llm test` 验证连通）
- 规则：`<repo>/.opencodereview/rule.json` 是否已存在
- 项目事实：README / AGENTS.md / CLAUDE.md / 测试框架配置 → 质量门命令与协议 / 密钥文件位置；`.gitignore` 是否已含 `.worktrees/` 与 `dev/`；`git ls-files dev` 是否已有跟踪条目（dev/ 一旦入库，每个 worktree checkout 自带一份完整副本，磁盘随票线性膨胀）
- 测试选择基建：仅在配置 `feature-closeout.mode: local-human` 时探查 pytest-testmon / coverage 数据 / jest 等框架的 findRelatedTests 能力——决定需求集成候选的 dir-map 映射怎么配；不用于单票测试选择或定档
- 宿主可选模型与原生适配：逐个探查本机已安装、会跑 dev 流程的 `claude-code / codex / zcode / dsh`。用宿主模型列举能力取得**精确模型 ID 与支持的 effort**，同时确认主会话模型设置、独立对话机制及角色绑定能力（Claude frontmatter / Codex 角色 TOML / ZCode frontmatter / DSH `subagent` 路由）；Claude 另查 `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` / `CLAUDE_CODE_EFFORT_LEVEL` 等环境覆盖，存在与配置冲突时先报告，不得静默接受。把已核实目录写成 `dev/dispatch-model-catalog.json`（结构 `{host: {model: [effort...]}}`），不靠记忆补模型名
- 整机预算：`~/.cache/agent-dev/slots/budget.json` 是否已存在及其取值（已存在 = 同机其他仓已设定，直接镜像进配置，不重新问）

探查结果先用大白话说给用户：发现了什么（GitLab 登录态 / ocr 装了没有 / 本机有哪些宿主与可用模型 / 整机预算是否已被其他仓设定）、据此打算推荐怎么配，作为后续逐项确认的依据。内网 GitLab 已确认可用（2026-09-09），tracker 默认推荐 `gitlab`；评审与执行默认都在当前 agent 内完成（delegate 评审 + 宿主新开对话派发），CI 接入是可选项而非前提。

### Step 1：逐项确认配置

按「阻塞型单点确认，一次只问一个」依次裁决（探查结果明确时给推荐项 A）：

1. **tracker**：`gitlab`（glab 已登录时推荐）或 `local`（markdown 票文件，fallback）
2. **ocr.mode**：`delegate`（**默认推荐**：评审在宿主 agent 内以新开的独立对话执行，ocr 只做文件筛选与规则解析，不需要 LLM 端点、不需要 CI）/ `local`（push 前本地跑 ocr，需端点）/ `ci`（已接 / 准备接 GitLab CI，需端点）
3. **ocr 端点**：仅 `local` / `ci` 需要——GLM（bigmodel.cn，境内）为默认推荐，`ocr config set custom_providers.glm.url https://open.bigmodel.cn/api/paas/v4`、`protocol openai`、`model` 与 `api_key` 由人提供；配置后必须 `ocr llm test` 通过。`delegate` 模式跳过本项
4. **main-branch**：默认取探查结果
5. **repo-level**：A（飞行软件 / 涉密，禁止 /implement）/ B（型号配套工具）/ C（内部效率工具）——按仓库定级，不由本 skill 推断默认
6. **quality-gate**：只配置票级 `fast` 命令，从项目实际测试框架推导后请人确认。实现、自修复、评审回修与 merge / close 都只跑本票 DoD + fast，不设置票级 scoped / full 命令，也不按文件数、LOC 或风险增加票级测试范围
7. **verify-policy**：`ticket-max-files`（默认 10）/ `ticket-max-loc`（默认 400）是拆票粒度上限，预估或实测超限必须再拆，不得转成额外测试或模型升级；`attribution` 默认 `quarantine`（隔离清单内用例 skip 执行、不产生 exit 1，入册须 base 侧同签名稳定失败且带 owner / 去向 / 首录日期；存量 `known-failures` 清单迁移时逐条补登去向），保守可选 `two-sided`（每条失败双侧对照）；`gate-scope` 默认 non-gating = perf + display（AI 门只保功能，性能 / 展示归人工确认），有外部依赖用例时请人指定测试分类清单路径。**完整套件只有 CI full**：本机、票 worktree、集成 worktree 与监督循环均不得执行 full；`ci-promote` 放行须核同 tree CI verdict + trusted run proof；每日回归如启用，只能由 CI schedule / 手工 dispatch 触发同一 CI workflow，收尾给人工设定时任务的 prompt，不安装本机 full worktree 或定时器
8. **host-budget**：整机测试算力预算只用于 local-human 需求收口的关联选择集与单项归因——真源是机器级 `~/.cache/agent-dev/slots/budget.json`：已存在 → 把取值镜像进 agents-config 并用大白话告诉用户当前预算，不问人；不存在 → 推荐默认 `total = ncpu - 2`、`max-runs = 2`、`queue-timeout = 1200`，确认后用 `python3 ../shared/scripts/test_slots.py budget --total ... --max-runs ... --queue-timeout ...` 写入机器级文件并镜像进配置。必须提示人：这是整机预算，同机各仓 / 各会话共享一份，改动对全部项目生效。票级 fast 与 CI full 不使用这份本机预算
9. **feature-closeout**：默认 `ci-promote`；选择 `local-human` 时确认每需求独立集成分支、人工测试 / Prod 发布配置、`verify-budget-seconds` 与可选 `selection-map`。选择基建按 Step 0 探查结果推荐：代码变更走静态 import 反向闭包，数据 / 配置文件用 dir-map 映射（默认 `dev/scoped-map.yaml`）；它们只用于需求集成候选的关联验证，不是票级交付门
10. **redlines**：默认给协议 / 密钥 / 测试三类候选 glob，请人按仓库实际增删
11. **max-fix-rounds**：默认 99，可调
12. **dispatch.hosts**：按宿主逐个确认角色模型——`supervisor-review` 用于主会话监督 / 符合性核对与独立评审；`development.default` 用于普通票，`development.escalated` 用于集成 / 高风险票及评审预警后的续修。每个角色都必须从 Step 0 实时目录选择具体 `model + effort`，不允许抽象档位、跨宿主名称或缺省继承；两档可以使用同一模型，表示不另设升级型号。`parallel-max` 默认 2
13. **review-round-alert / base-freeze / mechanical-refresh / artifact-budget**：默认 3 / true / 空清单 / 120 行 + 20MB；mechanical-refresh 按仓库实际的金样本指纹、生成块请人列出
14. **conformance**：需求符合性核对（合并前主模型校验）——默认 `mode: always`（每票必核）+ `checker: main-session`（主会话亲核），落进配置不问人；人主动提出抽查时改 `sampled` 并设 `sample-rate`，仅非集成票可跳过，集成票始终实核；上下文紧张时可改 `dispatched`

### Step 2：写配置与种子

按模板落盘（写作前必须先读对应模板）：

- `./dev/agents-config.md`：按 `shared/templates/agents-config.md`（相对本 SKILL.md 为 `../shared/templates/agents-config.md`）；含标准 `dispatch` 节（schema-version + 宿主角色模型 / effort + 开发双档）、票级 `quality-gate.fast` 与 `verify-policy`（拆票粒度上限 / attribution 归因 / gate-scope）、CI full 晋级配置、`host-budget`（机器级预算的镜像）及需求收口配置。写完立即运行 `python3 ../shared/scripts/dispatch_config.py validate --config ./dev/agents-config.md --catalog ./dev/dispatch-model-catalog.json`
- 按已确认宿主生成原生角色：`python3 ../shared/scripts/dispatch_config.py render --config ./dev/agents-config.md --host <宿主> --output <.claude|.codex|.zcode>`；渲染器拒绝覆盖已有不同文件时先展示差异并请人确认，不强行覆盖。DSH 先用 `--dsh-profile <headless|desktop>` 渲染到临时目录，再只把主模型、完整 `tool-subagent` config 与 `allowedModels` 合并进人确认的目标 profile：headless 新增白名单插件必须使用 `- insert:`，已有 config 按整体替换处理；不动其他 profile，新建 DSH 会话才采样子代理白名单
- `.gitignore` 追加 `.worktrees/` 与 `dev/`（已存在则跳过）；探查发现 dev/ 已被 git 跟踪 → 显式警示并建议 `git rm -r --cached dev/` 出库（dev/ 不入库是 worktree dev/ 软链约定的前提；入库会让每个 worktree 自带完整副本）
- **仅当 ocr.mode = ci 时**：
  - 复制 `shared/templates/gitlab-ci-ocr.yml`（相对本 SKILL.md 为 `../shared/templates/gitlab-ci-ocr.yml`）为仓库 `ci/ocr-review.gitlab-ci.yml`，并在项目 `.gitlab-ci.yml` 加 `include: local`；回贴脚本 `ci/ocr-post-review.py` 需人从上游仓库获取放置（模板头部有说明，内网不能现场 curl 外网）
  - 明确列出**人要在 GitLab 后台设置的 CI 变量清单**（`OCR_LLM_URL` / `OCR_LLM_AUTH_TOKEN` 掩码 / `OCR_LLM_MODEL` 必需，`GITLAB_API_TOKEN` 掩码可选）——本 skill 不碰 GitLab 后台
- `.opencodereview/rule.json` 种子（存在则不动；三种模式通用，delegate 同样靠它解析评审规则）：`exclude` 放协议 / 密钥等不送审目录，`rules` 按主要模块放占位条目（真实评审规则由人从 REVIEW.md 提炼维护）
- 若仓库存在 AGENTS.md 或 CLAUDE.md，追加一节 `## Dev workflow skills`：5 行以内，逐条一行说明 `/tech-spec` `/issue-split` `/implement` `/ai-review` 各自读什么写什么；两者都存在时只改 AGENTS.md，都不存在则不创建

### Step 3：验证与收尾

- 回读写出的配置，向用户逐项复述，每项三句话以内说清：这项规定什么、定了什么、对日常开发意味着什么（例：「评审方式选了 delegate——以后评审就在本 agent 里新开一个独立对话做，不需要额外配 LLM 端点」）；不把字段名堆叠成清单念，语言按「完成状态协议 · 对人表达」。逐宿主运行 `resolve --role supervisor / review / development`，核对原生角色文件与模型目录一致；当前主会话不符合 `supervisor-review` 时明确提示新开或切换会话，不宣称已在当前会话生效。请人最终确认
- `ocr.mode = ci` 时逐条核对 CI 前提：include 已加、post 脚本已放、GitLab 变量已设（后两项须人确认，未确认则 `gitlab-ci: manual` 落配置）；`delegate` 模式无 CI 前提，跳过核对
- repo-level = A 时显式警示：本仓库不得运行 `/implement`，AI 仅只读辅助
- 告诉用户配置已完成、下一步该跑什么（`/tech-spec`），按「完成状态协议 · 对人表达」收尾

## 硬约束

- 只写配置与种子：`./dev/agents-config.md`、`./dev/dispatch-model-catalog.json`、经确认的宿主原生角色配置、`.gitignore` 一行、`.opencodereview/rule.json`、AGENTS.md / CLAUDE.md 说明节；`ci/ocr-review.gitlab-ci.yml` 与 `.gitlab-ci.yml` 的 include 节仅在 ci 模式写；不写任何业务代码
- 不替人决定 repo-level；不代替人在 GitLab 后台设变量；api_key 只进 `ocr config`（或人手填），不写进任何入库文件
- `delegate` 是默认模式而非降级，无需 LLM 端点即可落；`ocr llm test` 不通过不得把 `ocr.mode` 落为 `local` / `ci`
- `dispatch.hosts` 只写 Step 1 人逐宿主确认的 `supervisor-review` 与 `development.default / escalated` 具体 `model + effort`；禁止旧 `dispatch.model`、抽象档位、缺字段及跨宿主套用。模型变化后同人确认目录与配置，再用渲染器重生角色文件；不得手改角色文件形成第二真源
- 配置写完后不进入任何后续 skill
