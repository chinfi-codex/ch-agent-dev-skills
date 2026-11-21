---
doc_type: tech-spec
feature_module: <feature-module>
feature_slug: <feature-slug>
campaign_id: <campaign-id>
feature_summary: <feature-summary>
version: YYYY-MM-DD
status: 草稿        # 草稿 | 已确认（待拆解）—— /issue-split 以 status = 已确认（待拆解） 为开工门禁
supersedes:          # 可空：本 tech-spec 整体取代的旧 tech-spec 文件名（取代链，机器可读；部分取代不填，在 3.2 节写清废弃项）
upstream:                  # 标准路径：定稿 PRD + pd-review-report；小变更路径：change-request（删 pd-review-report 行）
  prd: <PRD 文件名>                # 小变更路径改写 change-request 文件名
  pd-review-report: <pd-review-report 文件名>
---

# Tech Spec：<feature_name>

## 1. 目标与范围

一段话说清本次技术方案覆盖什么、不覆盖什么（对齐 PRD 范围边界，不自行扩大）。

## 2. 现状盘点（事实，自查所得）

现有模块、接口、数据流中与本次相关的部分。每条给出代码位置（文件 / 符号）。只写查证过的事实。

### 2.1 How / Why 调查与证据边界

轻量调查直接在此记录事实、引用、约束与未知；下表按需使用，不强制生成独立报告或成对填写 How/Why。

| 类型 | 内联证据或报告 / 相关版本 | 覆盖与主要结论 | 未决项 |
| --- | --- | --- | --- |
| How | `research/<topic>-how-YYYY-MM-DD.md` / `<SHA>` | 组件、流程、边界、共享状态 | Open Questions |
| Why | `research/<topic>-why-YYYY-MM-DD.md` / `<目标SHA>` | Preserve / Change / Avoid / Risk 与置信度 | Sources gap / Unknown |

说明调查范围与关键缺口；历史原因不影响本次决策时无需单独 Why。归档 How 记录相关文件/符号和未提交差异，复用只补查相关变化。Why 的 Direct / Supported 经当前适用性核验才形成确定约束，Inferred 进入假设，Speculative / Unknown 进入风险。

## 3. 方案设计

### 3.1 模块拆解

先按模块说清职责与边界，再以**文档树**列出本次实施对应的全部代码模块与文件，逐文件标注动作（`[新增]` / `[修改]` / `[删除]`）和一句话计划变动逻辑（改哪些符号、什么行为变化）。树是 `/issue-split` 拆票与票级 `declared-scope` 冻结的直接输入：方案范围外文件不得入树；被替代的旧实现及其失效引用以 `[删除]` 入树；架构规则 / 派生物变更（如 `docs/ARCH-CHECK.json`）同样入树并注明承接方式。

```text
<repo>/
  services/order/                      # 订单模块：本次改动主场
    order_service.py      [修改] close_order() 增加幂等键校验，回调重试不再重复关单
    idempotency.py        [新增] 幂等键生成与校验工具
  tests/order/
    test_order_close.py   [修改] 新增幂等重试场景 2 条
  docs/ARCH-CHECK.json   [修改] 新增幂等规则；由承接票更新规则并 render write
  docs/ARCHITECTURE.md   [修改] 由 render write 确定性重生成；禁止手改
```

### 3.2 架构影响（总览 / 层 / 组 / 规则）

<!-- 说明本需求对架构契约的影响：11 节总览中哪些节需要补全或更新，以及新增 / 修改 / 退役哪些层、组、依赖方向或代码规则。
     契约唯一机器真源是 docs/ARCH-CHECK.json；docs/ARCHITECTURE.md 只能由 arch_check.py render write 生成。
     首次出现架构影响时，必须补全 architecture.overview 的 1–10 节（没有该类对象在 overview.notes.<节名> 写“不适用”及理由），由本 skill 写入并 render write；
     overview.structure 必须按真实目录展开到 2–3 层子目录与关键文件，只列顶层目录视为未完成，补全时用目录清单核对；
     层 / 组 / 规则变更仍由恰好一张承接票更新并重新 render，该票 declared-scope 必须包含这两个文件。
     没有架构影响时保留表头并写“无”；不写稳定 ID 或 architecture-updates。 -->

| 总览节 | 动作 | 依据 | 承接方式 |
| --- | --- | --- | --- |
| 例：1–10 全部 | 首次补全 | How 报告 + 目录 / 依赖扫描 | 本 skill 写入 architecture.overview 并 render write |
| 例：3 核心组件 | 更新 | 本次新增 ingest 组件 | 本 skill 更新对应节并 render write |

| 层 / 组 | 动作 | 路径 / 规则 ID | 说明 | 验证方式 |
| --- | --- | --- | --- | --- |
| 例：app/services | new | grp-service / model-no-upward | 新增业务层组，禁止 model 反向依赖 | arch_check.py check + 承接票声明 scope |

### 3.3 数据与存储
### 3.4 关键流程（主路径 + 失败路径）
### 3.5 决策性片段（可选）

仅放比散文更精确表达决策的状态机 / reducer / schema / 最小调用 / type shape，裁剪到契约部分，不写业务实现。逐片标注来源：原型路径 + verdict 日期，或本 spec 3.7 的架构决策。无决策性片段时本节留空。

### 3.6 承重决策与候选综合

每个承重决策使用一个稳定 DEC-ID；无承重决策时明确写“无实质替代，理由：……”。

| DEC-ID | 问题与统一准则 | 结构不同候选 | Base + 现有复用锚点 | Graft / 拒绝项及理由 | 证伪触发 |
| --- | --- | --- | --- | --- | --- |
| DEC-01 | 决策、How/Why 证据、3–6 条准则 | A / B（必要时 C）及 judge / 降级披露 | 选中方案及 `path:symbol` 或 ARCH-CHECK 组 / 规则 | 吸收优点、拒绝方案与代价 | 哪些实现证据会触发 redesign |

候选与淘汰理由属于本 dated spec，不写成 current 架构事实。

### 3.7 公共接口 Usage 与调用点迁移

新增 / 修改 / 退役公共或跨模块接口时逐项填写；纯内部变更可写不适用及理由。

| 接口 / DEC-ID | 真实消费者与最小用法 | 输入 / 输出 / error / async / ownership | 现存调用点 → keep / migrate / delete → 覆盖测试 | 未穷举范围与探测方法 |
| --- | --- | --- | --- | --- |

最小调用和公共类型 shape 可在 3.5 引用；动态调用、反射和外部消费者无法穷举时必须显式说明，不得声称全量覆盖。

## 4. 约束与假设（分栏，不得混写）

| 类型 | 条目 | 依据 / 随运行反馈调整的触发条件 |
|---|---|---|
| 约束 | 例：数据不出域；协议向后兼容；判读 yaml 结构不变 | 依据来源（PRD / GLOSSARY / 仓库等级） |
| 假设 | 例：某解析库性能足够；某模块无并发写入 | 写明「若假设被证伪，方案如何调整」 |

## 5. 测试策略（TDD 接缝）

预先约定哪些接缝先写测试（需求级接口测试优先）：接缝位置、测试形态（单测 / 集成 / 脚本级）、验收命令草案。

### 5.1 功能验收计划（需求收口用）

唯一真源是同目录 `acceptance-plan.yaml`（`agent-dev.feature-acceptance/v2`），按 `shared/templates/acceptance-plan.yaml` 编写。标准路径必须逐条承接 PRD 的全部端到端 AC；小变更路径必须覆盖 change-request 的全部 `AC-Dxx` 与 `baseline_ac.retain`，replace / retire 不得混入。`documents` 用 path + SHA256 绑定真实需求文档，`case.source` 用 `{document, anchor}` 且与 case key 一致。每条 AC 写成可独立执行的完整测试 case，计划同时声明：

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
