## 票状态、派发字段与进度事件统一契约

机器真源分为三处：票 frontmatter 表示当前交付状态，`dev/features/<feature-module>/<feature-slug>/progress.jsonl` 表示追加事实流，`decisions/<decision-id>.json` 表示人工作决策及其回答 / 路由。三者必须能对账；面板、下钻或新会话接力不得另造第四份状态。

### 1. 票状态

| 状态 | 含义 | 是否视为下游依赖已完成 |
| --- | --- | --- |
| `proposed` | 票清单尚未确认 | 否 |
| `confirmed` | 可进入派发候选，仍需核对 `blocked-by` | 否 |
| `in-progress` | 实现或回修进行中 | 否 |
| `in-review` | 独立评审进行中 | 否 |
| `mr-submitted` | 等待主会话收口 | 否 |
| `needs-human` | 有同票活动 human-decision 包，等待人回答或路由收尾 | 否 |
| `done` | 已按交付流程合并并关账 | **是，唯一会解锁 `blocked-by` 的状态** |
| `deferred` | 明确延期，可另行重开 | 否 |
| `cancelled` | 明确取消，不代表需求已交付 | 否 |

历史展示层可以把 `deferred / cancelled` 归入“已关闭”泳道，但不能把它们计入 `done` 集合。Human Decision 的 `defer / cancel` 只产生上述关闭状态，不删除依赖边，不允许 `next_status=done`。

### 2. `dispatch` 块

主会话在真实派发前写入并维护：

```yaml
dispatch:
  host: zcode
  model: <由 agents-config 解析并绑定的实际模型>
  dispatched-at: 'YYYY-MM-DD HH:MM'
  attempts: 1
  dispatch_id: dispatch-<本次派发的稳定 ID>
  session_id: <宿主会话 ID；新会话尚未分配时可暂空>
  parent_session_id: <发起派发的主会话 ID>
```

- `attempts` 计算**新会话派发次数**，不计算原会话续接。首次派发为 1；打回后新开独立会话、`redispatch` 为 +1；`resume` 续接同一 `session_id` 不变。
- `dispatch_id` 标识一次新派发，用于和 route / 事件 / 宿主回执对账。`resume` 沿用旧 `dispatch_id` 和 `session_id`；`redispatch` 使用新 `dispatch_id`，新 `session_id` 由宿主返回后补齐。
- `parent_session_id` 表示谁发起本次派发，不用旧子会话冒充新子会话的父会话。
- `model` 不能从 decision prompt 猜测，仍由 agents-config 校验、角色解析与宿主原生绑定决定。
- `route_begin` 只给出 `attempts_before / attempts_after` 和 ID 计划；集成层实际派发时才改 `dispatch` 块并写 `dispatch` 事件。路由重放不得重复增加。

### 3. 事件信封与幂等

每行至少包含：

```json
{
  "ts": "YYYY-MM-DD HH:MM",
  "event": "human-decision",
  "issue": "issue-001",
  "event_id": "human-decision:dec-example:resolved:route-...",
  "decision_id": "dec-example",
  "state": "resolved",
  "route_id": "route-..."
}
```

- `ts` 使用本地 `%Y-%m-%d %H:%M` 或带时区 RFC3339；所有 replay / hosts / 对账读取器必须同时正确解析，不能把 RFC3339 当作 epoch 0。包内时间戳统一为带时区的 RFC3339。
- 所有新事件通过 `append_event_once(path, event, event_id)` 写入。同一 ID、完全相同内容重放返回已写；同一 ID 内容不同即冲突，不能覆盖历史。
- `event_id` 由写者根据稳定事实构造。例如每次呈现使用其 `presented_at` 派生，每次路由使用 `route_id`，所以取消后再次提问和明确失败后的重试不会互相覆盖，也不会因重放重复追加。
- 旧事件流若缺少 `event_id` 或含坏行，严格存储会拒绝继续追加。集成层迁移 / 回填时必须先按明确规则补稳定 ID；Human Decision 和对账不得猜测 ID、重写业务含义或跳过损坏后伪装成功。

### 4. 固定事件词表（原 11 个 + 1 个）

不得另造同义事件词：

| 范围 | 事件 | 必须表达的事实 |
| --- | --- | --- |
| 票 | `dispatch` | 新会话派发；附 `host / model / attempts`，并带 `dispatch_id / session_id / parent_session_id`（已分配时） |
| 票 | `returned` | 实现 / 评审子会话结构化回报到达；回报本身不是验收证据 |
| 票 | `verify-fail` | 验收复跑或符合性核对打回；附 `n` 和短摘要 |
| 票 | `mr-submitted` | MR 或本地草案已提交；附 IID / URL 或路径 |
| 票 | `needs-human` | 票进入人工决策；附原因和 `decision_id` |
| 票 | `merged` | 合并已发生并关账；附 `host`、合并 `commit`、精确 `branch` / `target` 与 `source_head`，供清理回退复核 |
| 票 | `circuit-break` | 无人值守熔断及原因 |
| 票 / 需求 | `digest` | 阶段摘要、无人值守心跳或关闭说明；不冒充具体状态迁移 |
| 需求，`issue: feature` | `feature-verified` | local-human 自动阶段完成且报告已生成；未验证项仍明确保留 |
| 需求，`issue: feature` | `feature-confirmed` | 人已完成合并前确认 |
| 需求，`issue: feature` | `feature-merged` | 需求已合入 main；附 commit |
| 票 | `human-decision` | 决策包迁移；必须附 `decision_id / state / route_id`，无路由时 `route_id: null` |

`human-decision.state` 与包状态一致：`pending / waiting / answered / routing / resolved / expired / cancelled / superseded`。`routing` 和 `resolved` 事件附当前 `route_id`；明确失败回到 `answered` 的事件也附失败路由 ID，以区别于首次回答。`resolved` 事件附 `next_status / strategy / attempts`，可作为“ack 后允许并执行票状态迁移”的对应事件。

### 5. 谁改状态，谁写事件

| 动作 | 票 / dispatch 写入者 | 同事务序列中的事件 |
| --- | --- | --- |
| 首次派发、打回后新开会话、decision `redispatch` | 主会话 / 宿主集成层写 `dispatch` 块，`attempts + 1`（首次从 0 到 1） | `dispatch` |
| decision `resume` | 主会话续接原会话，不增加 attempts，不伪造新会话字段 | 不写新的 `dispatch`；只写 `human-decision: routing` |
| 子会话回报 | 主会话接收，不直接据此改验收结论 | `returned` |
| 验收复跑 / 符合性打回 | 主会话更新 `verify-fails` 和票状态 | `verify-fail` |
| MR 提交 | 主会话写 MR 信息和票状态 | `mr-submitted` |
| 新建 human decision | 主会话先写 pending 包，再把票置 `needs-human` | `needs-human` + `human-decision: pending` |
| 呈现 / 未回答中断 / 回答 / 开始路由 / 到期 | 主会话只迁移决策包；票保持 `needs-human` | 对应 `human-decision` 状态 |
| 路由明确失败 | 主会话把包退回 `answered`，票继续 `needs-human` | `human-decision: answered`（带失败 route_id） |
| 收到真实 ack | 主会话先持久化 resolved 包，再把票改为计划 `next_status` | `human-decision: resolved`；若实际发生新派发，另有 `dispatch` |
| 明确延期 / 取消 | 主会话按受控计划写 `deferred / cancelled` | `human-decision: resolved` 或显式 `cancelled`；不得写 `merged` |
| 合并关账 | 主会话执行合并后把票置 `done` | `merged` |
| 对账补写 | 主会话只根据已经持久化的事实修包 / 票 | 用原稳定 `event_id` 补缺失事件，不创造新事实 |

事件不是“谁最后看见谁补一句”的自由日志。写票状态的职责方必须同时写对应事件；崩溃后对账可以按同一稳定 ID 补齐，但不能用摘要事件替代必须存在的状态事件。

### 6. Human Decision 对账口径

- 活动包包括 `pending / waiting / answered / routing / expired`，都应有同票 `needs-human`。`expired` 只是停止接受旧回答，不等于票已解锁。
- `resolved` 包必须含真实 ack；只有它能支持把孤立 `needs-human` 修复为计划的 `next_status`。
- `routing` 没有 ack 时结果未知：保留包和票，向宿主对账，不得重派、增加 attempts 或补 `dispatch`。
- `superseded` 必须由新包的显式 `supersedes` 链接支持；旧答案不迁移。
- `deferred / cancelled / superseded / resolved` 都不是活动包；若票仍为 `needs-human` 且没有另一活动包，对账只能依据真实 ack / 取消记录修复，否则报告冲突。
- 同一票出现多个无关联活动包、孤立 `needs-human`、损坏事件流或无法解释的包路径时，停止自动推进并报告，不猜一个“看起来合理”的状态。
