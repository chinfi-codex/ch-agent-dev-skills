# ch-pd-workflow

实施思路参考借鉴：https://github.com/garrytan/gstack
开发阶段（tech-spec → 拆票 → 实现 → 评审 → MR）的 skill 设计参考借鉴：https://github.com/mattpocock/skills（MIT）

> **上手请读 [使用指南.md](使用指南.md)**：工具与环境配置（GLM 端点 / glab / ocr / GitLab CI）+ 11 个 skill 的逐一使用说明。本 README 讲设计思路。

## 这套 skills 在解决什么问题

它把产品工作流分成 5 个角色：

- `/ceo-office`: 先看项目方向值不值得做
- `/pd-plan`: 再把需求问题、用户价值和范围收敛清楚
- `/issue`: 对已存在、通常已上线功能的小范围需求更新
- `/prd`: 再把已经明确的方案写成正式 PRD
- `/pd-review`: 最后检查这份 PRD 是否真的能交给设计、研发、测试继续往下做

## 开发阶段：从 PRD 到 Merge Request

`/pd-review` 可交付之后，接 5 个 dev skill，产物从 `./docs/` 树延伸到 `./dev/` 树。评审引擎为 [open-code-review](https://github.com/alibaba/open-code-review)（ocr，阿里开源 Apache-2.0），MR 走 GitLab（内网已确认）；**执行与评审默认都在当前 agent 内完成**：issue 生成后由主会话新开独立子代理会话逐票派发（默认用低一档的经济模型），评审默认 ocr `delegate` 模式（ocr 只筛文件 / 解析规则，独立子代理评审，无需 LLM 端点与 CI）：

```
/prd → /pd-review → /setup-dev（每仓库一次：agents-config + rule.json 种子；CI 种子仅 ci 模式落盘）
                  → /tech-spec   PRD → 技术方案（约束/假设分栏，预定 TDD 接缝）
                  → /issue-split 方案 → tracer-bullet 票（阻塞边 DAG + 可自动判定 DoD）
                                      → 门②：人审任务清单 → 票 confirmed
                  → 主会话派发   逐票新开独立子代理会话跑 /implement（低一档经济模型）：
                                      worktree 隔离 + TDD（日常只跑 fast 质量门）
                                      → 证据落盘（交付前跑 full 质量门）→ draft MR
                                      → /ai-review 独立评审（默认 delegate：ocr 筛文件 +
                                        规则解析，新开子代理评审；可选 local / ci 接 GLM 端点）
                                      → BLOCKER 回修 → MR ready
                  → 主会话收口   评审通过即合并 MR、票置 done、推进下一张——
                                      无需人审；只有 needs-human / 阻塞 / 无法裁决时才停下找人
```

- `/setup-dev`: 每仓库一次。探查 ocr / glab / GitLab CI 就绪度，收集 tracker（gitlab 首选 / local fallback）、主分支、质量门命令（fast 日常档 / full 交付档）、红线、仓库等级、ocr 模式（默认 delegate），写 `./dev/agents-config.md`，落 `.opencodereview/rule.json` 种子（`ci/ocr-review.gitlab-ci.yml` 仅 ci 模式落盘）
- `/tech-spec`: 把可交付 PRD 变成技术方案；约束与假设分栏；预定 TDD 接缝
- `/issue-split`: 拆垂直切片票（tracer bullet，纵向打通、独立可演示、声明阻塞边），DoD 写成「命令 + 期望输出」；门②人确认清单后发布，随后主会话自动按 DAG 逐票派发执行
- `/implement`: 一张票一个 worktree；通常由主会话以新开子代理会话派发（低一档经济模型）；TDD；自修复循环有轮次上限；成功只认落盘证据（evidence + commit hash），不信自报；收尾建 draft MR 触发独立评审并回修 BLOCKER；评审通过后**主会话直接合并**（子代理只到 MR ready）
- `/ai-review`: 驱动 / 解析 open-code-review 结果——delegate（默认）：ocr 筛文件 / 解析规则、当前 agent 内新开子代理评审；local：worktree 内跑 `ocr review --background-file <票文件>`；ci：取 CI artifacts 与 MR discussions。只评不改；critical/high + 红线命中 = BLOCKER

GitLab CI 接入要点（可选增强，仅 `ocr.mode: ci` 需要；详见 `shared/templates/gitlab-ci-ocr.yml` 与 `shared/templates/agents-config.md`）：MR push 触发、同 MR 串行（resource_group）、结果内联回贴 MR discussions、artifacts 留 `.ocr/ocr-result.json`；必需 CI 变量 `OCR_LLM_URL` / `OCR_LLM_AUTH_TOKEN`（掩码）/ `OCR_LLM_MODEL`，可选 `GITLAB_API_TOKEN`（api scope）；GLM 端点走 bigmodel.cn（境内，满足数据不出境）。

关键纪律：写查分离（评审必须独立实例——delegate / local 由新开子代理执行，ci 天然独立，实现会话不得自评）；执行段唯一人工门是门②（票清单确认），之后派发、实现、评审、合并、推进全自动，仅 `needs-human` / 阻塞 / 无法裁决时找人；质量门日常只跑 fast，full 档留在交付前；A 类仓库（飞行软件 / 涉密）不进 `/implement`。

## 发布之后：复盘与经验沉淀

`/review` 是全链末端的一环：一个 feature 的票全部 done、通过验收并确认发布后，把这个 feature-slug 在 `./docs/` 与 `./dev/` 两棵树里留下的全部过程产物读成一份复盘——

- **过程全景**：阶段时间线（每段时长与依据文件）、每票的开发轮次（自修复 / 评审回修 / needs-human）、全量问题清单
- **需求侧关键点**：从产品经理视角，需求文档阶段应加深思考的点（低分维度、爆雷的假设、返工次数），每条挂证据
- **开发侧关键点**：导致重复修改的坑，按根因分类（需求不清 / 方案缺口 / 实现疏忽 / 环境工具）

经验分两级沉淀：项目级追加到 `./docs/EXPERIENCE.md`（与仓库技术栈 / 业务强相关），通用级追加到 `~/.pd-workflow/general-experience.md`（跨项目的流程方法类）；相似条目自动合并计数，项目级条目复现 ≥2 个 feature 后晋升通用级。统计只认落盘文件，取不到标「缺失」，不信自报。


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

### 3. 两个横切机制：术语表与拷打

- **GLOSSARY（术语与数据口径）**：所有 skill 共用项目级 `./docs/GLOSSARY.md`，统一专业术语和指标口径。文档用词必须与之一致；讨论中确定的新术语会立即写入，跨文档、跨会话保持一致。
- **Grilling（轮次制拷打）**：`/pd-plan`、`/issue`、`/ceo-office` 在澄清阶段按「决策树 + frontier 轮次」反复拷打需求——一轮问完当前可问的独立问题，逐项覆盖需求意义、范围边界、异常、权限、数据口径、验收标准等 11 个维度，全部有归属并经你确认后才产出正式文档，减少遗漏。

如果不是新功能，而是对一个已存在、通常已上线的功能做局部优化，比如入口调整、规则修订、文案更新、阈值变化或轻量流程修补，用 `/issue`。

示例：

```text
请用 /issue 帮我整理“邀请成员”功能的优化需求：现在很多用户找不到入口，我们准备把入口前置到团队首页，并补一段引导文案。
```
