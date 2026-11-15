---
doc_type: tech-spec
feature_module: <feature-module>
feature_slug: <feature-slug>
feature_summary: <feature-summary>
version: YYYY-MM-DD
status: 草稿        # 草稿 | 已确认（待拆解）—— /issue-split 以 status = 已确认（待拆解） 为开工门禁
supersedes:          # 可空：本 tech-spec 整体取代的旧 tech-spec 文件名（flow-forward 取代链，机器可读；部分取代不填，在 3.2 节写清废弃项）
upstream:                  # 标准路径：定稿 PRD + pd-review-report；小变更路径：change-request（删 pd-review-report 行）
  prd: <PRD 文件名>                # 小变更路径改写 change-request 文件名
  pd-review-report: <pd-review-report 文件名>
---

# Tech Spec：<feature_name>

## 1. 目标与范围

一段话说清本次技术方案覆盖什么、不覆盖什么（对齐 PRD 范围边界，不自行扩大）。

## 2. 现状盘点（事实，自查所得）

现有模块、接口、数据流中与本次相关的部分。每条给出代码位置（文件 / 符号）。只写查证过的事实。

## 3. 方案设计

### 3.1 模块拆解
### 3.2 接口变更（新增 / 修改 / 废弃）

<!-- 每个变更点必须有稳定 ID，并由 /issue-split 逐 ID 写入承接票 frontmatter `architecture-updates`。
     架构事实只写 `docs/architecture/*.json` 分片；代码与证据生效后由承接票更新分片并重新生成只读首页。
     动作固定为 new / modify / retire；无变更时保留下方表头，不写占位记录。
     本节写的废弃项若取代旧 tech-spec 的接口，frontmatter supersedes 填被整体取代的旧 spec 文件名；部分取代在对应行填写被替代的稳定 ID。 -->

| 稳定 ID | 动作 | 名称 | 模块 | 源代码位置 | supersedes | 验证证据 |
| --- | --- | --- | --- | --- | --- | --- |

### 3.3 数据与存储
### 3.4 关键流程（主路径 + 失败路径）
### 3.5 决策性片段（可选，来自原型 verdict）

仅当 `./dev/features/<feature-module>/<feature-slug>/prototypes/` 的 verdict 含有已裁决的决策性片段时填写：状态机 / reducer / schema / type shape 等比散文更精确编码决策的片段，裁剪到决策相关部分，逐片标注来源（原型路径 + verdict 日期）。无原型 verdict 时本节留空。

## 4. 约束与假设（分栏，不得混写）

| 类型 | 条目 | 依据 / 随运行反馈调整的触发条件 |
|---|---|---|
| 约束 | 例：数据不出域；协议向后兼容；判读 yaml 结构不变 | 依据来源（PRD / GLOSSARY / 仓库等级） |
| 假设 | 例：某解析库性能足够；某模块无并发写入 | 写明「若假设被证伪，方案如何调整」 |

## 5. 测试策略（TDD 接缝）

预先约定哪些接缝先写测试（需求级接口测试优先）：接缝位置、测试形态（单测 / 集成 / 脚本级）、验收命令草案。

### 5.1 功能验收计划（需求收口用）

唯一真源是同目录 `acceptance-plan.yaml`，按 `shared/templates/acceptance-plan.yaml` 编写。PRD 的每个端到端 AC 必须逐条承接，并写成可独立执行的完整测试 case，计划同时声明：

- `executor`：`command | browser-agent | human`——谁执行操作、采集结果；
- `oracle`：`auto | auto+human-review | human`——谁作最终验收判断；
- 前置条件中的测试环境、初始状态与测试数据，以及「操作 + 输入 + 本步预期」步骤、最终量化 `expected` 和证据要求；
- `assertions` / `human_expect` 分别作为自动 / 人工验收标准，写明观察对象、比较符或精确目标值，并在适用时写明数值与单位、样本量 / 统计窗口、数据来源；需要人工时另填固定类型的 `human_reason`。

所有阈值必须来自 PRD、change request 或已确认决策，缺失时进入「待确认项」，不得编造。每条 required case 的全部验收标准均满足且证据齐备才通过；未执行或证据不足只能记 `unverified`。

本表只是从 YAML 同步的可读摘要，不得单独编辑成分工真源：

| AC | 行为点 | 执行方式 | 最终判定 | 人工原因 |
|---|---|---|---|---|
| AC-01 | 例：三态判读结果写入报告 | command | auto | — |
| AC-02 | 例：新图例功能正确且版式可读 | browser-agent | auto+human-review | visual-ux |
| AC-03 | 例：真实工作簿判读结论合理 | human | human | domain-judgment |

分类原则：能安全执行且有来自需求的客观断言 → 自动；功能可自动判断但呈现、领域合理性或风险后果需人把关 → AI 执行、人工复核；没有可靠机器判据或无法安全执行 → 人工。浏览器截图不能单独构成通过证据。`local-human` 冻结候选时同时冻结计划哈希，收口按计划逐条对账并生成正式验收报告。

## 6. 影响面与风险

受影响的模块 / 协议 / 其他 feature；风险与对应缓解。

## 7. 待确认项

拷打后仍开放的 `待确认` 条目，逐条写明影响与默认走向。
