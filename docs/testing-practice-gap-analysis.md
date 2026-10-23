# 高星 skill 包测试实践对照：差距分析与落地清单

> 2026-10-19。调研对象：obra/superpowers（~300k★）、pstack（cursor/plugins 官方包及 backnotprop 镜像）、wshobson/agents、VoltAgent/awesome-claude-code-subagents、github/spec-kit、contains-studio/agents、davila7/claude-code-templates、anthropics/skills。
> 对照对象：本仓库现行机制（verify-tier v2、known-failures 归因、once-at-final、ai-review、主模型符合性门、evidence 模板）。

## 0. 外部实践的两个流派

- **纪律铁律派（superpowers）**：TDD 是无条件铁律（"NO PRODUCTION CODE WITHOUT A FAILING TEST FIRST"，先写代码即删码重来）；RED/GREEN 验证节点是强制门禁；每条纪律带「借口→反击」表和红旗清单，条目全部来自真实违规记录；技能本身用 TDD 方法论开发（压力场景 → 采集借口 → 写反制条款 → 5+ 次复跑）。
- **证据驱动派（pstack）**：TDD 只是条件式工具（"Prefer no new test over a bad test"），真正的完成标准是真实运行证据；验证方式必须匹配变更表面（CLI 跑真实命令、UI 走真实流程）；跑不了的检查报 `inconclusive`；每个 PR 由未写代码的 verifier 独立重跑给 verdict（"Green CI and an approving bot review are not verdicts"）。
- 两派唯一的交集也是最硬的一条：**任何完成声明必须附当轮刚跑出的证据；agent 自述、绿 CI、bot 评审都不算数。**

## 1. 现状对照矩阵

| 共性模式 | 本仓库现状 | 判定 |
|---|---|---|
| 证据先于声明 | evidence.md 要求逐条实跑 DoD 命令记退出码，「禁止只引用 agent 自述」，`all-passed` 硬门 | ✅ 已对齐 |
| 亲眼见红（Verify RED） | implement Step 3 只有「先写失败测试再实现到绿」的顺序要求，无见红验证节点；bug 票无 revert 验证 | ❌ 缺位 |
| 禁止为绿灯削测试 | tests/** 红线 + 需求驱动更新三前提可溯 + ai-review 复核映射 | ✅ 已对齐，且比多数仓库精细 |
| 自审不算验收 | ai-review 独立评审 + process_check.py 机械核对 evidence；评审不重跑测试（成本取舍，与 superpowers 一致） | ✅ 已对齐（集成票可加强，见 3.7） |
| 验证范围 ≠ 交付范围 | 漂移检查 + 两级升档触发器（只升不降）+ 集成票/每日回归兜底 | ✅ 已对齐，且确定性定档规则优于所有受访仓库 |
| 覆盖率数字淡化 | 无覆盖率门槛 | ✅ 已对齐 |
| inconclusive 第三态 | env skip 清单 + non-gating（perf/display）两类特例，无通用「跑了但证明不了」状态 | ⚠️ 部分 |
| 开工基线验证 | worktree 建立后不跑基线；既有失败靠 known-failures 清单在验证时归因（事后） | ⚠️ 部分 |
| 反合理化工程 | 规则正确但无「借口→反击」结构；压力下（赶时间/沉没成本）靠条款自觉 | ❌ 缺位 |
| 技能自身被测 | sync.sh 分发四宿主，无「agent 读后是否真的遵守」的行为验证环节 | ❌ 缺位 |

本仓库独有、受访仓库都没有的优势，保持即可：确定性定档规则（文件数阈值+触发器，外部全靠自觉）、known-failures 归因清单、once-at-final + 槽位并发控制（外部无成本意识）、gate-scope 边界（比 VoltAgent 的硬指标务实）。

## 2. 差距详析与落地清单

按优先级排序。每条给出：差距本质 → 具体改动点（文件级）。

### 2.1【P1】亲眼见红：Verify RED 门禁

> **已落地（2026-10-19）**：implement Step 3 加见红验证（须断言失败、指向预期行为差异、立即通过即重写）；impl-log「做了什么」加见红证据登记；process_check.py 加 `tdd-red` REVIEW 告警（观察后转 FAIL）。

**差距**：「先写失败测试」只约束顺序，不约束验证。superpowers 的 Verify RED 是 MANDATORY 节点：必须确认测试 fail（不是 error）、失败原因指向预期行为差异；测试立即通过 = 在测已有行为，重写。pstack 同样要求「改生产代码前先运行确认因预期原因失败」。没有这一节点，「先写测试」可以形式上满足而测试从未见过红——这是 TDD 最大的自欺源。

**落地**：
- `skills/implement/SKILL.md` Step 3 加一条见红验证：新测试实现前先单独运行一次，确认 ① 退出码非零且是断言失败而非语法/导入错误；② 失败输出指向预期行为差异；立即通过的测试必须重写。
- `skills/shared/templates/impl-log.md` 的 TDD 登记行加「见红证据」列（测试名 + 失败输出截取指针）。
- `skills/shared/scripts/process_check.py` 可加静态检查：登记的 TDD 测试文件须存在对应见红记录（低成本，先可选告警，观察后转 FAIL）。

### 2.2【P1】bug 修复票的 revert 闭环

> **已落地（2026-10-19）**：票 frontmatter 加 `type` 字段（issue-split 拆票时判定，bug 票 DoD 必含复现测试命令）；implement Step 5 加回归有效性流程（revert 修复 → 见红 → 恢复 → 绿）；evidence 模板加「回归有效性」节；process_check.py 对 `type: bug` 票缺该节判 FAIL。

**差距**：superpowers 的回归测试流程是 "Write → Run (pass) → Revert fix → Run (MUST FAIL) → Restore → Run (pass)"——必须 revert 修复看测试变红，才算回归测试有效。当前 bug 票没有这一闭环，无法区分「测试真的复现了 bug」和「测试碰巧绿了」。

**落地**：票模板或 DoD 约定：bug 修复类票（可由票类型字段标识）evidence 加一节「回归有效性」：revert 修复 → 重跑（须红，记退出码）→ 恢复 → 重跑（须绿）。每票一次，成本极低。

### 2.3【P1】技能自身的压力测试与借口采集

**差距**：头部两家（superpowers writing-skills、pstack eval playbook）都在验证「技能改动是否真的改善 agent 行为」；中腰部仓库都不做——这是分水岭。本仓库技能分发四宿主，改纪律条款目前靠人工推演效果。

**落地（轻量版，不建 eval 仓库）**：
- 建 `docs/skill-pressure-scenarios.md`：给核心纪律条款（tests/** 红线、升档只升不降、禁止修改验收命令、needs-human 停等）各写 1-2 个压力场景（赶时间/沉没成本/权威施压/经济理由——本仓库真实事故素材现成：心跳跑光额度、周末误删、81M→814M）。
- 每次改动这些条款时，在 1-2 个宿主上跑对照（不带改动 vs 带改动），记录 agent 是否违规及逐字借口。
- 借口原文回填进技能的反合理化表（见 2.4），形成闭环：场景 → 借口 → 条款补丁 → 复跑。

### 2.4【P2】反合理化表机制

**差距**：superpowers 每条纪律带「借口→反击」对照表（如 "Too simple to test" → "Simple code breaks. Test takes 30 seconds."），条目全部来自 baseline 测试中 agent 的真实借口，并配红旗清单（出现 "just this once" 类措辞即触发删码重来）。纯规则文本在压力下容易被说服绕过。

**落地**：给 implement / ai-review 的纪律条款各加 3-5 条借口表，**条目必须来自自己的真实违规记录**（impl-log、评审报告、事故复盘里 agent 说过的话），不照抄 superpowers。从 2.3 的压力场景测试中持续回填。

### 2.5【P2】inconclusive 第三态泛化

**差距**：现有 env skip / non-gating 覆盖「依赖缺失」「性能/展示」两类，但没有通用「无法在匹配表面验证」的状态。pstack 要求：验证方式必须匹配变更表面（UI 改动走真实应用流程、存储改动读实际写入值），跑不了的报 inconclusive，不得自信宣称完成。

**落地**：evidence.md「人工确认项」节泛化为「未决项（inconclusive）」：凡 DoD 条目无法在匹配表面验证（UI 交互、外部服务行为等），列此节并说明替代验证手段；不进 `all-passed` 分母（语义不变），但收口报告须逐条对人说明。

### 2.6【P2】开工基线验证

**差距**：known-failures 是验证时的事后归因；开工不跑基线意味着「开工后、验证前被本票弄坏」的窗口靠清单比对兜底，滞后且可能混淆归因。superpowers 开工门禁："A dirty baseline makes every later failure ambiguous. Run the tests now."

**落地**：implement Step 1 worktree 建立后跑一次 fast 档（秒级）记 impl-log 首行；基线红 → 先报告再开工（是否带病前进是人的决定）。与 known-failures 互补：基线管开工时刻，清单管验证时刻。

### 2.7【P3】集成票的独立复验

**差距**：pstack shipping 门禁要求未写代码的 verifier 按用户方式重跑给独立 verdict，比 superpowers（评审不重跑测试）更严。当前 ai-review 只判代码 + 机械核对 evidence，证据全由实现方自证。

**落地（折衷，控制成本）**：仅集成票（feature 收尾、本来就走 full 档）评审时，由评审方抽查 1 条 DoD 命令实际重跑，结果记入评审报告；其余票维持现状。关键节点有一次独立复验，成本约一条命令。

### 2.8【P3】纪律条款改动的行为证据要求

**差距**：superpowers PR 模板要求：改行为塑形内容（禁令、红旗表）必须附对抗压力测试证据——"These are not prose — they are code."

**落地**：本仓库约定（写进 README 或 CONTRIBUTING 级文档）：sync.sh 分发前，若 diff 触及纪律条款（红线/门禁/禁令类语句），须附 2.3 场景清单中对应场景的验证记录（至少 1 个宿主触发过对应场景并观察到预期行为）。

## 3. 不建议引入的

- **覆盖率硬指标**（VoltAgent >90%）：与本仓库风险驱动路线冲突，superpowers 也把「只为覆盖率存在的测试」列为反模式。
- **无条件 TDD 铁律**（superpowers 全文版）：本仓库票粒度小、有大量协议文件/模板类票，pstack 的条件式更匹配；2.1 只补见红验证，不补铁律。
- **真实 LLM 行为测试进 CI**：superpowers 自己也只敢把静态门禁放 CI，行为测试在独立仓库 nightly 跑；本仓库同理，2.3 用人工触发即可。
- **评审者全面重跑测试**（pstack shipping 全文版）：成本与 once-at-final 设计冲突，只取集成票抽查（2.7）。

## 4. 补：ECC（affaan-m/ECC，~287k★）与回归测试专项

> 2026-10-23 追加。ECC = agent harness 优化系统（71 agents / 311 skills，npm 包 + 插件分发），总循环 `plan → test → implement → review → verify → remember → improve`。

### 4.1 ECC 的回归机制（七个受访项目里最重）

- **bug 级**：`ai-regression-testing` skill——"write tests for bugs that were found, not for code that works"；`test-collaboration` 强制每张 bug 修复创建/链接 TEST-ID 或显式人工出口，"Record before-fix failure and after-fix success"、"Code-only fixes are incomplete"。
- **模块级**：`module-regression` 的 REGRESSION.md 账本——每模块记下游消费者 + 可执行验收命令；"Verdict = exit code. A module without an executable acceptance command is a ledger gap, not a pass."；无法机械确认的边标 `unverified` 且**阻塞全绿结论**直到人确认。
- **套件级**：CI 每 PR 全量矩阵（多 OS × Node + pm lane）+ 80% 覆盖率硬门 + 发布 exact-SHA 关卡；**无 nightly**。套件运行时治理精细（33 路矩阵裁成两类 lane、flaky 平台串行、lane 测试清单动态派生防漂移）。
- **张力**：ECC 内部有两个声音——copilot-instructions 要 80% 覆盖，regression skill 说 "Don't aim for 100% coverage — aim for regression prevention"。

### 4.2 回归必要性判断（跨项目结论）

| 层级 | 各项目一致性 | 必要性 |
|---|---|---|
| bug 级闭环（先复现测试、修复验证、留证据） | superpowers / pstack / ECC 完全一致 | **必要**，零争议；本仓库已落地（04e60c3） |
| 改动面级（账本 / 闭包 / 选择集） | 三家各有一套：ECC 账本、superpowers 全套件、本仓库反向闭包 | **必要**，但实现应与依赖分析能力匹配 |
| 套件级全量 | 三个项目三个答案：ECC 每 PR 全量（npm 产品仓）、superpowers 不进 CI 只 nightly（技能文档仓）、pstack 不跑全量靠真实表面验证（插件仓） | **= f(分发形态 × 出错代价 × 单次成本)**，无普世答案 |

对本仓库的映射：

- 每票全量**不必要**——本仓库 320 份 evidence 实测 97% 空转（req-full-regression-frequency.md），与 pstack/superpowers 的缺席互相印证；C+D+F 分层正确。
- ECC 账本验证了 R2 quarantine 方向：known-failures 升级隔离清单 ≈ REGRESSION.md 的"last green commit + 退出码即裁决"。
- 一处可更严：模块隔离认定「闭包不完整」现状是标记录案、由每日回归兜底；ECC 对 unverified 边是**阻塞全绿直到人确认**。隔离认定争议处理可参考（代价是偶尔阻塞）。
- 若哪天把 skills 打包成分发产品（npm/plugin 市场），套件级标准才需向 ECC 模式（每 PR 全量 + 发布关卡）迁移。

