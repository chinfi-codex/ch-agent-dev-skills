## Dev 配置（agents-config）

dev 阶段 skill 开始工作前，先读取 `./dev/agents-config.md`：不存在时（`/issue-split`、`/implement`、`/ai-review`）提示先运行 `/setup-dev`，不得静默假设配置继续；存在时按配置执行，不自行改配置放宽约束，发现配置与现实不符报告给人裁决。

必要字段（结构见 `shared/templates/agents-config.md`）：

- `tracker`：`local`（markdown 票文件）或 `gitlab`（glab CLI）
- `main-branch`：主分支名（worktree 与 merge request 的基准）
- `repo-level`：A / B / C 仓库等级；A 类仓（飞行软件 / 涉密）禁止进入 `/implement`，只允许只读辅助
- `quality-gate`：只配置票级 `fast` 命令（秒级，类型 / lint / 单文件测试）。每张票在实现、自修复、评审回修与 merge / close 时都只执行自己的 DoD + `quality-gate.fast`；不得按文件数、LOC、模块影响面或风险名义改跑其他票级套件，也不设置票级 scoped / full 命令
- `host-budget`：本机重测试算力预算（`total` worker 总数 / `max-runs` 套数 / `queue-timeout` 排队秒数上限），只用于 local-human 需求收口的关联选择集与单项失败归因；fast 不受限。真源是机器级文件 `~/.cache/agent-dev/slots/budget.json`（同机各仓共享一份），agents-config 里只是镜像记录，执行以机器级文件为准；CI full 使用判读机自己的预算
- `verify-policy`：票粒度、失败归因与测试职责边界。
  - `ticket-max-files: 10` 与 `ticket-max-loc: 400` 只是拆票粒度上限：拆票时预估超任一上限必须再拆；实现期实测超任一上限必须 re-split，无法自动再拆则置 `needs-human`，不得把它解释成测试范围或模型路由的升级理由
  - 实测 diff 超出 `declared-scope` 时在 impl-log「漂移记录」逐文件登记；超粒度上限按上述 re-split / needs-human 处理，不超上限且仍属本票范围时登记后继续
  - `gate-scope`：AI 交付门只保证功能实现度（DoD 行为、契约、回归）；`non-gating` 类（默认性能基准 perf、展示样式 display）只在最终 commit 执行一次并落盘，不阻塞交付、不进 `all-passed` 分母，由人工验收确认；缺外部依赖的用例按分类清单显式 skip 并在 evidence 列出，不算失败也不算通过
  - `attribution`：先在失败默认按 `quarantine` 隔离。入册须 base 侧同签名稳定失败，且每条带 owner / 去向（修复票 / 永久删除决议）/ 首录日期，无去向不入册；清单内用例以 skip（标 quarantined）执行，不进 `all-passed` 分母，清单外失败按本票引入处理并只对单项补跑 base 侧对照。未执行项不是先在失败，不得入册冒充通过；feature 收口的预算延期只写 feature 级 deferred 证据。`two-sided` 为保守档（每条失败双侧同命令对照）。隔离清单由 CI full 报告刷新，超 `quarantine-max-age-days`（默认 14）的条目在 CI 回归记录与主会话监督输出中呈现
  - **full 只有 CI 入口**：本机、票 worktree、集成 worktree 与监督循环均不得执行 full。`ci-promote` 的 feature / branch / main 放行必须执行 `process_check.py --phase promote --ci-verdict <...> --ci-run-proof <trusted-gitea-run-proof.json>`，核对 CI verdict、run proof、实际 HEAD commit 与 tree 同源；分支前进或 tree 改变即重新验证。人工重跑也只能触发同一 CI workflow，不设本机补跑出口
  - `daily-regression` 如启用，只能由 CI schedule / 手工 dispatch 触发同一个 CI full workflow；记录 CI run / verdict / tree / 合入区间 / 归因与隔离清单变化。漏跑时只能在 CI 补触发，不得创建本机 worktree 或保存可冒充 CI 判决的本地引用。pandu 不启用 nightly，push 不自动部署
- `feature-closeout`：需求收口方式。`mode: ci-promote`（默认，缺此节同）以 feature / branch / main 的同 tree CI verdict + run proof 放行；`mode: local-human` 时全部票合入每需求独立集成分支（`integration-branch`，默认 `feat/<feature-slug>`）后，本机冻结候选与 `acceptance-plan.yaml` 计划哈希 → Agent 自动证据（`scoped_select.py` 关联选择集，indirect 预算 `verify-budget-seconds`；另按计划执行 command / browser-agent 功能 AC）→ 生成正式功能验收报告 → 按报告完成人工实测 / 复核 / 豁免 → 人显式确认 → 合 main → 按 `prod` 发布并冒烟，main push 后 CI full 作安全网而非合 main 前置。`selection-map` 是可选的 dir-map 映射表路径，缺省 / `null` 使用 `dev/scoped-map.yaml`；显式配置时给集成选择器追加 `--map <路径>`。feature 级 scoped 报告与 deferred 证据保留。`verify-instance` 有则部署到测试实例，`null` 则在集成 worktree 用开发启动器；功能验收计划的 `executor`（command / browser-agent / human）与 `oracle`（auto / auto+human-review / human）分离，合并前必须有脚本生成且未过期的 `acceptance-report.md`。main 冻结后只在 `main-drift-ignore` 路径前进时候选仍有效。门禁由 `shared/scripts/feature_closeout.py` 判定，流程见「监督循环 · 需求收口」。**票的目标分支**（worktree 基准、rebase 目标、合并目标、process_check 的 diff base、账实对账与清场的「已合并」判定）一律用 `feature_closeout.py target --issue <票文件>` 解析：票 frontmatter `target-branch` 显式指定 > local-human 下代入 `integration-branch` > `main-branch`；`since`（YYYY-MM-DD）之前创建的存量票保持 `main-branch` 默认。local-human 下票只合进集成分支，main 只接收需求收口的整需求合并；目标分支不存在即停，不退回 main
- `max-fix-rounds`：自修复轮次上限，默认 99；`review-round-alert`（默认 3）：同票评审达此轮数时主会话介入判断拆票 / 缩范围 / 升级模型（预警，不是硬停）
- `conformance`：需求符合性核对（主模型校验）——主会话在合并前核对「要的有没有、偏没偏」，与 ai-review 正交（两者都使用当前宿主 `supervisor-review`；评审判代码质量，需求语义符合性由主会话或另一独立监督评审会话核对）。方法：先独立枚举需求点（不看 impl-log 覆盖矩阵），再对照矩阵打开代码逐条核语义；结论落盘 `impl/<issue-id>/conformance-<issue-id>.md`，`process_check.py --phase close` 把关。`mode: always`（默认）每票必核；`sampled` 时所有非集成票都按 `sample-rate` 抽查，未抽中写 `verdict: skipped`，集成票一律必核；`checker: main-session`（默认亲核）或 `dispatched`（派发独立监督评审对话，上下文紧张时用）。缺此节按默认执行，即默认启用
- `base-freeze`：票开工时固定 base，实现与评审期间不追集成分支，只在合入前 rebase 一次；冲突解决后重跑受影响 DoD + fast
- `mechanical-refresh`：预授权机械刷新白名单（路径 glob + 字段 / 生成块）；diff 可机器证明只动所列字段时直接刷新并登记，不停等人；白名单外的既有测试 / 夹具改动仍按红线处理——唯一例外「需求驱动更新」：票 DoD / tech-spec 明确改变的行为，其冲突旧测试随票更新并逐条登记映射（见 /implement Step 2），不按红线停
- `artifact-budget`：impl-log / evidence 正文行数与 impl 目录体积上限；正文只留结论与指针，过程放 raw，中间轮原始产物只留最终轮
- `redlines`：红线文件 glob 清单（协议文件、密钥配置、验收判定文件等），命中即停，不得绕过
- `dispatch`：执行与模型配置。`schema-version: 1` + `executor: subagent`；`hosts.<claude-code|codex|zcode|dsh>` 按宿主分条。每条必须有：`supervisor-review.model / effort`（主会话监督、符合性核对与独立 `/ai-review` 共用角色模型，但评审另开独立会话）；`development.default / escalated.model / effort`（独立 `/implement`：普通票用 default，集成票 / 高风险票及评审预警后的回修用 escalated，绝不按测试范围路由）。开工前运行 `python3 ../shared/scripts/dispatch_config.py validate --config ./dev/agents-config.md --catalog ./dev/dispatch-model-catalog.json`；用 `resolve` 取当前宿主原生参数，缺当前宿主、旧 `dispatch.model`、字段缺失或模型 / effort 不在实时目录即停，不跨宿主套用、不静默继承。原生角色文件由 `render` 从本配置生成，不手工另建第二份模型真源。`parallel-max`：frontier 中 declared-scope 互不相交的票可并行派发的上限
- `ocr.mode`：评审模式。默认 `delegate`——ocr 只做文件筛选与规则解析（不调 LLM），评审由宿主 agent 内新开的独立对话执行；`local` / `ci` 为可选增强，需配置 LLM 端点


## 测试位置

- **票级循环内验证在本机**：TDD 见红 / 变绿、每条 DoD、fast、单项失败归因、独立评审复测与主会话验收复跑都在本票 worktree 执行；merge / close 仍只核 DoD + fast。fast 不经整机槽位包装，也不新增把票级测试提交到其他机器执行的通道
- **local-human 需求级验证在本机**：需求收口的 `scoped_select.py` 关联选择集与其单项失败归因在集成 worktree 经 `shared/scripts/test_slots.py run` 包装执行，预算与 feature 级 deferred 证据按 `feature-closeout` 配置处理；`acceptance-plan.yaml` 声明的 command / browser-agent 功能 AC 也在同一候选测试环境执行并落标准证据。它们不是票级交付门，也不得扩大为 full；宿主无浏览器能力时对应 AC 记 unverified，不伪造通过
- **远端只承接 CI full**：已登记 feature / branch / main 的 push，以及同一 CI workflow 的定时 / 手工触发，是完整套件的唯一入口。CI 在判读机独立 checkout 对应提交并自行调用判读机预算封装；本机、票内、监督循环都不向其提交命令、也不补跑 full。CI 不可用时保持未验证，不降级本机补跑
