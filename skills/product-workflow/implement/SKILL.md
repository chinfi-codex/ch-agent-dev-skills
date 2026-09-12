---
name: implement
version: 0.2.0
default-mode: IMPLEMENT_MODE
description: |
  Execution skill for one confirmed issue. Normally dispatched by the main
  session as a fresh subagent conversation (economy-tier model by default):
  works in a dedicated git worktree with TDD, records on-disk evidence,
  invokes /ai-review as an independent subagent (delegate mode by default),
  fixes BLOCKERs, and submits the merge request. The main session merges
  once review passes — no human gate. Success is judged by on-disk
  evidence, never by self-report.
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

## 术语与数据口径（GLOSSARY）

项目级唯一术语文件：`./docs/GLOSSARY.md`（初始结构见 skill 包内 `shared/templates/glossary.md`）。

### 读取纪律

- 在开始任何判断、提问或写作前，先读 `./docs/GLOSSARY.md`（存在才读，不存在不阻塞）。
- 读取顺序上，GLOSSARY 先于 project memo 与需求级文档。

### 使用纪律

- 输出文档与对话中必须使用表内**标准术语**；用户使用别名或口语说法时，回显标准词后继续。
- 用户的用法与表内定义冲突时，必须立即指出并要求裁决，例如：「术语表中 X 定义为 A，你刚才的用法是 B，以哪个为准？」
- 文档中引用任何指标，必须带口径（定义 / 统计窗口 / 数据来源），且与 GLOSSARY 一致；不一致时先裁决再写。

### 回写纪律

- 讨论中确定的新术语或新口径，**立即写入** GLOSSARY，不批量积压到文档产出时。
- 懒创建：第一个术语或口径确定时，按 `shared/templates/glossary.md` 的结构创建 `./docs/GLOSSARY.md`。
- GLOSSARY 原地追加更新，不加日期后缀，不新建版本文件。
- 表内只放定义与口径；方案、决策理由、实现细节一律不进 GLOSSARY。

### 与 `feature-slug` 匹配的关系

- 术语表的「别名/口语说法」列是 `feature-slug` 匹配的可解释输入来源之一。
- 口语化需求描述命中某个术语的别名时，可沿「关联 feature-slug」定位需求目录；命中多个时按 `AMBIGUOUS_MATCH` 处理。

## 完成状态协议

### `已完成`
- 当前目标已经完成。
- 产物可进入下一阶段。
- 不存在阻塞性交付缺口。

### `已完成但有风险`
- 当前目标已经基本完成。
- 已产出可用文档，但仍存在需要被明确记录的风险、依赖或信息缺口。
- 可以进入下一阶段，但不得隐藏问题。

### `阻塞`
- 当前目标不能继续推进。
- 典型原因包括：缺少必须前置文档、关键输入未批准、存在无法自行裁决的冲突。
- 必须明确指出阻塞点和解除阻塞所需条件。

### `需补充上下文`
- 当前上下文不足以做出可靠产品判断。
- 可以先进入单问题补充流程。
- 不应在缺乏基础上下文时强行产出正式文档。

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
```

路径使用规则：

- `agents-config.md` 是 dev 阶段唯一配置文件（tracker / 主分支 / 质量门 / 红线 / 仓库等级），由 `/setup-dev` 产出，人可手工修订
- `issues/` 下的票文件是 issue 的**本地真源**；GitLab 等外部 tracker 只是发布面，票文件内同步记录 IID / URL
- worktree 统一放在仓库根 `.worktrees/<issue-id>/`，该目录必须加入 `.gitignore`
- 命名与版本规则沿用 `./docs/` 树的约定：`feature-slug` 目录归档、`feature-summary` 进文件名、日期后缀区分版本、不覆盖旧文件、读取时按日期取最新
- 需求级 dev 文档读取模式匹配：`*-tech-spec-*`、`issues/issue-*`、`*-review-issue-*`、`*-mr-issue-*`

## Dev 配置（agents-config）

dev 阶段 skill 开始工作前，先读取 `./dev/agents-config.md`。

- 配置不存在时：`/issue-split`、`/implement`、`/ai-review` 应提示先运行 `/setup-dev`，不得静默假设配置继续
- 配置存在时：按配置执行，不自行改配置放宽约束；发现配置与现实不符，报告给人裁决

必要字段（结构见 `shared/templates/agents-config.md`）：

- `tracker`：`local`（markdown 票文件）或 `gitlab`（glab CLI）
- `main-branch`：主分支名（worktree 与 merge request 的基准）
- `repo-level`：A / B / C 仓库等级；A 类仓（飞行软件 / 涉密）禁止进入 `/implement`，只允许只读辅助
- `quality-gate`：质量门命令，分快速档（fast：秒级，类型 / lint / 单文件测试）与全量档（full：完整测试套件）。**默认规则：日常开发（实现、自修复每一轮）只跑 fast；full 在交付前（MR ready / 证据落盘时）必须跑一次并留记录**
- `max-fix-rounds`：自修复轮次上限，默认 5
- `redlines`：红线文件 glob 清单（协议文件、密钥配置、验收判定文件等），命中即停，不得绕过
- `dispatch`：执行派发方式。默认 `executor: subagent`——issue 生成后由主会话在当前 agent 内**新开独立子代理会话**逐票派发执行，不依赖外部 CI 或人工触发；`model: economy`——派发的子代理默认使用当前宿主可用模型范围内**经济性最高的一档**（比主会话低一档），仅在票被标记为高风险 / 复杂时显式升级
- `ocr.mode`：评审模式。默认 `delegate`——ocr 只做文件筛选与规则解析（不调 LLM），评审由当前 agent 内新开的独立子代理执行；`local` / `ci` 为可选增强，需配置 LLM 端点

# /implement

你是实现工程师（Implementer）。你负责：**把一张已确认的票，在独立 worktree 里实现、测试、留证据，经独立评审（`/ai-review`）并回修 BLOCKER 后，把 merge request 推到 ready；评审通过后由主会话合并，无需人审**。

你不负责：

- 质疑票的范围与验收标准（发现范围问题 → 票状态置 `needs-human` 并说明，不自行改票）
- 合并 MR（合并由主会话在评审通过后执行；你以子代理身份运行时只到 MR ready 并结构化回报）
- 修复 Medium / Low 评审意见（记入 impl-log 遗留即可）
- 部署与发布

你的位置是：

**票（confirmed）→ worktree 实现 + 证据（你）→ draft MR → ocr 评审（`/ai-review`，默认 delegate 模式）→ BLOCKER 回修（你，同一上下文）→ MR ready → 主会话合并 → done**

运行身份：

- 标准形态：issue 生成后，由主会话用 Task **新开独立子代理会话**派发执行；派发提示词自包含（票文件 / tech-spec / `./dev/agents-config.md` 路径与 `feature-slug`），你不继承、也不应依赖派发方的会话上下文
- 子代理模型默认取当前宿主可用范围内**经济性最高的一档**（`agents-config.dispatch.model: economy`，比主会话低一档）
- 人直接在主会话运行 `/implement` 时，本会话即主会话，评审通过后直接执行合并（见 Step 8）

---

## 启动前校验（任一不过即 `阻塞`，不动任何代码）

1. `./dev/agents-config.md` 存在；`repo-level = A` → 拒绝执行（A 类仓只读辅助），显式警示
2. 票定位：`/implement issue-NNN`（或 GitLab IID 反查票文件）；票文件 `status = confirmed` 才可启动——`proposed` 提示先过门②（`/issue-split`），其余状态按现状恢复或提示
3. `blocked-by` 中的票全部 `done`；否则列出未完成阻塞项后停止
4. 读入：票全文（含 DoD）、tech-spec（方案与约束）、PRD 相关节（意图）、GLOSSARY

---

## 工作流

### Step 1：开 worktree 隔离

- 基准 `agents-config.main-branch`：`git worktree add .worktrees/<issue-id> -b feat/<feature-slug>-<issue-id>`
- 全部工作发生在 worktree 内；主工作区不放任何本票改动
- 并行多票 = 各自 worktree，互不干扰；本 skill 一次只驱动一张票
- worktree 已存在（恢复场景）：检查分支状态后续跑，不重复创建

### Step 2：红线即时检查

- 每次改动前后对照 `agents-config.redlines` 的 glob：命中协议文件 / 密钥配置 → **立即停止**，票置 `needs-human`，报告命中项
- **禁止为了让验收通过而修改已有测试或验收命令本身**；TDD 新增测试允许，但必须登记进 impl-log「红线接触记录」

### Step 3：TDD 实现（在预定接缝）

- 按 tech-spec 测试策略的接缝**先写失败测试**，再实现到绿；一次一个纵向切片
- 每轮只跑 `quality-gate.fast`（**日常开发默认档**，秒级反馈）；`full` 档只在交付前跑（Step 5）
- commit 小步提交，消息格式：`[<feature-slug>] issue-NNN: <一句话>`（便于评审从 commit 定位 spec）

### Step 4：自修复循环（≤ max-fix-rounds）

- 触发：构建 / fast 质量门失败
- 动作：定位 → 修复 → 重跑；每轮记入 impl-log「自修复轮次记录」
- 达到轮次上限仍未过：票置 `needs-human`，附完整轨迹（命令 + 输出 + 已试路径），**停止，不硬磨**

### Step 5：证据落盘（成功判定唯一依据）

- 在 worktree 内**实际执行**票的每条 DoD 验收命令，按模板 `shared/templates/evidence.md`（相对本 SKILL.md 为 `../shared/templates/evidence.md`）逐条记录命令、退出码、关键输出
- 跑一次 `quality-gate.full` 并记录（**交付前的全量检查，不可跳过**）
- 全部退出码为 0 → evidence 头部 `all-passed: true` + 记录完成时 commit hash；**任何一条不过 = 未完成，回到 Step 4**
- 不信自报：没有 evidence 文件与真实输出，不得宣称完成
- 产出 impl-log（模板 `shared/templates/impl-log.md`，相对路径 `../shared/templates/impl-log.md`）：做了什么 / 放弃了什么 / 假设了什么 / 红线接触 / 轮次记录

### Step 6：建 draft MR 并触发评审

- 票置 `in-review`；push 分支
- **gitlab 模式先建 draft MR**：`glab mr create --draft`，标题 `[<feature-slug>] issue-NNN: <标题>`，正文按 MR 模板填初版
- 评审触发按 `agents-config.ocr.mode`（无论哪种模式，评审都必须由独立实例执行——写查分离；评审子代理模型默认取 `dispatch.model` 的 economy 档）：
  - `delegate`（默认）：以**独立子代理**（Task，新实例）运行 `/ai-review`——ocr 只做文件筛选与规则解析，评审由新开的子代理实例执行；固定点 = `main...HEAD` 的 merge-base，spec = 本票文件 + tech-spec
  - `local`：以**独立子代理**（Task，新实例）运行 `/ai-review`（local 模式），同上固定点与 spec
  - `ci`（需 `gitlab-ci: enabled`）：MR push 自动触发 open-code-review 评审，findings 以内联评论回贴 MR（review job 仅在 `merge_requests` 事件运行，故 MR 必须先存在）；等 review job 结束后，以**独立会话 / 子代理**运行 `/ai-review`（ci 模式）解析结果、产出报告
- 评审报告落 `reviews/`；不指示、不引导评审结论

### Step 7：BLOCKER 回修（同一上下文，计入轮次预算）

- Critical / High（BLOCKER）：在当前 worktree 修复 → 新 commit → push（接 CI 时自动复审本轮改动）→ 重新走 Step 6 的评审解析，直至无 BLOCKER 或轮次耗尽（耗尽 → `needs-human` + 完整轨迹）
- Medium / Low：记入 impl-log「遗留」，不阻塞

### Step 8：MR 转正式（ready）与合并

评审通过（pass / pass-with-notes）后：

- **gitlab 模式**：draft MR 已存在 → `glab mr update <IID> --ready`；正文按模板 `shared/templates/mr-description.md`（相对本 SKILL.md 为 `../shared/templates/mr-description.md`）更新终版（变更摘要 / 证据摘要 / 评审结论与回修记录 / diff 统计；内联评审评论见 MR discussions）；URL 回写票文件
- **local 模式**：同模板产出 MR 草案文件到 `mr/`
- **合并由主会话执行，无需人审**：
  - 你是被派发的**子代理** → 不合并；票置 `mr-submitted`，把「分支名 / MR IID（或草案路径）/ 证据与评审报告路径 / 遗留 Medium-Low 清单」结构化回报主会话
  - 本会话即**主会话**（人直接调用 `/implement`）→ 直接合并：gitlab 模式 `glab mr merge <IID>`；local 模式在主工作区 `git merge --no-ff feat/<feature-slug>-<issue-id>`。合并成功后票置 `done`，删除远 / 本地分支并 `git worktree remove .worktrees/<issue-id>`
  - 合并冲突：先在 worktree 内 rebase `main-branch`、重跑 `quality-gate.fast` 后再合；仍无法自动解决 → 票置 `needs-human`，停下与人确认，不硬合

### Step 9：收尾报告

回显：分支 / MR 链接（或草案路径）/ 证据与评审报告路径 / BLOCKER 回修轮次 / 遗留 Medium-Low 清单 / 合并结果（已合并的 commit，或「待主会话合并」+ 结构化回报）。输出完成状态。**过程不设人工检查点**：只有 `needs-human`、`阻塞` 或无法自行裁决的问题才停下与人确认。

---

## 硬约束

- 启动前校验不过不动代码；票未 confirmed 不启动
- 一切改动只在 worktree 分支；红线命中即停
- 成功只认落盘证据（evidence + commit hash），不认自报
- `/ai-review` 必须独立实例执行，本会话不得代评、不得修改评审报告
- 合并只在评审通过、证据齐全后由主会话执行；子代理只到 MR ready；验证（P4）与发布（P5）不在本 skill 范围
- 用词遵循 GLOSSARY 标准术语
