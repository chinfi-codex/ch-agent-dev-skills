## 监督循环（主会话职责，直到 feature candidate 合入与 finalize 完成）

票状态、dispatch 块、progress 事件和 human-decision 包统一遵守「票状态、派发字段与进度事件统一契约」。主会话不能用实现回报代替机器证据，也不能把分支删除单独当成已合并证明。

门②通过后，主会话自动进入监督循环，**派出后不停手，直到每票合入集成分支、feature candidate 收口和清理都有确定结果**：

1. **取票**：只读票 frontmatter 的 status / blocked-by，重算 frontier。依赖为 `merged` 或 `done` 才算满足；`merged` 表示依赖代码已在集成分支上，`done` 表示已通过 feature 验收。deferred / cancelled / needs-human 不解锁。
2. **派发**：local-human 首次派发前建立本需求独立集成分支（从当前 main 建，`feat/<module>/<feature>/<campaign>`）和集成 worktree。新战役使用独立 campaign 目录和集成分支；feature-slug 保持稳定，历史在途根目录继续原约定。按 Dispatch Protocol 为 frontier 票新开独立实现对话；先写 dispatch 块和事件。declared-scope 两两不相交的票可按 parallel-max 并行，有交集或疑义串行，不用关联测试闭包决定并行。
3. **跟踪**：成功结构化回报到达即写 returned。回报只提供分支、worktree、ticket context、verification、process-check-ticket、impl-log 和遗留线索，不作为成功依据。needs-human 不是成功回报。
4. **票级收口**：
   - 实现会话已执行 `process_check.py --phase ticket`；主会话复核其 JSON、implementer verification、票 scope 和活动 decision，不重复发起独立评审、supervisor 复跑或 conformance。
   - 只执行 `python3 ../shared/scripts/delivery.py merge --context <ticket-context.json>`。delivery 统一复核 target 未前进、票级门禁、implementer 证据与活动 decision，执行 no-ff 合并，写 `merged` 事件并**保留** worktree/branch。
   - `merged` 解锁下游，但不计入需求验收完成；只有 feature finalize 写 `accepted` 后才把票置 `done`。
   - 不得另跑裸 git / glab merge、公开 record-merge，也不得在票级调用 close-and-clean。
5. **CI 与晋级**：票级 merge 永远只核 DoD + fast，本机任何 worktree 不执行 full。server-enforced / cooperative-ci 由 `delivery.py promote` 经 CI API 核 run、artifact、ref、commit/tree、归因与外部能力；分支或 tree 前进即重新验证。server-enforced 能力未知即停，cooperative 输出必须注明无服务端强制。CI 不可用时保持未验证，不降级本机补跑。
6. **打回**：票级 process-check、implementer runner 失败、输出矛盾或活动 decision → 不合并，verify-fails +1 并写 verify-fail。未达 2 时退回实现会话，修复后刷新受影响 DoD 并重跑票级门禁；达 2 时创建 human-decision，不再自动打回。feature 级评审 BLOCKER 另按需求收口处理。
7. **推进与对账**：每轮先运行只读 `progress.py reconcile`，聚合 cleanup、delivery、human_decision 和 runtime 的真实状态。恢复时必须显式分类：`--kind cleanup --operation-id ...`、`--kind delivery --context ...` 或 feature 的 `--kind delivery --candidate-path ... --candidate-repo ...`、`--kind human_decision [--feature ...]` 或 `--kind runtime --operation-id ... --candidate ...`，并只在已有授权时加 `--apply`。完成票但仍有 worktree / ref / dev 残留时，在 feature finalize 统一处理；不从分支消失反推 merged。全部票 merged/done 后进入需求收口。
8. **每日 CI 记录（启用时）**：只读最新 run / verdict / proof 与 tree；清单外新失败已由 CI 二分到合入提交时开回修票并创建 human-decision，其他合入不暂停。漏跑只补触发同一 CI workflow；超期隔离项必须向人说明挂起时长、去向和建议。
9. **唯一问人路径**：needs-human、阻塞、verify-fails 达 2、无法解决的冲突或其他越界问题，统一由主会话按 Human Decision 协议 present → answer → route-begin → 原生调用 → route-ack。子会话不直接问人；ack 前不改票，路由失败不盲目重派。其余自修复、复跑打回和未达标的验证不叫人。

### 需求收口（local-human）

全部票 `merged` 或 `done` 后按以下顺序执行；所有产物写主工作区 `integration/[round/]`，不写集成 worktree、不提交到集成分支。`executor` 决定谁执行，`oracle` 决定谁最终验收，浏览器操作不能替代人的业务判断。

1. **前提**：全票 merged/done + 每票 merged commit 完整且为候选祖先、无活动 human-decision；`freeze` 只冻结候选与票组，**不再要求每票 conformance**。PRD 或 change-request delta + retained baseline AC 全部进入 acceptance-plan.yaml 且 validate-plan 通过。
2. **同步并冻结**：集成 worktree 先同步 main，冲突在集成分支解决；`feature_closeout.py freeze` 绑定 candidate / tree / 票组与计划哈希（新流程 --feature-dir 指当前 campaign；可选 `--round <执行轮次> --issues <本战役全部非 deferred 票>`，不改 feature-slug 或集成分支）。main 或候选之后任何前进都必须重新同步、freeze 并重跑 feature 门。
3. **整需求独立评审**：在冻结候选上发起**一次** `/ai-review`，评审 `main_head..candidate` 全 diff，按票 merge commit 分段并整体判断跨票契约。评审者必须独立于主会话和全部票 implementer；产出 `integration/feature-review.md` 与 `integration/feature-review-receipt.json`（`review_receipt.py create-feature`）。critical/high 未闭合或 verdict=blocked 即阻塞。
4. **候选 supervisor 复跑**：在集成 worktree 执行：
   ```bash
   python3 ../shared/scripts/verify_issue.py run-candidate \
     --candidate <integration/candidate.json> --repo <集成 worktree> \
     --identity-json <supervisor 身份>
   ```
   对每票冻结 DoD + fast 真实执行一次，写 `integration/raw/<generation>/<issue>/verification-supervisor.json` 与 `integration/candidate-verification.json`；不覆盖票级 implementer manifest。任一失败阻塞。
5. **Feature conformance**：完成 `integration/conformance-feature.md`，绑定 candidate/tree；按 tech-spec 全节与全部票覆盖矩阵并集逐条核对，coverage.issues / specs 精确覆盖本轮票与方案，每票至少一行有效证据。票级 conformance 不再必做，票级矩阵保留在 impl-log 供此处核对。
6. **准备候选环境与自动证据**：配置了 verify-instance 时由 integration_runtime 获取租约、部署并核 candidate；否则按显式 dev-service 配置启动集成 worktree 实例。后台执行 fast → `scoped_select.py`（经 test_slots 包装，direct 全跑、indirect 到预算停发并记 feature deferred，不跑 full）→ command / browser-agent AC 的真实执行与 generation 证据。自动 pass 必须有实际断言；浏览器还需 URL、actions、console/page error 和当前候选截图。清单外失败只做单项 base 归因，本需求引入则开修复票并重新 freeze。
7. **机器门禁与首次报告**：`feature_closeout.py report` 生成 acceptance-report.md，再 `check --phase verify --record`。该门同时核 feature review receipt、candidate-verification、conformance-feature、票状态 / merged 祖先、AC 漏项 / 重复 / 额外和证据环境；任一项失败不过。
8. **人工验收与最终报告**：先向人展示实际通过、失败、未验证、待人工原因及每条操作与预期。判据不足只能向 auto+human-review / human 严格调整并写 reclassification；未执行只能真人实测或显式豁免。人报问题回第 2 步重新 freeze、评审与复跑。明确 `merge-main` 原话、时间、候选 tree 和无附加条件写入 confirmation.md，再重新 report；沉默、取消或超时不算确认。
9. **合 main**：主会话只执行：
   ```bash
   python3 ../shared/scripts/delivery.py promote \
     --candidate <integration/candidate.json> --candidate-repo <集成 worktree> --repo <主工作区>
   ```
   delivery 在同一授权进程内复核 review receipt / candidate verification / conformance / 报告 / 确认哈希，合并、内部 record，并把候选 `merged` 票写成 `done` + `accepted`。不接受公开 record-merge 或任意 `--commit`。最终 tree 必须等于候选；push / Prod 不在本授权内。
10. **finalize 与集成清场**：feature-merged 后运行：
    ```bash
    python3 ../shared/scripts/feature_closeout.py finalize \
      --feature-dir <主工作区 feature 目录> --repo <主工作区> [--round <round名>]
    ```
    确认候选、feature-merged 事件和主 HEAD 一致；逐票 close-and-clean，再清理 integration worktree / 精确 upstream / 失效代际（多轮复用目录必须带本轮 `--round`，finalize 只清本轮候选与分支，不动旧轮结论产物）。任何一步失败都保持 needs-attention，不能用总结掩盖残留。
11. **发布与兜底**：push、Prod dry-run/sync、只读冒烟和 rollback 均需人另行明确授权；main push 后 CI full 是安全网。新失败归因到本需求则开修复票，影响 Prod 时提议 revert / 回滚，人确认后执行。

### 无人值守模式（人离场自动跑）

- **授权落盘**：`unattended.md` 写明可自主事项、禁止事项、预算和停止条件，经人过目后开跑。默认禁止 deny 命中、改 DoD、绕过 scope / 粒度、执行本机 full、合 main、Prod、push 和越过停止条件；额度阈值未指定时默认 80%。
- **队列即 DAG**：不另建队列文件；同一项目只跑一个循环，离场默认串行，只有授权明确允许才按 parallel-max。
- **无人值守不问人**：越界问题只创建 human-decision 包并保持 needs-human，写清业务问题与推荐方向，继续其他无依赖票；不调用 AskUserQuestion、不等待回答。
- **评审预警**：feature 评审多轮 blocked 时主会话判断拆票、首轮漏报或模型能力，写 digest，必要时续修改用 escalated，不停等人。
- **进度与恢复**：只信票 frontmatter + progress.jsonl + decisions/*.json + journal。新会话先 reconcile，再从 frontier 续跑；routing 无 ack 不得重派，旧 needs-human 缺包必须报告契约错误并重建问题。
- **停止**：frontier 空且全部票 merged/done、feature candidate 已 finalize，或剩余票全部等待 / 阻塞、预算 / 时限到或熔断时停止；写 digest、撤销本次心跳并汇总。feature 收口需要人确认 merge-main 时按授权停在该门，不自行越过。
- **熔断即停**：额度达阈值、耗尽、持续限流、鉴权失败，或连续 2 次心跳无实质推进，立即停止派发与合并、写 circuit-break、撤销心跳，不安排自动 retry。
- **心跳从紧**：优先主会话持续监督；只能定时唤醒时间隔不短于 2 小时且上一轮必须有实质进展，续跑另起新会话，不反复唤醒同一长会话。
- **离场前提醒**：电脑不休眠、宿主权限不等人点确认、模型额度与本次预算 / 停止阈值。合 main、Prod、push 永远不因 unattended.md 获得授权。
