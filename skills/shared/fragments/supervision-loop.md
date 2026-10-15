## 监督循环（主会话职责，直到完成合并）

门②（票清单确认）通过后，主会话自动进入监督循环，**派出后不停手，监督到每张票合并完成**：

1. **取票**：只读 `./dev/features/<feature-module>/<feature-slug>/issues/` 票文件的 frontmatter（`status` / `blocked-by`），重算 frontier（blocked-by 全部 `done` 的票）；票正文由被派发的实现对话自读，主会话不读
2. **派发**：按「派发协议」宿主适配机制为 frontier 票新开独立对话跑 `/implement issue-NNN`；frontier 中 `declared-scope` 两两不相交（含各自 scoped 反向闭包不触碰对方文件）的票按 `dispatch.parallel-max` 并行派发、各自 worktree；有交集或判定有疑义的串行。并行票合入按完成先后，后合者合入前 rebase 一次
3. **跟踪**：被派发对话的结构化回报（分支名 / worktree 路径 / MR IID 或草案路径 / 证据与评审报告路径 / 遗留 Medium-Low）只是线索，不作为成功依据
4. **收口**：先跑**流程核对**：`python3 ../shared/scripts/process_check.py --issue <票文件> --repo <票 worktree> --phase merge --json <impl/<issue-id>/process-check-merge.json>`——红线登记、漂移登记、档位下限（阈值 / 集成票）、evidence 头 `tier-executed` 达到 `tier-target`、`full-commit` 与 HEAD 的本票文件一致、`all-passed: true` 一次判完，退出码非 0 即退回实现对话补齐，不进合并；主会话不再手工逐字段核对 evidence 头。再核对评审报告头部结论 `pass` / `pass-with-notes`、MR ready（评审结论只判代码，测试执行状态以本步核对为准）。然后**验收复跑**（回执只是主张，不是证据）：主会话亲自在本票 worktree 内重跑 evidence 记录的每条 DoD 验收命令与 `quality-gate.fast`，退出码须全为 0 且输出与 evidence 记录无明显矛盾；worktree 已被实现对话自清时按分支名重建（`git worktree add .worktrees/<issue-id> feat/<feature-slug>-<issue-id>`）。scoped / full 档套件不重跑，以 evidence 档位门记录为准；复跑若涉及重套件（如人工指派的 scoped 抽查），同样经 `test_slots.py run` 包装并后台轮询（形态见 /implement Step 5）。复跑通过才执行合并（gitlab：`glab mr merge <IID> --delete-source-branch`；local：主工作区 `git merge --no-ff feat/<feature-slug>-<issue-id>`），票置 `done`，按「派发协议」清场序列收尾；合并冲突先在 worktree 内 rebase `main-branch`、重跑 `quality-gate.fast` 再合
5. **复跑打回**：复跑任一命令失败或输出与 evidence 明显矛盾 → 不合并：票 frontmatter `verify-fails` 计数 +1，失败命令与实际输出按派发协议派回实现对话修复（等同 BLOCKER 回修路径）；`verify-fails` 达 2 → 不再自动打回，票置 `needs-human` 按第 7 步处理
6. **推进**：每轮先做 worktree 对账（`git worktree list --porcelain` 与票状态比对，票已 `done` 但 worktree 仍在的按清场序列补删；`in-review` / `needs-human` 票的保留并列入总账），再重算 frontier 回到第 1 步；全部 `done` 后回显总账（每票：合并 commit / 证据与评审报告路径 / worktree 已清或保留原因 / 遗留 Medium-Low），输出完成状态
7. **唯一暂停条件**：回报 `needs-human` / `阻塞`、`verify-fails` 达 2、合并冲突自动 rebase 后仍无法解决、其他无法自行裁决的问题——与人单点确认后再继续；其余（含 BLOCKER 回修、fast 自修复、未达标的复跑打回）一律不问人

### 无人值守模式（人离场自动跑）

监督循环默认有人值守；人显式宣布离场（如「离场自动跑」）时进入本模式：

- **授权落盘**：先把本次授权边界写进 `./dev/features/<feature-module>/<feature-slug>/unattended.md` 给人过目——可自主做的事（默认 = 监督循环全部动作：派发 / 验收复跑 / 打回修复 / 合并 / 清场）、绝不可做的事（默认 = 命中 `agents-config.redlines`、改票 DoD 或档位、越过停止条件）、停止条件（下方三项 + 人给的预算 / 时限）。落盘即授权记录，也是崩溃后接力的依据
- **队列即 DAG**：不另建队列文件——门②确认的票清单 + 阻塞边即队列，每轮按第 1 步重算 frontier 取票
- **越界决策不擅自拍板**：遇到授权边界外又必须决定的事 → 决策内容与推荐方向写进 `decisions.md`，该票置 `needs-human`，继续跑其他票
- **评审轮次预警**：同票评审达 `review-round-alert` 轮 → 主会话读最近两轮 BLOCKER，判断是范围过大（拆出后续票）、首轮漏报堆积（要求评审一次列全）还是模型能力不足（续修改用 `escalated` 模型）；判定写进 `progress.md`，不在此停等人
- **进度留痕**：每关一票、每打回一次、每张 `needs-human`，都在 `progress.md` 追加一行（票号 / 结果 / 合并 commit / 时间）；`progress.md` + 票 frontmatter 是新会话接力的唯一依据
- **暂停改写为记录**：第 7 步的暂停条件在本模式下不中断循环——记 `decisions.md`、票置 `needs-human` 后继续跑其他票；命中**停止条件**才停：frontier 空且全部 `done`（正常完成）/ 剩余票全部 `needs-human` 或 `阻塞` / 人给的预算或时限到。停止后写 `progress.md` 总结行并回显总账
- **离场前提醒人**：电脑不休眠、宿主权限模式不等人点确认、模型额度限制
- **恢复**：新会话或崩溃重启后读 `unattended.md` + `decisions.md` + `progress.md` + 票 frontmatter，从 frontier 重算处续跑，不从头再来
