## Human Decision v1（主会话唯一写者）

本契约处理“必须由一个具体的人选择后才能继续”的单点决策。它不复述监督循环；票的 frontier、验收、合并和清场仍按原流程执行。

### 1. 唯一真源与角色边界

决策包唯一路径：

```text
dev/features/<feature-module>/<feature-slug>/campaigns/<campaign-id>/decisions/<decision-id>.json
```

包的 `schema` 固定为 `ch-agent.human-decision/v1`，结构与跨字段约束由 `shared/references/human-decision.schema.json` 和 `shared/scripts/human_decision.py` 共同校验。`scope.feature` 是 `<feature-module>/<feature-slug>`，`scope.campaign` 是当前 campaign-id（历史根目录包省略），`scope.issue` 是战役内稳定票号；CLI/API 的 feature 定位参数传 `module/feature/campaigns/campaign-id`；`scope.repo` 只能是 `.` 或仓库标签，不能代替 API 的仓库根参数，也不能存绝对路径。

| 角色 | 能做什么 | 不能做什么 |
| --- | --- | --- |
| 实现 / 评审子会话 | 构造请求，调用 `validate_request(envelope)`，把未持久化 envelope 结构化返回主会话 | 写决策包、改票、写事件、调用提问组件、记录回答、开始 / 确认路由 |
| 主会话 | 校验并 `request`，统一 `present` / `answer`，生成受控路由，接收宿主回执，查询与对账 | 代替人选择、猜测模糊自由文本、伪造宿主回执、直接置票 `done` |
| 宿主集成层 | 执行真实的原生提问、会话续接 / 新会话派发、已登记 supervisor operation，提供真实回执 | 把 decision prompt 当 shell 执行、让子会话自行问人、在结果不明时重派 |

所有 Python 写操作都必须显式传 `main_session=True`；缺少该标志即失败，不能只凭 `source.role` 自称主会话。`validate_request` 不写任何文件。CLI 对应标志为 `--main-session`。

无人值守时，主会话可以 `request(..., unattended=True)` 留下包、票和事件，但不得调用原生提问、等待回答或选择推荐项；`present(..., unattended=True)` 也原样返回 `pending`。循环是否继续其他票由监督契约决定，本模块不复制那部分逻辑。

### 2. 请求包与不可变依据

请求包含：

- `id`：小写稳定 ID，只允许字母数字和连字符；路径由模块构造，调用方不能提供文件路径。
- `scope`：`repo / feature / issue`。
- `source`：`host / skill / role / dispatch_id / session_id`，可带 `parent_session_id`；它们只用于续接和关联，不出现在对人的问题里。
- `basis`：`issue_sha256 / head_commit / report_sha256`。回答和开始路由必须传入主会话刚核对的 `current_basis`，三项必须完全一致。变化时旧答案不能套用，必须 `supersede` 创建新包；新包不复制旧答案或路由。
- `question`：`context / decision / why / recommendation / options`。`recommendation` 是一个选项 ID，不是自动答案。
- `evidence`：最多 12 条 `{path, sha256, summary}`。路径必须是仓内相对 POSIX 路径；创建时核对文件摘要。原始日志、内容快照和秘密不进入包。
- `presentation.header`：不超过 12 个字符的业务短标题。
- `expires_at`、`supersedes` 与完整时间戳。

每题 2–4 个互斥选项。选项有稳定 `id`、动作型 `label`、说明代价与风险的 `description`，以及受控 `route`：

| `action` | 允许的计划 | 允许的结果票状态 |
| --- | --- | --- |
| `continue` | `strategy=resume/redispatch`，受控 `role=development/review`、`tier=default/escalated`，以及自包含 `prompt` | `confirmed / in-progress / in-review / mr-submitted` |
| `supervisor` | 仅 `operation`，不得带自由 prompt 或模型路由 | 同上；operation 只可以是 `reconcile-routing / re-split / request-review / collect-evidence`；`redesign` 例外只能去 `proposed` |
| `defer` | 无 prompt、无会话计划 | `deferred` |
| `cancel` | 无 prompt、无会话计划 | `cancelled` |

任何位置都禁止 shell / command / args / env 等执行字段、任意未登记 operation、`next_status=done`、路径逃逸、秘密和大型日志。请求整体不得超过 128 KiB。模块只生成计划，永远不执行 prompt 或 operation。

### 3. 状态机与票状态不变式

```text
pending → waiting → answered → routing → resolved
    ↑        │
    └────────┘  问题卡取消 / Escape / 工具超时（未回答）

pending / waiting / answered --显式且到期 expire--> expired
pending / waiting / answered / expired --显式 cancel--> cancelled
活动包（routing 除外）--supersede--> superseded + 新 pending
```

- 活动状态 = `pending / waiting / answered / routing / expired`。`expired` 不再接受回答，但仍是活动包，继续要求原票保持 `needs-human`，直到显式取消或用新包替代。
- 同一票最多一个活动包。`request` 重放相同不可变内容返回原包；相同 ID 改 basis 或其他请求内容即冲突。新 ID 遇到同票活动包也会被拒绝，除非它明确 `supersedes` 旧包。
- `needs-human` 必须能定位到同票活动包。`request` 先写包，再把票置为 `needs-human` 并补事件。对账发现孤立 `needs-human` 时只报告，不猜之前状态，也不伪造一个包。
- 回答只接受 `waiting`，必须同时记录 `option_id` 和用户原文 `text`。原生组件的标签映射由主会话确定；模糊自由文本应继续澄清，不能猜成选项。
- 只有持久化宿主 ack 后才能进入 `resolved`，随后才允许把票从 `needs-human` 改为计划的 `next_status`。本模块在 `route_ack` 中按此顺序写入；集成层不得提前改票。
- `defer / cancelled` 都不是 `done`，不得删除或改写任何 `blocked-by`，也不得让下游按完成解锁。Human Decision 永远没有直接 `done` 路由。

### 4. 提问投影与未回答语义

`to_question_component(packet)` 返回宿主 `AskUserQuestion` 形状：

```json
{
  "questions": [{
    "header": "订单重试",
    "question": "……2–4 句……",
    "multiSelect": false,
    "options": [
      {"label": "增加防重复处理（推荐）", "description": "……"},
      {"label": "暂缓这张票", "description": "……"}
    ]
  }]
}
```

投影恰好一个问题；推荐项始终第一，标签由模块追加 `（推荐）`。可见字段只有 `header / question / multiSelect / options`，选项只有 `label / description`。不得暴露 decision ID、route ID、会话、内部角色、状态词、哈希、路径或原始日志，也不要求用户“回 A / B”。宿主自动提供的“其他”不重复添加。

只有纯文本降级使用 `to_text_fallback(packet)`：它为同一组选项显示 `1. 2. ...` 并提示“请回复选项编号，或直接说明你的选择”。编号不是持久化选项 ID；记录回答时仍保存稳定 option ID 与原文。

`cancel_presentation` 和 `timeout_presentation` 只把 `waiting` 退回 `pending`，保留“未回答”事实；它们不选择推荐、不取消票、不写 `expired`。迟到的取消 / 超时不能覆盖已存在的回答或路由。只有显式 `expire` 且已到 `expires_at` 才能写 `expired`。`cancel` 是另一项明确的管理操作，表示人要取消本票，绝不能由问题卡关闭动作触发。

### 5. 路由、计数与回执

`route_begin` 需要与回答时相同的 basis，生成并持久化唯一 `route_id` 和计划：

- `resume`：沿用原 `dispatch_id / session_id`，`attempts_after = attempts_before`，供主会话续接同一子会话；不新增一次派发。
- `redispatch`：生成新 `dispatch_id`，`session_id=null` 等宿主分配，`attempts_after = attempts_before + 1`，供主会话新开独立子会话。重放 `route_begin` 返回原 route，不再次计数。
- `supervisor`：只返回已登记 operation，不生成模型 / 会话升级指令。`redesign` 是承重设计被证伪时的受控路由：ack 后票回 `proposed`，不增加 dispatch attempts；主会话局部重跑 `/how`、`/why` 和 tech-spec 候选，新 spec 经 supersedes 接管，受影响票重新过门②并冻结新 context 后才能转 `confirmed`。旧验证、评审、conformance 和 candidate 不得沿用。
- `defer / cancel`：返回受控关闭计划，不增加派发次数。

集成层在实际新派发前，按计划更新票的 `dispatch` 块并追加 `dispatch` 事件；模型 / effort 仍由 agents-config 和宿主原生角色解析，本模块不猜模型。续接不新增 `dispatch` 事件，重派新增。无论哪种路由，ack 前票都保持 `needs-human`。

`route_ack(route_id, ack)` 只接受当前路由的真实宿主回执，先保存 ack 和 `resolved`，再改票及补事件。相同路由重放不能换一份不同回执。

只有宿主明确报告“未执行或已安全失败”时，才能 `route_fail(..., outcome_known=True)`：包回到 `answered`，保留原回答和失败事实，票继续 `needs-human`。之后人可以维持选择并生成新 route，也可以因 basis 变化重开新包。结果不明不得调用 `route_fail`，不得新建 route 或重派。

### 6. 恢复与分步写入对账

新会话接管或发现 `routing` 时，先运行 `reconcile`，再向宿主核对原 `route_id / dispatch_id / session_id`。模块恢复路由时只返回原计划，不会生成第二次派发。

写入顺序保证可修复性：

- 创建：新包 → 旧包替代关系（如有）→ 票 → 幂等事件。
- 回答 / 呈现 / 路由：包 → 幂等事件。
- ack：包含真实 ack 的 resolved 包 → 票 → 幂等事件。
- 替代采用“新包先写”：崩溃可能短暂留下两个有明确 `supersedes` 链接的包，对账可以完成旧包替代；不能反过来让票失去活动包。

`reconcile` 可以：

- 根据活动包补回票 `needs-human` 和缺失事件；
- 根据新包的显式 `supersedes` 链接完成旧包替代；
- 根据已持久化的真实 ack 补写计划中的票状态和 resolved 事件；
- 根据显式取消记录补写取消结果；
- 报告同票多个无关联活动包、孤立 `needs-human`、损坏包和未知路由。

它不能把 `routing` 猜成成功或失败，不能伪造用户回答 / 宿主 ack，不能复活旧 basis，不能替孤立 `needs-human` 选择先前状态。`routing` 且没有 ack 的包会列入 `unknown_routes`，保持不动。

### 7. API 与 CLI 入口

Python：

```text
validate_request                       request / supersede
present / cancel_presentation / timeout_presentation
answer                                 route_begin / route_ack / route_fail
expire / cancel                        show / list
active_for_issue / active_for_feature reconcile
to_question_component / to_text_fallback
```

CLI 子命令与上面同名，路由函数使用连字符：

```bash
python3 human_decision.py validate-request --request envelope.json --repo REPO
python3 human_decision.py request --main-session --repo REPO --request envelope.json [--unattended]
python3 human_decision.py present --main-session --repo REPO --feature M/S --id DECISION [--channel text]
python3 human_decision.py answer --main-session --repo REPO --feature M/S --id DECISION --option-id OPTION --text '用户原话' --current-basis basis.json
python3 human_decision.py route-begin --main-session --repo REPO --feature M/S --id DECISION --current-basis basis.json
python3 human_decision.py route-ack --main-session --repo REPO --feature M/S --id DECISION --route-id ROUTE [--ack ack.json]
python3 human_decision.py route-fail --main-session --repo REPO --feature M/S --id DECISION --route-id ROUTE --reason '宿主明确未执行' --outcome-known
python3 human_decision.py reconcile --main-session --repo REPO [--feature M/S]
```

另有 `expire / cancel / supersede / show / list / active / cancel-presentation / timeout-presentation`。`--request -` 可从 stdin 读取 envelope；`--current-basis` 和 `--ack` 接收 JSON 文件。
