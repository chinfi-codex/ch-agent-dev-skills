## 前置说明

- 先定位当前项目上下文：项目 `slug`、当前工作分支、当前 feature 名称或任务名
- 开始判断或写作前，先读现有上下文文档；上游文档区分项目级与需求级：项目级取最新 `project memo`，需求级先定位唯一 `feature-slug` 再读该目录下的上游文档
- 读取顺序（各类型取最新版本，不存在的跳过）：`./docs/GLOSSARY.md` → 最新 `project memo` → 最新 `feature brief` → 最新 `PRD` → 最新 `pd-review-report`
- 所有正式产物统一写入 artifact 根目录，不把关键上下文散落在临时回复中
- 行为边界：只做产品工作流内的判断、提问、整理与写作；不输出技术实现方案、数据库设计、API 设计、任务拆解；上下文不足先显式说明缺口，再进入单问题补充；发现已有文档与当前结论冲突，指出并在新产物中统一口径

## `feature-module` 归档规则

`feature-module` 是大功能块级分组标识（默认中文，如「交易链路」「性能优化」），命名与 `feature-slug` 同规：项目内唯一、稳定、一经建立不因标题调整而改变。需求级文档一律按 `./docs/features/<feature-module>/<feature-slug>/` 两级归档；一个 `feature-slug` 恰好归属一个 `feature-module`，先有模块、后有 slug。模块划分与归属登记在 `./docs/GLOSSARY.md` 的「feature-module 归档」节，模块发现 = 读该节 + `./docs/features/` 目录一层。

- 模块新建与归属确认只有 `/pd-plan` 可执行，且必须经用户最终确认后才建目录；其余技能一律不得擅自新建模块或需求目录
- 归属判据是主价值归属：砍掉该需求，哪块业务能力受损最大就归哪个模块；跨模块大需求在 issue 层拆，不动模块归属

## `feature-slug` 识别规则

`feature-slug` 是需求级唯一稳定标识（默认中文），定位 `./docs/features/<feature-module>/<feature-slug>/`；一经建立不因标题调整而改变。用户直接给出 slug 时优先按其定位；否则在 `./docs/features/` 下做最多两层（模块层 / slug 层）的可解释匹配，只用可解释规则，不模糊猜测。输入来源：

- `./docs/GLOSSARY.md` 的「别名/口语说法」「关联 feature-slug」列与「feature-module 归档」节的成员列
- 目录名（模块层 + slug 层）；文档头部 `feature_slug` / `feature_name`；文档标题

匹配结果三类：

- `EXACT_MATCH`：唯一高置信命中——回显「当前需求已匹配到 <feature-module>/<feature-slug>（<feature_name>）」后继续
- `AMBIGUOUS_MATCH`：多个合理候选——单问题确认，不自行选择
- `NO_MATCH`：无可接受候选——`/pd-plan` 可作新需求处理（先确认 `feature-module` 归属，用户最终确认后再确认新 `feature-slug`）；`/prd`、`/pd-review` 不得擅自新建模块或需求目录，返回 `需补充上下文` 或 `阻塞`

匹配到存量平铺目录（`features/` 直下一级 slug 目录、无模块层）时：可读可用，回显迁移建议（GLOSSARY 登记 + `git mv` 移入模块目录），不自动迁移；新需求一律落两级结构。

## `feature-summary` 使用规则

- `feature-summary` 是需求级文档文件名中的中文摘要名（4-12 个汉字，简短可搜索），标识大功能下的具体子功能或本次子范围；不进目录名，不替代 `feature-slug`；同一 slug 下允许多个
- 写需求级文档前必须同时确定唯一 `feature-module`、`feature-slug` 与本次 `feature-summary`；用户只给大功能名且无法从上下文唯一推断时，先提问确认；回显归档信息时三者同时回显
