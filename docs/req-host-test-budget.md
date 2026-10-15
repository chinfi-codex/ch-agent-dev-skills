# 需求说明：本机测试算力预算（整机槽位）

> 状态：已实现（2026-10-15，本仓库内；sync.sh 分发待人确认）· 提出：2026-10-15 · 仓库：ch-agent-dev-skills（skill 规则仓）
> 姊妹需求：[req-full-regression-frequency.md](req-full-regression-frequency.md)（full 降频）。本需求只管「怎么跑」，不管「跑几次」

## 实现备注（2026-10-15，与原设计的偏差）

- 只暴露 `run` / `status` / `budget` 三个子命令：`acquire` / `release` 跨调用持锁必然泄漏（acquire 进程退出即死、锁即被回收，持锁成立不了），已砍。锁 = 每持有者一个 flock 文件、wrapper 存活期间持有，kill -9 由内核放锁，不做 pid 存活检查（抗 pid 复用）
- 预算真源 = 机器级 `~/.cache/agent-dev/slots/budget.json`（agents-config 的 `host-budget` 是镜像）；`queue-timeout` 默认 1200s；`--want` 缺省 = total // max-runs
- wrapper 采样记录运行期 `load-peak`，stderr 末行打印 `queue-seconds / run-seconds / load-peak / exit` 摘要，evidence 直接抄
- 测试：`skills/shared/scripts/tests/test_test_slots.py` 5 例（并发上限 / 少拿不少于一 / kill -9 回收 / 排队超时退 75 / budget 与缺省 want），`python3 -m unittest discover -s skills/shared/scripts/tests` 全绿
- 未做：board.py 槽位显示（可选，拆后续）；pandu 限流前后实测对比待机器空闲时段由人跑（见 §5）

## 1. 背景与实测

本机同时运行 5–10 个 agent 任务（Claude Code / ZCode / Codex 跨多个仓库），各自执行 scoped / full 测试。2026-10-15 现场实测：

- 硬件：10 核（4 个性能核 + 6 个能效核）、24GB 内存
- 负载：load average 67（约为核数的 6.7 倍）
- 同时在跑 4 个重测试进程（yufeng-pandu-agent 的 `tools/check.py` 一个，`scoped_tests.py` / `scoped_driver.py` 三个）。每个进程默认并发 `jobs=min(6, cpu)`，合计约 24 个 Python worker，另有 Chrome、Codex、Claude 的界面进程
- 后果：
  - 单次运行被拉长 3–6 倍，撞上 180s 的单项超时，产生假失败。issue-047 的 evidence 记录了「负载 flake 串行重跑 PASS 3」和「base 同超时 1」
  - 每个假失败都要补跑归因，负载进一步加重，形成正反馈

根因：并发度按单个进程设置；`dispatch.parallel-max` 只管单个 feature 内并行几张票，没有任何跨会话、跨项目的整机上限。

## 2. 目标

1. 整机同时运行的重测试 worker 总数受控，不再超卖
2. 消除负载导致的超时假失败和由此引发的归因重跑
3. 排队时间和运行时间分开可见，便于后续调参

非目标：减少测试总量（姊妹需求负责）。要明确一点：CPU 密集场景下，限流本身不会提高总吞吐，收益在于单次耗时稳定、不再出假失败、不再重跑。

## 3. 需求

### R1 整机槽位信号量

- 新增脚本 `skills/shared/scripts/test_slots.py`，只用标准库，跨宿主可用：
  - `acquire --want N --label <项目/票/档位>`：阻塞直到拿到槽位，打印实际分到的槽位数；`release`；或用包装形式 `run --want N -- <命令>`，自动拿槽、注入 `JOBS` 环境变量、退出时释放（异常和被 kill 时也要释放）
  - 状态放在 `~/.cache/agent-dev/slots/`（跨项目共享，不放仓库里）；用文件锁（`fcntl.flock`）；持有方进程已不存在的槽位自动回收（记录 pid 和启动时间）
  - 预算：总 worker 数 `total`（默认 `ncpu - 2`）、同时运行的重套件数 `max-runs`（默认 2）；可以少拿（拿到的少于 `want` 但不少于 1）
  - `status`：列出持有者、排队者和等待时长
- 只有 scoped / full / 归因补跑需要拿槽，fast 档不受限

### R2 配置与规则接入

- `agents-config.md` 新增 `host-budget`：`total`、`max-runs`、`queue-timeout`（排队超时后报告而非静默等待）。因为是整机预算，同机各仓的值应当一致；`/setup-dev` 写入时提示这一点
- `quality-gate.scoped` / `full` 的说明改为经 `test_slots.py run` 包装执行，并发度取分到的槽位数（例：`pytest -n $JOBS`、`check.py --jobs $JOBS`）
- `/implement`：重套件一律后台执行，输出和退出码落文件后轮询，避免撞宿主 Bash 的 10 分钟超时（下游已有 `.logs/scoped_driver.py` 这种做法，把它规范化）
- evidence 档位门记录新增 `queue-seconds` / `run-seconds` / `jobs`

### R3 负载感知的超时处理

- 单项超时发生在高负载时（运行期间 1 分钟 load 超过 ncpu）：先串行单独重跑一次再归因；重跑通过的记 `load-flake`，不进入失败归因，evidence 列出
- 高负载下的超时不得直接记成本票引入的失败，也不得直接记成先在失败

## 4. 涉及文件

- 新增 `skills/shared/scripts/test_slots.py`，测试放在 `skills/shared/scripts/tests/`
- `skills/shared/templates/agents-config.md`、`skills/shared/fragments/dev-config.md`：`host-budget` 与 quality-gate 包装说明
- `skills/setup-dev/SKILL.md.tmpl`：采集或确认 `host-budget`
- `skills/implement/SKILL.md.tmpl`：Step 4 / 5 / 8 的执行形态、R3 的超时处理
- `skills/shared/fragments/supervision-loop.md`：主会话验收复跑若涉及重套件，同样走槽位
- `skills/shared/templates/evidence.md`：新字段
- 可选：`skills/board/board.py` 显示当前槽位占用（读 `status`）
- 改完运行 `node --experimental-strip-types skills/scripts/gen-skill-docs.ts`；需要 `bash sync.sh` 分发时先问人

## 5. 验收

- 单元 / 集成测试覆盖：
  - 并发启动 5 个 `run --want 6`，`total=8`、`max-runs=2` 时，任意时刻持有 worker 数不超过 8、运行数不超过 2
  - 持有方被 `kill -9` 后，槽位能被回收
  - 排队超过 `queue-timeout` 时，返回非 0 并给出说明
- 在本机用 yufeng-pandu-agent 的 `tools/check.py --jobs $JOBS` 做实测对比：3 个 full 同时提交，限流前后各跑一轮，记录总墙钟、单次墙钟、超时项数和 load 峰值，写进实现说明
- 生成后的 SKILL.md 与模板一致

## 6. 待人裁决

- `total` 是按 `ncpu - 2` 还是只算性能核（本机 4 个性能核 + 6 个能效核，能效核跑测试明显更慢）
- 下游仓的 `check.py` / `scoped_tests.py` 要读 `JOBS`，这需要改下游仓：由本需求的实现方直接改，还是在下游另开票
