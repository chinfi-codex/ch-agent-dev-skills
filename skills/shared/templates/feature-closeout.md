# 需求收口产物模板（feature-closeout: local-human）

功能验收计划的唯一真源是需求目录 `acceptance-plan.yaml`，结构见 `shared/templates/acceptance-plan.yaml`。其余产物写**主工作区** `./dev/features/<feature-module>/<feature-slug>/integration/[round/]`，不写集成 worktree、不提交到集成分支。`feature_closeout.py freeze` 冻结代码候选、票组、merged commit 与计划哈希；票只须 `merged` 或 `done`，不再要求每票 conformance。`check` 对候选评审、候选复跑、feature conformance、计划、实际结果、人工确认和报告逐条对账；`finalize` 在 feature-merged 后写 accepted/done 并清理。

## 0. 候选级门（freeze 之后、report 之前各做一次）

| 产物 | 产生方式 | 必须绑定 | 阻塞条件 |
| --- | --- | --- | --- |
| `integration/feature-review.md` + `integration/feature-review-receipt.json` | 独立 `/ai-review` 一次审 `main_head..candidate`；`review_receipt.py create-feature` 生成回执 | candidate / tree / merge_base / diff_sha256 / plan_sha256 / 报告哈希 / 全部 implementer 身份 | reviewer 与任一 implementer 同 session/agent；verdict=blocked；critical/high 未闭合；候选、报告或覆盖变化 |
| `integration/candidate-verification.json` | `verify_issue.py run-candidate --candidate ... --repo <集成 worktree>` 逐票真实执行冻结 DoD+fast | candidate / tree / main_head / plan_sha256；每票 `verification_path` + SHA256 | 任一票失败、漏票、额外票、证据哈希不匹配或候选不 clean |
| `integration/conformance-feature.md` | 主会话或独立监督会话按 tech-spec 全节 + 全部票矩阵并集核对 | candidate / tree / coverage.issues / specs hash | verdict≠conform、漏票、漏 spec、空表或证据指针无效 |

票级不再生成 `review-receipt.json`、不再跑 supervisor 复跑或 `conformance-<issue>.md`；票级覆盖矩阵保留在 impl-log，供 feature conformance 汇总。

## 1. evidence.md（关联自动检查）

````markdown
# 需求收口自动证据：<feature-slug>

```yaml
candidate: <candidate.json 的 candidate 完整 commit>
candidate_tree: <candidate.json 的 candidate_tree>
all-passed: true            # 仅指实际执行的关联自动检查；不代表每条业务 AC 已验证
ran:
  direct: 0                 # 需求 diff 直接测试 + 新增测试（全跑、不受预算）
  indirect: 0               # 反向闭包 + scoped-map 映射中实际执行的
  quarantined: 0
  skipped-env: 0
  deferred: 0               # 预算截断 / 闭包不可枚举，由 main push CI full 兜底
deferred:
  - <测试或数据路径>：<原因>
scoped-report: integration/raw/<generation>/scoped.json
generation: <artifact-generation-manifest.json 中的当前代际>
full-deferred: ci           # 本机证据不代表完整套件通过
```

## 结论
一句话：选择集多大、跑了什么、失败如何归因（指针指向 verify-log.md）。有业务 AC 未验证时不得写“全部验收通过”。
````

## 2. business-results.json（计划中的每条 AC 恰好一条）

```json
{
  "schema": "agent-dev.feature-business-results/v2",
  "candidate": "<candidate.json 的 candidate 完整 commit>",
  "candidate_tree": "<tree>",
  "plan_sha256": "<candidate.json acceptance_plan.sha256>",
  "environment": {"name": "integration-test", "url": "http://127.0.0.1:8000", "lease_id": "<实例租约或null>", "service_id": "<开发服务或null>", "generation": "<当前代际>"},
  "items": [
    {
      "id": "AC-1", "oracle": "auto", "executor": "command", "status": "pass",
      "execution": {"executor": "command", "command": "python3 tools/tests/ac1.py", "exit": 0, "started": true, "generator": "verify_issue", "candidate": "<candidate 完整 commit>", "candidate_tree": "<tree>"},
      "assertions": [
        {"type": "output", "expected": "三态计数一致", "actual": "三态计数一致", "status": "pass"}
      ],
      "artifacts": [
        {"type": "raw", "path": "integration/raw/<generation>/ac1.json", "sha256": "<hex>", "candidate": "<candidate 完整 commit>", "candidate_tree": "<tree>", "generation": "<当前代际>"}
      ]
    },
    {
      "id": "AC-2", "oracle": "auto+human-review", "executor": "browser-agent", "status": "pass",
      "execution": {
        "executor": "browser-agent", "url": "http://127.0.0.1:8000/reports",
        "actions": ["打开报告", "框选图表", "恢复原图"],
        "console_errors": [], "page_errors": [],
        "candidate": "<candidate 完整 commit>", "candidate_tree": "<tree>"
      },
      "assertions": [
        {"type": "dom", "expected": "图例存在且原图恢复", "actual": "图例存在且原图恢复", "status": "pass"},
        {"type": "network", "expected": "保存请求 200", "actual": "保存请求 200", "status": "pass"}
      ],
      "artifacts": [
        {"type": "screenshot", "path": "integration/raw/<generation>/ac2.png", "sha256": "<hex>", "candidate": "<candidate 完整 commit>", "candidate_tree": "<tree>", "generation": "<当前代际>"}
      ],
      "human": {"status": "confirmed", "by": "<人>", "at": "<时间>", "note": "版式可读"}
    },
    {
      "id": "AC-3", "oracle": "human", "executor": "human",
      "human": {"status": "pending"}
    },
    {
      "id": "AC-4", "oracle": "auto", "executor": "command",
      "status": "unverified", "reason": "真库无授权",
      "human": {"status": "waived", "by": "<人>", "note": "合 main 后另行安排"}
    }
  ]
}
```

字段规则（`check` 机器把关）：

- 计划中的每条 AC 必须恰好出现一次；缺失、额外、重复都失败。`required: false` 的失败 / 待确认不阻塞合并，但仍须登记并进入报告；字段缺失、证据无效等结构问题始终阻塞。
- `executor`：`command | browser-agent | human`，表示谁执行；`oracle`：`auto | auto+human-review | human`，表示谁最终判断。两者默认必须等于冻结计划。
- 自动部分 `status`：`pass | fail | unverified`。`pass` 必须有 `execution` 和至少一条带 `expected / actual / status=pass` 的实际断言；`unverified` 必须有 `reason`，不得计入通过。
- `command` 通过必须有实际命令与 `exit: 0`；`browser-agent` 通过必须有实际 URL、actions、空的 console/page error 列表，以及至少一个有效截图产物。截图不能代替行为断言。
- `artifacts.path` 相对需求目录，不得指向目录外；文件必须存在且 SHA256 匹配，并登记进当前 `artifact-generation-manifest.json` 的 candidate tree / generation。旧代际截图即使 path/hash 正确也不能挂到新候选。浏览器截图只是必需辅助证据，不是充分通过条件。
- `human.status`：`pending | confirmed | waived | fail`；`confirmed / waived` 必须带 `by`，`waived` 必须带 `note`。自动未验证且未调整分类时只能豁免，不能写 `confirmed` 冒充自动通过。
- 已在关联选择集中执行的业务测试直接采集产物，不为出证据重复执行。完整回归、功能验收与发布风险在报告中分栏，不相互替代。

### 执行后分类调整

只能向更严格方向调整：`auto → auto+human-review → human`；执行方式只能从自动通道调整为 `human`。例如浏览器不可用后由人实际测试：

```json
{
  "id": "AC-2", "oracle": "human", "executor": "human",
  "reclassification": {
    "from_oracle": "auto+human-review", "from_executor": "browser-agent",
    "to_oracle": "human", "to_executor": "human",
    "reason": "environment-unavailable", "note": "浏览器工具不可用，改由人按冻结步骤实测"
  },
  "human": {"status": "confirmed", "by": "<人>", "at": "<时间>", "note": "功能与版式均已确认"}
}
```

调整必须有合法 `reason` 和具体 `note`，进入报告的「AI 分类调整」。不得把 `human` 降为自动判定；确需降低人工要求时回到技术方案修订 `acceptance-plan.yaml` 并重新冻结。

## 3. acceptance-report.md（合并前功能验收报告）

由 `feature_closeout.py report --feature-dir <需求目录> --repo <集成 worktree>` 生成，**不得手改**。报告从冻结计划、实际结果、关联证据与人工确认确定性生成；`check --phase verify|merge` 会逐字核对是否过期。

- 自动阶段完成后生成一次：给出关联自动检查、每条 AC 的 AI 结果、未验证项，以及“哪些仍需人工、为什么、操作步骤与期望”。
- 人工结果写回并记录合并确认后再生成一次：形成最终逐条结果、分类调整、豁免和明确合并建议。
- 报告结论只分事实：不可合并 / 等待自动验收 / 等待人工处理 / 验收条件已满足（有豁免时醒目标注）。报告不代替人的合并、发布或 push 授权。
- 候选 tree 或计划哈希变化后，旧报告不能重新生成，必须重新冻结并刷新结果。

## 4. confirmation.md（人显式确认）

````markdown
# 需求收口人工确认：<feature-slug>

```yaml
candidate: <candidate.json 的 candidate 完整 commit>
candidate_tree: <tree>
main_head: <candidate.json 冻结时的 main 完整 commit>
confirmed-by: <人>
confirmed-at: YYYY-MM-DD HH:MM
quote: "<人在对话里的原话>"
conditions: []        # 合 main 前必须为空；有附条件就先完成并重新确认
scope: merge-main     # 标量且只能是 merge-main；deploy-prod / push 必须另行明确授权
report_sha256: <最终 acceptance-report.md 的 SHA256>
business_results_sha256: <最终 business-results.json 的 SHA256>
```
````

沉默、超时、「看着行」之类非确认语不算确认；确认后候选 tree、main HEAD 或计划哈希变化须重新确认。合 main 与内部 record 只由 `delivery.py promote` 在同一授权进程完成，不提供公开 record-merge 或任意 --commit 入口。

## 7. 集成运行资源与清场

- 实例租约与开发服务记录保存在 integration runtime  registry，绑定 repo / feature / round、candidate、worktree、lease/service ID 和日志代际；business-results.environment 必须引用同一 ID。
- `feature-merged` 后运行 `feature_closeout.py finalize --feature-dir <需求目录> --repo <主工作区>`：核对候选与 feature-merged 事件，逐票 `merged → done` 并写 `accepted`，再逐票 close-and-clean，最后清理 integration worktree / 精确 upstream / 失效代际。`--no-cleanup` 只写状态，用于先完成关账再单独清理。
- 停止服务或释放租约失败、候选不匹配、外部能力未知时保留资源并报告 needs-attention；不得用 `git worktree prune` 冒充集成清场完成。
- 最终 candidate、验收报告、确认、最终 scoped 结果与被引用截图保留；失效代际和 salvage 只能经独立预检确认后清理。


## 5. verify-log.md（候选变更与回修轨迹）

| 时间 | 候选 tree | 计划哈希 | 触发 | 跑了什么 | 结果 | 下一步 |
|---|---|---|---|---|---|---|

## 6. conformance-feature.md

`issue: feature`，并绑定 `candidate: <完整 commit>`、`candidate_tree: <tree>`、`verdict: conform`。多叶子不在各票提前做 feature 全量；全部票 merged/done 后只在本 candidate 统一核一次。YAML 必须声明完整作用域，正文必须有非空覆盖表，且每张票至少一行有效证据：

```yaml
issue: feature
candidate: <完整 commit>
candidate_tree: <tree>
verdict: conform
checker: main-session
checker_session: <实际会话>
date: YYYY-MM-DD
coverage:
  issues: [issue-001, issue-002]       # 与 candidate 冻结票组精确一致
  specs:
    - {path: dev/features/<module>/<slug>/<spec>.md, sha256: <当前 spec SHA256>}
```

| 编号 | 需求点 | 来源（spec 节锚点） | 票 | 判定 | 证据 |
| --- | --- | --- | --- | --- | --- |
| 1 | <feature 行为点> | <spec>#<节> | issue-001 | 已实现 | <candidate HEAD 的代码:符号 + 测试 + verification-supervisor.json#DoD-1> |
| 2 | <跨叶子接缝> | <spec>#<节> | issue-002 | 已实现 | <该票 impl 下的代码:符号 + 测试 + verification-supervisor.json#DoD-1> |

对 tech-spec 全节枚举需求点，对照各票覆盖矩阵并集抓票间接缝缺口。另核 PRD 或 change-request delta + retained baseline AC 全部进入 `acceptance-plan.yaml`，且每条 AC 都有执行方式、最终判定和实际结果，不能用票级 DoD 并集代替需求级功能验收。只有 YAML 头、漏票/漏 spec、空表、未知票或不存在的代码/测试/verification fragment 均不得通过。
