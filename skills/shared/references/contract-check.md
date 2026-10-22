# 架构契约巡检规则（contract_check）

> 配套脚本：`shared/scripts/contract_check.py`（相对各 SKILL.md 为 `../shared/scripts/contract_check.py`）。
> 原则（对齐 spec-kit analyze）：**只报告、不改文件**；唯一例外是 `--init` 初始化盘点生成基线草稿。
> v1 为报告模式：由人或在 `/review` 复盘时运行，输出漂移清单；误报率稳定前不进 `process_check.py` 门禁。

## 术语与模型

- **tech-spec = flow-forward**：日期快照，不覆盖旧文件；取代关系由 frontmatter `supersedes: <旧 tech-spec 文件名>` 显式声明，机器可查。
- **ARCHITECTURE.md = living**：`./docs/ARCHITECTURE.md` 是「现行态」唯一单源，原地更新。写权限分节：模块表由 `/pd-plan` 登记时直写；约束表由 `/tech-spec` 定稿时直写（仅跨 feature 级）；**接口表只随 feature 分支更新**（接口在代码合并后才成为现行态）。
- **flow-back**：实现期 diff 实际改动了接口表已登记的接口（按其「代码位置」列判定），而票未声明契约 DoD → 同分支补记接口表并在 impl-log「红线接触记录」登记映射，评审按契约 diff 复核。

## 检查项（`--check`，退出码 0 通过 / 1 有发现 / 2 输入错误）

| ID | 检查 | 判定（确定性规则） |
| --- | --- | --- |
| MOD-1 | 模块四方一致 | GLOSSARY「feature-module 归档」↔ `docs/features/` 一层目录 ↔ `dev/features/` 一层目录 ↔ ARCHITECTURE.md「模块现行态」表，四处的模块集合应一致；`docs/features/` 与 `dev/features/` 互相允许对方先行的瞬时差（只提示，不 FAIL） |
| SPEC-1 | supersedes 链完整 | 每份 tech-spec 的 `supersedes`（若有）指向的文件存在；链无环 |
| SPEC-2 | 契约义务已派发 | `status: 已确认（待拆解）` 的 tech-spec 若「3.2 接口变更」节非空，则该 feature-slug 下至少一张票在 DoD 或「人工确认项」含 `ARCHITECTURE` 字样，或接口表「来源」列已引用该 tech-spec。**祖父条款**：早于 ARCHITECTURE.md 建档日（文件头 `baseline` 注释）的历史 spec 只 INFO 提示待补录，不 FAIL |

MOD-1 / SPEC-1 对 GLOSSARY 与 ARCHITECTURE 的表解析只取节内**第一张表**（说明性附表不混入口径）；删除线包裹的行（`~~已退役模块~~`）不参与对账。
| CODE-1 | 接口登记↔代码 | 接口表每行「代码位置（文件:符号）」：文件存在，符号名可在文件内检索到（子串级，语言无关）。**多分支口径**：第 6 列「分支」为空的行在当前检出核实；填分支名的行用 `git show <分支>:<文件>` 在该分支核实——多现行分支项目的架构差异逐条标注即可机器核对；非 git 仓库的分支行只 INFO 不 FAIL |
| CODE-2 | 模块登记↔代码 | 模块表每行「代码位置」路径存在 |

## 初始化盘点（`--init`）

新接入项目先生成基线草稿，**人工确认后才算生效**：

```bash
python3 ../shared/scripts/contract_check.py --init --repo <项目根>
```

脚本读取 GLOSSARY「feature-module 归档」+ `docs/features/`、`dev/features/` 目录结构，生成 `docs/ARCHITECTURE.md` 骨架：模块表预填（代码位置留待核对），接口表与约束表留空，由人工 / agent 按代码现状补全并逐条标注「文件:符号」证据。已存在 `docs/ARCHITECTURE.md` 时拒绝覆盖。

## 巡检输出纪律

- 报告只列发现（FIND 行）与通过项计数，不改任何文件；发现由文档阶段裁决后按各节写权限回写。
- 模块表的瞬时差（文档目录先行、契约后补）不报 FAIL，避免堵正常在途需求；接口表与代码不一致（CODE-1/2）一律报 FAIL——登记过的契约必须是代码事实。
