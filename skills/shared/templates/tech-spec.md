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

<!-- 本节非空 ⇒ /issue-split 会把「更新 ARCHITECTURE.md 接口表」生成承接票的契约 DoD（可自动判定：git diff 命中接口条目）。
     tech-spec 定稿时不直接回写接口表——接口在代码合并后才成为现行态，由承接票在 worktree 分支内落现行态。
     本节写的废弃项若取代旧 tech-spec 的接口，frontmatter supersedes 填被整体取代的旧 spec 文件名；部分取代在下方逐条写清废弃对象。 -->

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

唯一真源是同目录 `acceptance-plan.yaml`，按 `shared/templates/acceptance-plan.yaml` 编写。PRD 的每个端到端 AC 必须逐条承接，计划同时声明：

- `executor`：`command | browser-agent | human`——谁执行操作、采集结果；
- `oracle`：`auto | auto+human-review | human`——谁作最终验收判断；
- 前置状态、操作步骤、业务期望、自动断言、证据要求；需要人工时填写固定类型的 `human_reason` 与人工判断标准。

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
