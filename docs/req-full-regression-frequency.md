# 需求说明：full 回归降频与基线治理（C+D+F 三层方案）

> 状态：已实现 · 提出：2026-10-15 · 方案改订：2026-10-15（merge-train → 每日回归 → C+D+F；R1 已落地）· R2 落地：2026-10-23（告警通路偏差：/board 已删除，超期条目改由每日回归记录列出 + 主会话监督循环 6a 呈现）· 仓库：ch-agent-dev-skills（skill 规则仓）
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

**方案改订记录**：初版 R1 为 merge-train（每合入 K 张批量 full）；二版改为「每日定时回归为唯一套件级回归」并取消集成票 full。三版（当前，已落地）对比各维度后定为 C+D+F：

| 方案 | 结论 |
|---|---|
| A 每票 full（原状） | 弃：97% 的运行一无所获，白天抢 CPU |
| B 每合入 K 张 | 弃：仍在白天跑；多 feature 交错时定位范围不比 D 清楚 |
| **C feature 收尾（集成票）** | **保留**：feature 关闭前整体验证，责任明确；单用时看不到 feature 之间的回归 |
| **D 每工作日定时** | **新增**：补 C 的跨 feature 漏洞，夜间 / 低负载时段跑；延迟最多 1 天 |
| E 空闲触发 | 作为 D 漏跑的兜底（`max-gap-days`） |
| **F 按风险（迁移 / 鉴权）** | **保留票内 full**：出错代价高，不等 D |
| G 发布前 | 不单独采用 |

## 2. 目标

1. 票内 full 只留给迁移 / 鉴权、超阈值和集成票；跨 feature 回归由 main 每工作日一次 full 兜底（已落地）
2. 先在失败不再产生 exit 1，full 退出码重新有意义（quarantine，R2，已落地）
3. 定档 / 升档原因可统计（`tier-reason`，字段已落地，统计待实现）

非目标：本机并发 / 算力调度（姊妹需求，已实现）；评审流程（已在 472e568 完成）。

## 3. 需求

### R1 套件级回归三层（C+D+F）——已落地（见本次提交）

- **票内 full 只剩三类**：full 级触发器（数据迁移 / schema、鉴权 / 权限）、实测超 `scoped-max-*` 阈值、集成票（DAG 终点票）。其余票 DoD + fast + scoped
- **触发器改级**：「公共接口 / API 签名」从 full 级降为 scoped+ 级，选择集 = 调用方反向闭包；scoped+ 级闭包不可枚举时不再升 full，标「闭包不完整」仍 scoped，剩余风险由集成票与每日回归兜底
- **集成票（C）**：保留 full 与「模块隔离认定」；合并候选 tree 与某次通过的每日回归 tree 相同时，evidence 填 `full-ref` 复用，免跑
- **每日回归（D）**：`verify-policy.daily-regression`（enabled / record-dir / worktree / skip-if-unchanged / max-gap-days）。定时器**由人自设**，skill 不安装；/setup-dev 收尾时给出定时任务 prompt。每次落盘 `dev/full-runs/<date>-<tree8>.md`，含 `tree:` 与 `result:` 行（process_check 读这两行判定 full-ref）
- **新失败处理**：在「上次通过 tree..本次 tree」合入区间二分（每步只跑失败项）→ 责任票 needs-human 并开回修票；合入不暂停。主会话监督循环每轮读最新记录（supervision-loop 6a）
- **漏跑兜底（E）**：有进行中 feature 且最近记录早于 `max-gap-days`（默认 3）→ 主会话空闲时补跑一次
- **tier-reason**：票 frontmatter 新字段，/issue-split 定档、/implement 升档时写；process_check 输出

### R2 基线隔离（quarantine）——已落地（2026-10-23）

- `known-failures` 清单升级为隔离清单：列入的用例在 full / scoped 中以 skip（标 quarantined）执行，不再产生 exit 1；evidence 列出隔离数
- 入清单条件：base 侧同签名稳定失败；每条必须写 owner 或去向（修复票 / 永久删除决议）
- 清单由每日 full 顺带刷新；env-skip 项不销记（环境缺失的轮次不做整体替换，保留历史条目）；条目超过 M 天未处理，在 /board 告警（M 进 config，M=14）
- 隔离条目保留签名，每日 full 以「只跑隔离项、不判红」抽查签名漂移（隔离用例被改动影响时可见）
- 现有「清单外单项补跑 base 对照」的逻辑保留，用于每日 full 的新失败判定

### R3 触发器命中统计（tier-reason）

- 已落地：票 frontmatter `tier-reason`；/issue-split 定档、/implement 升档时写；process_check.py 输出
- 待实现：/board 汇总各触发器导致 full 的占比，供人调整触发器
- 二版提出的「scoped 选择集 > 30% 时不在票内跑」未采用：三版保持「选择集大小不是升档 / 降档理由」，scoped 票照跑全闭包；槽位落地后若 scoped 墙钟仍过高再议

## 4. 剩余工作与涉及文件

R1 已在本次提交落地（agents-config、dev-config、supervision-loop 6a、implement、issue-split、ai-review、setup-dev、evidence / issue 模板、process_check.py 的 full-ref 与 tier-reason，测试 `skills/shared/scripts/tests/test_process_check.py`）。

R2 已于 2026-10-23 落地：`agents-config.md` 模板 `attribution: quarantine` 隔离语义 + `quarantine-max-age-days`、dev-config fragment 归因段、implement Step 5 归因段、evidence 模板 `quarantined` 字段与隔离记录行、每日回归记录含签名漂移抽查与超期条目、supervision-loop 6a 超期条目呈现、新增清单格式模板 `shared/templates/quarantine-list.yaml`、setup-dev 配置引导（存量清单迁移补登去向）。

剩余：

- R3 统计：`skills/board/board.py`（board 当前已从仓库移除，恢复后再做）
- 判读仓迁移：`dev/known-failures.yaml` 46 条存量逐条补登 owner / 去向（无去向不入册），`dev/agents-config.md` 的 `attribution` 改 `quarantine`
- 改完运行 `node --experimental-strip-types skills/scripts/gen-skill-docs.ts`；需要 `bash sync.sh` 分发时先问人

## 5. 验收

- R1（已过）：`python3 -m unittest discover -s skills/shared/scripts/tests -p "test_process_check.py"` 7 项通过，覆盖集成票须 full、review 阶段推迟 full、full-commit 过期、full-ref 复用 tree 相同且 pass 的记录、拒绝 tree 不符或结果非 pass 的记录
- R1（待人）：定时任务设好后手动触发一次，确认 `dev/full-runs/` 记录含 `tree:` / `result:` 行，且二分 → needs-human 链路走通
- R2：隔离用例以 skip 运行、full 退出码为 0；evidence 列出隔离数；超期条目可被列出
- 在 yufeng-pandu-agent 上按新规则回放最近 10 张已合并的 full 档票，估算票内 full 次数下降与每日 full 墙钟，写进实现说明

## 6. 已裁决

- 方案：C+D+F（见方案改订记录）；集成票 full 与模块隔离认定保留
- 票内 full 范围：数据迁移 / schema、鉴权 / 权限、超阈值、集成票；公共接口签名降为 scoped+
- 每日回归定时器由人在宿主里自设，skill 只给 prompt；漏跑由 `max-gap-days`（默认 3）空闲补跑兜底
- 隔离清单超期天数 M = 14
