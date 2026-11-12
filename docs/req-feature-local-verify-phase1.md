# 需求集成验证一期：独立集成分支 + 本机自动证据 + 人工测试确认合入 + 本机 Prod 发布（pandu agent 仓）

- 状态：实施版（r3，2026-11-12 增加功能验收计划、浏览器 Agent 证据与合并前正式报告；**整需求 PR 路线已放弃**）
- 日期：2026-11-06 初稿；2026-11-09 修订 r2；2026-11-12 修订 r3
- 适用仓：`~/Desktop/yufeng-pandu-agent`（pandu agent）；流程与脚本落在 skills 仓（`skills/shared/`），pandu 只做配置
- 路线关系：本文是需求收口的唯一实施设计；不做整需求 PR、PR 触发 CI、commit status 回写或 PR 证据包。main push 后 CI full 仍是套件级安全网，不是需求 PR 门禁
- 事实核对基线：pandu `main@7c79622`，2026-11-09 只读核查

## 0. r3 落地要点

1. PRD 的端到端场景使用稳定 AC 编号；技术方案同目录产出唯一真源 `acceptance-plan.yaml`。
2. 每条 AC 用 `executor`（command / browser-agent / human）与 `oracle`（auto / auto+human-review / human）分开描述“谁执行”和“谁最终判断”。
3. 冻结候选时同时冻结计划哈希与快照；结果逐条对账，漏项、额外、重复、旧计划结果均不得合并。
4. 浏览器通过必须有实际操作、DOM / 网络等行为断言、console/page error 和有效截图；截图或 Agent 自述不能单独算通过。
5. `feature_closeout.py report` 在自动阶段后与人工确认后各生成一次正式 `acceptance-report.md`，明确人工原因、实际结果和合并建议；旧 `summary` / `human-checklist.md` 路径取消。
6. 整需求 PR 路线放弃，不保留二期实施前提。

## 0.1 r2 修订要点（相对初稿）

| # | 初稿问题 | r2 处置 |
|---|---|---|
| 1 | 合 main 前置条件（同 tree CI full + 真库对照）被悄悄挪到合并后，却写成「升级」 | 明确**修订** `agents-config.md`「集成分支并回 main」条：合 main 前置 = 本机自动证据 + 人工测试确认；CI full 改为合并后套件级兜底，并补回滚流程（§7.4） |
| 2 | 共用集成分支合 main 会带入其他需求未确认的票 | `local-human` 收口**强制每需求独立集成分支** `feat/<feature-slug>`；共用分支上的需求只能整分支一起收口（§2） |
| 3 | 证据写进集成 worktree 会改动被冻结的 tree / dev 软链与入库 dev 冲突 | 收口证据**只写主工作区** `dev/`，**永不提交到集成分支**；冻结 tree 只认集成分支提交（§6.2） |
| 4 | 冻结后 main 前进，合并结果 tree ≠ 验证过的 tree | 冻结记录 `main_head`；合并前 main 只允许在 `dev/**`、`docs/**` 等非行为路径上前进，触及代码即重新同步冻结（§4、§7.3） |
| 5 | 高频多需求并行、人工测试优先、本机 Prod 测试 | 自动证据各 worktree 并行；人工测试是唯一串行点；人工测试实例按租约轮转；合 main 后发布本机 Prod 做冒烟与抽查（§5–§7） |
| 6 | 自动与人工分工不清 | tech-spec 阶段给每条 AC 标 `oracle: auto / human / auto+human-review`，收口按此生成人工清单（§3） |
| 7 | 机械门禁靠人工核字段 | 新增共享脚本 `feature_closeout.py`（freeze / check / summary），三个相位机器把关（§8） |

---

## 1. 目标与范围

```text
main
 └─ feat/<feature-slug> 集成分支（.worktrees/integration-<feature-slug>/，每需求独立）
      ├─ issue-001 … issue-N：评审通过 → merge --no-ff 进集成分支（现行监督循环）
      │
      ├─ ① 收口前提：全票 done + 每票 conformance + feature 级覆盖核对
      ├─ ② 同步 main → 冻结候选（feature_closeout.py freeze）
      ├─ ③ Agent 自动证据（本机，后台）：fast + 需求 direct + 关联 indirect + command / 浏览器功能 AC
      │      └─ 生成首次功能验收报告 → check --phase verify ── 自动阶段材料齐全才叫人
      ├─ ④ 人工按报告实测 / 复核：AI 判据不足可升级为人工，未执行不得写通过
      │      └─ 发现问题 → 回修 → 新候选 → 增量自动证据 → 只复测受影响清单项
      ├─ ⑤ 人显式确认（对话内原话落盘）→ 生成最终验收报告 ── check --phase merge
      ├─ ⑥ 主工作区 main：git merge --no-ff feat/<feature-slug>
      ├─ ⑦ 发布本机 Prod（mac/prod sync）→ Agent Prod 冒烟 + 人抽查
      └─ ⑧ 人指示 push → 判读机 CI full 套件级兜底
```

不做：整需求 PR、PR 触发 CI、commit status 回写、`ci_supervisor.proof()` 改造、`test-full.yaml` 触发集改动与 PR 证据包；该路线已于 2026-11-12 放弃。票级验证固定为 DoD + fast（2026-11-11 用户裁定），不跑票级 scoped / full。

与现行约束的关系：

- 本机关联验证 ≠ full：跑的是按需求 diff 换算的选择集；票级 evidence 只记录 DoD + fast，不含 scoped / full 档位或 full 延期字段；需求级 evidence 写 `full-deferred: ci`，本机不跑完整套件。
- `agents-config.md`「验证范围收紧」（票内只 DoD + fast）不受影响：收口验证是需求级动作，不在任何票内执行。
- `agents-config.md`「集成分支并回 main」条被本方案**修订**：`feature-closeout.mode: local-human` 的需求，合 main 前置条件改为 §7 的人工确认门；同 tree CI full 判决改为合并后兜底。未启用 `local-human` 的分支仍按原条执行。
- 合 main 永远是人工动作：无人值守模式下也停在确认点，`unattended.md` 写明也不得越过。

---

## 2. 集成分支（每需求独立）

| 项 | 规则 |
|---|---|
| 命名 | `feat/<feature-slug>`；worktree `.worktrees/integration-<feature-slug>/`。用 `feat/` 前缀是为了与 pandu 部署通道规则对齐（`codex/*` 被 test 通道拒绝） |
| 建立 | 门②通过后、首次派发前，`git worktree add .worktrees/integration-<feature-slug> -b feat/<feature-slug> main`（显式 start-point） |
| 独立性 | `local-human` 收口要求一个集成分支只承载一个需求。现存共用分支（如 `feat/A1规则配置数据链`）上的需求不能单独走本流程合 main：要么整分支所有需求一起收口，要么按原条执行 |
| 登记 | 写入 `dev/agents-config.md`「特性集成分支」表 |
| dev/ | pandu 的 `dev/` 被 git 跟踪，集成 worktree 内的 `dev/` 是随 checkout 来的实体目录；收口产物一律写主工作区 `dev/` 的绝对路径，不写 worktree 内 `dev/`、不提交到集成分支 |
| 归属 | 集成分支与 worktree 不属于任何票；清场不得删除，合 main 后按清场序列处置 |
| 冲突 | 票分支合入冲突：票 worktree 内 rebase 集成分支、重跑 fast 与冲突文件闭包测试后再合；仍无法解决 → needs-human |

高频并行下的并发模型：

- 每个需求的票开发、自动证据各在自己的 worktree 并行，重测试经 `test_slots.py run` 共享整机槽位；
- 人工测试是唯一串行点（同一时刻人只测一个需求），人工测试实例按租约排队；
- 合 main 串行（主工作区单 checkout），合并后 Prod 可批量 sync。

---

## 3. 功能验收计划：执行与判定分离（技术方案阶段就定）

原则：**Agent 证明「按规格跑通」，人判断「是不是我要的」并承担风险**。PRD 的端到端场景使用稳定 AC 编号；技术方案同目录产出 `acceptance-plan.yaml` 作为唯一分工真源，§5.1 只放可读摘要。

每条 AC 同时有两个维度：

| 字段 | 取值 | 含义 |
|---|---|---|
| `executor` | `command / browser-agent / human` | 谁执行操作、采集结果；浏览器 Agent 是一种执行通道，不代表最终验收权 |
| `oracle` | `auto / auto+human-review / human` | 谁作最终判断；混合类由 AI 断功能、人复核呈现 / 领域合理性 / 风险后果 |

AI 分类必须可解释：

1. 预期来自已确认需求，能安全执行、稳定观察并写客观断言 → `auto`；需要 UI 操作时优先 `browser-agent`。
2. 功能可自动断言，但意图、展示 / 手感、真实数据领域结论或高风险后果需要人最终把关 → `auto+human-review`，AI 先执行并采集证据。
3. 没有可靠机器判据、当前环境无法安全执行或证据不足 → `human` 或 `unverified`，不得把未执行写成通过。
4. `human_reason` 使用固定类型：`intent / visual-ux / domain-judgment / high-risk / no-reliable-oracle / environment-unavailable / evidence-insufficient`，并写人工步骤与期望。
5. 执行后只能向更严格方向调整（`auto → auto+human-review → human`，自动执行 → human）并记录 `reclassification`；降低人工要求必须回到技术方案修订计划并重新冻结。

### 3.1 Agent 自动完成

| 环节 | 内容 | 产物 |
|---|---|---|
| 静态门 | `config_lint` / `skill_lint` / `import_lint`（fast） | 退出码 + 日志 |
| 需求 direct | `merge_base..candidate` diff 的直接测试 + 新增测试，全跑、不受预算 | `raw/scoped.json` |
| 关联 indirect | import 反向闭包 + `dev/scoped-map.yaml` 映射；预算截断与不可枚举部分进 deferred | `raw/scoped.json` + deferred 清单 |
| 失败归因 | 清单外失败只对单项在 base 侧对照；quarantine 内 skip | `verify-log.md` |
| command 功能 AC | 实际命令、退出码、业务输出与断言 | v2 `business-results.json` |
| 浏览器功能 AC | 实际 URL / actions、DOM / 网络 / 状态断言、console/page error、有效截图 | v2 `business-results.json` + artifacts |
| 首次验收报告 | 汇总自动结果，逐条列出未验证和仍需人工的原因、步骤与期望 | `acceptance-report.md` |
| 人工测试准备 | 部署候选测试实例并核 deploy-state、预截图、新旧报告对比、性能基准只记录 | artifacts + 测试环境信息 |
| 合 main 后 Prod | sync mac/prod，核部署提交 = 合并提交，`status` + 只读业务冒烟；失败提议 `rollback-deploy` | Prod 回执 |

浏览器截图必须配行为断言；宿主没有浏览器能力时记 `unverified`，不得伪造执行。已在关联选择集执行的业务测试直接采集结果，不重复跑。

### 3.2 人工确认

| 项 | 理由 |
|---|---|
| 需求意图满足度 | AC 字面可测，「解决了业务问题没有」不可测 |
| 展示 / 样式 / 交互手感 | AI 可断功能与留截图，人最终看呈现和手感 |
| 性能可接受度 | perf 类只记录数字 |
| 真实数据抽查 | 代表性真实工作簿 / 判读任务由人看领域结论合理性 |
| 风险豁免 | `unverified` AC、deferred 兜底项、quarantine 超期、遗留 Medium/Low |
| 不可逆动作 | 确认合 main、确认发布 Prod、指示 push |

人工项应由 AI 先收敛为明确的判断问题，每条带原因、实例链接或启动命令、操作步骤、期望与预截图；不要把已经能自动判定的操作重复丢给人。

---

## 4. 候选冻结

1. 收口前提：全部票 `done` 且 `merged`；每票 conformance `conform`；无未解决 needs-human；feature 级覆盖核对落盘 `integration/conformance-feature.md`（复用 conformance 模板）。
2. 同步 main：集成 worktree 内 `git merge main`，冲突在集成分支解决；`method/`、`missions/` 配置变更重新转换并通过对照验收。
3. 冻结：`feature_closeout.py freeze` 写 `integration/candidate.json`：

```yaml
schema: agent-dev.feature-candidate/v2
feature: <feature-module>/<feature-slug>
integration_branch: feat/<feature-slug>
main_head: <冻结时 main HEAD>        # 必须 == merge_base（main 已同步进集成分支）
merge_base: <git merge-base main HEAD>
candidate: <集成分支 HEAD commit>
candidate_tree: <HEAD^{tree}>
frozen_at: <时间>
issues: [{id, status, merged_commit, conformance}]
acceptance_plan:
  path: acceptance-plan.yaml
  sha256: <计划文件 SHA256>
  cases: [AC-01, AC-02]
```

冻结语义：

- 冻结后集成分支不得新增提交；回修产生新候选，重新 freeze（`supersedes` 记旧 tree），自动证据跑增量；
- 自动证据、人工清单、确认记录都绑定同一 `candidate_tree` 与计划哈希；tree 或计划变了，旧证据、人工确认与报告失效（脚本校验）。计划快照写 `integration/acceptance-plan.json`；
- main 在冻结后只在 `feature-closeout.main-drift-ignore`（默认 `dev/**`、`docs/**`）内前进时，候选仍有效；触及其他路径 → 重新同步冻结。这条放宽是为高频并行准备的：其他会话往 main 提交 dev/ 产物不应让本需求反复重冻。

---

## 5. Agent 自动证据（本机）

### 5.1 选择集

以 `merge_base..candidate` 为输入：

- ① direct：diff 中生产代码对应的直接测试 + 新增测试，全跑、不可延期、不受预算；
- ② indirect：a. import 反向闭包；c. `dev/scoped-map.yaml` 数据映射。
- 架构契约关联（初稿 ②b）一期降为人工清单辅助项：主会话对照 `docs/ARCHITECTURE.md` 接口表列出消费者模块，写进验收报告「建议留意」，不进机器选择集；机器化顺延二期。

```bash
python3 tools/test_slots.py run --label "feature-verify/<feature-slug>" -- \
  .venv/bin/python ~/.zcode/skills/shared/scripts/scoped_select.py \
    --repo .worktrees/integration-<feature-slug> --base <merge_base> --run --jobs '$JOBS' \
    --budget-seconds <feature-closeout.verify-budget-seconds> \
    --report <主工作区>/dev/features/<module>/<slug>/integration/raw/scoped.json
```

- 本机配方沿用 `agents-config.md`：`.venv/bin/python`、`env -u PANDU_STATE_ROOT -u PANDU_BASELINE_MODE`、`TMPDIR=/private/tmp`；
- 后台执行，日志落集成 worktree `.logs/`（不提交），轮询结果；
- 预算到点只停发新 indirect，未执行项进 deferred；不可枚举部分记 `deferred: ci-only-full`；deferred 全部列入验收报告；
- 任何时候不在本机跑完整套件。

### 5.2 失败归因与回修

清单外失败 → 只对失败单项在 `merge_base` 同命令对照：base 侧同签名稳定失败 → 按 quarantine 入册条件登记；base 侧通过 → 本需求引入 → 在对应票 worktree 修复（开回修票或原票 `reopened`，不把已 `merged` 的票回退为 in-progress）→ 本地重合入集成分支 → 重新 freeze → 增量重跑（fast + 改动文件 direct + 其闭包增量）。基础设施性失败修复环境后重跑该项。每轮记 `integration/verify-log.md`。

### 5.3 判定

`integration/evidence.md` yaml 头：

```yaml
candidate_tree: <tree>
all-passed: true            # 仅指实际执行项
ran: {direct: N, indirect: N, quarantined: N, skipped-env: N, deferred: N}
deferred: [<测试或数据路径：原因>]
scoped-report: integration/raw/scoped.json
full-deferred: ci
```

`feature_closeout.py report` 生成首次 `acceptance-report.md` 后，`feature_closeout.py check --phase verify` 才允许进入人工阶段：候选 tree 与计划哈希一致、关联检查 `all-passed: true`、计划 AC 无漏项 / 额外 / 重复、自动证据有效、feature 级 conformance `conform`、报告未过期。自动 AC 可如实标 `unverified` 交人处理，但不得计入通过或写成“全绿”；通过后追加 `feature-verified` 事件（含义是自动阶段完成，不是所有 AC 都已通过）。

---

## 6. 人工测试

### 6.1 人工测试环境

优先级：

1. **人工测试实例**（`feature-closeout.verify-instance`）：本机独立实例，跟踪 `feat/<feature-slug>`，有独立 state/report 根，不影响 Prod。租约文件保证同一时刻只给一个需求用；切换需求经 `track --branch` 置 `tracks_pending_rewind` 后 sync。
2. **集成 worktree 开发启动器**：没有测试实例时，人工测试直接在集成 worktree 用开发启动器 / 本地页面跑，Agent 在验收报告里给出启动命令。

为什么不直接发 Prod：pandu `topology.json` 的 `prod` 通道写死跟踪 `main` 且只允许快进（`TRACK_REWOUND`）；把 A 需求候选发上 Prod 后，B 需求候选或退回 main 都会被拒，并行需求会在 Prod 上互相卡死；`sync` 还会 cancel Prod 在途任务。Prod 只发布已合 main 的提交。

pandu 现状（2026-11-09 核查）：本机 L2 只有 `mac/prod` 一个实例，`test` 通道 `allow: ["main"]`、`deny: ["codex/*","worktree-*"]`。建 `mac/test` 实例需改 topology（加实例、allow `feat/*`）并执行 `prepare-deploy`，属部署操作，另行授权后实施；在此之前走第 2 条。

### 6.2 合并前功能验收报告

`feature_closeout.py report` 从冻结计划与实际结果生成 `integration/acceptance-report.md`，不得手改，也不再维护 `human-checklist.md`：

```markdown
# 功能验收报告：<feature-slug>

- 候选、测试环境、计划覆盖、关联自动检查（分开陈述）
- AI 完整验收 A/B；AI 执行人工复核 C/D；纯人工 E/F；未验证逐条列出

## 逐条验收结果
| AC | 需求来源 | 执行方式 | 最终判定 | AI 结果 | 人工原因 | 人工结果 | 证据 |

## 人工验收清单
每条：为什么需要人工 → 操作步骤 → 期望 → 当前结果

## AI 分类调整
原计划 → 实际归属 + reason + note；无则写无

## 未验证、延期与跳过
不得省略 deferred / unverified / quarantine 等边界

## 合并建议
不可合并 / 等待自动验收 / 等待人工处理 / 验收条件已满足（有豁免时醒目标注）
```

自动阶段完成后生成首次报告，人据此实测或复核；写回人工结果与确认原话后重新生成最终报告。`check` 逐字核对报告，手写摘要、旧候选报告或旧计划报告都不能放行。

### 6.3 确认语义

- 只有对话内人的显式确认才算；沉默、超时不算；
- 原话、时间、候选 tree 落盘 `integration/confirmation.md`，人工清单逐条结果写回 v2 `business-results.json`（`confirmed` / `waived`+理由 / `fail`）；
- 自动未验证项如由人实际测试，必须登记向 `human` 的分类调整；未实际测试只能豁免，不能写确认冒充通过；
- 附条件确认 → 条件落地后（如需改代码即新候选，只复测受影响条目）再进合并；
- 人在报告里报问题 → 回 §5.2 回修。

---

## 7. 合 main、发布 Prod、兜底与回滚

### 7.1 合并前核对

`feature_closeout.py check --phase merge`：候选 tree 与计划哈希未变；`verify` 相全部项；最终 `acceptance-report.md` 与当前结果逐字一致；人工清单全部 `confirmed`/`waived`；未调整分类的 `unverified` auto AC 均有人豁免；`confirmation.md` 绑定同一 tree；main 自 `main_head` 起只在 drift-ignore 路径前进。

### 7.2 合并序列

1. 主工作区须在 `main`，否则停下报告（不在集成 worktree 合 main）；
2. `git merge --no-ff feat/<feature-slug>`；
3. 合并 commit 记入 `evidence.md`，追加 `feature-merged` 事件（附 commit）。

### 7.3 发布本机 Prod

确认材料已写明「合并后发布 Prod」，人的确认覆盖本次发布：

1. `python3 tools/pandu_env.py sync --instance mac/prod --dry-run` → 通过后正式 sync（多个需求连续合并时可合并为一次 sync，减少 Prod 在途任务被 cancel）；
2. `status --instance mac/prod` 核部署提交 = main HEAD；Agent 跑最终验收报告列出的 Prod 只读冒烟；人按清单抽查 1–2 条；
3. 前置：主工作区需干净（local 来源 sync 校验 `DEV_TREE_DIRTY`），不干净时停下报告。

### 7.4 兜底与回滚

- push 由人指示；main push 触发判读机 CI full = 套件级兜底；
- Prod 冒烟失败 → 提议 `rollback-deploy --instance mac/prod`（执行前等人确认）；
- CI full 新失败归因到本需求 → 需求状态打回 verifying：开回修票走完整流程；影响 Prod 时先提议 `git revert -m 1 <合并 commit>` + 回滚部署，人确认后执行。

---

## 8. 机器门禁、状态与留痕（skills 仓实施）

| 交付物 | 内容 |
|---|---|
| `shared/scripts/feature_closeout.py` | `validate-plan`（计划结构）、`freeze`（全票 / conformance / main / 计划 → 候选与快照）、`check --phase verify|merge [--record]`、`report`（生成正式验收报告）、`record-merge` |
| `shared/templates/feature-closeout.md` | evidence 头、v2 business-results.json、正式验收报告、confirmation.md |
| `shared/templates/acceptance-plan.yaml` | 稳定 AC、执行方式、最终判定、自动断言、人工原因与证据契约 |
| `shared/templates/tech-spec.md` | §5.1 功能验收计划摘要与 YAML 唯一真源链接 |
| `shared/fragments/supervision-loop.md` | 事件词表 +3：`feature-verified`、`feature-confirmed`、`feature-merged`；第 6 步全部 done 后按 `feature-closeout.mode` 进入收口；新增「需求收口（local-human）」节 |
| `shared/fragments/dev-config.md` + `templates/agents-config.md` | `feature-closeout` 配置块（默认 `ci-promote` 保持原行为） |
| `shared/fragments/dev-artifact-paths.md` | `integration/` 目录 |
| `shared/scripts/progress.py` | 回放渲染新事件 |

产物目录（主工作区）：

```text
dev/features/<feature-module>/<feature-slug>/
├── acceptance-plan.yaml    # 技术方案阶段的功能验收计划唯一真源
└── integration/
    ├── candidate.json          # 冻结代码候选与计划哈希（freeze 写）
    ├── acceptance-plan.json    # 冻结计划快照
    ├── evidence.md             # 关联自动证据头 + 结论
    ├── business-results.json   # 每条 AC 的实际执行 + AI 结果 + 人工结果
    ├── acceptance-report.md    # 首次 / 最终功能验收报告（report 生成）
    ├── confirmation.md         # 人确认原话 + 候选 tree
├── conformance-feature.md  # feature 级覆盖核对
├── verify-log.md           # 候选变更与回修轨迹
├── raw/scoped.json
└── artifacts/              # 截图、报告（受留存与体积控制约束）
```

## 9. pandu 配置落地

- `dev/agents-config.md`：新增 `feature-closeout` 块（`mode: local-human`，`verify-instance: null` 待建，`prod` 命令）；修订「push 口径」「集成分支并回 main」「票收口归档」三条与集成分支命名；修正隔离清单文件名为 `tools/test_quarantine.yaml`。
- 不改 topology 与部署代码；`mac/test` 实例另行授权建立。

## 10. 遗留决策

1. 建 `mac/test` 人工测试实例（topology 加实例 + allow `feat/*` + `prepare-deploy`）：部署操作，待授权；
2. 收口验证预算：沿用 1200s，首个试运行需求实测后调整；
3. 架构契约关联机器化：二期。
