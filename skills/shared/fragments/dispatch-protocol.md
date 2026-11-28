## 派发协议（标准角色配置）

dev 阶段的「派发」只依赖一个宿主无关原语：**主会话通过宿主 agent 新开独立对话执行自包含提示词**。模型、角色、会话句柄、候选和路由回执必须来自同一份 agents-config 与宿主真实返回，不靠提示词口头指定。

### 配置校验与角色解析

主会话开始监督、实现会话启动、发起评审前都先执行：

```bash
python3 ../shared/scripts/agents_config.py validate --config ./dev/agents-config.md
python3 ../shared/scripts/dispatch_config.py validate --host <当前宿主> --config ./dev/agents-config.md --catalog ./dev/dispatch-model-catalog.json
```

执行时必须从当前会话确定宿主，显式传入 `--host`；仅校验该宿主的模型、effort、实时目录及原生角色。其他宿主目录缺失或模型不可用不阻塞当前宿主，不根据模型名称推断宿主。`agents_config.py validate` 是配置结构检查，不代表任一宿主的模型可用性已经验证。

- **主会话监督**：取当前宿主 `supervisor-review`，用 `resolve --role supervisor` 核对实际 model / effort。配置写入不会 retroactively 切换现有会话，不符即停。
- **开发实现**：普通票取 `development.default`；集成 / 高风险及评审预警后的续修取 `development.escalated`，用 `resolve --role development`。不得按测试范围路由。
- **独立评审与派发的符合性核对**：固定 `supervisor-review`，用 `resolve --role review`；必须另开独立对话，写查分离不可降级。
- 原生角色只从配置生成。`render --check` 只读检查缺失或过期；确需更新由 `/setup-dev` 明确重生，不手改第二份模型真源，也不静默覆盖项目配置。
- 旧 dispatch.model、缺字段、当前宿主无条目、模型 / effort 不在实时目录或角色过期即停，不跨宿主套用、不自动降级。

### 派发原语与结构化回报

- 派发 = 主会话启动独立对话执行 `/implement issue-NNN` 或 `/ai-review`；新对话不继承、也不依赖主会话上下文。
- 提示词必须包含：票、tech-spec、agents-config、feature-module / slug、ticket context（开工后）、execution context（有人 / 无人值守）、dispatch_id、parent_session_id，以及必要的 worktree / 分支和报告路径。
- 真实派发前，主会话按 Progress Events 在票 frontmatter 写 `dispatch` 块（host / model / dispatched-at / attempts / dispatch_id / session_id / parent_session_id）并追加 `dispatch` 事件。session ID 尚未分配时可暂空，宿主返回后立即补齐。
- `pr` 模式集成分支的推送按 campaign 授权，由主会话或用户执行，实现子 agent 不 push；`local-human` 仍由用户本机统一 push。
- 成功回报包含分支、worktree、MR、ticket context、verification、process-check-ticket、impl-log 和遗留；它只是线索，不是验收证据。
- 需要人裁决时，实现与评审子会话只返回未持久化 human-decision envelope，不直接问人、不写 returned / mr-submitted。主会话统一持久化、提问和路由。
- resume 沿用 dispatch/session，attempts 不增；redispatch 新开独立对话，主会话更新 dispatch 块、attempts +1 并写新 dispatch 事件。路由没有真实 ack 前票保持 needs-human，结果不明不得重派。

### 宿主适配

| 宿主 | 主会话监督 | 独立对话与原生角色参数 | worktree / 能力边界 |
| --- | --- | --- | --- |
| Claude Code | 项目 settings 的 `model` / `effortLevel` | `Task`；`subagent_type = supervisor-review / development / development-escalated`；环境变量覆盖模型 / effort 时即停 | 同机注册 worktree；session / worktree 活性未知时保留 |
| ZCode | 当前会话须核对 `supervisor-review.model` / `thoughtLevel` | `Agent`；角色 frontmatter 绑定 model / thoughtLevel；复审和回答后可按 agentId 用 `SendMessage` 续接 | 同 Claude Code |
| Codex | 项目 config 的 `model` / `model_reasoning_effort` | 原生多代理；`agent_type` 同上，角色 TOML 绑定模型 / effort | 自管沙箱必须由 adapter 回报路径和可达性；异文件系统不可达时保留唯一分支并报 external-pending |
| DSH | profile `agent-default-model` 的 provider / model / reasoningEffort | `subagent` 显式传 provider / model / reasoning_effort，只取 resolve 选出的 allowedModels | 以真实 adapter 能力为准；主会话 `git worktree prune` 不能冒充异文件系统清理 |

- 未登记宿主先扩展 schema、实时目录与 adapter，不得拿其他宿主模型名凑合。
- frontmatter 中的 `Task` 是 Claude Code 命名；其他宿主映射为自身派发工具，协议语义不变。
- 宿主完全没有独立对话能力时，主会话只能产出自包含提示词与 resolve 结果，请人在对应模型的新对话启动；无法确认实际模型不得开工，也不得在主会话顺便实现。评审没有独立环境时同样停止，绝不自评。
- feature 复审优先续接原评审对话，输入为新 candidate 范围、同目录 `review-handoff.json`、上轮报告与 receipt；续接不得改变 supervisor-review 模型 / effort。handoff 的增量与闭合线索只安排检查顺序，完整 feature diff、全部规定输入与本轮独立判断不缩减。跨 feature 一律新开。票级不再发起独立评审。
- 宿主能力由 `host_adapters.py` 区分 available / unavailable / unknown。配置里写了能力不等于实际可用；评审定稿先 `write_review_binding` 通过宿主原生接口附着本次 issue / round / head / tree / report 绑定，再 `verify_review_session` 实时重读。`host-probes.json` 只保存写回执供对账，单独文件或 capability bool 不构成证明。浏览器不可用时 AC 记 unverified 或转人工，session 活性 unknown 时禁止删除其 worktree。

### 清场触发与统一入口

清场只在**合并与关账事实已确认之后、收尾总结之前**发生；MR ready、子会话退出、Stop hook 或面板轮询都不触发删除。

```bash
# 默认只预检，返回 operation_id 与 confirm
python3 ../shared/scripts/progress.py close-and-clean --repo <主工作区> --issue <本战役票的完整路径>

# 只有预检内容与真实授权一致才执行
python3 ../shared/scripts/progress.py close-and-clean --repo <主工作区> --issue <本战役票的完整路径> \
  --apply --operation-id <预检ID> --confirm <完整指纹>

# 每轮监督先只读对账；只恢复已有授权 journal 或已证明 merged 的残留
python3 ../shared/scripts/progress.py reconcile --repo <主工作区>

# 恢复必须显式分类，不能用 cleanup 恢复其他类型事务
python3 ../shared/scripts/progress.py reconcile --repo <主工作区> --kind cleanup --operation-id <ID> --apply
python3 ../shared/scripts/progress.py reconcile --repo <主工作区> --kind delivery --context <ticket-context.json> --apply
python3 ../shared/scripts/progress.py reconcile --repo <主工作区> --kind delivery --candidate-path <candidate.json> --candidate-repo <集成worktree> --apply
python3 ../shared/scripts/progress.py reconcile --repo <主工作区> --kind human_decision [--feature <module/slug>] --apply
python3 ../shared/scripts/progress.py reconcile --repo <主工作区> --kind runtime --operation-id <service/lease ID> --candidate <完整OID> --apply
```

- 清理器**永不合并代码**，没有 action=merge 或可任意切换的 dev-only。中途搁置只能显式 `--intent discard` 重新预检，先 salvage 再关闭为 deferred，不解锁依赖。
- 目标分支只取 ticket context，不能回退 query / main。worktree、detached、locked、prunable、branchless、remote-only、本地 / 远端 ref、dev、session、实例和服务都由同一份计划覆盖。
- confirm 绑定票 / config、refs、完整 ignored 状态、软链、session / runtime 和 dev GC 项；apply 锁内重验，强制删除前再扫描。活跃会话、日志增长、错误软链或外部能力未知都停止。
- `.logs/**`、未收录证据与报告默认 salvage；dev 软链只有解析后等于主工作区 dev 才不跟随打包。依赖和构建缓存可按计划删除，但最终证据、最后成功代际和被引用捕获物不得删除。
- journal 逐步记录 expected / observed；崩溃后 `reconcile` 按真实状态幂等恢复。部分完成、prune 失败、远端 OID 变化或剩余路径非空都必须报告，不能显示“清理完成”。
- 票合入集成分支只写 `merged` 并随即释放 worktree：`delivery.py merge` 在合并事务完成后内置一次授权 `release-worktree` 清理（门禁 = 票状态 merged + 目标分支祖先关系或精确 merged 记录；salvage `.logs` 等未收集物后删 worktree，branch / dev / 票状态不动）。释放失败或被会话 / runtime 阻塞时合并不受影响，走 `reconcile --kind cleanup --apply` 恢复；票级仍不做 close-and-clean。
- local-human 的 integration worktree / 分支、逐票 branch 与 dev 过程物，统一在 feature-merged 后由 `feature_closeout.py finalize` 清理：逐票 close-and-clean，再停服务 / 释放租约，salvage 并清 Git 资源。finalize 失败保持 needs-attention，可重跑；不能用分支消失反推验收完成。
- 单独处理已完成历史票时仍可用 `close-and-clean --issue`；它只接受真实 `done` 票，不把 `merged` 当作已验收。
