## 监督循环（主会话职责，直到完成合并）

门②（票清单确认）通过后，主会话自动进入监督循环，**派出后不停手，监督到每张票合并完成**：

1. **取票**：只读 `./dev/features/<feature-module>/<feature-slug>/issues/` 票文件的 frontmatter（`status` / `blocked-by`），重算 frontier（blocked-by 全部 `done` 的票）；票正文由被派发的实现对话自读，主会话不读
2. **派发**：按「派发协议」宿主适配机制为 frontier 票新开独立对话跑 `/implement issue-NNN`；默认按依赖序逐张串行，人明确要求并行才同时派多张
3. **跟踪**：被派发对话的结构化回报（分支名 / worktree 路径 / MR IID 或草案路径 / 证据与评审报告路径 / 遗留 Medium-Low）只是线索，不作为成功依据
4. **收口**：主会话亲自核对落盘产物的**结构化头部**——evidence yaml 头 `all-passed: true` 且 `tier-executed` 达到票 `verify-tier`（或已按 `verify-policy` 升档并留记录）、评审报告头部结论 `pass` / `pass-with-notes`、MR ready——执行合并（gitlab：`glab mr merge <IID> --delete-source-branch`；local：主工作区 `git merge --no-ff feat/<feature-slug>-<issue-id>`），票置 `done`，按「派发协议」清场序列收尾；头部字段缺失或相互矛盾时才展开全文核对；合并冲突先在 worktree 内 rebase `main-branch`、重跑 `quality-gate.fast` 再合
5. **推进**：每轮先做 worktree 对账（`git worktree list --porcelain` 与票状态比对，票已 `done` 但 worktree 仍在的按清场序列补删；`in-review` / `needs-human` 票的保留并列入总账），再重算 frontier 回到第 1 步；全部 `done` 后回显总账（每票：合并 commit / 证据与评审报告路径 / worktree 已清或保留原因 / 遗留 Medium-Low），输出完成状态
6. **唯一暂停条件**：回报 `needs-human` / `阻塞`、合并冲突自动 rebase 后仍无法解决、其他无法自行裁决的问题——与人单点确认后再继续；其余（含 BLOCKER 回修、fast 自修复）一律不问人
