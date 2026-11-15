# Evidence：<issue-id>

> **本文件由 `verify_issue.py` 从结构化运行记录确定性生成，不得手工编写或修改通过结论。**
> 唯一机器证据是 `raw/<generation>/verification-implementer.json` 与 `raw/<generation>/verification-supervisor.json` 及其引用的原始日志、完整 HEAD/tree、退出码和 SHA256。正文只保留结论与指针，超过 `artifact-budget.evidence-max-lines` 时流程核对失败。

## 候选与执行身份

| 项 | 实现会话 | 主会话复跑 |
| --- | --- | --- |
| verification 文件 | `verification-implementer.json` | `verification-supervisor.json` |
| 完整 source HEAD | `<由 runner 写入>` | `<由 runner 写入>` |
| source tree | `<由 runner 写入>` | `<由 runner 写入>` |
| runner / 宿主回执 | `<实际执行身份>` | `<实际执行身份>` |
| artifact generation | `<代际 ID>` | `<代际 ID>` |

## TDD 见红记录

每个新增测试列出：测试名、首次独立执行命令、非零退出码、预期行为差异的断言失败输出、日志路径与 SHA256。语法 / 导入 / 运行错误不算见红；首次通过或没有真实记录的新测试不得进入交付。

## DoD 验收命令执行记录

逐条对应冻结 ticket context 的 DoD，不得增删、替换或只引用 Agent 自述。

### <DoD-ID>

- 命令：`<context 中的原始命令>`
- 工作目录：`<票 worktree>`
- 期望 / 实际退出码：`<期望>` / `<实际>`
- 期望输出匹配：`<实际匹配结果>`
- 起止时间 / 耗时：`<runner 记录>`
- 关键输出：`<可判定部分>`
- 原始日志与 SHA256：`<artifact generation 中的路径与哈希>`

## fast 质量门记录

- 命令：`<quality-gate.fast>`，期望 / 实际退出码与耗时
- 原始日志与 SHA256：`<路径与哈希>`
- quarantine：清单版本、显式 skip 数及每项 owner / 去向 / 首录日期；未执行项不得列入
- 清单外失败：单项 branch / base 同命令对照、签名与归因；新增或未归因失败必须令本次运行失败
- load-flake（如有）：超时单项、load-peak、串行重跑结果
- env skip：用例、缺失依赖及分类清单来源；不算通过也不算失败

> 本票不执行关联选择集或完整套件。分支 / main 晋级由 `delivery.py promote` 在线核对 CI run 与候选 tree，票级 evidence 不代表 CI full 已通过。

## 回归有效性（仅 `type: bug`）

由 `verify_issue.py bug-regression` 生成三段真实记录：

1. 修复后回归测试变绿；
2. 临时 revert 生产修复、保留回归测试后必须断言见红；
3. 恢复修复后重跑必须变绿。

每段记录命令、完整候选、退出码、关键输出、日志与 SHA256。revert 后不变红或恢复失败，均不能完成。

## 人工确认项（non-gating）

性能 / 展示类结果只陈述命令、原始输出与参考值，不进入功能通过分母，也不由 AI 代替人判定体验或性能是否可接受。

## 产物代际

引用 `artifact-generation-manifest.json`：当前 generation、candidate tree、raw / OCR / 截图路径与哈希。当前、最后成功及被引用证据始终保留；只有已失效且完成引用切换的代际才能进入 `close-and-clean` 的 GC 预检。
