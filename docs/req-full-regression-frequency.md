# 需求说明：full 回归降频与基线治理（每日定时回归方案）

> 状态：待实现 · 提出：2026-10-15 · 方案改订：2026-10-15（merge-train → 每日定时回归，补强点已裁决）· 仓库：ch-agent-dev-skills（skill 规则仓）
> 姊妹需求：[req-host-test-budget.md](req-host-test-budget.md)（本机测试算力预算，已实现）；本需求的每日回归经 `test_slots.py run` 包装执行

## 1. 背景与数据

dev 流水线（/issue-split → /implement → /ai-review → 主会话合并）按票 `verify-tier` 分档验证，full 档票与集成票必跑完整套件（`full-runs: once-at-final`）。来自下游仓 `~/Desktop/yufeng-pandu-agent` 的实测数据（320 份 evidence）：

| 指标 | 数据 |
|---|---|
| 实际执行 full 的 evidence | 约 123 份；票面 full 档占比约 65% |
| full 抓到本票新引入失败 | 约 4–5 次（issue-034 的 3 项 YAML shim、某 issue-006 的 5 项、issue-036-worker-v3-fix 的 1 项），命中率约 3–4% |
| 单次 full 墙钟 | 中位数约 6 分钟，P90 约 19 分钟，最长约 25 分钟，不含归因 |
| main 基线 | 长期是红的：先在失败 18–58 项，每次 full 的结果都是 exit 1 加逐项归因 |
| scoped 大闭包 | 观测到单次 scoped 跑 10–39 分钟，不比 full 便宜（注意：在 load 67 下测得，槽位落地后需重新校准） |

结论：full 按票执行，成本高、收益低；基线一直红，又把每次 full 都变成了归因任务。

**方案改订记录**：初版 R1 为 merge-train（每合入 K 张票批量 full + tree hash 去重）。2026-10-15 改订为每日定时回归——merge-train 的批量状态机 / tree 去重 / 集成票门语义复杂度高，每日方案机制最小（定时器 + 槽位 + 落盘 + quarantine）；代价是失败发现窗口从「K 张合入」放宽到「≤ 1 个工作日」，且 3–4% 的命中率是在 scoped 先滤过一遍的流水线上测得的，票内收敛后每日 full 的新失败率会高于此数——失败是延迟发现，不是不发现。

## 2. 目标

1. 票内交付门收敛为 DoD + fast + 小闭包 scoped；套件级回归从「按票」降为「main tree 每个工作日至少一次 full」
2. 先在失败不再产生 exit 1，full 退出码重新有意义（quarantine）
3. 定档 / 升档原因可统计（`tier-reason`），票内 scoped 的占比阈值按槽位落地后的实测校准

非目标：本机并发 / 算力调度（姊妹需求，已实现）；评审流程（已在 472e568 完成）。

## 3. 需求

### R1 票内门收敛 + 每日定时 full

**票内执行**：

- 所有票：DoD + fast；独立评审照旧（评审不跑套件，现状不变）
- scoped：选择集占全套 ≤ 30% 时票内照跑（全闭包）；> 30% 时不跑票内，留给每日 full。阈值进 config（`daily-regression.scoped-in-ticket-max`，默认 0.30），槽位落地后按实测校准
- **例外，保留票内 full**：命中 full 级触发器「数据迁移 / schema」「鉴权 / 权限」的票，评审通过后的最终 commit 跑一次（once-at-final 语义对例外票不变）
- 集成票不再强制票内 full；「模块隔离认定」机制一并移除（其职责被 30% 线 + 每日 full 覆盖）。「每 feature 至少一次 full」不变式改写为「main tree 每个工作日至少一次 full」——它就是定时任务的目标节奏，不做票级门、不阻塞单票合入、不设监控告警

**每日 full（定时任务）**：

- 触发 = 定时任务，只此一层：macOS 用 launchd `StartCalendarInterval`（睡眠错过会在唤醒时补跑一次；cron 可作 fallback）。不做补跑检查、不做时效告警——机器全天睡眠就跳过当天，漏跑容忍
- 频率上限：每天最多一次，按日期去重（当日已有记录不再跑；当日失败后也不当日重跑——修复验证靠责任票的 scoped 与次日 full）
- 执行：专用 worktree（`.worktrees/daily-full`）checkout main HEAD，经 `test_slots.py run` 包装（遵守整机预算，后台执行轮询）；落盘 `dev/full-runs/<date>-<tree8>.md`（tree hash、自上次通过以来的合入清单、结果、归因、耗时、jobs / queue-seconds）
- 出现清单外新失败：在「上次通过 tree..本次 tree」合入区间二分定位（每步只跑失败项，不跑整套）→ 责任票置 `needs-human`，由原实现对话回修；二分期间合入不暂停（乐观合并，窗口即一个工作日）
- 新增薄脚本 `skills/shared/scripts/daily_full.py`：更新 worktree → test_slots 包装跑 full → 原始输出与摘要落 `dev/full-runs/`、同日去重；归因 / 清单刷新 / needs-human 路由由主会话或人读结果后执行，不进脚本
- 定时器的安装 / 改时点进 /setup-dev（生成 launchd plist 或 cron 行，人确认后安装）

### R2 基线隔离（quarantine）

- `known-failures` 清单升级为隔离清单：列入的用例在 full / scoped 中以 skip（标 quarantined）执行，不再产生 exit 1；evidence 列出隔离数
- 入清单条件：base 侧同签名稳定失败；每条必须写 owner 或去向（修复票 / 永久删除决议）
- 清单由每日 full 顺带刷新；env-skip 项不销记（环境缺失的轮次不做整体替换，保留历史条目）；条目超过 M 天未处理，在 /board 告警（M 进 config，M=14）
- 隔离条目保留签名，每日 full 以「只跑隔离项、不判红」抽查签名漂移（隔离用例被改动影响时可见）
- 现有「清单外单项补跑 base 对照」的逻辑保留，用于每日 full 的新失败判定

### R3 触发器命中统计（tier-reason）

- /issue-split 定档、/implement 升档时，在票 frontmatter 记 `tier-reason:`（命中的触发器名或阈值项），process_check.py 一并输出
- /board 汇总各触发器占比，供人调整触发器（可拆后续）
- 原「scoped 选择集 ≥ 50% 改跑 full」的想法废弃，由 R1 的 30% 线取代：选择集占比决定**票内跑不跑**，不再是升档理由；verify-policy 注释按此改写

## 4. 涉及文件

- `skills/shared/templates/agents-config.md`：verify-policy 移除「集成票一律 full」「模块隔离认定」，`full-runs: once-at-final` 收敛为仅例外票；新增 `daily-regression`（enabled / time / scoped-in-ticket-max）；known-failures 隔离语义
- `skills/shared/fragments/dev-config.md`：同步字段说明
- `skills/shared/fragments/supervision-loop.md`：收口不再查 full-commit（例外票除外）；feature 总账不等 full
- `skills/implement/SKILL.md.tmpl`：Step 5 / 8 去掉票内 full（例外票保留 once-at-final）；scoped 30% 判定；evidence 字段
- `skills/issue-split/SKILL.md.tmpl`：定档写 `tier-reason`；集成票不再一律 full；粒度红线不变
- `skills/shared/templates/evidence.md`、`issue.md`：`tier-reason` 字段；`full-commit` 仅例外票
- `skills/shared/scripts/process_check.py`：merge 阶段——例外票（`tier-reason` 命中迁移 / 鉴权触发器）须票内 `full-commit`，其余票不查 full；输出 tier-reason
- 新增 `skills/shared/scripts/daily_full.py`，测试放 `skills/shared/scripts/tests/`
- `skills/board/board.py`：隔离超期告警（触发器占比可拆后续）
- `skills/setup-dev/SKILL.md.tmpl`：`daily-regression` 采集与定时器安装
- 改完运行 `node --experimental-strip-types skills/scripts/gen-skill-docs.ts` 重新生成 SKILL.md；需要 `bash sync.sh` 分发时先问人

## 5. 验收

- 文档：上述字段和流程在模板、fragment、生成后的 SKILL.md 中一致；`grep` 不到残留的「集成票必跑 full」「full 档票必跑」旧表述（例外条款除外）
- process_check.py：补合成仓用例（例外票缺票内 full → FAIL；非例外 full 档票无 full-commit → PASS；tier-reason 正确输出）。用例落到 `skills/shared/scripts/tests/`
- daily_full.py：同日重复调用只跑一次；落盘字段齐全（tree hash / 合入区间 / 结果 / jobs / queue-seconds）
- 在 yufeng-pandu-agent 上按新规则回放最近 10 张已合并的 full 档票，估算票内 full 次数（应只剩例外票）与每日 full 的墙钟，写进实现说明
- 定时器安装后手动触发一次，验证 worktree 更新 → 槽位包装 → 落盘链路

## 6. 已裁决

- 例外范围：仅「数据迁移 / schema」「鉴权 / 权限」保留票内 full；公共接口签名**不入**例外（命中率基数低，每日 full + 二分兜底；观察 tier-reason 统计一个周期再议）
- 隔离清单超期天数 M = 14；每日时点在 /setup-dev 确认（建议工作日午休 12:30，机器醒着且负载低）
- scoped 票内占比阈值 = 30%，槽位落地后按实测校准
- 方案改订：merge-train（K 批量）废弃，定时任务是唯一套件级回归节奏；触发只此一层，漏跑容忍，无补跑检查 / 时效告警
