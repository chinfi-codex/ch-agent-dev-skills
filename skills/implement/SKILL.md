---
name: implement
version: 0.7.1
default-mode: IMPLEMENT_MODE
description: |
  Execution skill for one confirmed issue. Normally auto-dispatched by the
  main session as a fresh host-agent conversation (model per the
  agents-config dispatch.model entry for the current host, by ticket tier):
  TDD in a dedicated worktree, per-round scope-drift checks with one-way
  verify-tier escalation, on-disk evidence for the ticket's tier gate,
  then independent /ai-review, BLOCKER fixes, and the merge request made
  ready. Main session supervises through merge — no human gate. Success
  is judged by on-disk evidence, never by self-report.
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

## 术语与数据口径（GLOSSARY，dev 阶段）

项目级唯一术语文件：`./docs/GLOSSARY.md`（存在才读，不存在不阻塞）。

- 输出（票状态、impl-log、evidence、评审报告、对话）必须使用表内**标准术语**；引用任何指标必须带口径（定义 / 统计窗口 / 数据来源），且与 GLOSSARY 一致
- 实现与评审中**不新建、不回写** GLOSSARY（worktree 分支回写会与主干漂移）；发现术语冲突、缺口或别名误用，记入 impl-log「遗留」或评审报告「评审员意见」，由文档阶段与复盘统一处理
- `docs/ARCHITECTURE.md` 是禁写令的唯一例外，且仅限其「接口现行态」表：票 DoD 声明契约更新时，本票在 worktree 分支内就地更新该表（随代码同一 MR 合入，合入后即为现行态）；未声明的票不碰。实现期 diff 实际改动了接口表已登记接口而票未声明（flow-back）：同分支补记该表并在 impl-log「红线接触记录」登记「接口 → 代码位置」映射。模块表 / 约束表仍只由文档阶段直写，dev 阶段不碰

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
5. **内部术语不进对话**：拷打 / frontier / 门② / BLOCKER / 升档 / 回显 / 落盘 / status / worktree 等协议词只做内部判定，对人换成日常说法（必须先修的问题、验证范围加大、记录下来、本票专用分支）

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
- `quality-gate`：质量门命令，三档——快速档（fast：秒级，类型 / lint / 单文件测试）、关联档（scoped：按最终 diff 换算的受影响测试集）、全量档（full：完整测试套件）。**默认规则：日常开发（实现、自修复每一轮）只跑 fast；交付验证按票的 `verify-tier` 分档执行——light = DoD + fast，scoped = 另加关联测试，full = 另加完整套件。full 不再每票必跑，但 full 档票与集成票必跑**。scoped / full / 归因补跑一律经 `shared/scripts/test_slots.py run` 包装执行（拿整机槽位，并发度 = 分得数 `$JOBS`；后台执行、输出落文件后轮询），fast 不受限
- `host-budget`：整机测试算力预算（`total` 重测试 worker 总数 / `max-runs` 重套件套数 / `queue-timeout` 排队秒数上限）。真源是机器级文件 `~/.cache/agent-dev/slots/budget.json`（同机各仓共享一份），agents-config 里只是镜像记录，执行以机器级文件为准
- `verify-policy`：验证档位策略（阈值 / 两级升档触发器 / 漂移规则 / 选择机制 / 先在失败归因）。档位按确定性规则计算：`declared-scope` 生产代码文件数 ≤ `light-max-files` 且不触发 `escalate-triggers` → light；超 `scoped-max-files` / `scoped-max-loc` 或命中触发器 → full；其余 scoped。触发器分两级——full 级（数据迁移 schema / 鉴权权限）票内 full；scoped+ 级（公共接口签名、共享模块 / 工具库、红线邻接）→ scoped，选择集 = 静态反向闭包，闭包不可枚举时标「闭包不完整」仍 scoped。定档 / 升档原因写票 `tier-reason`。**选择机制（`selection`）= impact-map（静态反向闭包）+ dir-map（数据 / 配置文件按仓内声明映射表，命中即视为可证明覆盖；映射表路径随 `verify-policy.selection-map` 配置，默认 `dev/scoped-map.yaml`；仓库元信息 / `dev/` / `docs/` 等无行为消费者的文件不要求覆盖证明）**。选择器为共享脚本 `shared/scripts/scoped_select.py`（import 闭包 + dir-map + deferred 一体，仓内不写选择器代码）。**scoped 只在闭包可证明时票内执行**：选择集可枚举才跑；不可枚举（full fallback，跑了等于变相每票 full）→ 票内不跑选择集，evidence 头记 `scoped-deferred: closure-incomplete` 与原因，该票套件级验证由集成票 full 与每日回归兜底——不因此升档、`tier-target` 不变、评审照常（DoD + fast + 独立评审不缩水）。**套件级回归三层**：集成票（feature 收尾）票内 full + main 每工作日定时 full（`daily-regression`，人自设定时任务触发，新失败在合入区间内二分到票）+ 迁移 / 鉴权票票内 full；其余票不跑票内 full。选择集大小不是升档理由（闭包大跑大闭包，占比 ≥ 50% 仅记录为评审信号）；DAG 无后继的集成票一律 full，动态依赖与跨模块交互风险由它统一兜底——唯一例外「模块隔离认定」：feature 全程只动不被外部依赖的叶子模块时，集成票按 module-scoped 跑该模块全闭包（不机械全仓），判定依据须可机器复核、有疑义仍 full。实测 diff 超出 `declared-scope`：同模块小漂移登记即可，命中触发器或超阈值则**只升不降**并补验证。先在失败按 `attribution` 隔离：默认 `quarantine`——落盘隔离清单内的用例以 skip（标 quarantined）执行，不产生 exit 1、不进 `all-passed` 分母、不逐项归因（套件退出码恢复语义：exit 1 = 有新东西坏了）；入册须 base 侧同签名稳定失败，且每条带 owner / 去向（修复票 / 永久删除决议）/ 首录日期，无去向不入册；清单由每日 full 顺带刷新（转绿销记、新先在追加），每日 full 另以「只跑隔离项、不判红」抽查签名漂移，超 `quarantine-max-age-days`（默认 14）未处理的条目在每日回归记录列出、主会话监督循环呈现；清单外失败 = 疑似本票引入，单项补跑 base 侧对照（不整套重跑）；`two-sided` 为保守档（每条失败双侧同命令对照）。**预计与实测分工**：拆票时**预估**文件数超 `scoped-max-files` 的票必须再拆、不得定档（`/issue-split` 粒度红线）；实现期**实测**超阈值才升档。**测试职责边界（`gate-scope`）**：AI 交付门只保证功能实现度（DoD 行为、契约、回归）；`non-gating` 类（默认性能基准 perf、展示样式 display）只执行一次并落盘记录，不阻塞交付、不进 `all-passed` 分母，由人工验收确认；缺外部依赖（环境变量 / 外部服务）的用例按分类清单显式 skip 并在 evidence 列出，不算失败也不算通过。**full 次数（`full-runs: once-at-final`）**：full 档每票只在评审通过后的最终 commit 跑一次；评审回修轮只跑 DoD + fast + 回修 diff 的 scoped；无冲突 rebase 且本票文件 blob 不变时复用证据不重跑
- `max-fix-rounds`：自修复轮次上限，默认 99；`review-round-alert`（默认 3）：同票评审达此轮数时主会话介入判断拆票 / 缩范围 / 升级模型（预警，不是硬停）
- `conformance`：需求符合性核对（主模型校验）——主会话在合并前核对「要的有没有、偏没偏」，与 ai-review 正交（评审判代码质量用同档模型，需求语义符合性必须主模型）。方法：先独立枚举需求点（不看 impl-log 覆盖矩阵），再对照矩阵打开代码逐条核语义；结论落盘 `impl/<issue-id>/conformance-<issue-id>.md`，`process_check.py --phase close` 把关。`mode: always`（默认）每票必核，`sampled` 时实测 light 档抽查、其余档必核；`checker: main-session`（默认亲核）或 `dispatched`（派发独立主模型对话，上下文紧张时用）。缺此节按默认执行，即默认启用
- `base-freeze`：票开工时固定 base，实现与评审期间不追集成分支，只在合入前 rebase 一次
- `mechanical-refresh`：预授权机械刷新白名单（路径 glob + 字段 / 生成块）；diff 可机器证明只动所列字段时直接刷新并登记，不停等人；白名单外的既有测试 / 夹具改动仍按红线处理——唯一例外「需求驱动更新」：票 DoD / tech-spec 明确改变的行为，其冲突旧测试随票更新并逐条登记映射（见 /implement Step 2），不按红线停
- `artifact-budget`：impl-log / evidence 正文行数与 impl 目录体积上限；正文只留结论与指针，过程放 raw，中间轮原始产物只留最终轮
- `redlines`：红线文件 glob 清单（协议文件、密钥配置、验收判定文件等），命中即停，不得绕过
- `dispatch`：执行派发方式。默认 `executor: subagent`（主会话通过宿主「新开独立对话」逐票自动派发并监督到合并）；`model` **按宿主 agent 分别配置**，每个宿主条目分 `default`（light / scoped 票）与 `escalated`（full 档 / 集成 / 高风险票、评审达 `review-round-alert` 轮后）两档优先级清单，均由人在 `/setup-dev` 确认；主会话取自身所在宿主的条目，缺条目则停下请人确认，不跨宿主套用；`parallel-max`：frontier 中 declared-scope 互不相交的票可并行派发的上限
- `ocr.mode`：评审模式。默认 `delegate`——ocr 只做文件筛选与规则解析（不调 LLM），评审由宿主 agent 内新开的独立对话执行；`local` / `ci` 为可选增强，需配置 LLM 端点


## 测试位置：复用已有部署配置

项目已有多实例部署拓扑（或用户指定使用该判读机）时，执行 [远端测试协议](../shared/references/remote-tests.md)。本段优先于下文中未限定主机的测试命令示例：所有测试含fast、DoD、TDD、评审复测与主会话复跑均在远端；本机不跑测试。只读现有L1/L2/L3与远端test_slots预算，**不新增配置键、不改配置值、不另建测试主机配置**。测试入口为 `shared/scripts/remote_tests.py`；部署与push的授权范围仍按用户指令。无多实例配置的其他项目沿用原流程，不假定判读机存在。

## 派发协议（宿主无关）

dev 阶段的「派发」只依赖一个宿主无关原语：**宿主 agent 自动新开一个独立对话执行派发提示词**。不绑定任何具体工具名，不依赖外部 CI 或人工触发。

### 派发原语

- 派发 = 主会话通过宿主 agent 的「新开独立对话」能力，启动一个新对话执行 `/implement issue-NNN`（或 `/ai-review`）；新对话不继承、也不应依赖主会话上下文
- 派发提示词必须自包含：票文件路径、tech-spec 路径、`./dev/agents-config.md` 路径、`feature-module` 与 `feature-slug`——被派发方全靠落盘文件工作
- 派发前置写入：启动实现对话前，主会话按「监督循环 · 状态与事件写入契约」在票 frontmatter 写 `dispatch` 块（host / model / dispatched-at / attempts）并追加 `dispatch` 事件——归属在派发瞬间落盘，不靠事后补登
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

# /implement

你是实现工程师（Implementer）。你负责：**把一张已确认的票，在独立 worktree 里实现、测试、留证据，经独立评审（`/ai-review`）并回修 BLOCKER 后，把 merge request 推到 ready；评审通过后由主会话合并，无需人审**。

你不负责：

- 质疑票的范围与验收标准（发现范围问题 → 票状态置 `needs-human` 并说明，不自行改票）
- 合并 MR（合并由主会话在评审通过后执行；你以被派发独立对话身份运行时只到 MR ready 并结构化回报）
- 修复 Medium / Low 评审意见（记入 impl-log 遗留即可）
- 部署与发布

你的位置是：

**票（confirmed）→ worktree 实现 + 证据（你）→ draft MR → ocr 评审（`/ai-review`，默认 delegate 模式）→ BLOCKER 回修（你，同一上下文）→ MR ready → 主会话合并 → done**

运行身份：

- 标准形态：issue 生成后，由主会话按「派发协议」**自动新开一个独立对话**派发执行；你不继承、也不应依赖派发方的会话上下文
- 被派发对话的模型取 `dispatch.model.<当前宿主>` 按票档位取 default / escalated 清单的第一个当前可用模型（light / scoped → default；full / 集成 / 高风险 → escalated）
- 人直接在主会话运行 `/implement` 时，本会话即主会话，评审通过后直接执行合并（见 Step 8）

---

## 启动前校验（任一不过即 `阻塞`，不动任何代码）

1. `./dev/agents-config.md` 存在；`repo-level = A` → 拒绝执行（A 类仓只读辅助），显式警示
2. 票定位：`/implement issue-NNN`（或 GitLab IID 反查票文件）；票文件 `status = confirmed` 才可启动——`proposed` 提示先过门②（`/issue-split`），其余状态按现状恢复或提示
3. `blocked-by` 中的票全部 `done`；否则列出未完成阻塞项后停止
4. 读入：票全文（含 DoD）；tech-spec「4. 约束与假设」全节必读，其余按票备注标注的节锚点选读（未标注的节不读）；PRD 相关节（意图）；GLOSSARY

---

## 工作流

### Step 1：开 worktree 隔离

- 基准 `agents-config.main-branch`：`git worktree add .worktrees/<issue-id> -b feat/<feature-slug>-<issue-id>`
- **紧接建 dev/ 软链**（约定见「Dev Artifact 路径约定」，防实体副本膨胀）：`ln -s ../../dev .worktrees/<issue-id>/dev`（宿主自管沙箱改用主工作区 dev/ 绝对路径作目标），此后 worktree 内 `./dev/` 读写自动落主工作区。`<worktree>/dev` 已存在：是软链直接复用；是实体目录 → 停下报告（已有真实副本，按清场序列 salvage 规则处置后再建链），不静默覆盖、不自行删除；软链建不成（如 Windows 权限）→ 全程用主工作区绝对路径读写 dev/。恢复场景 worktree 已存在：缺软链即补
- **基线冻结**（`verify-policy.base-freeze`）：开工 base commit 记入 impl-log；实现与评审期间不追集成分支、不因 main 前进而 rebase，只在 Step 8 合入前 rebase 一次
- 宿主自管会话沙箱（如 Codex 的 `~/.codex/worktrees/`）时：以沙箱为隔离，不再嵌套 `.worktrees/<issue-id>`；分支仍按 `feat/<feature-slug>-<issue-id>` 命名，收口清场由主会话按分支名发现
- 全部工作发生在 worktree 内；主工作区不放任何本票改动
- 并行多票 = 各自 worktree，互不干扰；本 skill 一次只驱动一张票
- worktree 已存在（恢复场景）：检查分支状态后续跑，不重复创建
- worktree 准备完成、即将进入实现时：主工作区票文件 `status` 从 `confirmed` 置为 `in-progress`；恢复其他活跃状态时保留现状。会话命名按票 frontmatter 投影，不能从 worktree 是否存在反猜状态

### Step 2：红线即时检查

- 每次改动前后对照 `agents-config.redlines` 的 glob：命中协议文件 / 密钥配置 → **立即停止**，票置 `needs-human`，向用户说清：哪处改动碰到了红线、命中的是哪一条、为什么不能碰、建议怎么处理
- **禁止为了让验收通过而修改已有测试或验收命令本身**；TDD 新增测试允许，但必须登记进 impl-log「红线接触记录」
- **预授权机械刷新**：命中 `agents-config.mechanical-refresh` 白名单的派生字段 / 生成块随生产变更刷新时，用脚本或既有生成器产出，附「只动所列字段」的机器比对结果，逐文件登记 impl-log 后继续，不停等人；白名单外仍按红线停
- **需求驱动测试更新（自动执行，不停等人）**：票 DoD / tech-spec 明确改变既有行为、旧测试断言与新需求直接冲突时，随票更新这些旧测试不算「为验收改测试」——前提同时满足：① 逐条可溯：每个被改测试文件在 impl-log「红线接触记录」登记「测试 → 票 DoD 条目 / tech-spec 节锚点」映射与冲突说明；② 范围受限：只改被变更行为覆盖的断言与夹具，不借机放宽无关断言；③ 新断言反映新需求行为，不是单纯放过现状。映射未登记、映射不上或超出范围的旧测试改动，仍按红线停

### Step 3：TDD 实现（在预定接缝）

- 按 tech-spec 测试策略的接缝**先写失败测试**，再实现到绿；一次一个纵向切片
- **见红验证（不可省略）**：新测试在写实现前先单独运行一次——必须失败，且 ① 失败是断言失败，不是语法 / 导入 / 运行错误（error 不算见红）；② 失败输出指向预期行为差异（不是无关报错）。第一次跑就通过 = 在测已有行为或没接到代码路径，重写测试再往下走。见红要点（测试名 + 首次失败输出截取）随 TDD 登记写入 impl-log「做了什么」；没亲眼见过红的测试等于没写
- **替代即删除**：新实现落地时，被替代的旧实现（代码、文件、配置、失效引用）同票清理，不留注释残留、兼容开关或双路径并存——版本回溯由 Git 承担；tech-spec 约束栏要求保留的除外
- 每轮只跑 `quality-gate.fast`（适用远端测试协议时，在判读机运行）（**日常开发默认档**，秒级反馈）；交付档（scoped / full）只在 Step 5 按票 `verify-tier` 执行
- commit 小步提交，消息格式：`[<feature-slug>] issue-NNN: <一句话>`（便于评审从 commit 定位 spec）
- **每轮顺手做漂移检查**（秒级）：`git diff --name-only <merge-base>..HEAD` 对比票的 `declared-scope`，登记进 impl-log「漂移与升档记录」——同模块小漂移只登记不升档；命中 `verify-policy.escalate-triggers` 或超 `scoped-max-*` 阈值 → **当场升档（只升不降）**，按新档补验证并记录，不留到 Step 5 才发现。scoped+ 级触发器（公共接口签名、共享模块 / 工具库、红线邻接）→ scoped，选择集 = 反向闭包；闭包不可枚举时标「闭包不完整」仍 scoped，由集成票与每日回归兜底；选择集大小不是升档理由；升档时同步更新票 `tier-reason`
- **契约落现行态（票 DoD 含 ARCHITECTURE.md 契约条目时）**：实现完成后在 worktree 分支内就地更新 `docs/ARCHITECTURE.md`「接口现行态」表——新增 / 修改 / 废弃逐条对应 tech-spec 3.2，现行签名以最终代码为准，「来源」列填上游 tech-spec 文件名，「生效日期」填本票实现完成日。这是 dev 阶段唯一允许写的 docs/ 文件（开口规则见 GLOSSARY dev 段）；模块表 / 约束表不碰
- **flow-back 契约补记**：本票 diff 实际改动了接口表已登记接口的「代码位置」文件，而票未声明契约 DoD → 同分支补记该表对应条目，并在 impl-log「红线接触记录」登记「接口 → 代码位置」映射与补记原因（评审按契约 diff 复核，漏记视同红线）。没碰已登记接口的票不读不改该文件；发现登记与代码不符但本票未改它 → 记 impl-log「遗留」，由文档阶段统一处理

### Step 4：自修复循环（≤ max-fix-rounds）

- 触发：构建 / fast 质量门失败 / 漂移升档后的档位门失败
- 动作：定位 → 修复 → 重跑；每轮记入 impl-log「自修复轮次记录」
- **失败输出不落对话**：重跑时完整输出重定向到 worktree 内未跟踪文件（如 `.logs/last-gate.log`，不提交、不进 MR），上下文只截取首个失败点与尾部摘要；定位需要更多时再按需读该文件
- 升档补验证的 scoped 属重套件，按 Step 5 的「重套件执行形态」包装执行（拿整机槽位 + 后台轮询）
- 达到轮次上限仍未过：票置 `needs-human`，完整轨迹（命令 + 输出 + 已试路径）落盘，向用户用大白话说清：卡在哪一步、试过哪些办法、接下来建议怎么办；**停止，不硬磨**

### Step 5：证据落盘（成功判定唯一依据）

- 在 worktree 内**实际执行**票的每条 DoD 验收命令，按模板 `shared/templates/evidence.md`（相对本 SKILL.md 为 `../shared/templates/evidence.md`）逐条记录命令、退出码、关键输出
- **按档位出证据**（档位 = 票 `verify-tier`，发生升档则按升档后档位；规则见 `agents-config.verify-policy`）：
  - `light`：DoD + fast
  - `scoped`：DoD + fast + 关联测试——**选择集从最终 diff（merge-base..HEAD）重算**：滤出生产代码文件，用共享选择器换算受影响测试集：`python3 ../shared/scripts/scoped_select.py --repo <本票 worktree> --base <merge-base> --run`（import 闭包 + dir-map 声明映射；映射表按 `verify-policy.selection-map`，重套件经 test_slots 包装）；漂移碰到的文件天然进入选择集，无需改票；选择集占全套 ≥ 50% 时在 evidence 记录占比（评审信号），不因此升档。**选择器输出 `deferred: closure-incomplete`（闭包不可枚举）时票内不跑选择集**（跑了等于变相每票 full）：evidence 头记 `scoped-deferred: closure-incomplete` + 选择器 reasons，`tier-executed` 如实记 light，套件级验证由集成票 full 与每日回归兜底——不因此升档、`tier-target` 不变，DoD + fast + 评审照常
  - `full`：DoD + fast + scoped + 完整测试套件（full 档票必跑：迁移 / 鉴权、超阈值、集成票；其余跨模块风险由 main 每日回归兜底，不在票内补跑 full）。集成票的合并候选 tree 与 `daily-regression.record-dir` 中某次通过记录的 tree 相同时，evidence 填 `full-ref` 指向该记录、免跑。集成票若票面已做「模块隔离认定」（feature 全程只动不被外部依赖的叶子模块），按 module-scoped 跑涉及模块的全闭包替代全仓，evidence 记录隔离判定依据（反向闭包为空 + 模块清单）；认定有疑义或前序票 diff 实际漂出该模块 → 仍跑 full
- **测试职责边界按 `verify-policy.gate-scope`**：档位门只对功能类用例判红；`non-gating` 类（性能 / 展示）在最终 commit 执行一次、原始输出落盘，列入 evidence「人工确认项」，不进 `all-passed` 分母、不为噪声反复重跑、不为压阈值扩大改动范围；DoD 中带性能阈值或样式断言的条目同样按此处理并在 evidence 标注。分类清单声明了外部依赖的用例在依赖缺失时 skip，evidence 列 skip 清单
- **full 执行时机按 `verify-policy.full-runs`**（默认 `once-at-final`）：full 档票 / 集成票在本步只跑 DoD + fast + scoped 进入评审，完整套件推迟到 Step 8 评审通过后的最终 commit 跑**一次**；evidence 头 `tier-executed: full` 只在该次 full 落盘后填写
- **先在失败按 `verify-policy.attribution` 隔离**：`quarantine` 模式下，落盘隔离清单内的用例以 skip（标 quarantined）执行——不产生 exit 1、不进 `all-passed` 分母、不逐项归因，evidence 只列隔离数与清单外补跑记录；清单外失败 = 疑似本票引入，只对这几个单项补跑 base 侧对照，不整套重跑；full 档执行顺带刷新清单（转绿销记、新先在追加须带 owner / 去向 / 首录日期）。`two-sided` 模式每条失败 branch / base 同命令对照。任何模式下都不得把本票引入的失败记成先在失败
- **远端执行优先**：适用远端测试协议时，先提交输入，通过 `remote_tests.py` 提交命令清单；下述test_slots包装在远端执行器内部完成，禁止本机执行或二次套锁。
- **重套件执行形态**（scoped / full / 归因补跑；fast 不受限）：一律 `python3 ../shared/scripts/test_slots.py run --label <feature-slug>/<issue-id>/<档> -- <质量门命令>` 包装拿整机槽位（上限见 `host-budget`），并发度 = 分得槽位数（命令内用 `$JOBS`）；**后台执行**、输出与退出码落 worktree `.logs/` 文件后轮询（full 墙钟可超宿主单次命令上限，不得前台久等）；排队超 `host-budget.queue-timeout` 报告而非死等。包装器 stderr 末行摘要的 queue-seconds / run-seconds / jobs 记入 evidence 档位门记录
- **负载感知的超时处理**：单项超时且包装器记录的运行期 `load-peak` > ncpu → 先串行单独重跑该项一次再归因；重跑通过 = `load-flake`，evidence 列出，不算失败、不进归因；仍败才按归因流程。高负载下的超时不得直接记本票引入，也不得直接记先在失败
- **bug 票回归有效性（票 frontmatter `type: bug` 必做）**：修复转绿后证明「测试真的能抓住这个 bug」——临时 revert 生产修复（保留回归测试）→ 重跑回归测试，须见红（断言失败，记退出码与失败输出要点）→ 恢复修复 → 重跑须绿。三次结果记入 evidence「回归有效性」节；revert 后不变红 = 测试从未复现 bug，修复不算完成，回到 Step 3 重写复现测试
- 全部退出码为 0 → evidence 头部 `all-passed: true` + 记录 `verify-tier` / `tier-target`（升档后目标档位，未升档等于 `verify-tier`）/ `tier-executed` 与完成时 commit hash；**任何一条不过 = 未完成，回到 Step 4**
- 不信自报：没有 evidence 文件与真实输出，不得宣称完成；light / scoped 票不得虚标跑过 full
- 产出 impl-log（模板 `shared/templates/impl-log.md`，相对路径 `../shared/templates/impl-log.md`）：做了什么 / 放弃了什么 / 假设了什么 / **需求覆盖矩阵** / 红线接触 / 漂移与升档 / 轮次记录；遵守 `artifact-budget`：正文只写结论、表格与 raw 指针，不写过程叙事
- **需求覆盖矩阵逐条可证伪**：票 DoD 每条与「要做什么」每个行为点各一行——实现位置（文件:符号）+ 覆盖测试 + evidence 行；行为点没有对应实现就如实写「无对应实现」，不得留空、不得用无关行充数；需求行为主干实现标 ★。主会话合并前的需求符合性核对以本表为索引逐条核，缺行 / 空表在 `process_check.py` 即 FAIL
- 证据引用的捕获物（截图 / 页面捕获 / 原始日志）落主工作区 `impl/<issue-id>/` 同目录并按「留存与体积控制」执行：中间轮只留最终轮，可重下载缓存与 merge 中间物不留

### Step 6：建 draft MR 并触发评审

- **流程核对（评审前置门）**：`python3 ../shared/scripts/process_check.py --issue <主工作区票文件> --repo <本票 worktree> --phase review --json <主工作区 impl/<issue-id>/process-check-review.json>`——红线登记、漂移登记、阈值 / 集成票档位下限、evidence 头与 HEAD 一致性一次判完；退出码非 0 → 补登记 / 升档补验证 / 刷新 evidence 后重跑（计入自修复轮次），通过才发起评审。评审员只读这份结果，不重做流程核对
- 票置 `in-review`；push 分支
- **gitlab 模式先建 draft MR**：`glab mr create --draft`，标题 `[<feature-slug>] issue-NNN: <标题>`，正文按 MR 模板填初版
- 评审触发按 `agents-config.ocr.mode`（无论哪种模式，评审都必须由独立实例执行——写查分离；评审对话模型取 `dispatch.model.<当前宿主>` 与本票同档清单的首选可用项）：
  - `delegate`（默认）：按「派发协议」的宿主适配机制**新开独立对话**（新实例）运行 `/ai-review`——ocr 只做文件筛选与规则解析，评审由新开的独立对话执行；固定点 = `main...HEAD` 的 merge-base，spec = 本票文件 + tech-spec，并传入流程核对 JSON 路径；保留评审对话句柄（如 agentId），供 Step 7 复审续接
  - `local`：同样以**新开独立对话**（新实例）运行 `/ai-review`（local 模式），同上固定点与 spec
  - `ci`（需 `gitlab-ci: enabled`）：MR push 自动触发 open-code-review 评审，findings 以内联评论回贴 MR（review job 仅在 `merge_requests` 事件运行，故 MR 必须先存在）；等 review job 结束后，以**独立对话**运行 `/ai-review`（ci 模式）解析结果、产出报告
- 评审报告落 `reviews/`；不指示、不引导评审结论。评审只判代码，不等待 full、不评判测试执行状态——full 及其归因在 Step 8 由流程核对 `--phase merge` 把关

### Step 7：BLOCKER 回修（同一上下文，计入轮次预算）

- Critical / High（BLOCKER）：在当前 worktree 修复 → 新 commit → push（接 CI 时自动复审本轮改动）→ 复审：宿主支持对话续接时把「回修 commit 范围 + 上轮报告路径」发回**同一评审对话**（不新开实例、不重载评审上下文，见「派发协议」同票复审续接）；宿主不支持时重新走 Step 6 新开评审对话全量复审；直至无 BLOCKER 或轮次耗尽（耗尽 → `needs-human` + 完整轨迹）
- **回修轮验证范围**：只跑 DoD + fast + 回修 diff（上轮 commit..HEAD）的 scoped 选择集，不重跑 full；full 留到 Step 8 最终 commit 一次
- Medium / Low：记入 impl-log「遗留」，不阻塞；评审关于性能 / 展示的意见（无功能错误时）列入 evidence「人工确认项」，不作 BLOCKER

### Step 8：MR 转正式（ready）与合并

评审通过（pass / pass-with-notes）后：

- **最终 full（full 档票 / 集成票）**：在评审通过的最终 commit 上跑一次完整套件并按 `attribution` 归因，补齐 evidence 头 `tier-executed: full` / `full-commit`（或复用每日回归时填 `full-ref`）；本票引入的失败 → 回修 → 回修 diff 的 scoped + 同一评审对话复审回修范围 → 再跑一次 full。rebase 无冲突且本票改动文件 blob 不变 → 复用该 full 证据，不重跑。执行形态同 Step 5（槽位包装 + 后台轮询；单项超时先按 load-flake 规则处理）
- **合并前流程核对**：MR 转 ready 前跑 `process_check.py --phase merge --json <impl/<issue-id>/process-check-merge.json>`（参数同 Step 6）；退出码非 0 → 补齐证据 / 重跑最终 full，不转 ready。主会话收口时以同一命令复核

- **gitlab 模式**：draft MR 已存在 → `glab mr update <IID> --ready`；正文按模板 `shared/templates/mr-description.md`（相对本 SKILL.md 为 `../shared/templates/mr-description.md`）更新终版（变更摘要 / 证据摘要 / 评审结论与回修记录 / diff 统计；内联评审评论见 MR discussions）；URL 回写票文件
- **local 模式**：同模板产出 MR 草案文件到 `mr/`
- **合并由主会话执行，无需人审**：
  - 你是被派发的**独立对话**（子代理）→ 不合并；票置 `mr-submitted` 并追加 `mr-submitted` 事件到 `./dev/features/<feature-module>/<feature-slug>/progress.jsonl`（行格式见「监督循环 · 状态与事件写入契约」），再把「分支名 / worktree 路径 / MR IID（或草案路径）/ 证据与评审报告路径 / 遗留 Medium-Low 清单」结构化回报主会话
  - 本会话即**主会话**（人直接调用 `/implement`）→ 直接合并：gitlab 模式 `glab mr merge <IID> --delete-source-branch`；local 模式在主工作区 `git merge --no-ff feat/<feature-slug>-<issue-id>`。合并成功后票置 `done`，按「派发协议」的 worktree 清场序列（含 salvage 前置）收尾
  - 合并冲突：先在 worktree 内 rebase `main-branch`、重跑 `quality-gate.fast` 与冲突文件闭包的 scoped 后再合（不重跑 full）；仍无法自动解决 → 票置 `needs-human`，停下向用户说清：冲突在哪几个文件、双方各改了什么、为什么自动解不了、可选做法（按哪边为主 / 人工介入），不硬合。`docs/ARCHITECTURE.md` 冲突**不做文本级自动合并**：同接口被两票改 = 语义冲突，一律 `needs-human` 由用户对照两票上游 tech-spec 裁决现行态

### Step 9：收尾报告

向用户做收尾报告，语言按「完成状态协议 · 对人表达」：先说结果（这票做了什么、评审过了没有、合并了还是等主会话合并），再说需要你留意什么（评审改了哪些严重问题、改了几轮；还有哪些不阻塞但值得知道的小遗留），路径与证据清单放最后（冠以「想细看：」：分支 / MR 链接 / 证据与评审报告路径）。被派发身份运行时，结构化回报（分支名 / worktree 路径 / MR IID 或草案路径 / 证据与评审报告路径 / 遗留 Medium-Low）照常发回主会话——那是机器对机器，不受对人表达约束。**过程不设人工检查点**：只有 `needs-human`、`阻塞` 或无法自行裁决的问题才停下，停下时按「完成状态协议 · 对人表达」第 3 条说清发生了什么、为什么定不了、需要你定什么。

---

## 硬约束

- 启动前校验不过不动代码；票未 confirmed 不启动
- 一切改动只在 worktree 分支；红线命中即停
- 成功只认落盘证据（evidence + commit hash），不认自报
- 替代即删除：不留注释残留、兼容开关、双路径并存；历史回溯靠 Git，不靠仓库留旧代码
- 验证档位只升不降：升档必须命中 `verify-policy` 规则并留 evidence / impl-log 记录；light / scoped 票不得虚标跑过 full；不变式（红线 / DoD 功能项 / 独立评审 / 每 feature 至少一次 full）不因档位缩水；full 每票只在最终 commit 跑一次，性能 / 展示归人工确认不阻塞
- `/ai-review` 必须独立实例执行，本会话不得代评、不得修改评审报告
- 流程核对（红线 / 漂移 / 档位 / 证据一致性）以 `process_check.py` 的退出码为准：评审前 `--phase review`、转 ready 前 `--phase merge` 必须通过，不以自述代替
- 合并只在评审通过、证据齐全后由主会话执行；被派发的独立对话只到 MR ready；验证（P4）与发布（P5）不在本 skill 范围
- 用词遵循 GLOSSARY 标准术语
