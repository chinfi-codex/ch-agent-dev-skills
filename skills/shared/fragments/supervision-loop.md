## 监督循环（主会话职责，直到完成合并）

门②（票清单确认）通过后，主会话自动进入监督循环，**派出后不停手，监督到每张票合并完成**：

1. **取票**：只读 `./dev/features/<feature-module>/<feature-slug>/issues/` 票文件的 frontmatter（`status` / `blocked-by`），重算 frontier（blocked-by 全部 `done` 的票）；票正文由被派发的实现对话自读，主会话不读
2. **派发**：按「派发协议」宿主适配机制为 frontier 票新开独立对话跑 `/implement issue-NNN`；frontier 中 `declared-scope` 两两不相交（含各自 scoped 反向闭包不触碰对方文件）的票按 `dispatch.parallel-max` 并行派发、各自 worktree；有交集或判定有疑义的串行。并行票合入按完成先后，后合者合入前 rebase 一次
3. **跟踪**：被派发对话的结构化回报（分支名 / worktree 路径 / MR IID 或草案路径 / 证据与评审报告路径 / 遗留 Medium-Low）只是线索，不作为成功依据
4. **收口**：先跑**流程核对**：`python3 ../shared/scripts/process_check.py --issue <票文件> --repo <票 worktree> --phase merge --json <impl/<issue-id>/process-check-merge.json>`——红线登记、漂移登记、档位下限（阈值 / 集成票）、evidence 头 `tier-executed` 达到 `tier-target`、`full-commit` 与 HEAD 的本票文件一致、`all-passed: true`、impl-log 需求覆盖矩阵一次判完，退出码非 0 即退回实现对话补齐，不进合并；主会话不再手工逐字段核对 evidence 头。再核对评审报告头部结论 `pass` / `pass-with-notes`、MR ready（评审结论只判代码，测试执行状态以本步核对为准）。然后**验收复跑**（回执只是主张，不是证据）：主会话亲自发起复跑（适用远端协议时，通过remote_tests在判读机固定本票提交执行；禁止本机复跑）；重跑 evidence 记录的每条 DoD 验收命令与 `quality-gate.fast`，退出码须全为 0 且输出与 evidence 记录无明显矛盾；worktree 已被实现对话自清时按分支名重建（`git worktree add .worktrees/<issue-id> feat/<feature-slug>-<issue-id>`）。scoped / full 档套件不重跑，以 evidence 档位门记录为准；复跑若涉及重套件（如人工指派的 scoped 抽查），同样经 `test_slots.py run` 包装并后台轮询（形态见 /implement Step 5）。复跑通过后执行**需求符合性核对**（主模型校验，与 ai-review 正交——评审判「写得好不好」，本步判「要的有没有、偏没偏」，专门抓 diff 与退出码里看不见的缺席与偏差）：
   - **反向枚举先行**：先不看 impl-log 覆盖矩阵，从票「要做什么 / 不做什么」+ tech-spec 锚点节独立枚举需求点清单，防止被实现方叙事带偏
   - **逐条核映射**：对照 impl-log「需求覆盖矩阵」，打开映射指向的代码位置核对语义（不只看 evidence 退出码；至少覆盖全部 ★ 关键路径行），逐条判 `已实现 / 语义偏差 / 未实现`；只照抄矩阵不打开代码等于没核
   - **集成票加做 feature 级核对**：对 tech-spec 全节（不只票锚点）枚举 feature 级需求点，对照本 feature 各票覆盖矩阵的并集，抓票间接缝缺口（A 票以为 B 票做）
   - 结论按模板 `shared/templates/conformance.md` 落盘 `impl/<issue-id>/conformance-<issue-id>.md`（`verdict: conform / deviate / missing` + `commit`；`deviate / missing` 逐条标打回原因分类：未实现 / 语义偏差 / DoD 本身窄，供复盘归因）
   - **执行身份**：默认主会话亲核（输入有界：票全文 + spec 锚点节 + diff stat + 覆盖矩阵 + 抽查代码，不读全量代码）；上下文紧张时按派发协议开独立对话执行（模型取 `dispatch.model` escalated 首选），主会话只读结论；核对产物全部落盘，上下文只留结论行
   - **分档触发**按 `conformance.mode`：`always`（默认）每票必核；`sampled` 时实测 light 档票抽查（未抽中写 `verdict: skipped` 落盘留痕），scoped / full / 集成票一律必核
   - 核对通过后跑**收口流程核对** `--phase close`（= `--phase merge` 全部项 + conformance 文件存在、`verdict: conform`、commit 与 HEAD 本票文件一致），通过才执行合并（gitlab：`glab mr merge <IID> --delete-source-branch`；local：主工作区 `git merge --no-ff feat/<feature-slug>-<issue-id>`），票置 `done`，按「派发协议」清场序列收尾；合并冲突先在 worktree 内 rebase `main-branch`、重跑 `quality-gate.fast` 再合
5. **打回**（验收复跑失败 / 符合性核对未过，共用 `verify-fails` 计数）：复跑任一命令失败或输出与 evidence 明显矛盾，或符合性核对 `deviate / missing` → 不合并：票 frontmatter `verify-fails` +1；未达 2 时票从 `mr-submitted` 退回 `in-progress`，失败命令与实际输出（或缺口清单）按派发协议派回实现对话修复（等同 BLOCKER 回修路径）；修复后重跑受影响 DoD 命令、聚焦缺口条目与回修 diff 重核，重新过评审并转 ready 后再置 `mr-submitted`，再跑 `--phase close` 复核；`verify-fails` 达 2 → 不再自动打回，票置 `needs-human` 按第 7 步处理
6. **推进**：每轮先做 worktree 对账（`git worktree list --porcelain` 与票状态比对，票已 `done` 但 worktree 仍在的按清场序列补删；`in-review` / `needs-human` 票的保留并列入总账），再重算 frontier 回到第 1 步；全部 `done` 后向用户做收尾总结（语言按「完成状态协议 · 对人表达」）：先一句话说整体结果（共几票、全合并了还是有关卡），再逐票一句大白话（这票交付了什么、合在哪个提交、有什么遗留要注意、worktree 已清或为何保留），最后列证据与评审报告路径索引（冠以「想细看：」）
6a. **每日回归结果接收**：每轮读 `daily-regression.record-dir` 最新记录——有清单外新失败且已二分到合入提交 → 对应票置 `needs-human`、按派发协议开回修票进 frontier，合入不暂停；存在进行中 feature 而最近记录早于 `max-gap-days` → 空闲时（无票在跑重套件）补跑一次，按每日回归同一流程落盘
7. **唯一暂停条件**：回报 `needs-human` / `阻塞`、`verify-fails` 达 2、合并冲突自动 rebase 后仍无法解决、其他无法自行裁决的问题——与人单点确认后再继续；其余（含 BLOCKER 回修、fast 自修复、未达标的复跑打回）一律不问人

### 无人值守模式（人离场自动跑）

监督循环默认有人值守；人显式宣布离场（如「离场自动跑」）时进入本模式：

- **授权落盘**：先把本次授权边界写进 `./dev/features/<feature-module>/<feature-slug>/unattended.md` 给人过目——可自主做的事（默认 = 监督循环全部动作：派发 / 验收复跑 / 符合性核对 / 打回修复 / 合并 / 清场）、绝不可做的事（默认 = 命中 `agents-config.redlines`、改票 DoD 或档位、越过停止条件）、**预算与停止条件**（人给的费用 / token / 时长预算与时限；额度停止阈值人未指定时默认 = 账号额度用量 ≥80% 即停，其余停止条件见下）。用大白话写，让人看完能直接说「可以」或指出哪条不行；落盘即授权记录，也是崩溃后接力的依据。**预算与停止条件未经人过目确认，不开跑**
- **队列即 DAG**：不另建队列文件——门②确认的票清单 + 阻塞边即队列，每轮按第 1 步重算 frontier 取票
- **单循环、默认串行**：同一项目同时只跑一个无人值守循环——开跑前先确认没有仍在运行的旧循环或遗留定时唤醒，有则先停掉再进入本模式；离场期间并行派发上限按 1（串行）执行，仅当人在授权里明确同意并行时才按 `dispatch.parallel-max`
- **越界决策不擅自拍板**：遇到授权边界外又必须决定的事 → 决策内容与推荐方向写进 `decisions.md`，该票置 `needs-human`，继续跑其他票
- **评审轮次预警**：同票评审达 `review-round-alert` 轮 → 主会话读最近两轮 BLOCKER，判断是范围过大（拆出后续票）、首轮漏报堆积（要求评审一次列全）还是模型能力不足（续修改用 `escalated` 模型）；判定写进 `progress.md`，不在此停等人
- **进度留痕**：每关一票、每打回一次、每张 `needs-human`，都在 `progress.md` 追加一行（票号 / 结果 / 合并 commit / 时间）；`progress.md` + 票 frontmatter 是新会话接力的唯一依据
- **暂停改写为记录**：第 7 步的暂停条件在本模式下不中断循环——记 `decisions.md`、票置 `needs-human` 后继续跑其他票；命中**停止条件**才停：frontier 空且全部 `done`（正常完成）/ 剩余票全部 `needs-human` 或 `阻塞` / 人给的预算或时限到 / 命中下方熔断。任何停止都做三件事：写 `progress.md` 总结行、撤销本次创建的定时唤醒（循环停了心跳不许还在）、按第 6 步的收尾总结格式向用户汇报
- **熔断即停，不 retry**：命中任一立即停止派发与合并、记 `progress.md` 并撤销定时唤醒，不安排「过会儿再试」，保持停止等人裁决：①账号额度用量达授权阈值（默认 80%），或已额度耗尽 / 持续限流 / 鉴权失败；②定时唤醒续跑时连续 2 次唤醒没有任何票状态推进（空转只烧额度，不产出）
- **定时唤醒（心跳）从紧**：优先在主会话内持续跑监督循环，不为离场另建高频定时任务；宿主只能靠定时唤醒续跑时才建，间隔不短于 2 小时、仅在上一轮有实质进展时续，唤醒后按「恢复」条另起新会话接力
- **离场前提醒人**：电脑不休眠、宿主权限模式不等人点确认、模型额度限制与本次预算 / 停止阈值
- **恢复**：新会话或崩溃重启后读 `unattended.md` + `decisions.md` + `progress.md` + 票 frontmatter，从 frontier 重算处续跑，不从头再来；续跑一律另起新会话靠落盘文件接力，不反复唤醒同一长会话（超长历史每轮全量重发会放大 token 消耗）
