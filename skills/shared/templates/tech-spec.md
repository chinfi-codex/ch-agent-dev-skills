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

### 5.1 验收判据（AC oracle，需求收口用）

每条需求级验收点标判据归属：Agent 证明「按规格跑通」，人判断「是不是要的」。`feature-closeout.mode: local-human` 时，收口按本表生成 `integration/business-results.json` 与人工清单（建议人工条目 ≤ 10）。

| AC | 行为点 | oracle | 自动执行入口 / 人工步骤 | 期望 |
|---|---|---|---|---|
| AC-1 | 例：三态判读结果写入报告 | auto | `python3 tools/tests/<e2e>.py --evidence-dir <dir>` | 退出 0，三态计数与金样一致 |
| AC-2 | 例：新图例在报告里看得清 | auto+human-review | 自动：DOM 断言图例存在；人：看版式 | 人确认可读 |
| AC-3 | 例：真实工作簿判读结论合理 | human | 在测试实例跑 <代表性工作簿> | 领域结论与人判断一致 |

`oracle` 取值：`auto`（有确定性判据：退出码 / 断言 / 数值 / DOM）· `auto+human-review`（自动断功能，人再看呈现或领域合理性）· `human`（意图满足度、交互手感、展示样式、性能可接受度、真实数据结论合理性）。

## 6. 影响面与风险

受影响的模块 / 协议 / 其他 feature；风险与对应缓解。

## 7. 待确认项

拷打后仍开放的 `待确认` 条目，逐条写明影响与默认走向。
