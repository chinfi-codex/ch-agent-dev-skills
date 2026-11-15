## 监督循环（主会话职责，直到合并与清场完成）

票状态、dispatch 块、progress 事件和 human-decision 包统一遵守「票状态、派发字段与进度事件统一契约」。主会话不能用实现回报代替机器证据，也不能把分支删除单独当成已合并证明。

门②通过后，主会话自动进入监督循环，**派出后不停手，直到每票合并、关账和清场都有确定结果**：

1. **取票**：只读票 frontmatter 的 status / blocked-by，重算 frontier。只有全部依赖为 done 才可派发；deferred / cancelled 不解锁。票正文由实现会话自读。
2. **派发**：local-human 首次派发前建立本需求独立集成分支和集成 worktree。按 Dispatch Protocol 为 frontier 票新开独立实现对话；先写 dispatch 块和事件。declared-scope 两两不相交的票可按 parallel-max 并行，有交集或疑义串行，不用关联测试闭包决定并行。
3. **跟踪**：成功结构化回报到达即写 returned。回报只提供分支、worktree、MR、ticket context、verification、receipt、报告和遗留线索，不作为成功依据。needs-human 不是成功回报。
4. **收口**：
   - 先跑 `process_check.py --phase merge`，核对 implementer runner、严格 scope、架构、预算、独立 review receipt 与精确候选；再确认评审结论和 MR ready。
   - 主会话在同一票 worktree 对同一候选执行 `verify_issue.py run supervisor`，只重跑冻结 DoD + fast，输出与 implementer 证据不得矛盾。工作区已被外部回收时按分支重建并验证 dev 软链；异文件系统不可达则保持未验证，不假装复跑。
   - 完成需求符合性核对：先不看覆盖矩阵，从票和 spec 反向枚举，再打开代码逐条核全部 ★；集成票另核 tech-spec 全节与各票矩阵并集。always 每票必核，sampled 由 context digest 确定性决定，集成票不得 skipped。
   - 跑 `process_check.py --phase close`，核 supervisor runner、receipt、conformance、architecture、活动 decision 和候选一致性。
   - 只执行 `python3 ../shared/scripts/delivery.py merge --context <ticket-context.json>`。delivery 统一执行合并、done + merged、journal，并在总结前调用 close-and-clean。不得另跑裸 git/glab merge 或公开 record-merge。
5. **CI 与晋级**：票级 merge / close 永远只核 DoD + fast，本机任何 worktree 不执行 full。server-enforced / cooperative-ci 由 `delivery.py promote` 经 CI API 核 run、artifact、ref、commit/tree、归因与外部能力；分支或 tree 前进即重新验证。server-enforced 能力未知即停，cooperative 输出必须注明无服务端强制。CI 不可用时保持未验证，不降级本机补跑。
6. **打回**：supervisor runner 失败、输出矛盾或 conformance deviate / missing → 不合并，verify-fails +1 并写 verify-fail。未达 2 时退回实现会话，修复后刷新受影响 DoD、聚焦缺口重核、重新评审与 ready；达 2 时创建 human-decision，不再自动打回。
7. **推进与对账**：每轮先运行只读 `progress.py reconcile`，聚合 cleanup、delivery、human_decision 和 runtime 的真实状态。恢复时必须显式分类：`--kind cleanup --operation-id ...`、`--kind delivery --context ...` 或 feature 的 `--kind delivery --candidate-path ... --candidate-repo ...`、`--kind human_decision [--feature ...]` 或 `--kind runtime --operation-id ... --candidate ...`，并只在已有授权时加 `--apply`。完成票但仍有 worktree / ref / dev 残留时走 close-and-clean，不从分支消失反推 merged。全部 done 后按所选模式进入需求收口或总结，总结必须列出每票交付、合并提交、遗留和真实清理结果。
8. **每日 CI 记录（启用时）**：只读最新 run / verdict / proof 与 tree；清单外新失败已由 CI 二分到合入提交时开回修票并创建 human-decision，其他合入不暂停。漏跑只补触发同一 CI workflow；超期隔离项必须向人说明挂起时长、去向和建议。
9. **唯一问人路径**：needs-human、阻塞、verify-fails 达 2、无法解决的冲突或其他越界问题，统一由主会话按 Human Decision 协议 present → answer → route-begin → 原生调用 → route-ack。子会话不直接问人；ack 前不改票，路由失败不盲目重派。其余自修复、评审回修和未达标的复跑打回不叫人。

### 需求收口（local-human）

全部票 done 后按以下顺序执行；所有产物写主工作区 integration/，不写集成 worktree、不提交到集成分支。`executor` 决定谁执行，`oracle` 决定谁最终验收，浏览器操作不能替代人的业务判断。

1. **前提**：全票 done + merged 完整 commit 且为候选祖先、每票 conformance conform、无活动 human-decision；feature 级覆盖落 `conformance-feature.md`；PRD 稳定 AC 全部进入 acceptance-plan.yaml 且 validate-plan 通过。
2. **同步并冻结**：集成 worktree 先同步 main，冲突在集成分支解决；`feature_closeout.py freeze` 绑定候选 tree、计划哈希与快照。main 或候选之后任何前进都必须重新同步、freeze 和验证，旧结果、报告与确认失效。
3. **准备候选环境**：配置了 verify-instance 时由 integration_runtime 获取租约、部署并核 candidate；否则按显式 dev-service 配置启动集成 worktree 实例。运行记录包含 lease/service ID、candidate 和日志代际。浏览器不可用时 AC 记 unverified 或转人工。
4. **自动证据与首次报告**：后台执行 fast → `scoped_select.py`（经 test_slots 包装，direct 全跑、indirect 到预算停发并记 feature deferred，不跑 full）→ command / browser-agent AC 的真实执行与 generation 证据。自动 pass 必须有实际断言；浏览器还需 URL、actions、console/page error 和当前候选截图。清单外失败只做单项 base 归因，本需求引入则开回修票并重新 freeze。
5. **机器门禁**：`feature_closeout.py report` 生成首次 acceptance-report.md，再 `check --phase verify --record`；AC 漏项 / 重复 / 额外、证据失效或运行环境不匹配均不过。
6. **人工验收与最终报告**：先向人展示实际通过、失败、未验证、待人工原因及每条操作与预期。判据不足只能向 auto+human-review / human 严格调整并写 reclassification；未执行只能真人实测或显式豁免。人报问题回第 2 步。明确 `merge-main` 原话、时间、候选 tree 和无附加条件写入 confirmation.md，再重新 report；沉默、取消或超时不算确认。
7. **合 main**：主会话只执行：
   ```bash
   python3 ../shared/scripts/delivery.py promote \
     --candidate <integration/candidate.json> --candidate-repo <集成 worktree> --repo <主工作区>
   ```
   delivery 在同一授权进程内复核、合并和内部 record，不接受公开 record-merge 或任意 --commit。最终结果 tree 必须等于候选；push / Prod 不在本授权内。
8. **集成清场**：feature-merged 后运行 feature 级 close-and-clean。确认候选后无新提交、无共享需求，先停开发服务、释放实例租约，再 salvage、清 integration worktree / 精确 upstream 和失效代际；任何一步失败都报告 needs-attention。
9. **发布与兜底**：push、Prod dry-run/sync、只读冒烟和 rollback 均需人另行明确授权；main push 后 CI full 是安全网。新失败归因到本需求则开回修票，影响 Prod 时提议 revert / 回滚，人确认后执行。

### 无人值守模式（人离场自动跑）

- **授权落盘**：`unattended.md` 写明可自主事项、禁止事项、预算和停止条件，经人过目后开跑。默认禁止 deny 命中、改 DoD、绕过 scope / 粒度、执行本机 full、合 main、Prod、push 和越过停止条件；额度阈值未指定时默认 80%。
- **队列即 DAG**：不另建队列文件；同一项目只跑一个循环，离场默认串行，只有授权明确允许才按 parallel-max。
- **无人值守不问人**：越界问题只创建 human-decision 包并保持 needs-human，写清业务问题与推荐方向，继续其他无依赖票；不调用 AskUserQuestion、不等待回答。
- **评审预警**：达到 review-round-alert 时主会话判断拆票、首轮漏报或模型能力，写 digest，必要时续修改用 escalated，不停等人。
- **进度与恢复**：只信票 frontmatter + progress.jsonl + decisions/*.json + journal。新会话先 reconcile，再从 frontier 续跑；routing 无 ack 不得重派，旧 needs-human 缺包必须报告契约错误并重建问题。
- **停止**：frontier 空且全 done、剩余票全部等待 / 阻塞、预算 / 时限到或熔断时停止；写 digest、撤销本次心跳并汇总。暂停单个票不等于停止整个循环。
- **熔断即停**：额度达阈值、耗尽、持续限流、鉴权失败，或连续 2 次心跳无实质推进，立即停止派发与合并、写 circuit-break、撤销心跳，不安排自动 retry。
- **心跳从紧**：优先主会话持续监督；只能定时唤醒时间隔不短于 2 小时且上一轮必须有实质进展，续跑另起新会话，不反复唤醒同一长会话。
- **离场前提醒**：电脑不休眠、宿主权限不等人点确认、模型额度与本次预算 / 停止阈值。合 main、Prod、push 永远不因 unattended.md 获得授权。
