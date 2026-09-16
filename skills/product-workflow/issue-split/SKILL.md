---
name: issue-split
version: 0.3.0
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
  into tracer-bullet tickets with blocking edges and auto-checkable DoD
  commands, gets gate-2 (task list) confirmation, publishes to the
  configured tracker — then the main session auto-dispatches each ticket
  as a fresh host-agent conversation (host-agnostic: Task in Claude Code,
  Agent in ZCode, or the host's equivalent new-conversation mechanism;
  economy-tier model by default), supervises execution until each ticket
  is merged, and advances through the DAG without further human gates.
  Default mode is DOC_MODE.
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

- 默认进入 `DOC_MODE`
- 只有用户明确说出 `批准写代码`、`go implement`、`开始实现`，才能切到 `IMPLEMENT_MODE`
- “顺手改一下”“直接做了吧”这类表述，不算批准，仍视为 `DOC_MODE`

### `DOC_MODE`

- 只允许读代码、读文档、写文档
- 只允许写入：`./docs/**`、`specs/**`、`ADR/**`、`*.md`、`*.mdx`
- 可产出：design、spec、ADR、TODO、checklist、change request、PRD、review report
- 禁止写或改：源码、测试、脚手架、运行配置
- 禁止触碰：`*.py`、`*.js`、`*.ts`、`*.tsx`、`tests/**`、`src/**`、`app/**`、`package.json`、`pyproject.toml`、`requirements.txt`
- 禁止执行实现导向命令：`python`、`pytest`、`node`、`npm`、`bun`、`cargo`、`go test`、build scripts

### 停止条件

1. 读取相关代码与文档
2. 产出 design/spec/doc
3. 总结“若获批将实现什么”，但不实现
4. 明确请求批准
5. 立即停止并等待

### 批准规则

- 用户未使用明确批准词时，必须重申仍在 `DOC_MODE`
- 未获批准，不得写任何源码、测试、脚手架、配置变更

### 宿主边界

- 这套规则主要是流程约束
- 若宿主支持 hook、ACL、wrapper，应由宿主做硬拦截
- 在 Codex-compatible host 中，如无宿主级拦截，本 skill 仅为 advisory，不保证技术隔离

## 术语与数据口径（GLOSSARY）

项目级唯一术语文件：`./docs/GLOSSARY.md`（初始结构见 skill 包内 `shared/templates/glossary.md`）。

### 读取纪律

- 在开始任何判断、提问或写作前，先读 `./docs/GLOSSARY.md`（存在才读，不存在不阻塞）。
- 读取顺序上，GLOSSARY 先于 project memo 与需求级文档。

### 使用纪律

- 输出文档与对话中必须使用表内**标准术语**；用户使用别名或口语说法时，回显标准词后继续。
- 定义按「它是什么」写，不按「它做什么」写——GLOSSARY 是词汇表，不是设计文档。
- 用户命中某术语的「拒绝词」时：回显标准词，说明该说法已被淘汰及原因，再继续。拒绝词与别名不同：别名是可接受的口语说法（用于匹配），拒绝词是被明确淘汰、会引发歧义的说法。
- 用户的用法与表内定义冲突时，必须立即指出并要求裁决，例如：「术语表中 X 定义为 A，你刚才的用法是 B，以哪个为准？」
- 文档中引用任何指标，必须带口径（定义 / 统计窗口 / 数据来源），且与 GLOSSARY 一致；不一致时先裁决再写。

### 回写纪律

- 讨论中确定的新术语或新口径，**立即写入** GLOSSARY，不批量积压到文档产出时。
- 准入测试：只收项目专属术语与数据口径；通用编程概念、行业通行词不收（即便项目内高频出现）。
- 讨论中明确淘汰的说法，立即记入对应术语的「拒绝词」列，防止同一批词汇反复回潮。
- 懒创建：第一个术语或口径确定时，按 `shared/templates/glossary.md` 的结构创建 `./docs/GLOSSARY.md`。
- GLOSSARY 原地追加更新，不加日期后缀，不新建版本文件。
- 表内只放定义与口径；方案、决策理由、实现细节一律不进 GLOSSARY。

### 与 `feature-slug` 匹配的关系

- 术语表的「别名/口语说法」列是 `feature-slug` 匹配的可解释输入来源之一。
- 口语化需求描述命中某个术语的别名时，可沿「关联 feature-slug」定位需求目录；命中多个时按 `AMBIGUOUS_MATCH` 处理。

## 提问格式

提问分两种形态：

- **拷打轮次**：需求澄清阶段按「拷打规则（Grilling）」执行，一轮可问多个相互独立的 frontier 问题
- **阻塞型单点确认**：`feature-slug` 歧义裁决、模式 / 方向批准、术语冲突裁决、是否进入下一阶段等，一次只问一个

本节规则对两种形态的每一道题都适用。

## 通用提问原则

先把当前未决问题归类为三种之一：

- `可假设继续`：对当前判断影响较小，带着默认假设继续，并在输出中显式写出假设
- `必须提问后继续`：影响核心判断、关键前提、模式选择、优先级、规则边界或最终结论，不能绕过
- `仅记录为低优先级风险`：不影响当前判断，暂记为风险或待确认项

判为 `必须提问后继续` 的典型情形（会改变以下任一项）：

- 核心判断是否成立；当前讨论对象到底是什么；用户真正要做出的决策是什么
- 目标、范围或优先级是否变化
- 关键规则、边界、约束或成功标准如何定义；谁是目标用户 / 购买者 / 决策者
- 商业模型中的关键变量；市场切口或增长路径；当前阶段的判断口径
- 最终输出是否可能误导用户

判为必须提问后，必须先发问再继续——不要用「可以先假设」绕过高影响问题，也不要把高影响问题沉入「待确认项」。提问用 `AskUserQuestion`，不自行脑补答案。

## 每次提问必须遵循以下格式

1. Re-ground 当前上下文
   - 用 2-4 句重述当前讨论对象、当前阶段、当前要解决的问题

2. 说明为什么必须问
   - 明确指出这个问题会影响哪一个核心判断
   - 如果不问清，会导致什么判断失真

3. 给出当前最推荐的判断方向
   - 先给 recommendation，但要显式说明它仍依赖用户确认
   - recommendation 不能伪装成结论

4. 在适合做决策分叉时，再给 A / B / C options
   - `A` 为推荐选项
   - `B` 为保守或替代选项
   - `C` 为激进、延后或不同路径选项
   - 如果当前问题不是“选项分叉题”，不要强行给 A / B / C

5. 阻塞型单点确认一次只问一个
   - 单点确认不合并多个决策点
   - 拷打轮次不受此限：相互独立的 frontier 问题一轮问完

## 提问风格要求

- 问题短、具体、直击判断核心，不为礼貌加缓冲
- 关键变量缺失时先提问，不输出大段结论
- 回答仍抽象就继续追问，直到足以支撑判断

## 完成状态协议

### `已完成`
- 产物可进入下一阶段，不存在阻塞性交付缺口。

### `已完成但有风险`
- 已产出可用文档，仍存在须显式记录的风险、依赖或信息缺口；可进入下一阶段，但不得隐藏问题。

### `阻塞`
- 当前目标不能继续推进（缺必须前置文档 / 关键输入未批准 / 存在无法自行裁决的冲突）。
- 必须明确指出阻塞点和解除阻塞所需条件。

### `需补充上下文`
- 上下文不足以做出可靠产品判断；先进入单问题补充流程，不强行产出正式文档。

## Dev Artifact 路径约定

`./docs/` 树（产品阶段）延伸出 `./dev/` 树（开发阶段），两棵树由同一 `feature-slug` 贯通：

```text
./dev/
  agents-config.md
  features/
    <feature-slug>/
      <feature-summary>-tech-spec-YYYY-MM-DD.md
      issues/
        issue-001-<短名>.md
      impl/
        issue-001/
          impl-log-issue-001.md
          evidence-issue-001.md
      reviews/
        <feature-summary>-review-issue-001-YYYY-MM-DD.md
      mr/
        <feature-summary>-mr-issue-001-YYYY-MM-DD.md
      prototypes/
        <短名>/
          <原型文件（一次性）>
          <短名>-verdict-YYYY-MM-DD.md
```

路径使用规则：

- `agents-config.md` 是 dev 阶段唯一配置文件（tracker / 主分支 / 质量门 / 红线 / 仓库等级），由 `/setup-dev` 产出，人可手工修订
- `issues/` 下的票文件是 issue 的**本地真源**；GitLab 等外部 tracker 只是发布面，票文件内同步记录 IID / URL
- `prototypes/` 归档 `/prototype` 的一次性原型与 verdict：原型代码只进此目录，不进产品源码；verdict 沿用日期后缀版本规则，是 /pd-plan、/prd、/tech-spec 的上游输入
- worktree 统一放在仓库根 `.worktrees/<issue-id>/`，该目录必须加入 `.gitignore`
- 命名与版本规则沿用 `./docs/` 树的约定：`feature-slug` 目录归档、`feature-summary` 进文件名、日期后缀区分版本、不覆盖旧文件、读取时按日期取最新
- 需求级 dev 文档读取模式匹配：`*-tech-spec-*`、`issues/issue-*`、`*-review-issue-*`、`*-mr-issue-*`、`prototypes/*-verdict-*`

## Dev 配置（agents-config）

dev 阶段 skill 开始工作前，先读取 `./dev/agents-config.md`。

- 配置不存在时：`/issue-split`、`/implement`、`/ai-review` 应提示先运行 `/setup-dev`，不得静默假设配置继续
- 配置存在时：按配置执行，不自行改配置放宽约束；发现配置与现实不符，报告给人裁决

必要字段（结构见 `shared/templates/agents-config.md`）：

- `tracker`：`local`（markdown 票文件）或 `gitlab`（glab CLI）
- `main-branch`：主分支名（worktree 与 merge request 的基准）
- `repo-level`：A / B / C 仓库等级；A 类仓（飞行软件 / 涉密）禁止进入 `/implement`，只允许只读辅助
- `quality-gate`：质量门命令，分快速档（fast：秒级，类型 / lint / 单文件测试）与全量档（full：完整测试套件）。**默认规则：日常开发（实现、自修复每一轮）只跑 fast；full 在交付前（MR ready / 证据落盘时）必须跑一次并留记录**
- `max-fix-rounds`：自修复轮次上限，默认 99
- `redlines`：红线文件 glob 清单（协议文件、密钥配置、验收判定文件等），命中即停，不得绕过
- `dispatch`：执行派发方式。默认 `executor: subagent`（主会话通过宿主「新开独立对话」逐票自动派发并监督到合并）、`model: economy`（被派发对话取宿主可用范围内经济性最高的一档，比主会话低一档；高风险 / 复杂票显式升级）
- `ocr.mode`：评审模式。默认 `delegate`——ocr 只做文件筛选与规则解析（不调 LLM），评审由宿主 agent 内新开的独立对话执行；`local` / `ci` 为可选增强，需配置 LLM 端点

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
- 必须先确定唯一 `feature-slug`；读取该目录下最新 `*-tech-spec-*` 文档
- tech-spec 不存在或状态仍为 `草稿`（未确认）→ 直接 `阻塞`
- 补读：`./docs/GLOSSARY.md`、该 feature-slug 最新 PRD（意图核对）

---

## 工作流

### Step 0：确定拆解输入

回显 tech-spec 版本与关键结论（模块 / 接口 / 约束 / TDD 接缝），确认拆解基线。

### Step 1：起草垂直切片票

每张票是一条 **tracer bullet**：

- **纵向切穿所有相关层**（数据 / 逻辑 / 界面 / 测试），不是按层横切
- 独立可演示：完成后能演示一个完整的行为，不是半截基础设施
- 一张票的上下文能装进一个实现会话；装不下就再拆
- **声明阻塞边**（blocked-by）：以 DAG 表达依赖，编号按依赖序
- 首张票应是最细的端到端贯通（先打穿，再加厚）

例外——宽改造（大范围重构）：按 expand–contract 序列拆（加新形 → 分批迁移 → 删旧形），每批独立成票、可独立验收，不得拆出「中间态不可用」的票。

**DoD 必须写成可自动判定的验收命令**：每票 1–3 条「命令 + 期望输出」，从 tech-spec 测试策略的接缝与验收命令草案导出。禁止「功能正常」「符合预期」类不可判定表述；写不出可判定命令的验收标准，退回 `/tech-spec` 补测试策略。

### Step 2：门②——清单确认（阻塞型，必须人批准）

向用户呈现编号清单，一次审全部：

- 每票一行：编号 / 标题 / 交付什么 / blocked-by / 验收命令
- 附整体 DAG 与建议执行顺序（frontier 在哪几张）
- 按「提问格式」确认：粒度对不对、阻塞边对不对、验收命令能不能判定
- 用户要求调整 → 修订后重新呈现；**未获确认不发布任何票**

粒度失败路径：单票改动横跨超过两个模块 → 自动再拆或标记需人工裁决，不硬发。

### Step 3：发布

确认后按 `agents-config.tracker` 发布：

- **local 模式**（默认）：按模板 `shared/templates/issue.md`（相对本 SKILL.md 为 `../shared/templates/issue.md`）在 `./dev/features/<feature-slug>/issues/` 下每票一个文件，依赖序编号（issue-001 起），frontmatter `status: confirmed`
- **gitlab 模式**：本地票文件照写（本地真源），另用 `glab issue create` 逐票建 issue：标题 `[<feature-slug>] issue-NNN <标题>`，正文取票文件内容，label `ready-for-agent`；把 IID / URL 回写票文件 frontmatter。glab 命令失败：报告错误并保持 local 产物完整，不阻塞
- 修改父级 issue / tech-spec / PRD：禁止

### Step 4：收尾与派发协议（门②后由主会话自动执行）

- 回显：票数、DAG、frontier 票清单（blocked-by 为空的票）
- 本 skill 的职责到此结束；**门②是执行段之前唯一的人审检查点，之后全程不再等人审核**。主会话按上方「派发与监督协议（宿主无关）」逐票派发、监督执行直到逐票合并完成（实现动作发生在被派发的独立对话内，不违反本会话 DOC_MODE）

---

## 硬约束

- tech-spec 未确认不开拆；拆解不改变方案内容
- 门②未过不发布；发布只写票文件与 tracker issue，不碰源码
- DoD 不可自动判定的票不允许存在
- 票一旦 confirmed，标题与 DoD 不得由本 skill 单方面修改；变更须重新过门②
- 分工：派发、监督与合并在主会话，实现与评审各自在独立的新开对话；主会话不代写实现、不代评
- 用词遵循 GLOSSARY 标准术语
