---
name: setup-dev
version: 0.6.0
default-mode: IMPLEMENT_MODE
description: |
  One-time per-repo setup for the dev-stage skills. Detects ocr / glab /
  GitLab/Gitea CI readiness, host capabilities, collects tracker mode, main
  branch, real DoD + fast gate, deny/test-change/generated-refresh policies,
  repo level and review trust, then writes strict schema-version 2
  ./dev/agents-config.md plus CI/rule seeds. Run before dev skills.
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

## Dev Artifact 路径约定

`./docs/` 树（产品阶段）延伸出 `./dev/` 树（开发阶段），两棵树由同一 `feature-module` / `feature-slug` / `campaign-id` 贯通：

```text
./dev/
  agents-config.md
  features/
    <feature-module>/
      <feature-slug>/
        campaigns/
          <campaign-id>/
            <feature-summary>-tech-spec-YYYY-MM-DD.md
            acceptance-plan.yaml         # 本 campaign 功能验收计划唯一真源：AC / 执行方式 / 最终判定 / 人工原因
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
            integration/                # 本 campaign 收口产物；可选 integration/<round>/ 为内部执行轮次，不改变 campaign 身份或分支；结构见 shared/templates/feature-closeout.md
              candidate.json            #   冻结候选、票组、merged commit 与计划哈希（freeze 写）
              acceptance-plan.json      #   冻结时的计划快照
              review-handoff.json       #   完整评审范围、上轮 findings 与修复证据定位（freeze 写，不是通过结论）
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
- `research/` 归档 `/how` 的当前机制解释和 `/why` 的历史证据调查；仅复杂、可复用或明确要求的调查独立归档，轻量证据直接进入 spec 2.1；How 按相关实现/依赖/契约及工作区差异复核，Why 按相关来源取证并保留置信度。tech-spec 把采纳结论、引用和约束收进正式方案，issue/implement 仍以冻结 spec 为契约，不直接追随一份可漂移的研究文件
- `unattended.md` 是无人值守授权记录；`decisions/*.json` 是需要人裁决的问题、回答和路由状态的唯一真源。`progress.jsonl` 只保存不可变事件历史，不再使用 `decisions.md` / `progress.md` 自由文本双真源
- dev/ 产物（票文件 / impl-log / evidence / 评审报告及其引用的捕获物）一律写**主工作区**的 dev/ 树——worktree 是独立 checkout，写进它的 dev/ 清场即丢；worktree 内只放代码与 `.logs/` 等临时产物。**worktree 内不得出现 dev/ 实体副本**（每票一份完整副本 = 磁盘随票线性膨胀，清场还可能丢真源）：建 worktree 后立即把 `<worktree>/dev` 软链到主工作区 dev/（按工作目录深度计算到主工作区 dev/ 的相对目标；宿主自管沙箱改用主工作区 dev/ 的绝对路径），此后 worktree 内对 `./dev/` 的读写自动落主工作区；软链建不成即停止，当前仅支持具备 POSIX 文件锁与软链能力的 macOS / Linux；不得复制 dev/ 实体目录，也不把主工作区绝对路径回退当作等效能力。架构契约只编辑 `docs/ARCH-CHECK.json`（11 节 `architecture.overview` + 规则）；`docs/ARCHITECTURE.md` 只能由 `arch_check.py render write` 确定性重生成并随契约提交，任何开发阶段均不得手工编辑。
- worktree 统一放仓库根 `.worktrees/<module>/<feature>/<campaign>/<issue-id>/` 并加入 `.gitignore`；宿主自管会话沙箱（如 Codex）时以沙箱为隔离、不嵌套开此目录，但分支名仍按 `feat/<module>/<feature>/<campaign>-<issue-id>`——清场一律按分支名发现（见「派发协议」），不按路径猜
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
  - `local-human`：每 campaign 独立集成分支；票只做内环并合入集成分支（状态 `merged`）。新战役使用独立 campaign 目录与分支；feature-slug 不变。全部票 merged/done 后 freeze 候选与验收计划并生成 review-handoff，在候选上并行发起整需求独立评审与 `run-candidate` 复跑，两者完成后统一做 feature conformance，再执行 scoped 关联选择集及 command / browser-agent / human AC，生成正式报告，完成人工实测 / 复核 / 豁免，并取得明确 `merge-main` 确认后才由 `delivery.promote` 合 main，随后 finalize 转 done 并清理（多轮目录 finalize/close-and-clean 带本轮 `--round`，旧轮结论产物不动）。同 candidate 复跑可幂等复验或用 `--resume` 补跑；新 candidate 仍全票执行。push / Prod 始终另行授权。
- `feature-closeout.ci`：server / cooperative 必填 `{provider, base-url, repository, workflow, artifact, token-env}`；local-human 必须为 null。配置只存环境变量名，不存 token、verdict 或 proof。main 或候选前进后必须重新同步、freeze 和验证，没有 drift-ignore 豁免。
- `conformance`：只在 feature 冻结候选上执行一次，不再每票必做。主会话或独立监督会话先反向枚举需求，再打开代码按 tech-spec 全节与全部票覆盖矩阵并集逐条核对；`integration/conformance-feature.md` 必须绑定 candidate/tree、精确覆盖本轮票与 spec hash，空正文、缺来源、缺代码 / 测试 / evidence 指针均不能通过。票级覆盖矩阵保留在 impl-log 供 feature 核对。
- `base-freeze`：由 ticket context 冻结 base 与 target；实现和评审期间不追目标分支。目标前进只能走 `delivery.rebase`，之后旧验证、评审和符合性结论全部失效并重新生成。
- `artifact-budget / retention`：限制 impl-log / evidence 行数及 impl / integration 体积；raw / OCR / 截图按 generation manifest 管理。当前、最后成功和被引用证据始终保留，失效代际与 salvage 只能经 `close-and-clean` 预检确认后清理。
- `integration-runtime`：显式配置开发服务与 verify-instance 的启动、就绪、停止和租约；运行记录绑定 candidate。停止服务、释放租约失败时不得清集成 worktree。
- `dispatch`：`supervisor-review` 用于主会话监督、feature 独立评审与 feature conformance；`development.default / escalated` 分别用于普通票和集成 / 高风险 / feature 评审 BLOCKER 后的修复票。先运行 `agents_config.py validate`，再运行 `dispatch_config.py validate --host <当前宿主> --catalog` 和 `resolve`；模型 / effort 不在实时目录、缺当前宿主或角色文件过期即停。原生角色只由配置生成，`render --check` 只读核对，不手改第二份真源。
- `enforcement`：声明评审信任级别与宿主 / 分支 / SHA 能力要求。宿主不能证明独立评审身份时不得冒充 host-verified；非 server-enforced 不得宣称具有服务端分支保护。
- `ocr.mode`：默认 `delegate`，OCR 只筛文件与规则，由独立宿主对话评审；`local / ci` 为可选增强，必须配置对应端点和 CI。

## 测试位置

- **票级验证在本票 worktree**：TDD 见红 / 变绿、每条 DoD、fast 和单项失败归因；票级只跑内环，不跑独立评审、supervisor 复跑或 conformance。fast 不经整机槽位包装。
- **feature 候选复跑在集成 worktree**：`verify_issue.py run-candidate` 对每票冻结 DoD + fast 真实执行一次，证据写 `integration/raw/<generation>/`，不覆盖票级 implementer manifest。与完整独立评审并行；同 candidate 重验通过可复用，`--resume` 只补缺失 / 失败 / 无效票，新 candidate 不跨代复用。
- **local-human 需求级验证在本机候选环境**：`scoped_select.py` 关联选择集与单项归因经 `test_slots.py run` 包装；command / browser-agent AC 复用真实执行器和 generation 证据。direct 全跑，indirect 到预算停发并记 feature 级 deferred，不扩大为 full；宿主无浏览器能力时对应 AC 记 unverified 或转人工，不伪造通过。
- **远端只承接 CI full**：已登记分支的 push 与同一 CI workflow 的定时 / 手工触发是唯一入口。`delivery.promote` 在线核对实际 run 与候选 tree；本机、票内和监督循环不得保存或伪造本地 full 判决。

Codex 可选配置 `dispatch.hosts.codex.supervisor: {model, effort}`，单独指定主会话监督模型；省略时沿用 `supervisor-review`。独立评审仍使用 `supervisor-review`，开发仍使用 `development`。该字段不适用于其他宿主，整份配置与角色校验均须接受并校验此可选路由。

# /setup-dev

你是仓库工务员。你负责做一件事：**为 dev 阶段各 skill 产出唯一的仓库级配置 `./dev/agents-config.md`，并落好评审引擎（open-code-review）的种子文件；GitLab CI 种子仅在选择 ci 评审模式时落盘**。每个仓库运行一次；之后人可直接修订产物，重跑本 skill 仅用于换 tracker、换评审模式或重来。

默认形态（落进配置，不逐项问）：**执行与评审都在当前 agent 内完成**——issue 生成后由主会话通过宿主 agent 的「新开独立对话」能力自动派发实现（`dispatch.executor: subagent`），票级内环通过后合入集成分支，全部票 merged/done 后由主会话在 feature candidate 上统一执行一次独立评审与候选复跑。`dispatch.hosts` 按宿主分别配置 `supervisor-review`（主会话监督 + feature 独立评审 / 符合性核对）与 `development`（独立实现，普通票 default，集成 / 高风险票及 feature 评审 BLOCKER 后的修复票 escalated）的 `model + effort`；Step 1 逐宿主由人确认，原生角色文件由统一渲染器生成。评审默认 `ocr.mode: delegate`（ocr 只筛文件 / 解析规则，新开的独立对话评审整个 feature diff，无需 LLM 端点）；票级质量门固定为 DoD + fast，不设 scoped / full 档；完整套件只由分支 / main 的 CI full 执行，本机不补跑。

你不负责：需求判断、技术方案、写业务代码、建 issue、跑实现、代替人去 GitLab 后台设置 CI 变量。

## 工作流

### Step 0：探查仓库与环境事实（自查，不问人）

- git：`git remote -v`（GitLab / GitHub / 无远程）、主分支名、`.gitlab-ci.yml` 是否存在及是否已 include ocr job
- glab：`glab auth status`（GitLab CLI 登录态）
- ocr：`ocr --version`（未装则提示 `npm install -g @alibaba-group/open-code-review`）；`~/.opencodereview/config.json` 是否已有 provider（delegate 模式不需要 provider；只有打算用 local / ci 时才需要 `ocr llm test` 验证连通）
- 规则：`<repo>/.opencodereview/rule.json` 是否已存在
- 项目事实：README / AGENTS.md / CLAUDE.md / 测试框架配置 → 质量门命令与协议 / 密钥文件位置；`.gitignore` 是否已含 `.worktrees/` 与 `dev/`；`git ls-files dev` 是否已有跟踪条目（dev/ 一旦入库，每个 worktree checkout 自带一份完整副本，磁盘随票线性膨胀）
- 架构契约：`docs/ARCH-CHECK.json` 与 `docs/ARCHITECTURE.md` 是否已存在；若不存在，本 skill 会初始化空规则与 11 节架构总览骨架并安装 pre-push hook。总览正文不在本 skill 生成，由首次出现架构影响的 `/tech-spec` 3.2 补全。旧 `docs/architecture/*.json` 只提示迁移删除，不再读取
- 测试选择基建：仅在配置 `feature-closeout.mode: local-human` 时探查 pytest-testmon / coverage 数据 / jest 等框架的 findRelatedTests 能力——决定需求集成候选的 dir-map 映射怎么配；不用于单票测试选择或定档
- 宿主可选模型与原生适配：逐个探查本机已安装、会跑 dev 流程的 `claude-code / codex / zcode / dsh`。用宿主模型列举能力取得**精确模型 ID 与支持的 effort**，同时确认主会话模型设置、独立对话机制及角色绑定能力（Claude frontmatter / Codex 角色 TOML / ZCode frontmatter / DSH `subagent` 路由）；Claude 另查 `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` / `CLAUDE_CODE_EFFORT_LEVEL` 等环境覆盖，存在与配置冲突时先报告，不得静默接受。把已核实目录写成 `dev/dispatch-model-catalog.json`（结构 `{host: {model: [effort...]}}`），不靠记忆补模型名
- 宿主能力：用 `host_adapters.py` 只读探测独立实现 / 评审、复审与回答后续接、session 活性、worktree 文件系统、浏览器和运行资源能力，区分 available / unavailable / unknown。配置里写了不代表实际可用；host-verified 评审必须先通过原生接口写入绑定，再实时调用 `verify_review_session` 重读；host-probes.json 只保存写回执，不单独构成证明。server-enforced 还用 `ci_proof.py capabilities` 在线核 CI API、workflow、protected branch、required checks、有效权限和 expected-SHA，任何未知都不得落为该模式
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
7. **verify-policy**：`ticket-max-files`（默认 10）/ `ticket-max-loc`（默认 400）是拆票粒度上限；`scope: strict` 冻结所有 changed paths，越界重新确认票，不能登记放行。`deny` 列绝对禁止路径；`test-change` 列允许 TDD 与需求驱动更新的测试路径，必须有 runner 见红 / DoD 映射，票级核对后在 feature 级评审统一复核；`generated-refresh` 每条声明稳定 ID、输出 glob 和唯一生成命令，流程核对重跑生成器比较 tree。`attribution` 默认 quarantine（入册须 base 同签名、owner / 去向 / 首录日期），可选 two-sided；`gate-scope` 默认 non-gating = perf + display，外部依赖用例显式 skip。**完整套件只有 CI full**，本机与任何 worktree 不补跑；每日回归只能由同一 CI workflow schedule / dispatch 触发
8. **host-budget**：整机测试算力预算只用于 local-human 需求收口的关联选择集与单项归因——真源是机器级 `~/.cache/agent-dev/slots/budget.json`：已存在 → 把取值镜像进 agents-config 并用大白话告诉用户当前预算，不问人；不存在 → 推荐默认 `total = ncpu - 2`、`max-runs = 2`、`queue-timeout = 1200`，确认后用 `python3 ../shared/scripts/test_slots.py budget --total ... --max-runs ... --queue-timeout ...` 写入机器级文件并镜像进配置。必须提示人：这是整机预算，同机各仓 / 各会话共享一份，改动对全部项目生效。票级 fast 与 CI full 不使用这份本机预算
9. **feature-closeout**：默认 `server-enforced`，只有 Step 0 已在线证明服务端能力才可选，并填写 `ci: {provider, base-url, repository, workflow, artifact, token-env}`；CI API 可用但无完整分支保护时可显式选 `cooperative-ci`，报告必须注明无服务端强制；选择 `local-human` 时确认独立集成分支、验收计划、runtime 与可选 Prod 配置，`ci` 必须为 null；并说明每次新交付使用独立 campaign 目录与集成分支，feature-slug 保持稳定，round 只作本战役内部执行轮次；历史根目录沿用原约定。main / 候选前进后一律重新 freeze，没有 main-drift-ignore。选择基建只用于需求集成候选，不是票级交付门
10. **enforcement**：选择评审信任 `host-verified`（宿主真实回执）或 `operator-attested`（人工另开独立对话的协作证明）；宿主无法证明时不得选前者。server-enforced 必须 require-protected-branch + require-expected-sha，其他模式不得冒称这些保证
11. **max-fix-rounds**：默认 99，可调
12. **dispatch.hosts**：按宿主逐个确认角色模型——`supervisor-review` 用于主会话监督、feature 独立评审与符合性核对；`development.default` 用于普通票，`development.escalated` 用于集成 / 高风险票及 feature 评审 BLOCKER 后的修复票。每个角色都必须从 Step 0 实时目录选择具体 `model + effort`，不允许抽象档位、跨宿主名称或缺省继承；两档可以使用同一模型，表示不另设升级型号。`parallel-max` 默认 2。DSH 若选择 host-verified，还必须显式配置 `native_session_command`：同一单行原生命令负责写入评审绑定与读取真实 session JSON；null 或无原生写读能力时不得选择 host-verified
13. **review-round-alert / base-freeze / generated-refresh / artifact-budget / retention**：默认 3 / true / 空清单 / 120 行 + 20MB / 保留两个代际；派生物按仓库实际生成器逐条确认，raw / OCR / 截图必须有 generation manifest，salvage 只按确认策略清理
14. **conformance**：默认 `mode: always` + `checker: main-session`。人主动提出抽查时改 `sampled` 并设 `sample-rate`，是否抽中由 gate2 context digest + issue ID 确定性计算，不能由填写者自报 skipped；集成票始终实核。上下文紧张时可改 `dispatched`，仍需真实独立会话回执

### Step 2：写配置与种子

按模板落盘（写作前必须先读对应模板）：

- `./dev/agents-config.md`：按 `shared/templates/agents-config.md`（相对本 SKILL.md 为 `../shared/templates/agents-config.md`），顶层 `schema-version: 2`。写完立即运行 `python3 ../shared/scripts/agents_config.py validate --config ./dev/agents-config.md`，再运行 `dispatch_config.py validate --host <当前宿主> --catalog ./dev/dispatch-model-catalog.json`；任何旧字段、未知字段或非法模式组合都必须修正，不能带入后续流程
- 按已确认宿主生成原生角色：`python3 ../shared/scripts/dispatch_config.py render --config ./dev/agents-config.md --host <宿主> --output <.claude|.codex|.zcode>`；渲染器拒绝覆盖已有不同文件时先展示差异并请人确认，不强行覆盖。DSH 先用 `--dsh-profile <headless|desktop>` 渲染到临时目录，再只把主模型、完整 `tool-subagent` config 与 `allowedModels` 合并进人确认的目标 profile：headless 新增白名单插件必须使用 `- insert:`，已有 config 按整体替换处理；不动其他 profile，新建 DSH 会话才采样子代理白名单
- `.gitignore` 追加 `.worktrees/` 与 `dev/`（已存在则跳过）；探查发现 dev/ 已被 git 跟踪 → 显式警示并建议 `git rm -r --cached dev/` 出库（dev/ 不入库是 worktree dev/ 软链约定的前提；入库会让每个 worktree 自带完整副本）
- **仅当 ocr.mode = ci 时**：
  - 复制 `shared/templates/gitlab-ci-ocr.yml`（相对本 SKILL.md 为 `../shared/templates/gitlab-ci-ocr.yml`）为仓库 `ci/ocr-review.gitlab-ci.yml`，并在项目 `.gitlab-ci.yml` 加 `include: local`；回贴脚本 `ci/ocr-post-review.py` 需人从上游仓库获取放置（模板头部有说明，内网不能现场 curl 外网）
  - 明确列出**人要在 GitLab 后台设置的 CI 变量清单**（`OCR_LLM_URL` / `OCR_LLM_AUTH_TOKEN` 掩码 / `OCR_LLM_MODEL` 必需，`GITLAB_API_TOKEN` 掩码可选）——本 skill 不碰 GitLab 后台
- `.opencodereview/rule.json` 种子（存在则不动；三种模式通用，delegate 同样靠它解析评审规则）：`exclude` 放协议 / 密钥等不送审目录，`rules` 按主要模块放占位条目（真实评审规则由人从 REVIEW.md 提炼维护）
- 架构契约初始化：`docs/ARCH-CHECK.json` 不存在时运行 `python3 ../shared/scripts/arch_check.py init --repo <仓库根>`（生成空规则与 `docs/ARCHITECTURE.md` 的 11 节骨架），存在则不动；同名 `docs/ARCHITECTURE.md` 已存在但不是托管生成物时 `init` 会拒绝覆盖，先请人明确移走旧文件。已有 `docs/ARCH-CHECK.json` 但缺 `docs/ARCHITECTURE.md`（含从旧 `docs/ARCHITECT.md` 升级）时运行 `python3 ../shared/scripts/arch_check.py render write --repo <仓库根>` 重新生成，并提示人确认后删除旧 `docs/ARCHITECT.md`。随后运行 `python3 ../shared/scripts/arch_check.py install-hook --repo <仓库根>` 安装 pre-push 预警（保留已有 hook）。总览正文由首次出现架构影响的 `/tech-spec` 3.2 按真实证据补全，本 skill 不代写架构事实；旧 `docs/ARCHITECT.md` 不再读取，提示人确认后删除
- 若仓库存在 AGENTS.md 或 CLAUDE.md，追加一节 `## Dev workflow skills`：5 行以内，逐条一行说明 `/tech-spec` `/issue-split` `/implement` `/ai-review` 各自读什么写什么；两者都存在时只改 AGENTS.md，都不存在则不创建

### Step 3：验证与收尾

- 回读写出的配置，向用户逐项复述，每项三句话以内说清：这项规定什么、定了什么、对日常开发意味着什么；不把字段名堆叠成清单念。逐宿主运行 `resolve --role supervisor / review / development`，并用 `dispatch_config.py render --check` 核对原生角色文件没有缺失或过期；当前主会话不符合 `supervisor-review` 时明确提示新开或切换会话，不宣称已在当前会话生效。请人最终确认
- 架构契约初始化后运行 `python3 ../shared/scripts/arch_check.py validate --repo <仓库根> --worktree --rules docs/ARCH-CHECK.json` 与 `render check --worktree`，并确认 pre-push hook 已安装；告诉用户 11 节总览此时只是骨架，首次架构变更会由 `/tech-spec` 3.2 补全；票级不做架构检查，feature candidate 的 `freeze / check` 才强制 arch-check
- `ocr.mode = ci` 时逐条核对 CI 前提：include 已加、post 脚本已放、GitLab 变量已设（后两项须人确认，未确认则 `gitlab-ci: manual` 落配置）；`delegate` 模式无 CI 前提，跳过核对
- repo-level = A 时显式警示：本仓库不得运行 `/implement`，AI 仅只读辅助
- 告诉用户配置已完成、下一步该跑什么（`/tech-spec`），按「完成状态协议 · 对人表达」收尾

## 硬约束

- 只写配置与种子：`./dev/agents-config.md`、`./dev/dispatch-model-catalog.json`、经确认的宿主原生角色配置、`.gitignore` 一行、`.opencodereview/rule.json`、`docs/ARCH-CHECK.json` / `docs/ARCHITECTURE.md`（仅初始化时）、`.git/hooks/pre-push`（arch-check 托管块）、AGENTS.md / CLAUDE.md 说明节；`ci/ocr-review.gitlab-ci.yml` 与 `.gitlab-ci.yml` 的 include 节仅在 ci 模式写；不写任何业务代码
- 不替人决定 repo-level；不代替人在 GitLab 后台设变量；api_key 只进 `ocr config`（或人手填），不写进任何入库文件
- `delegate` 是默认模式而非降级，无需 LLM 端点即可落；`ocr llm test` 不通过不得把 `ocr.mode` 落为 `local` / `ci`
- server-enforced 未经在线能力证明不得落配置；协作 / 本地模式不得冒称服务端强制。配置中的 token-env 只能是环境变量名，token、CI verdict / proof 不得写入配置
- `dispatch.hosts` 只写 Step 1 人逐宿主确认的 `supervisor-review` 与 `development.default / escalated` 具体 `model + effort`；禁止旧 `dispatch.model`、抽象档位、缺字段及跨宿主套用。模型变化后同人确认目录与配置，再用渲染器重生角色文件；不得手改角色文件形成第二真源
- 配置写完后不进入任何后续 skill
