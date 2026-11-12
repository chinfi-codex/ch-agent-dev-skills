# 整需求 PR 收口测试模式设计（pandu agent 仓）

- 状态：**已放弃（2026-11-12 用户裁定）**。本文仅保留历史调研，不进入实施；现行收口见 `req-feature-local-verify-phase1.md` r3
- 日期：2026-11-06
- 适用仓：`~/Desktop/yufeng-pandu-agent`（pandu agent，Gitea + 判读机 CI）
- 关联文档：`req-test-verification-refactor.md`（ci-only 基础方案及其第 13 章 pandu 落地需求）；本方案在其基础上补齐「整需求 PR 触发、一次候选 full、业务端证据聚合」三段

> **废弃说明：自 2026-11-12 起，本文所有“待评审 / 顺延二期 / 推进 / 未决问题”均不再构成实施待办。只保留历史设计推理，禁止据此新建整需求 PR 或 PR CI 工作。**
- 事实核对基线：pandu `main@d39b933df7a8478af7a5f229979c1654bafc3ad2`，2026-11-06 只读核查

---

## 1. 目标与非目标

### 1.1 目标

把 pandu 的测试收口粒度定为**整需求（feature-slug）**，而不是每张 issue：

```text
issue-001 worktree ─┐
issue-002 worktree ─┼─ 本地 merge --no-ff → slug 集成 worktree
issue-003 worktree ─┘                              │
                                                   │ 全部 issue done
                                                   │ 整需求符合性核对
                                                   │ 冻结最终候选 commit/tree
                                                   ▼
                                        推送一个需求分支
                                                   │
                                        创建一个整需求 PR
                                                   │
                                  CI：一次候选 full + 业务证据采集
                                                   │
                                     PR 展示证据，通过后合并
```

1. issue 不各自 push、不建 issue PR、不跑 full；
2. 所有 issue 评审通过后本地合入 slug 集成 worktree（延续现行约定）；
3. 全部 issue done 后整需求提交一个 PR；
4. PR 触发**一次候选 full**（判读机 CI）；
5. PR 附带**业务端验收证据**（AC 映射 + 实际执行结果 + 截图/报告），不是只贴 full 绿；
6. full 与业务证据为 PR 合并门禁，通过才合 main。

### 1.2 非目标

- 票级本机验证统一为 TDD 见红 / DoD + fast（2026-11-11 用户裁定），不跑票级 scoped / full；
- 不重建 full（复用现有 `test-full` workflow 与判决链）；
- 不在本票/本机补跑 full（ci-only 语义不变）；
- 不改变部署授权链（deploy 仍手工 dispatch，与 PR CI 分离）。

---

## 2. 关键定义：「一次 full」= once-per-candidate

**一次 full 指每个冻结候选 commit/tree 跑一次，不是需求生命周期只跑一次。**

以下任一情况产生新候选，旧证据失效，须重新 full 并刷新业务证据：

- 评审回修新增 commit；
- 业务验收失败后的修复提交；
- 同步最新 main 导致 tree 改变；
- PR 更新（synchronize）改变测试对象。

旧 SHA 的绿色判决不得放行新 tree。同 tree 的合并 SHA 变化可复用判决（tree 相同）；tree 不同必须重跑。

现行 `concurrency: full-${{ github.ref }}` 的 `cancel-in-progress`（`test-full.yaml:20-22`）只取消同 ref 并行旧 run，不限制顺序重跑次数；次数约束由流程门禁承担，不是 workflow 承担。

---

## 3. 各阶段测试职责

| 阶段 | 测试内容 | 明确不做 |
|---|---|---|
| issue 实现 | TDD 见红/转绿、DoD、fast | 不跑 scoped / full、不 push、不建 issue PR |
| issue 合入 slug worktree | 冲突处理、重跑本票 DoD + fast、独立评审、票级 conformance | 不跑 scoped / full |
| 全需求冻结 | 全部票 done、feature 级覆盖核对（各票覆盖矩阵并集 + tech-spec 全节）、同步 main、记录 base/head/tree | 不在本机补 full |
| 整需求 PR | **一次候选 full**、业务 AC 实际执行、证据聚合落 PR | 不拿旧票证据冒充最终候选证据 |
| 合并后（main） | 主干回归 full、部署及部署验收 | 部署成功不代替 PR 测试通过 |

三个验证结论必须分开、不得合并表述：

```text
full.pass      = 完整功能回归通过
business.pass  = 本需求声明的业务 AC 全部实际执行并通过
release_ready  = 满足发布条件（含真库/性能等人工确认项）
```

---

## 4. 配置语义（`dev/agents-config.md` 扩展）

把「执行位置」与「执行粒度/触发/次数」分开建模（以下为**新增字段建议**，pandu 当前未落地）：

```yaml
verify-policy:
  ticket-max-files: 10                # 只约束拆票粒度，不触发额外测试
  ticket-max-loc: 400                 # 实测超限重新拆票
  ci-full:
    granularity: feature              # 新增：以整需求为验证单位（非 per-ticket）
    trigger: pull-request             # 新增：整需求 ready PR 触发；需求分支普通 push 不触发
    runs: once-per-candidate          # 新增：每个冻结候选 tree 一次
    business-evidence: required       # 新增：PR 必须附业务端证据
```

`ci-full.trigger: pull-request` 的理由：若需求分支普通 push 也触发 full，则「推送分支建 PR」与「PR synchronize」会各跑一次，破坏 once-per-candidate。触发集合与 `tools/tests/full_ci_workflow_smoke.py` 的静态契约须同步修改（该测试当前断言只覆盖 main、无 PR 触发，`full_ci_workflow_smoke.py:37-41`）。

---

## 5. pandu 现有能力复用清单（已落地，2026-11-06 核对）

| 能力 | 现状 | 依据 |
|---|---|---|
| main push / 手工 dispatch 触发 full | 已落地 | `.gitea/workflows/test-full.yaml:6-15` |
| 判读机身份 + 机器预算校验 | 已落地 | `test-full.yaml:41-46`（hostname==pandu-judge、budget.json） |
| 精确 checkout `github.sha` + HEAD 核对 | 已落地 | `test-full.yaml:32-39`、`:53` |
| 独立锁定 CI runtime（run_id+attempt+tree 隔离、ready.json 身份） | 已落地 | `tools/ci_runtime.py:100-162` |
| full 命令（`check.py --no-cache --verdict-json`，经 test_slots 拿预算） | 已落地 | `test-full.yaml:62-89` |
| base 侧失败单项归因（不重跑整套） | 已落地 | `test-full.yaml:91-104`、`tools/ci_attribute.py:323-389` |
| 机器判决 `pandu.test-verdict/v1`（commit/tree/ref/run_id/ran_n/归因/quarantine/skip/worktree_changes） | 已落地 | `tools/check.py:66-95`；最终 job 强制 `pass=true`，`test-full.yaml:106-125` |
| 判决发布 publication.json（judge 本地证据目录） | 已落地 | `tools/ci_report.py:11-49`、`test-full.yaml:149-161` |
| CI 与部署隔离（deploy 仅手工 dispatch、按 ref 串行） | 已落地 | `.gitea/workflows/deploy.yaml:9-35` |
| 票分支不 push、本地合入集成、统一 push/CI | 已是现行约定 | `dev/agents-config.md:181-183` |
| 特性集成分支 + 集成 worktree | 已是现行约定 | `dev/agents-config.md:197-216` |

---

## 6. 缺口与改造项（本方案要补的）

| # | 缺口 | 改造内容 |
|---|---|---|
| G1 | PR 触发未接通 | `test-full.yaml` 增加 ready PR 触发（`pull_request` 事件，类型限定 opened/synchronize/reopened 且带 ready 标记或独立 `test-full-pr` 触发路径）；checkout 改为 PR merge 候选（Gitea 下为 merge refs 或显式 merge 后 push 的候选分支），绑定最终 head/tree/base |
| G2 | 业务证据聚合器不存在 | 新增证据收集器：消费本次 run 已发布的 verdict/publication + 业务 AC 执行结果，产出第 8 节证据包；不重跑任何测试 |
| G3 | PR 可见证据回写缺失 | 当前仅 Actions summary + judge 本地目录（`test-full.yaml:127-161`），无 commit status / PR 评论回传；最小实现：PR 描述模板贴 summary.md，机器真源留 judge 目录 + manifest 哈希 |
| G4 | `ci_supervisor.proof()` 会重跑 full | 禁止将其用作 PR 证明收集器（`tools/ci_supervisor.py:152-188`、`:214-216` 是独立重算型证明器）；改造为只读消费已发布 verdict/publication + API 身份校验的收集器，复用其 `pandu.ci-run-proof/v1` schema |
| G5 | 通用需求验收聚合不存在 | 新增 `acceptance.yaml`（第 7 节）+ requirement-coverage 聚合（feature 级覆盖矩阵并集） |
| G6 | full 结果 ≠ 业务端证据 | full 的 excluded/skip/quarantine 项必须进入 PR「未验证」清单（第 7.3 节）；被 skip 的 required AC 不得计入 business.pass |
| G7 | `ready.json` 未随证据发布 | `ci_report.py` 目前只复制 verdict 目录与 smoke JSON（`ci_report.py:24-47`），未复制 `ready.json`；补入 publication 目录，保证运行环境身份可复核 |
| G8 | 文档滞后 | `dev/agents-config.md:149-160` 仍写 main/query push full 与需求分支 push 兜底，与 workflow 现状（仅 main）不一致，随本方案一并修订；`protocol/workflow/仓库契约.md:108-116`「无 PR 评审关卡」需更新 |

---

## 7. 业务端证据设计

### 7.1 需求验收映射 `acceptance.yaml`

每个需求目录新增（schema 建议 `pandu.feature-acceptance/v1`）：

```yaml
schema: pandu.feature-acceptance/v1
feature_slug: 共享绘图交互
cases:
  - ac_id: AC-01
    title: 用户在正式报告页面框选并恢复图表
    requirement: tech-spec.md#交互行为        # PRD/tech-spec 锚点
    issues: [issue-077, issue-078, issue-080]
    layer: ui                                 # api | e2e | ui | live
    required: true
    test: phase2_render_smoke.py
    artifacts:
      - artifacts/ui/full-chart.png
      - artifacts/ui/zoom-chart.png
      - artifacts/ui/browser-numeric-log.json
  - ac_id: AC-02
    title: 报告业务结论和交付状态正确
    layer: e2e
    required: true
    test: a1_v3_e2e_smoke.py
```

覆盖关系链：`PRD/tech-spec 行为点 → issue → 业务 AC → 实际执行的测试 → 业务结果与产物`。

### 7.2 证据产生方式

1. **已在 full 中执行的业务测试不重复运行**：给 full 命中的业务测试加标准证据输出（AC ID、业务输入、预期/实际、request/report ID、delivery/gate 状态、产物 SHA256）；收集器从本次 full 执行结果采集。
2. **被 full 排除的必需 AC（浏览器等）**：单独 `business-acceptance` job，只执行 acceptance.yaml 声明的用例，同一候选 commit/tree，不重跑 full。判读机当前按策略跳过 Playwright（`tools/test_categories.yaml:68-76` 的 `needs-playwright` + `skip_on_judge`）；若判读机不补浏览器能力，对应 AC 只能如实标「未验证」，不得计入 business.pass。
3. **真库/真实实例验收单独分层**：A1 live、split-runtime live 等有业务写入副作用（真实请求、请求状态、报告），不进默认 PR CI；需要时用专用测试实例 + 部署该 PR 候选 commit + 显式授权，并记录环境/部署 SHA/请求 ID。没有该环境时列入 release_ready 前置项。

### 7.3 证据等级与未验证清单

- 每条 AC 标 `evidence_level: synthetic | frozen-host | test-db | production`；
- full 中 perf/display excluded、`skipped-env`、`skipped-policy`、quarantined 项逐条列入 PR「未验证/延期/隔离」清单（字段已在 verdict 中：`tools/check.py:66-95`）；
- **full 绿但 required AC 被 skip ⇒ business.pass ≠ true**，不得合并。

---

## 8. PR 证据包

每个候选生成一组（schema 建议 `pandu.feature-pr-evidence/v1`）：

```text
<feature-slug>/<head-sha>/
├── manifest.json                 # 机器索引与总判决
├── summary.md                    # 可直接贴 PR 的摘要
├── requirement-coverage.md       # issue + tech-spec + AC 并集核对
├── business-results.json         # 每条 AC 一条结构化结果
├── ci/
│   ├── full-verdict.json
│   ├── publication.json
│   ├── check-smoke.json
│   └── ready.json                # 依赖 G7 补齐
├── selection/scoped.json         # 有 scoped 延期时保留
├── logs/                         # fast、DoD、业务、浏览器原始输出指针
├── artifacts/                    # ui/ 截图与浏览器日志、reports/ 交付摘要
└── SHA256SUMS
```

`manifest.json` 最小字段：

```yaml
schema: pandu.feature-pr-evidence/v1
feature_slug: /
pr_number: / pr_url: /
base_commit: / head_commit: / head_tree: / source_ref: /
tested_commit: / tested_tree:        # 测 PR merge 候选时与 head 分开
issues: [{id, merge_commit, evidence, conformance}]
full: {run_id, run_attempt, event, ref, commit, tree, status, pass,
       ran_n, quarantined_n, quarantine_config_sha256,
       skipped_env_n, skipped_policy, verdict_sha256}
business: {pass, cases}              # → business-results.json
unverified: [...]                    # 实库、性能、其他宿主等
side_effects: {external_network, persistent_state, deployment}
generated_at:
```

PR 页面摘要模板：

```markdown
## 整需求验收
- 需求：<feature-slug>；Issue：N/N 已合入
- 候选：<head-sha> / <tree>
- Full：PASS（Run #id，实际执行 N 项）
- 业务 AC：M/N PASS
- 需求覆盖：无缺失
- 隔离/跳过：逐条列出
- 未验证边界：真库 / 性能 / 其他
- 证据：business-results、截图、报告、full-verdict（judge 目录 + SHA256SUMS）
```

截图必须配合行为断言（DOM/数值/网络/pageerror），单独截图不构成通过证据。

大产物不入 Git（pandu `.gitignore` 忽略 `dev/` 与 `*.png` 新生成文件）；机器真源留 judge run-owned 目录，PR 只贴 manifest 摘要与指针。

---

## 9. PR 合并门禁与失败闭环

### 9.1 允许合并的全部条件

1. 所有 issue 已合入 slug 集成分支且置 `done`；
2. feature 级需求覆盖核对通过（conformance）；
3. 当前候选 full `pass=true`（verdict 的 commit/tree 与候选一致）；
4. 所有 required 业务 AC 实际执行且通过（business.pass=true）；
5. 无未说明的 skip / quarantine / 旧证据；
6. PR 证据包与当前候选 commit/tree 一致（SHA256SUMS 可复核）。

### 9.2 失败回修闭环

```text
full / 业务 AC 失败
  → 开回修 issue（或退回原 issue）
  → slug 集成分支形成新候选
  → PR synchronize
  → 新候选重新一次 full
  → 刷新全部业务证据
```

- 基础设施失败、runner 掉线、checkout/上传失败 = **未得到有效判决**，不得转成通过或 quarantine（与现有 full 语义一致）；
- 未通过不得合并 main；`verify-fails` 达 2 转 needs-human（沿用监督循环现行规则）。

### 9.3 main push 的回归 full（见 §11 未决问题 1）

PR 候选 full 与 main 合并后 full 职责不同：前者是合并前验收证据，后者是主干回归。现阶段保留两者；若要严格执行全局 once-per-candidate，需走 §11 未决问题 1 的改造。

---

## 10. 最小落地路径

1. **slug 集成交付物**：所有 issue 本地评审后合入 `.worktrees/<feature-slug>`（或现行共享集成分支），保留每票 commit/evidence/conformance；
2. **候选冻结**：合入最新 main、工作区干净，记录 base/head/tree；业务证据在该最终 tree 上生成；
3. **整需求业务验收**：acceptance.yaml 声明 AC → 至少一份适用业务链（A1/A2/A3 离线 e2e）+ 真实浏览器 UI（若 required）+ 覆盖表；未验项不得省略；
4. **一次候选 full**：最小改动先走「PR 源候选分支手工 dispatch `test-full.yaml`（带 base）」；PR 自动触发属 G1 改造票；
5. **证据包发布**：复用 `ci_report.py` verdict/publication + G7 补 ready.json + 新增业务聚合器（G2）；
6. **PR 摘要回填**：按 §8 模板贴 summary；
7. **合并前另核**：候选证据一致性 + 合并后 main full 策略确认。

实施拆票建议：G1（PR 触发+静态契约）与 G2/G5（acceptance schema+聚合器）为两张独立票；G4（proof 收集器改造）与 G7（ready.json）可合并为一张小票；G8 文档修订随任何一票顺带。

---

## 11. 未决问题（评审时定夺）

1. **main push 自动 full 去留**：保留 = 合并后必再跑一次 full（全局并非一次）；去掉/同 tree 复用跳过 = 严格一次但主干失去独立回归。建议：保留 main 回归 full，PR full 定位为合并前候选证据（本文按此书写）。
2. **判读机浏览器能力**：needs-playwright 用例 `skip_on_judge: true`，required UI AC 在 PR CI 无处执行——补判读机浏览器 runner，还是 UI AC 永远人工/本机执行并如实标未验证。
3. **真库验收授权链**：live 类 AC 的专用测试实例、候选部署与写入授权如何审批留痕（与 deploy 手工授权链对齐）。
4. **同 tree 复用口径**：规则仓设计允许「main 合并只改 SHA 不改 tree 时复用集成分支判决」，但 `process_check.py --phase promote` 现要求 commit/ref/tree 全一致（`process_check.py:544-552`）；PR 流程落地时须统一（建议 PR 门禁按 tree 复用、main 放行维持现行严格口径并写明）。
5. **PR 触发的权限面**：PR 事件的 token 权限、fork 场景（本仓单租户，预计不涉及）与判读机 secrets 下放边界。

---

## 附：与现行约定的关系

- 本方案**不推翻** `dev/agents-config.md:181-183`（票分支不 push、本地合入、统一 push/CI）与 §「特性集成分支」（`:197-216`），只是在「统一 push/CI」之后补上 PR、候选 full 与业务证据三段；
- 与 `req-test-verification-refactor.md` 第 13 章的差异：该章按「需求分支 push 触发 full」设计，本方案改判为「整需求 ready PR 触发」以维持 once-per-candidate，并以本方案为准推进；该章其余判读机要求（精确检出、runner 限定、预算单层、权限最小化、证据保存、失败语义）全部沿用。
