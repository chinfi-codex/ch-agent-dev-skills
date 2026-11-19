# 契约巡检规则（v3，ARCH-CHECK 模型）

> 配套脚本：`shared/scripts/contract_check.py`；架构规则：`shared/scripts/arch_check.py` 与 `shared/references/arch-check.md`。
> 原则：**只报告、不改文件**；唯一写入例外是 `--init` 初始化 `docs/ARCH-CHECK.json` / `docs/ARCHITECTURE.md`。

## 术语与模型

- **`docs/ARCH-CHECK.json` = 唯一架构机器真源**：11 节架构总览 + 分层、组、依赖方向、禁用调用、参数 / 类行数、注释、重复度。
- **`docs/ARCHITECTURE.md` = 确定性生成的架构总览**：1–10 节为项目架构，11 节指向 GLOSSARY，附录 A–E 为规则；由 `arch_check.py render write` 生成，`render check` 字节比对，禁止手改。
- **`docs/GLOSSARY.md`**：feature-module 归属登记单源；不再有 `modules.json`。
- **票级不做架构检查**；arch-check 在 feature candidate 的 `freeze / check` 强制，pre-push 只做提前预警。
- **旧 `docs/architecture/*.json`** 不再读取；迁移后删除。

## 检查项（`--check`，0 通过 / 1 有发现 / 2 输入错误）

| ID | 检查 | 确定性判定 |
| --- | --- | --- |
| MOD-1 | 模块登记 | GLOSSARY「feature-module 归档」首张表：模块名非空且不重复；缺表只 INFO |
| SPEC-1 | 取代链 | tech-spec frontmatter `supersedes` 目标存在、不自指、链无环 |
| GUIDE-1 | 总览唯一生成源 | `docs/ARCH-CHECK.json` 存在时校验 schema / 规则语义，并 `render check` 字节比对 `docs/ARCHITECTURE.md`；缺规则只 INFO |

MOD-1 只取节内第一张表；删除线退役行不参与对账。

## 与 ARCH-CHECK 的分工

- `contract_check.py --check` 是复盘 / 显式诊断的文档一致性巡检，不替代 `arch_check.py check` 的代码结构检查。
- `/review` 同时运行两者：`arch_check.py check` 报 error / warn，`contract_check.py --check` 报 MOD-1 / SPEC-1 / GUIDE-1。
- 规则语义、豁免与误报处理见 `arch-check.md`；事实更正由责任阶段更新规则并 `render write`。

## 初始化

```bash
python3 ../shared/scripts/contract_check.py --init --repo <项目根>
```

等价于 `arch_check.py init`：创建 `docs/ARCH-CHECK.json` 空规则与 `docs/ARCHITECTURE.md` 的 11 节骨架；已有规则或未托管的同名文档时拒绝覆盖。

## 巡检输出纪律

- 报告列出 FAIL / INFO 及计数，不改任何业务文件。
- 架构规则事实错漏交由责任阶段更新 `docs/ARCH-CHECK.json`；语义争议进入 human-decision。
- 票级 `process_check`、`delivery.start`、`ticket_context.check_plan` 均不调用本脚本的架构检查。
