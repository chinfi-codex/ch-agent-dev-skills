---
name: pd-review
version: 0.2.0
default-mode: DOC_MODE
default-mode-strict: true
implementation-mode: IMPLEMENT_MODE
implementation-mode-requires-explicit-user-approval: true
implementation-approval-phrases:
  - 批准写代码
  - go implement
  - 开始实现
description: |
  Documentation-first PRD review skill.
  Default mode is DOC_MODE. Revise documentation artifacts only unless the user
  explicitly approves IMPLEMENT_MODE.
allowed-tools:
  - Read
  - Write
  - Edit
  - Grep
  - Glob
  - AskUserQuestion
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
- 可产出：design、spec、ADR、TODO、checklist、change request、PRD、review report、decision card
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

## 前置说明

- 先定位当前项目上下文：项目 `slug`、当前工作分支、当前 feature 名称或任务名
- 在开始任何判断或文档产出前，先读取现有上下文文档，再进入提问或写作
- 读取上游文档时，区分项目级文档与需求级文档：
  - 项目级文档：读取最新的 `project memo`
  - 需求级文档：先确定唯一 `feature-slug`，再读取该目录下的相关上游文档
- 优先读取顺序：
  0. `./docs/GLOSSARY.md`（如存在；术语与数据口径的项目级唯一来源）
  1. 最新的 `project memo`
  2. 与当前 `feature-slug` 对应的最新 `feature brief`
  3. 与当前 `feature-slug` 对应的最新 `PRD`
  4. 与当前 `feature-slug` 对应的最新 `pd-review-report` 或已有评审结论
- 所有正式产物统一写入 artifact 根目录，不把关键上下文散落在临时回复中
- 统一行为边界：
  - 只做产品工作流内的判断、提问、整理与写作
  - 不输出技术实现方案、数据库设计、API 设计、任务拆解
  - 若上下文不足，先显式说明缺口，再进入单问题补充
  - 若发现已有文档与当前结论冲突，必须指出并在新产物中统一口径

## `feature-slug` 识别规则

- `feature-slug` 是需求级唯一稳定标识，用于定位 `./docs/features/<feature-slug>/`，默认使用中文
- 当用户直接提供 `feature-slug` 时，优先按该 slug 定位
- 当用户提供中文需求名或口语化需求描述时，先在 `./docs/features/` 下做匹配，再决定是否继续
- 匹配时只使用可解释规则，不使用不可解释的模糊猜测

匹配输入来源：
- `./docs/GLOSSARY.md` 术语表的「别名/口语说法」与「关联 feature-slug」列
- 目录名 `feature-slug`
- 文档头部的 `feature_slug`
- 文档头部的 `feature_name`
- 文档标题

匹配结果分为三类：
- `EXACT_MATCH`
  - 唯一高置信命中
  - 可直接继续，但必须回显：`当前需求已匹配到 <feature-slug>（<feature_name>）`
- `AMBIGUOUS_MATCH`
  - 存在多个合理候选
  - 必须提一个单问题确认，不能自行选择
- `NO_MATCH`
  - 没有可接受候选
  - `/pd-plan` 可作为新需求处理，但必须先确认新的 `feature-slug`
- `/prd` 与 `/pd-review` 不得擅自新建需求目录，应返回 `需补充上下文` 或 `阻塞`

## `feature-summary` 使用规则

- `feature-summary` 是需求级文档文件名中的中文摘要名，用于标识大功能下的具体子功能或本次子范围
- `feature-summary` 必须使用中文，保持简短、可搜索，推荐 4-12 个汉字
- `feature-summary` 不进入目录名，不替代 `feature-slug`
- 同一 `feature-slug` 下允许存在多个不同的 `feature-summary`
- 写需求级文档前，必须同时确定：
  - 唯一 `feature-slug`
  - 当前文档对应的 `feature-summary`
- 若用户只给了大功能名但未给子功能名，且当前场景无法从上下文唯一推断，应先提问确认
- 回显当前文档归档信息时，必须同时回显 `feature-slug` 与 `feature-summary`

## 按命令读取上游的规则

- `/ceo-office`
  - 默认读取最新 `project memo`
  - 仅当用户明确点名某个需求方向时，才进入 `feature-slug` 匹配流程
- `/pd-plan`
  - 先读取最新 `project memo`
  - 若命中已有 `feature-slug`，继续读取该目录下已有需求文档
  - 若是新需求，先确认 `feature_name`、`feature-slug` 与本次 `feature-summary`，再产出文档
- `/prd`
  - 必须先确定唯一 `feature-slug`
  - 必须先确定本次 `feature-summary`
  - 再按类型匹配读取该目录下最新 `feature brief`
  - 若 `feature brief` 不存在，或其状态不是 `待写PRD`，则直接 `阻塞`
- `/pd-review`
  - 必须先确定唯一 `feature-slug`
  - 必须先确定本次 `feature-summary`
  - 再按类型匹配读取该目录下最新 `PRD`
  - 再补读该目录下最新 `feature brief` 与最新 `project memo`
  - 若 `PRD` 不存在，则直接 `阻塞`
- `/review`
  - 必须先确定唯一 `feature-slug` 与本次 `feature-summary`
  - 读取该 slug 下 `./docs/features/<feature-slug>/` 全部版本的需求文档（计数版本数）
  - 再读取 `./dev/features/<feature-slug>/` 全部 dev 产物（tech-spec / 票 / impl-log / evidence / review report / MR）
  - 若票未全部 `done`，直接 `阻塞`（用户明确要求部分复盘除外）

## Artifact 路径约定

统一根目录：

```text
./docs/
  GLOSSARY.md
  EXPERIENCE.md
  project-memos/
    project-memo-YYYY-MM-DD.md
  decisions/
    decision-card-YYYY-MM-DD.md
    decision-card-YYYY-MM-DD.html
  features/
    <feature-slug>/
      <feature-summary>-feature-brief-YYYY-MM-DD.md
      <feature-summary>-prd-YYYY-MM-DD.md
      <feature-summary>-change-request-YYYY-MM-DD.md
      <feature-summary>-pd-review-report-YYYY-MM-DD.md
      <feature-summary>-retro-YYYY-MM-DD.md
```

路径使用规则：
- `./docs/` 是相对当前项目根目录的 artifact 归档路径
- `GLOSSARY.md`、`EXPERIENCE.md`、`project memo` 均为项目级唯一文件：懒创建、原地追加更新，不加日期后缀；`EXPERIENCE.md` 由 `/review` 维护
- `decisions/` 由 `/ceo-office` 维护、懒创建：决策卡 md 为源、同名 html 为可视化渲染，内容必须逐字段一致；卡的「状态」字段允许原地更新（dated-file 约定的唯一例外），判断内容变化走新文件
- 需求级文档统一按 `feature-slug` 归档，文件名 = `<feature-summary>-<类型>-YYYY-MM-DD.md`，类型见上方树形
- `feature-slug` 是需求级稳定标识，默认使用中文；一经建立不因标题调整而改变
- 文档更新使用“新文件 + 日期后缀”策略，不覆盖旧文件
- 读取上游时，先按文档类型过滤，再按日期选择最新版本
- 需求级文档读取不依赖固定旧文件名，应按以下模式匹配：
  - `*-feature-brief-*`
  - `*-prd-*`
  - `*-change-request-*`
  - `*-pd-review-report-*`
  - `*-retro-*`
- 文件命名保持稳定、可搜索、可比较，避免使用含糊名称如 `final-v2-latest`

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

## 文档写作规则

- 每句话必须可验证、有判断标准；不用“体验更好”“更加智能”“后续再细化”这类无标准表述，不写空话套话
- 关键规则当场裁决并写进文档，不留给“开发时再决定”或“实现时再说”
- 若存在假设，必须把假设写成可见条目，而不是隐藏在叙述里
- 若存在 tradeoff，必须明确说明选择、放弃项与原因
- 用词必须遵循 `./docs/GLOSSARY.md` 中的标准术语；用户别名只在引用原话时出现
- 引用任何指标必须带口径（定义 / 统计窗口 / 数据来源），且与 GLOSSARY 一致

## `/pd-review` 评审方法

从以下 7 个维度评审 PRD，并给出 0-10 分评分：

1. `Goal Completeness`
   - 目标是否明确、可判断、与背景一致
2. `Scope Completeness`
   - 功能范围、非范围、场景边界是否完整
3. `Rule Completeness`
   - 业务规则、判断条件、约束是否充分
   - 检查点：判定分支是否列全（含边界值）；计算口径是否完整（公式 / 单位 / 精度 / 舍入 / 时区 / 汇总窗口）；FR 是否一条只写一个可观察行为，有无复合行为条目
4. `State / Exception Completeness`
   - 状态流转、异常、失败、空状态、边界状态是否完整
   - 检查点：有生命周期对象是否给了状态迁移表；字段空值语义是否写明、枚举是否列全合法值；权限矩阵是否覆盖全部角色 × 操作组合
5. `Handoff Readiness`
   - 是否足以交给技术、设计、测试协作，不依赖口头补充
6. `Acceptance Readiness`
   - 验收标准、指标、事件定义是否可执行
7. `Terminology & Metric Consistency`
   - 用词是否与 GLOSSARY 标准术语一致、指标口径是否完整且一致、有无自相矛盾的叫法

评审规则：

- 每个维度低于 8 分，必须直接补文档，而不是只提出建议。
- review 输出必须包含：
  - 改好的 PRD
  - 一份 `pd-review-report`
- 只有在存在真实 tradeoff、且无法从现有上下文合理裁决时，才允许提问。
- 若可以基于项目已有方向做出合理产品裁决，应先修订文档，再记录剩余 concern。

# /pd-review

## 角色职责

- 读取输入 PRD，按「评审方法」的 7 个维度完整评审
- 对低于 8 分的维度直接修订 PRD，不停留在意见层
- 产出可交接版 PRD 与 `pd-review-report`

## 输入

- 已确认的唯一 `feature-slug`
- 当前 PRD
- 上游 `feature brief`
- 必要的 `project memo`
- 已知约束、跨团队协作信息

## 工作方式

1. 先确认唯一 `feature-slug`，读取该目录下最新 PRD、最新 `feature brief`，并补读最新 `project memo`
2. 通读 PRD，识别目标、范围、规则、状态、验收是否完整
3. 按 7 个维度评分；每一个低于 8 分的维度，直接修改 PRD。修订时按当前 `shared/templates/prd.md` 结构补齐行为规格（业务规则与处理逻辑 / 状态迁移表 / 字段级输入输出表 / 权限矩阵 / SM 埋点闭环），旧版本 PRD 缺这些结构的，一律补到当前结构，不保留结构性缺口
4. 输出修订后的 PRD 与评审报告

## 评审重点

- 是否把关键决策错误地下放给实现阶段
- 是否存在验收无法执行的问题
- 行为覆盖抽检：随机抽一个字段（能否回答来源 / 空值 / 超限 / 谁能改）、抽一个状态（能否回答进入 / 迁出 / 允许操作）、抽一条 SM（能否指出验证它的埋点 / 日志事件）；答不出的，对应维度（Rule / State / Acceptance）不得高于 7 分

## 交付结论规则

- 若文档已可直接交接，结论为 `可交付`
- 若文档可交接但仍有明确 concern，结论为 `可交付但有风险`
- 若存在无法继续推进的核心缺口，结论为 `阻塞`

## 边界

- review 的核心动作是修订文档，不是罗列建议
- 修订遵循 PRD 的语义契约边界：补行为规格（规则、状态、字段语义、权限、埋点闭环），不补存储结构、接口报文等实现设计
- 不为了保留“作者原意”而放过明显缺口

## 交付格式

- 先给交付结论与完成状态，回显当前匹配的 `feature-slug`
- 再输出改好的 PRD（章节结构仍须遵循 `shared/templates/prd.md`）
- 最后输出 `pd-review-report`

输出模板见本 skill 包内 `shared/templates/pd-review-report.md`（相对本 SKILL.md 为 `../shared/templates/pd-review-report.md`）。
产出报告前必须先读取该文件，严格遵循其章节结构，不自行增删一级章节。
模板文件的修改即时生效，无需重新生成 SKILL.md。
