# PRD Skill 产出质量分析：对照 GitHub 优质 PRD 项目

> 调研日期：2026-09-16。
> 分析对象：`skills/product-workflow/prd/SKILL.md.tmpl`（生成 SKILL.md）+ `shared/templates/prd.md`（产出模板）+ `shared/templates/feature-brief.md`（上游契约）。
> 说明：当前没有找到该 skill 的真实产出样本，本报告分析的是"skill 指令 + 模板结构"所决定的**产出质量上限**——即按此 skill 严格执行，产出物会天然具备什么、天然缺什么。
> 基准来源（均已抓取原文核实，URL 附于各处）：
> - [github/spec-kit](https://github.com/github/spec-kit)（GitHub 官方 Spec-Driven Development 工具包，`templates/spec-template.md`）
> - [snarktank/ai-dev-tasks](https://github.com/snarktank/ai-dev-tasks)（面向 AI 编程代理的 PRD→任务流，`create-prd.md`）
> - [bmad-code-org/BMAD-METHOD](https://github.com/bmad-code-org/BMAD-METHOD)（`skills/bmad-prd/assets/prd-template.md` + `prd-validation-checklist.md`）
> - [Atlassian PRD 模板](https://www.atlassian.com/software/confluence/templates/product-requirements-document)（业界标准参照）
> - [EARS 需求句式](https://alistairmavin.com/ears/)（Rolls-Royce 2009 年发布，Kiro/AWS spec 采用）

---

## 1. 基准项目做法速览

| 基准 | 需求条目写法 | 验收机制 | 成功度量 | 假设处理 | 优先级 |
|---|---|---|---|---|---|
| spec-kit | `FR-001` 全局编号 + **MUST** 关键词 + `[NEEDS CLARIFICATION]` 内联标记 | 每条 user story 带 Given/When/Then 验收场景 | `SC-001` 编号，要求 technology-agnostic、可度量 | 独立 Assumptions 章节 | 每个 story 标 P1/P2/P3，且要求**独立可测**（单 story 即成 MVP） |
| ai-dev-tasks | 编号功能需求，"The system must..." | 无独立验收章节，靠 Goals + Success Metrics | 独立 Success Metrics 章节 | 写作前强制 3-5 个澄清问题（编号问题 + 字母选项） | 无 |
| BMAD (bmad-prd) | `FR-N` 全局编号嵌套在 Feature 下，每条 FR 带 **Consequences (testable)** 可测试后果 | FR 的可测试后果即验收；UJ（用户旅程）全局编号 `UJ-N`，FR/SM 交叉引用 | `SM-N` 编号 + **counter-metrics**（防止优化错目标） | 内联 `[ASSUMPTION: ...]` 标签 + 文末 **Assumptions Index** 汇总 | 通过 MVP Scope（In/Out）表达，延期项标注 v2/v3 |
| Atlassian | 需求表格：需求 + user story + 重要级别 + 关联 Jira | 无逐条验收，靠 Success Metrics 表 | 目标与度量配对成表 | 独立 Assumptions 章节（用户/技术/商业三类） | 重要级别列 |
| EARS/Kiro | 五种句式：ubiquitous / WHEN（事件）/ WHILE（状态）/ IF-THEN（异常）/ WHERE（可选），"the system shall..." | 每条需求下列编号验收标准，每条都是 EARS 句 | — | — | — |

三个 GitHub 原生项目的共同取向：**需求条目化（编号）、语句规范化（MUST/SHALL/EARS）、验收可测试化（Given/When/Then 或 testable consequences）、引用稳定化（下游工件按 ID 引用条目）**。这是"AI 代理可消费"的 PRD 与传统叙述式 PRD 的核心分野。

---

## 2. 当前 skill 的优势（对照后仍成立的差异化能力）

这些不是客套，是对照基准后确认**领先或独有**的部分，优化时必须保留：

1. **GLOSSARY 术语纪律**。项目级唯一术语文件、懒创建、立即回写、别名可解释匹配、指标必须带口径（定义/统计窗口/数据来源）。五个基准中只有 BMAD v6 有同等强度的 glossary 纪律（"FRs, UJs, and SMs use Glossary terms verbatim; introducing a synonym is a discipline violation"），其余三个完全没有。
2. **完成状态协议**（已完成 / 已完成但有风险 / 阻塞 / 需补充上下文）+ 上游契约（Feature BR 待确认未闭环不得写入正式要求）。基准项目中没有等价物——它们假设输入质量，本 skill 显式校验输入质量。
3. **技术对接说明三段式**（前端侧 / 后端侧 / 数据与接口 + 兼容性要求）。spec-kit 刻意 tech-agnostic（技术方案留给 plan 阶段），ai-dev-tasks 的技术考虑是可选项。对本 workflow"研发拿到即可承接"的定位而言，这一章是正当优势，且 SKILL 已声明边界（不替研发设计实现细节）。
4. **待确认问题四分类**（产品 / 技术 / 数据接口 / 依赖），比各家单一的 Open Questions 列表更可执行。
5. **流程治理**：DOC_MODE 硬边界、归档纪律（不覆盖、日期版本化、glob 匹配读最新）、提问分级（拷打轮次 vs 阻塞型单点确认）。这是团队级工程化能力，基准项目均不具备。

---

## 3. 关键差距（按严重度排序）

### P0-1：SKILL.md 与模板不一致，"验收口径"被静默吞掉

这是本次分析发现的**最确定性的产出缺陷**，且是内部 bug，不需要外部基准就能确诊：

- SKILL.md「工作流 Step 2：建立 PRD 骨架」声明文档必须包含九个部分，其中包括**验收口径**；Step 5 整节要求"每个关键模块写清什么情况下算完成、什么结果是正确的"。
- 但 `prd.md` 模板的一级章节里**没有"验收口径"章节**。
- 同时 SKILL.md 规定"严格遵循其章节结构，**不自行增删一级章节**"。

三者叠加的后果：agent 严格按模板输出时，SKILL 里强调的验收口径要求会被模板结构静默吞掉，产出物天然没有验收标准——而验收标准恰恰是下游 `tech-spec`、`issue-split`、`pd-review` 最需要的东西。

同类型不一致还有一处：Step 3 要求每个模块写清**前置条件、结果输出**，但模板的模块结构（功能目标/用户操作/系统处理/规则说明/状态与反馈/异常与边界/权限）里**没有这两个字段**。

### P0-2：功能需求无编号、无规范关键词、无可测试后果，下游无法稳定引用

对照三个 GitHub 原生项目，这是与"AI 代理可消费"标准的最大差距：

- 当前模板的「功能需求详述」是**叙述式小节**（模块 1：功能目标/用户操作/系统处理/…），条目没有全局编号，没有 MUST/SHALL 类规范关键词，没有逐条的可测试后果。
- spec-kit：`FR-001: System MUST ...`，BMAD：`FR-1` 全局编号 + 每条 FR 的 "Consequences (testable): System returns HTTP 429 when ..."。
- 后果：下游 `tech-spec` 引用需求时只能说"见 PRD 模块 2 的规则说明第 3 条"；`issue-split` 拆票时无法做需求条目到票的映射；`pd-review` 无法逐条判定验收。引用不稳定 → 任何文档改版都会造成下游引用漂移。

### P0-3：成功度量整章缺失

所有五个基准都有显式的成功度量机制（Atlassian 目标-度量配对表、ai-dev-tasks Success Metrics、spec-kit `SC-001`、BMAD `SM-N` + counter-metrics）。当前 PRD 模板完全没有这一章。

考虑到本 workflow 的分工（商业判断在 `/ceo-office` 与 `/pd-plan`，Feature Brief 里有"完成后希望达成什么结果"），PRD 不应重做战略度量，但应当**承接** Feature BR 的目标并把它落成可验收的度量口径。目前的模板连承接的位置都没有。

### P1-4：「假设」这一中间层缺失

SKILL.md 要求"若存在假设，必须把假设写成可见条目"，但模板没有 Assumptions 章节，假设只有两个去处：混进正文叙述（不可见）或混进「待确认问题」（被当成阻塞项）。

基准的共识是假设与待确认分开：spec-kit 和 Atlassian 有独立 Assumptions 章节；BMAD 用内联 `[ASSUMPTION: ...]` 标签 + 文末 Assumptions Index 汇总，评审时可逐条裁决。假设（带默认前提继续推进）≠ 待确认（必须回答才能闭环），当前模板把两者压成一类，会人为抬高阻塞密度。

### P1-5：需求句式只有负面约束，没有正面规范

SKILL.md 有大量"不要写什么"（禁止"体验更好""后续再细化"、避免"尽量、适当、必要时"），但没有"应该怎么写"的句式规范。负面约束的执行依赖 agent 自觉，效果不稳定。

EARS 是业界最成熟的正面规范，且天然适配中文：

- 事件驱动：**当** 用户提交表单 **时**，系统 **应** 校验全部字段。
- 状态驱动：**当** 会话处于活跃状态 **时**，系统应每 5 分钟刷新令牌。
- 异常处理：**如果** 支付失败，**则** 系统应通知用户并保留订单 30 分钟。
- 可选能力：**在** 开启审核模式 **的情况下**，系统应…

### P1-6：功能模块无优先级，下游 issue-split 缺少关键输入

spec-kit 要求每个 user story 标 P1/P2/P3 且独立可测（单独交付即成 MVP）；BMAD 用 MVP Scope In/Out + 延期标注表达。当前 PRD 模板的功能模块没有优先级字段——上游 pd-plan 虽有 MVP 模式判断，但判断结果没有在 PRD 的模块级落地。`issue-split` 拆票时需要的正是模块/条目级优先级。

### P2-7：Non-Goals 粒度不足

当前模板在「背景与目标」里有一行"不在本次解决的问题"。BMAD 的做法更细，分两层：产品级 Non-Goals（我们不做 X、不变成 Y）与 MVP 级 Out of Scope（本次不做、标注延期到 v2/v3、附理由），并允许在 FR 内联 `[NON-GOAL for MVP]`。当前单层写法容易把"永远不做"和"下次再做"混在一起。

### P2-8：产出前自检缺失

BMAD 附带一份七维质量 rubric（`prd-validation-checklist.md`），维度包括：decision-readiness（决策是否被诚实呈现）、substance over theater（识别"家具式"内容—— persona theater、NFR theater、vision theater）、done-ness clarity（工程师是否知道每条 FR 怎样算完成，"Be unforgiving here"）、scope honesty、downstream usability（ID 连续性、交叉引用可解析）、strategic coherence、shape fit。

本 workflow 有独立的 `/pd-review` 下游环节，但 prd skill 自身在产出前没有任何 self-check 清单，质量问题全部后移给评审环节。低成本的做法是借鉴 rubric 精简出 5-6 条自检项，让 agent 在写完 PRD 后、输出状态结论前过一遍。

### P2-9：用户场景颗粒度可加深（非硬伤）

BMAD 的 UJ（用户旅程）写法值得借鉴：named persona + entry state（从哪来、是否已登录）+ path（3-5 个具体节拍）+ climax（价值交付的确切时刻、用户如何感知）+ resolution（结束后状态）。当前模板的"目标用户/核心场景/触发时机"四字段偏薄，但深度实际落在模块详述和用户流程里，可接受。建议在「用户流程」的主流程步骤上补"入口状态"和"结束状态"两要素即可，不必引入 persona 叙事。

---

## 4. 优化方案

按"成本从低到高、确定性从高到低"分四层。**A 层是修 bug，建议立即做**；B/C 层是结构升级；D 层是机制增强。

### A. 修复 SKILL 与模板不一致（立即，半小时）

1. 模板补一级章节 `## 验收口径`（位置在「非功能要求」之后、「待确认问题」之前），与 SKILL Step 2 骨架对齐。
2. 模板模块结构补 `#### 前置条件` 与 `#### 输入与输出` 两个字段，与 Step 3 对齐。
3. 反向同步：若认为某些骨架项应由上游承接（如"范围定义"已并入背景与目标），则改 SKILL Step 2 的骨架清单，保持两处字面一致。**SKILL 与模板必须只有一个事实源**。

### B. 模板结构升级（核心改造）

按基准共识改造 `prd.md`，建议的一级章节结构（变化处加 ★）：

```text
# PRD 需求名称
## 文档信息
## 背景与目标
## ★ 成功度量          —— 承接 Feature BR 目标，落成可度量口径，指标引用 GLOSSARY
## 术语与数据口径
## ★ 范围定义          —— 本次纳入 / 本次不纳入（标注延期去向）/ 产品级 Non-Goals
## 功能需求详述        —— 模块结构不变，但每条规则/行为升级为编号条目：
##                        FR-<模块号>-<序号>，句式按 EARS，每条带「可测试后果」
## 用户流程            —— 主流程补入口状态/结束状态；异常流程保留
## 交互与展示要求
## 技术对接说明
## 非功能要求
## ★ 验收口径          —— 按 FR 编号逐条给"完成的判定条件"，预期内异常清单
## ★ 假设              —— 带默认前提继续推进的条目，逐条编号，供评审裁决
## 待确认问题          —— 保留四分类；与"假设"分工：阻塞项才进这里
## 附录 / 参考资料
```

关键细则：

- **FR 编号**：`FR-1-1`（模块 1 第 1 条）全局唯一，下游 tech-spec / issue-split / pd-review 按 ID 引用。编号规则写进模板说明。
- **可测试后果**：借鉴 BMAD，每条 FR 下强制至少一条"可测试后果"（可验证条件或可度量结果），写不出后果的 FR 自动降级进「待确认问题」。
- **优先级**：模块级标 P0/P1/P2，与上游 pd-plan 的 MVP 模式结论对齐。
- **成功度量**：写明口径（定义/统计窗口/数据来源）并与 GLOSSARY 一致——现有术语纪律直接复用，是顺手的优势。

### C. 需求句式规范（EARS 中文化）

在 SKILL.md「输出要求」或文档写作规则 fragment 中增加正面句式规范，四种句式覆盖绝大多数需求语句：

```text
- 事件：当 <触发> 时，系统应 <响应>。
- 状态：当 <前置状态> 持续时，系统应 <行为>。
- 异常：如果 <异常情况>，则系统应 <处理>。
- 可选：在 <功能开关/版本> 的情况下，系统应 <能力>。
- 无条件能力：系统应 <能力>。（ubiquitous，少用，优先转化为前四种）
```

配套纪律保留现有负面约束（禁"尽量/适当/必要时"），形成"正面句式 + 负面清单"的双层约束。

### D. 机制增强

1. **产出前自检清单**：借鉴 BMAD rubric 精简为 6 条，写入模板尾部（或独立 `prd-self-check.md`，由 SKILL 引用），agent 输出状态结论前逐条过：
   - 每条 FR 是否有编号、是否带至少一条可测试后果？
   - 是否存在"体验更好/合理性能/优雅降级"类无边界表述？（NFR 必须有阈值）
   - 成功度量是否承接自 Feature BR 且口径与 GLOSSARY 一致？
   - 假设与待确认是否分置，待确认是否真的是阻塞项？
   - 每条 FR 能否被下游按 ID 引用（编号连续、无断号）？
   - 是否存在模板章节写了但内容为空话的小节？（宁删小节，不留家具）
2. **假设回写机制**：评审（pd-review）裁决假设后，结果回写 PRD 新版本或 GLOSSARY，形成闭环——现有"新文件 + 日期后缀"归档纪律天然支持。
3. **暂不建议引入** persona 叙事、counter-metrics、Amazon PR/FAQ：前两者与本 workflow 的上游分工重叠（战略判断在 pd-plan），后者面向的是 0→1 立项而非需求承接，引入会打破"不重复做上游工作"的现有哲学。

### E. 明确不动的东西

- GLOSSARY 纪律、完成状态协议、技术对接三段式、待确认四分类、归档纪律——对照基准后确认是差异化优势，全部保留。
- "技术友好但不越界"的边界声明（不替研发设计实现细节）保留；引入 FR 编号后，tech-spec 按 ID 承接即可，PRD 不需要加深技术深度。

---

## 5. 改造后的预期效果

| 维度 | 现状 | 改造后 |
|---|---|---|
| 验收标准 | 模板无此章，被静默吞掉 | 按 FR 逐条判定，pd-review 可逐条打勾 |
| 需求可追溯 | 叙述式小节，引用靠位置描述 | FR 全局编号，tech-spec/issue-split 按 ID 引用 |
| 成功度量 | 整章缺失 | 承接 Feature BR，口径与 GLOSSARY 一致 |
| 语句确定性 | 只有负面清单 | EARS 正面句式 + 负面清单双层约束 |
| 开放项管理 | 假设与阻塞混在一处 | 假设 / 待确认分层，评审可逐条裁决假设 |
| 质量把关 | 全部后移给 pd-review | 产出前 6 条自检 + 下游评审双层把关 |

一句话总结：**当前 skill 的流程治理和术语纪律已经超过 GitHub 主流基准，短板集中在"需求条目的工程化表达"——编号、句式、可测试后果、验收、度量。先把 SKILL 与模板的不一致修掉，再按 B 层方案升级模板，产出质量即可对齐 spec-kit/BMAD 一线水平，且保留现有差异化优势。**
