## 架构图谱与经验的消费义务

- 架构事实唯一真源是 `docs/architecture/*.json`（`pandu.architecture-graph/v2`）。读取现行架构或影响分析使用：
  ```bash
  python3 ../shared/scripts/architecture_graph.py --repo <项目根> [--module <模块>|--interface <关键词>|--version <版本>]
  ```
  默认查询排除 needs_verification / superseded / retired，release_only 仅按匹配版本查询。无分片的旧首页必须先 `migrate-legacy`，不得把旧表与分片合并成双真源。
- 所有开发技能按模块 / 分类 / 适用条件查询项目经验；通用经验只在适用时消费。记录命中的稳定经验 ID、适用结论和冲突，不把关键词命中当适用结论。
- `/pd-plan` 记录受影响模块及现行架构 ID；模块归属按 GLOSSARY 登记，图谱不另造归属。
- `/tech-spec` 在 3.2 用固定表逐条声明 `稳定 ID / new|modify|retire|describe / 名称 / 模块 / 源代码位置 / supersedes / 验证证据`，同时标记版本适用范围和命中的项目经验。
- `describe` 只适用于现有 ID 的职责文字纠正。表内沿用现有名称、模块、源码锚点和替代关系，证据栏用 `—`。承接票单独声明全部 ID，只能更新这些记录的非空 `responsibility`，可随唯一生成器更新首页。票级与候选级都核对提交差异及所属合入，记录的状态、历史证据、关系和其他字段保持原值。新增、删除、迁移分片或夹带源码、测试、配置会拒绝。独立的确认补充方案可承接此类票，其 frontmatter 用 `acceptance_from: <原方案文件名>` 继承同需求已验证的验收计划。补充规格必须全为 `describe`，其全部票的冻结范围限于架构分片及生成首页。同需求再次修正同一记录须另行确认补充规格。候选按第一父链核验每次真实合入及所声明的 ID，拒绝未经声明的中间改动和终态漂移。
- `/issue-split` 把每个 ID 写入承接票 frontmatter `architecture-updates`，同时拆出失败场景和验证义务；不得只在集成票留空泛责任。SPEC-2 按 ID 对账，不再以正文出现 architecture 字样放行。
- `/implement` 按票更新分片、证据与替代关系。`current` 必须有完整 verified_commit 且为候选 HEAD 祖先，候选上的源码 hash / symbol 未漂移，证据 path / hash / commit / tree 可追溯；否则保持 needs_verification。
- `source` 始终是唯一主绑定和主代码证据绑定；可选记录级 `sources` 只登记辅助源码，逐项沿用 `path / symbol / sha256` 约束，同一 path+symbol+sha256 不得重复；同一文件的不同符号可共存，完全重复的主 / 辅助绑定只在迁移时去重并留审计，同一路径 hash 不一致即拒绝。`current / release_only` 必须逐个核对辅助源码在 verified_commit 与候选 HEAD 的存在、bytes SHA256 和 symbol，verified_commit 须为 HEAD 祖先且 verified_commit..HEAD 不得漂移；release_only 的主 source 仍只作历史核验。多源码或预填 hash 不会把 needs_verification 自动提升为 current。
- 未声明 ID 的源码 diff 命中接口记录的主 source 或任一辅助 sources 时均执行 flow-back，补分片、验证信息和 impl-log 接口→代码映射；删除 / 重命名主源码必须 retired / superseded。graph-only 无票 / spec 义务的变更同样拒绝。v1 迁移必须保留辅助源码及其多源码验证，不得删除或降级为只核主 source。
- `docs/ARCHITECTURE.md` 只能由以下命令生成；禁止手改：
  ```bash
  python3 ../shared/scripts/architecture_graph.py render write --repo <项目根>
  python3 ../shared/scripts/architecture_graph.py render check --repo <项目根>  # 只读字节比对
  ```
- `/process_check --phase ticket` 只在票声明 `architecture-updates`、改动架构产物或命中已绑定源码时执行票级架构门，并只判 base..head 新增错误（baseline 仅 INFO）；feature `freeze` / `check` 在 candidate 上执行全量 active-spec、flow-forward/back 与首页字节比对。`/review` 另运行 `contract_check.py --check` 做完整契约巡检。事实更正需已生效代码 / 发布证据，职责或语义争议交回原责任环节或 human-decision。
- 经验消费须记录命中的经验 ID；需求、项目和通用经验维持既有分层，并分别记录最后复现与最后核实。规则级建议关联承接票、延期决定或拒绝理由。
