# ch-agent-dev-skills

<video src="ch-agent-dev-skills-intro.mp4" controls muted playsinline width="100%"></video>

> 🎬 一分钟流程视频；打不开就[点此观看](ch-agent-dev-skills-intro.mp4)。

实施思路参考 [gstack](https://github.com/garrytan/gstack)；开发阶段 skill 设计参考 [mattpocock/skills](https://github.com/mattpocock/skills)（MIT）。

> **上手请读 [使用指南.md](使用指南.md)**：环境配置 + 13 个 skill 逐一说明。本 README 讲设计思路。

## 13 个 skill 总览

三个阶段加三个横切 skill，一条流水线从「值不值得做」走到「发布后沉淀」：

| 阶段 | skill | 定位 |
| --- | --- | --- |
| 产品判断 | `/ceo-office` | CEO 视角判断方向值不值得做 |
| 产品规划 | `/pd-plan` | 拷打式澄清，产出 feature brief |
| 小变更入口 | `/issue` | 已上线功能的小范围变更（change-request） |
| 需求文档 | `/prd` | 把确认的规划产物写成正式 PRD |
| 交接审查 | `/pd-review` | 审查补齐 PRD，给出可交付结论 |
| 开发初始化 | `/setup-dev` | 每仓库一次：agents-config 与评审规则种子 |
| 技术方案 | `/tech-spec` | PRD / change-request → 技术方案，预定 TDD 接缝 |
| 拆票 | `/issue-split` | 方案 → tracer-bullet 票：阻塞 DAG + 可判定 DoD + verify-tier |
| 实现 | `/implement` | 一票一 worktree：TDD、落盘证据、draft MR |
| 独立评审 | `/ai-review` | open-code-review 驱动（delegate / local / ci），只评不改 |
| 原型出口 | `/prototype` | 不可拷打问题做一次性原型，verdict 回填方案 |
| 复盘沉淀 | `/review` | 发布后读全量过程产物，复盘并沉淀两级经验 |
| 会话交接 | `/handoff` | 当前会话压缩成自包含交接提示词，不落盘 |

正式产物按 `./docs/features/<feature-module>/<feature-slug>/` 归档，同一需求沿用同一 `feature-slug`。

## 仓库结构与同步

```
skills/
├── <skill-name>/      每个 skill 一个目录：SKILL.md.tmpl 是源码，SKILL.md 是生成产物
├── shared/fragments/  多个 skill 共享的纪律与流程片段（构建时内联进 SKILL.md）
├── shared/templates/  输出格式模板（agent 运行时按指针读取）
└── scripts/           构建脚本（gen-skill-docs.ts 等）
sync.sh / sync.ps1     重新生成全部 SKILL.md，平铺分发到 Claude Code / ZCode / Kimi Code / Codex
tools/run-sync.cjs     跨平台调用入口
```

改了 `SKILL.md.tmpl` 或 `shared/` 后跑 `bash ./sync.sh` 即完成重建与分发；直接安装使用见 [使用指南.md](使用指南.md) 2.5 节。

## 这套 skills 在解决什么问题

它把产品工作流分成 5 个角色：

- `/ceo-office`: 先看项目方向值不值得做
- `/pd-plan`: 再把需求问题、用户价值和范围收敛清楚
- `/issue`: 对已存在、通常已上线功能的小范围需求更新
- `/prd`: 再把已经明确的方案写成正式 PRD
- `/pd-review`: 最后检查这份 PRD 是否真的能交给设计、研发、测试继续往下做

## 开发阶段：从 PRD 到 Merge Request

`/pd-review` 可交付后接 5 个 dev skill，产物从 `./docs/` 延伸到 `./dev/`；小变更例外：`/issue` 的 change-request 跳过 `/prd`、`/pd-review`，直接作为 `/tech-spec` 输入。评审引擎为 [open-code-review](https://github.com/alibaba/open-code-review)（ocr），MR 走 GitLab。执行与评审默认都在当前 agent 内完成、派发宿主无关：主会话用宿主的「新开独立对话」能力自动逐票派发 `/implement`（默认低一档经济模型）并监督到逐票合并；评审默认 `delegate`——新开独立对话评审，无需 LLM 端点与 CI：

```
/prd → /pd-review → /setup-dev（每仓库一次：agents-config + rule.json 种子；CI 种子仅 ci 模式落盘）
                  → /tech-spec   PRD / change-request（小变更）→ 技术方案（约束/假设分栏，预定 TDD 接缝）
                  → /issue-split 方案 → tracer-bullet 票（阻塞边 DAG + 可自动判定 DoD
                                      + 每票 verify-tier：light/scoped/full，集成票强制 full）
                                      → 门②：人审任务清单（含档位）→ 票 confirmed
                  → 主会话派发   逐票由宿主 agent 新开独立对话跑 /implement（低一档经济模型）：
                                      worktree 隔离 + TDD（日常只跑 fast 质量门，每轮漂移检查）
                                      → 证据落盘（按票 verify-tier 出档位门证据）→ draft MR
                                      → /ai-review 独立评审（默认 delegate：ocr 筛文件 +
                                        规则解析，新开独立对话评审；可选 local / ci 接 LLM 端点）
                                      → BLOCKER 回修 → MR ready
                  → 主会话收口   核对落盘产物后合并 MR、票置 done、推进下一张——
                                      派发后不停手，监督到逐票合并完成；无需人审，
                                      只有 needs-human / 阻塞 / 无法裁决时才停下找人
```

- `/setup-dev`: 每仓库一次。探查 ocr / glab / GitLab CI 就绪度，收集 tracker（gitlab 首选 / local fallback）、主分支、质量门三档命令与 verify-policy（fast 日常 / scoped 关联 / full 全量；档位阈值、升档触发器、selection 机制）、红线、仓库等级、ocr 模式（默认 delegate），写 `./dev/agents-config.md`，落 `.opencodereview/rule.json` 种子（`ci/ocr-review.gitlab-ci.yml` 仅 ci 模式落盘）
- `/tech-spec`: 把可交付 PRD 或 change-request（`/issue` 小变更路径）变成技术方案；约束与假设分栏；旧实现清理属方案范围（替代即删、不留兼容残留，版本回溯靠 Git）；预定 TDD 接缝
- `/issue-split`: 拆垂直切片票（tracer bullet，纵向打通、独立可演示、声明阻塞边），DoD 写成「命令 + 期望输出」；按 verify-policy 给每票定 verify-tier 与 declared-scope（DAG 终点集成票强制 full）；门②人确认清单（含档位）后发布，随后主会话按「派发与监督协议」自动按 DAG 逐票派发、监督到合并
- `/implement`: 一张票一个 worktree；通常由主会话以宿主新开独立对话派发（低一档经济模型）；TDD；替代即删除（被替代旧实现同票清理，不留兼容残留，版本回溯靠 Git）；自修复循环有轮次上限（默认 99）；成功只认落盘证据（evidence + commit hash），不信自报；收尾建 draft MR 触发独立评审并回修 BLOCKER；评审通过后**主会话直接合并**（被派发对话只到 MR ready）
- `/ai-review`: 驱动 / 解析 open-code-review 结果——delegate（默认）：ocr 筛文件 / 解析规则、宿主 agent 内新开独立对话评审；local：worktree 内跑 `ocr review --background-file <票文件>`；ci：取 CI artifacts 与 MR discussions。只评不改；critical/high + 红线命中 + 旧实现该删没删（无 tech-spec 约束依据）= BLOCKER

GitLab CI 接入要点（可选，仅 `ocr.mode: ci` 需要，详见 `skills/shared/templates/gitlab-ci-ocr.yml`）：MR push 触发、同 MR 串行（resource_group）、结果内联回贴 MR discussions、artifacts 留 `.ocr/ocr-result.json`；必需 CI 变量 `OCR_LLM_URL` / `OCR_LLM_AUTH_TOKEN`（掩码）/ `OCR_LLM_MODEL`，可选 `GITLAB_API_TOKEN`（api scope）。

关键纪律：写查分离（评审必须独立实例，实现会话不得自评）；执行段唯一人工门是门②（票清单确认），之后派发、实现、评审、合并全自动，仅 `needs-human` / 阻塞 / 无法裁决时找人；质量门日常只跑 fast，交付验证按票 verify-tier 分档、只升不降，不变式（红线 / DoD / 独立评审 / 每 feature 至少一次 full）不缩水；A 类（最高风险）仓库不进 `/implement`。宿主没有「新开独立对话」能力时按兜底执行：主会话产出自包含提示词请人在新对话启动，评审独立性不降级。

## 发布之后：复盘与经验沉淀

`/review` 是全链末端：feature 的票全部 done、验收并确认发布后，把它在 `./docs/` 与 `./dev/` 两棵树里的全部过程产物读成一份复盘——

- **过程全景**：阶段时间线（每段时长与依据文件）、每票的开发轮次（自修复 / 评审回修 / needs-human）、全量问题清单
- **需求侧关键点**：从产品经理视角，需求文档阶段应加深思考的点（低分维度、爆雷的假设、返工次数），每条挂证据
- **开发侧关键点**：导致重复修改的坑，按根因分类（需求不清 / 方案缺口 / 实现疏忽 / 环境工具）

经验分两级沉淀：项目级追加到 `./docs/EXPERIENCE.md`（与仓库技术栈 / 业务强相关），通用级追加到 `~/.pd-workflow/general-experience.md`（跨项目的流程方法类）；相似条目自动合并计数，项目级条目复现 ≥2 个 feature 后晋升通用级。统计只认落盘文件，取不到标「缺失」，不信自报。

## 全程横切：原型出口与会话交接

- `/prototype`：拷打判定某题「不可拷打」后的一次性原型出口（详见哲学第 3 节）；verdict 落盘到 `./dev/features/<feature-module>/<feature-slug>/prototypes/`，已裁决的决策性片段是 `/tech-spec` 唯一允许内联的代码。
- `/handoff`：会话交接——把当前会话压缩成自包含提示词，输出在对话里供复制到新会话（不落盘）；提示词按路径引用既有产物（`./docs/`、`./dev/`、commits、issues）并指名下一会话应调用的 skill，显式调用时带上下一会话的目的。


## 这套方法背后的哲学

### 1. CEO 视角：先判断方向，再讨论功能

`/ceo-office` 对应的不是“老板拍脑袋”，而是一种更克制的项目判断方式。

它会优先看这些问题：

- 这件事到底在服务谁
- 用户有没有真实而高频的需求
- 我们准备提供的方案，是否真的匹配这个需求
- 这件事的资源代价、机会成本和阶段优先级是什么
- 相比现有替代方案，我们到底有什么独特价值


示例：

```text
请用 /ceo-office 帮我判断：我想做一个给中小团队用的 AI 版销售复盘工具，这件事当前阶段值不值得做？
```


### 2. 产品设计环节：先定义问题，再定义方案

`/pd-plan` 和 `/prd` 的设计原则很明确：

- 先把问题讲清楚，再把功能写清楚
- 先把边界写清楚，再把细节补完整
- 先减少协作摩擦，再追求文档好看
- 最终输出尽可能对技术友好


当 `feature brief` 已经确定，你要把它写成正式产品需求文档时，用 `/prd`。

示例：

```text
请基于当前项目里已经确认的 feature brief，用 /prd 产出正式 PRD。
```

当 PRD 已经写完，你想确认它是否能交给设计、研发、测试继续落地时，用 `/pd-review`。

示例：

```text
请用 /pd-review 审查当前 PRD，直接补齐不足的地方，并给出可交付结论。
```

不要一上来就跳到 `/prd`。如果前面的判断没做清楚，PRD 只会把混乱写得更完整。

### 3. 三个横切机制：术语表、拷打与原型出口

- **GLOSSARY（术语与数据口径）**：所有 skill 共用项目级 `./docs/GLOSSARY.md`，统一专业术语和指标口径。文档用词必须与之一致；讨论中确定的新术语会立即写入，跨文档、跨会话保持一致。被明确淘汰的说法记入术语的「拒绝词」列，命中时回显标准词并说明已拒绝，防止词汇反复回潮。
- **Grilling（轮次制拷打）**：`/pd-plan`、`/issue`、`/ceo-office` 在澄清阶段按「决策树 + frontier 轮次」反复拷打需求——一轮问完当前可问的独立问题，逐项覆盖需求意义、范围边界、异常、权限、数据口径、验收标准等 11 个维度，全部有归属并经你确认后才产出正式文档，减少遗漏。
- **Prototype（不可拷打问题的出口）**：有一类问题靠对话拷问不出答案——「这个交互应该是什么感觉」「这套状态机在尴尬路径下走得通吗」。拷打判定某题不可拷打后挂 `待确认（需原型）`，不阻塞其余问题；调 `/prototype` 做一次性原型（LOGIC：纯逻辑核 + 非开发者可点的单文件 HTML，guided walkthrough 覆盖快乐路径 / 尴尬边界 / 应非法操作；UI：3 个结构迥异变体），人上手裁决后 verdict 落盘到 `./dev/features/<模块>/<slug>/prototypes/`，拷打以一行答案继续。裁决产出的状态机 / schema 等**决策性片段**是 tech-spec 唯一允许内联的代码（模板 3.5 节，标注来源）。

如果不是新功能，而是对一个已存在、通常已上线的功能做局部优化，比如入口调整、规则修订、文案更新、阈值变化或轻量流程修补，用 `/issue`。

示例：

```text
请用 /issue 帮我整理“邀请成员”功能的优化需求：现在很多用户找不到入口，我们准备把入口前置到团队首页，并补一段引导文案。
```
