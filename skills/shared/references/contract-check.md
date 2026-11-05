# 架构契约巡检规则（contract_check）

> 配套脚本：`shared/scripts/contract_check.py`；分片兼容读取器：`shared/scripts/architecture_graph.py`；图谱 schema：`shared/references/architecture-graph.schema.json`（相对各 SKILL.md 为 `../shared/scripts/contract_check.py`）。
> 原则（对齐 spec-kit analyze）：**只报告、不改文件**；唯一例外是 `--init` 初始化盘点生成基线草稿。
> v1 为报告模式：由人或在 `/review` 复盘时运行，输出漂移清单；误报率稳定前不进 `process_check.py` 门禁。

## 术语与模型

- **tech-spec = flow-forward**：日期快照，不覆盖旧文件；取代关系由 frontmatter `supersedes: <旧 tech-spec 文件名>` 显式声明，机器可查。
- **图谱分片 = 唯一写入源**：模块、接口、约束均写入 `./docs/architecture/*.json` 对应分片；`./docs/ARCHITECTURE.md` 是由 `architecture_graph.py --homepage-out` 生成的短索引。`/pd-plan`、`/tech-spec`、承接实现票按图谱 ID 更新各自负责记录，证据不足时保留 `needs_verification`。
- **flow-back**：实现期 diff 改动图谱接口记录绑定的源文件而票未声明契约 DoD → 同分支补记分片，并在 impl-log「红线接触记录」登记映射，由评审按证据复核。

## 检查项（`--check`，退出码 0 通过 / 1 有发现 / 2 输入错误）

| ID | 检查 | 判定（确定性规则） |
| --- | --- | --- |
| MOD-1 | 模块归属与目录一致 | 图谱项目：GLOSSARY「feature-module 归档」↔ kind=module 的图谱记录精确一致；旧项目：GLOSSARY ↔ ARCHITECTURE 模块表一致。`docs/features/` 与 `dev/features/` 目录差仅 INFO。|
| SPEC-1 | supersedes 链完整 | 每份 tech-spec 的 `supersedes`（若有）指向的文件存在；链无环 |
| SPEC-2 | 契约义务已派发 | `status: 已确认（待拆解）` 的 tech-spec 若「3.2 接口变更」节非空，则该 feature-slug 下至少一张票在 DoD 或「人工确认项」含 `ARCHITECTURE` 字样，或接口表「来源」列已引用该 tech-spec。**祖父条款**：早于 ARCHITECTURE.md 建档日（文件头 `baseline` 注释）的历史 spec 只 INFO 提示待补录，不 FAIL |

MOD-1 / SPEC-1 对 GLOSSARY 与 ARCHITECTURE 的表解析只取节内**第一张表**（说明性附表不混入口径）；删除线包裹的行（`~~已退役模块~~`）不参与对账。
| GRAPH-1 | 图谱记录保鲜与关系完整性 | `docs/architecture/*.json` 按 `architecture-graph.schema.json`；稳定 ID 唯一；源路径/符号有效；current 记录绑定验证提交与源哈希；验证提交之后源文件变化时报 stale；关系目标存在，supersedes 无环。`needs_verification` 不进入现行查询。|
| GRAPH-2 | 唯一写入源 | 有图谱分片时，`ARCHITECTURE.md` 不得含模块、接口或约束旧表；无图谱分片的旧项目继续兼容旧表读取。|

## 初始化盘点（`--init`）

无图谱分片的旧项目可生成兼容草稿；已有分片的项目使用图谱工具生成首页：

```bash
python3 ../shared/scripts/contract_check.py --init --repo <项目根>
```

旧项目的 `--init` 仅生成兼容迁移草稿，已有文件时拒绝覆盖。图谱项目用 `architecture_graph.py --homepage-out docs/ARCHITECTURE.md` 生成确定性短首页，详细架构事实只写分片。

## 巡检输出纪律

- 报告只列发现（FIND 行）与通过项计数，不改任何文件；发现由文档阶段裁决后按各节写权限回写。
- 模块表的瞬时差（文档目录先行、契约后补）不报 FAIL，避免堵正常在途需求；接口表与代码不一致（CODE-1/2）一律报 FAIL——登记过的契约必须是代码事实。
