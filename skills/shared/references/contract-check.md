# 架构契约巡检规则（contract_check v2）

> 配套脚本：`shared/scripts/contract_check.py`；图谱与首页：`shared/scripts/architecture_graph.py`；严格 schema：`shared/references/architecture-graph.schema.json`。
> 原则：**只报告、不改文件**；唯一写入例外是 `--init` 初始化盘点草稿、`architecture_graph.py migrate-legacy` 一次性迁移和 `render write` 确定性首页生成。
> v2 同时接入 `/review` 复盘与 `process_check review / merge / close`；FAIL 会阻止对应交付，不再只是事后报告。

## 术语与模型

- **tech-spec = flow-forward**：日期快照，不覆盖旧文件；整体取代关系由 frontmatter `supersedes` 显式声明。
- **图谱分片 = 唯一写入源**：模块、接口、约束均写 `docs/architecture/*.json`（`pandu.architecture-graph/v2`）。`docs/ARCHITECTURE.md` 只是 `architecture_graph.py render write` 的确定性短首页，禁止手改。
- **逐 ID 义务**：tech-spec 3.2 固定表的每个稳定 ID 必须恰好落入一张票的 `architecture-updates`，再对照分片动作、supersedes、代码和证据；正文出现 architecture 或“来源引用”不再构成放行。
- **flow-back**：实现 diff 命中接口记录绑定的主 source 或任一辅助 sources 而票未声明 ID 时，必须同分支补分片、验证信息和 impl-log 接口→代码映射；删除 / 重命名主源码必须 retired / superseded。graph-only 无票 / spec 义务的变更同样拒绝。
- **多源码绑定**：`source` 是唯一主绑定和主代码证据绑定；可选 `sources` 是辅助源码数组，对象沿用 source 的 `path / symbol / sha256` 约束；同一 path+symbol+sha256 不得重复，同文件不同符号可共存，同一路径 hash 不一致即拒绝。`current / release_only` 对辅助源码逐个核对 verified_commit 与候选 HEAD 的文件存在、bytes SHA256、symbol 及区间无漂移，verified_commit 必须为 HEAD 祖先；release_only 主 source 保持仅历史提交核验。
- **current**：完整 verified_commit 为候选 HEAD 祖先，候选上的源码 bytes / SHA256 / symbol 未漂移，证据 path / hash / commit / tree 可追溯；否则只能 needs_verification。release_only 不进入默认现行查询；needs_verification 不因配置多源码或填入 hash 自动提升。

## 检查项（`--check`，0 通过 / 1 有发现 / 2 输入错误）

| ID | 检查 | 确定性判定 |
| --- | --- | --- |
| MOD-1 | 模块归属 | GLOSSARY「feature-module 归档」与 kind=module 的记录精确一致；空图谱不得掩盖已有模块事实 |
| SPEC-1 | 取代链 | tech-spec supersedes 目标存在且链无环 |
| SPEC-2 | flow-forward 义务 | 3.2 每个稳定 ID ↔ 恰好一张票 architecture-updates ↔ 分片动作 / 关系 ↔ 代码证据；缺任一环 FAIL。祖父历史只 INFO，不自动 current |
| GRAPH-1 | 图谱 schema 与保鲜 | document / record / source / sources / relation / evidence 严格类型与未知字段检查；辅助绑定唯一且同路径 hash 一致；ID 唯一、重用 / 消失、关系 dangling、supersedes 环、冲突 current、current 祖先 / hash / 证据全部核对 |
| GRAPH-2 | 首页唯一生成源 | `render check` 字节比较磁盘首页与分片生成结果；手改正文、计数或过期内容均 FAIL。无分片旧项目必须先 migrate-legacy |
| FLOW-BACK | 实现反补义务 | 按 base/head diff 核主 source 与所有辅助 sources 的新增 / 修改 / 删除 / rename、票声明、分片更新、验证信息与 impl-log 映射 |

MOD-1 / SPEC-1 的表解析只取节内第一张表；删除线退役行不参与对账。文档目录先行造成的模块瞬时差仅 INFO，登记过的接口与代码事实不一致一律 FAIL。

## 一次性迁移与首页

无分片旧项目使用：

```bash
python3 ../shared/scripts/architecture_graph.py migrate-legacy --repo <项目根>
python3 ../shared/scripts/architecture_graph.py render check --repo <项目根>
```

迁移只把旧表导入 `legacy-migration.json` 并保留原始行、hash 和审计记录；所有记录均为 `needs_verification`，不得自动提升 current。已有有效分片或已经迁移时拒绝重复迁移，不保留运行时旧表 + 分片合并读取。v1 图谱迁移同样必须保留 `sources` 辅助源码、路径唯一约束和逐个源码验证，不得删除多源码信息或退化为只核主 source。

首页写入与检查是两个独立命令：

```bash
python3 ../shared/scripts/architecture_graph.py render write --repo <项目根>
python3 ../shared/scripts/architecture_graph.py render check --repo <项目根>
```

`--init` 只生成初始化盘点草稿，已有文件拒绝覆盖；它不能代替迁移或证据核验。

## 巡检输出纪律

- 报告列出 FIND / FAIL 及通过计数，不改任何业务文件；事实更正由责任阶段按分片写权限处理。
- `process_check` 聚合架构结果时保持只读；生成器验证只能在独立临时工作区运行，不能借检查改写票 worktree。
- 模块、接口或约束语义有争议时进入 human-decision，不以复盘或文本合并覆盖另一票已确认的契约。
