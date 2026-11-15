# @ch-agent/dsh-progress-panel

DSH 右侧栏开发进度面板：分支树与需求 × issue 泳道共用票、事件、会话和活动决定包投影。面板只读展示 `decision` / `contract_error`，不提供第二个人工批准入口。

## HTTP 与清理

- `GET /api/ch-progress/payload`、`GET /api/ch-progress/swim`：20 秒缓存的只读视图。
- `GET /api/ch-progress/summary?force=1`：宿主模型摘要，5 分钟缓存；密钥只经环境传递。
- `POST /api/ch-progress/close-clean/preview`：`{repo, issue|feature_dir, intent}`，生成 operation ID 与确认指纹。
- `POST /api/ch-progress/close-clean/apply`：同一对象加 `operation_id`、`confirm`，要求 `x-ch-progress-action: apply`。
- `POST /api/ch-progress/reconcile`：只读聚合 cleanup、delivery、human_decision、runtime；提供方失败分类展示。
- `POST /api/ch-progress/reconcile/apply`：恢复指定 operation ID，要求相同确认头；CLI 另需显式 `--kind`。
- 仅支持 POSIX（macOS/Linux）真实文件锁和进程组；Windows 明确拒绝清理/恢复，不提供伪回退。超时终止整个进程组并等待退出，结果不明时保持占用并要求对账。
- 旧 `close`、`cleanup/*` 和 merge/action 路由均返回 404。清理永不合并代码；stale/锁冲突返回 409，保护资源返回 403 且客户端不给执行按钮。

`repo` 仅接受 profile 中登记的根目录。客户端每 10 秒轮询，写操作后同时失效两个视图缓存。操作结果展示 journal 状态、operation ID、salvage 和 `remaining_paths`；有 warning 即为 `needs-attention`，不会显示为完成。

## 数据契约

- 票 frontmatter 与 `progress.jsonl` 提供状态和过程事实；`done` 时间优先取 merged 事件，其次取 status 注释日期。
- `needs-human` 必须能投影到 `human_decision.active_for_issue` 的活动包；缺包或坏包显示契约错误，旧 needs-human note 不再是决定真源。
- 会话绑定区分主仓、实际 worktree、完整分支、issue 和 revision；已 release 的绑定不进入活动视图。
- 依赖分 `internal` / `external` / `body` / `milestone`，目标缺失保留 `resolved:false`，不跨需求猜同号票。

## 清理与资源边界

- 普通 `close` 只接受真实 done 且已并入票目标分支，或有完整 OID、可重验的精确 merged 记录。目标只由 `ticket_context.target_branch` 解析。
- 非 done 必须显式 `discard`；先保存 branch.patch、未提交补丁和文件，再删除资源并将票置 deferred，不解锁依赖。
- Saga 状态：authorized → quiesced → salvaged → accounted → worktree-removed → refs-updated → dev-compacted → completed。journal 在 Git common dir 的 `agent-dev/operations/`。
- worktree 确认包含全量 ignored 快照；`.logs` 默认 salvage。正确 dev 软链不跟随，broken/wrong-target 拒绝。活动会话、活性未知、日志增长或未停服务阻止删除。
- 主工作区、main/target/integration、backup、codex/preserve、bare 和无精确 journal 的 detached/branchless 资源受保护。remote-only 不降级 dev-only；本地 ref 用 expected-old CAS，远端仅处理 OID 未变的精确 upstream。
- 产物只按 generation manifest 的 supersedes 链清理；当前、running、最近成功、活动引用、staging 和唯一恢复包保留。无 manifest 不按 mtime 猜。

## 验证

```bash
python3 -m unittest -v \
  skills/shared/scripts/tests/test_progress_cleanup.py \
  skills/shared/scripts/tests/test_progress_swim.py \
  skills/shared/scripts/tests/test_artifact_gc.py \
  skills/shared/scripts/tests/test_integration_runtime.py \
  skills/shared/scripts/tests/test_host_adapters.py
python3 -I skills/shared/scripts/progress.py close-and-clean --repo /path/to/repo --issue issue-001
python3 -I skills/shared/scripts/progress.py reconcile --repo /path/to/repo --operation-id <id>
```
