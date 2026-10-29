# 需求说明：测试验证体系重构（确定性执行器 + 判读机 CI 合并队列）

> 状态：待评审 · 提出：2026-10-28 · 仓库：ch-agent-dev-skills（skill 规则仓）
> 输入：本轮提供的八项重构方案。
> 2026-10-28 补充：明确 pandu agent 仓落地范围；full 不在本机执行，开发流程内只由分支 push 触发判读机 Gitea Actions，在被 push 分支的精确提交上执行。
> 2026-10-28 用户设置修订：pandu push full 适用于 `main`、`feat/**`、`query`；关闭 push CI 在判读机的自动部署；关闭每日 nightly full。下文与之不一致的“每日回归 / nightly”表述以本修订为准。
> 2026-10-29 修订：删除远端测试协议与 `remote_tests.py` 通路。循环内验证（TDD / fast / DoD / scoped / 归因 / 复测）回本票 worktree 本机执行，判读机远端只保留 push 触发的 CI full。
> 承接：[req-full-regression-frequency.md](req-full-regression-frequency.md)、[req-host-test-budget.md](req-host-test-budget.md)、[req-main-model-conformance.md](req-main-model-conformance.md)。
> 本文只定义落地需求与验收口径，不涉及本轮代码实现。

## 1. 背景与问题

当前开发流水线把测试执行、失败归因和验收复跑放在编码 agent 的工作循环内，主要产生四类成本：

1. **等待成本**：票内需要等待 scoped，部分票还要等待 full；full 与归因直接拉长编码会话。
2. **归因成本**：清单外失败依赖 agent 阅读日志、补跑 base 并判断责任，消耗大量 token，且结论可能不一致。
3. **规则成本**：light / scoped / full 三档、两级触发器、反向闭包、手工映射、`full-ref` 和多项特例共同维护，理解与改造成本高。
4. **反馈质量不足**：存量冒烟占比较高，编码循环内缺少足够快、能直接验证行为变化的单元测试。

本轮方案输入中的估算为：scoped 约 7 分钟，部分票另加 full 约 40 分钟；既有文档中的历史样本则为 full 中位约 6 分钟、P90 约 19 分钟。两组数据统计窗口和环境不同，**实施第一阶段必须重新采集基线，不得把估算值写成已实测收益**。

## 2. 改订结论

本需求落地后，对现行 C+D+F 方案作如下改订：

- **取消票内 full**：包括迁移、鉴权等高风险票，以及 feature 收尾集成票。高风险票改在合并队列中单独成批、优先执行 full。
- **full 只在判读机 CI 执行**：本机、编码 agent 与监督循环均不得执行 full。开发流程内由需求分支 push 触发 Gitea Actions，CI 必须 checkout 被 push 分支的精确提交；每日回归与人工重跑也只能复用同一 CI workflow。
- **保留每日回归**：继续发现跨 feature 回归和合并队列未覆盖的问题，不因队列上线而删除；每日回归在判读机 CI 运行，不设本机补跑出口。
- **保留并强化隔离清单**：继续跳过已确认的先存失败，但增加数量上限、修复票绑定和 14 天处理时限。
- **保留整机槽位，删除远端执行协议**：循环内测试在本票 worktree 经整机槽位包装执行；CI runner 内复用判读机既有算力预算，不另建平行预算，不嵌套套锁。
- **保留 TDD 见红、独立代码评审和需求符合性要求**：本次移动的是测试执行与套件级回归，不削弱实现纪律和写查分离。
- **需求级验收逐步改为可执行黑盒测试**：不能自动化的语义判断仍保留人工或主模型核对，不以“已有验收测试”为由全部取消。

目标形态只保留两类验证：

| 验证位置 | 内容 | 作用 |
|---|---|---|
| 循环内验证 | lint、本票单元测试、受影响测试、票级 DoD | 给编码 agent 快速反馈，决定是否可合入需求分支 |
| 队列验证 | full、需求级黑盒验收测试 | 决定需求分支是否可合回 main，并负责批次失败定位和自动回退 |

fast 是循环内验证的命令子集，不再与 light / scoped / full 组成另一套档位体系。

## 3. 目标与非目标

### 3.1 目标

1. 测试执行和失败归因由确定性执行器完成，agent 只消费小型判决，不阅读完整套件日志。
2. 编码票通过后立即合入需求分支，agent 不等待 full；main 只接受 full 与需求级验收均通过的需求分支。
3. 以真实依赖记录替代手工映射和触发器，循环内只运行受影响测试。
4. 缩短套件墙钟，并让新逻辑在循环内获得可执行的行为反馈。
5. 将部署环境验证从编码票拆出，避免环境状态阻塞代码完成判定。
6. 让隔离清单持续减少，不再无限期积累先存失败。

### 3.2 非目标

- 不重写全部存量冒烟测试；只在新逻辑落地和旧逻辑被修改时补充可单测边界。
- 不为了提速删除独立代码评审、TDD 见红或需求覆盖要求。
- 不允许未通过 full 与需求级验收的需求分支合入 main。
- 不在第一阶段重建整套 CI 平台；执行器和队列应优先复用现有脚本、远端判读机与定时任务能力。
- 不承诺未经实测的“40 分钟降至 5～10 分钟”等收益数字；这些只作为试点观察目标。

## 4. 总体流程

### 4.1 编码票主流程

1. 实现 agent 按 TDD 完成代码和本票单元测试。
2. 确定性执行器在指定 commit 上运行 lint、本票单测、受影响测试与票级 DoD。
3. 执行器输出判决；实现 agent 只读取判决和必要的首个失败摘要，完成修复循环。
4. 流程核对、独立代码评审和需求符合性核对继续使用同一份判决及其证据指针。
5. 评审通过后，主会话将票合入当前 feature 的需求分支，编码 agent 当场结束，不等待 full。
6. 票的“代码已集成”和 feature 的“已具备合回 main 条件”分别记录，不用票已合入需求分支冒充 feature 已验证完成。

### 4.2 合并队列主流程

1. 主会话将已通过循环内验证与评审的票合入需求分支，形成一次普通同名 push；本机不执行 full。
2. push 触发判读机上的 Gitea Actions。workflow 以 push 事件中的分支和 SHA 为准 checkout，核对 HEAD、tree 和分支推进状态后运行 full 与需求级黑盒验收测试。
3. CI 将核心判决写入 Checks / commit status，并把完整判决、运行元数据和原始日志作为 artifacts 保存。本机与 agent 只读取核心判决和证据指针。
4. 全部通过时，需求分支获得合回 main 的资格；合入前必须确认通过判决仍对应候选 tree。
5. 出现本批次新失败时，合并队列在批次内二分定位责任提交，自动 revert 责任票并创建回修票。二分只重跑失败项；full 复验仍通过回退后的新 push 触发 CI。
6. 回退后未恢复通过、无法无冲突回退或责任无法唯一确定时，停止自动处理并交监督循环。
7. 需求分支发生任何新合入或自动回退后，原 full 与验收判决失效，必须由新 push 产生的新 CI 判决替代。

### 4.3 发布与部署验证

- 编码票只验证代码和可在执行器上运行的行为。
- 部署验证作为独立阶段，由人或运维流程在部署完成后触发；它阻塞对应部署或发布，不反向把编码票标记为代码未完成。

## 5. 功能需求

### R1 确定性测试执行器与统一判决（P0，第一阶段）

#### R1.1 输入与执行

- 执行器至少接受：目标 commit、归因 base、测试范围、仓库位置和执行环境标识。
- 执行器必须在与后续消费者一致的代码 tree 上执行；运行期间 tree 改变时，本次结果作废。
- 循环内测试在票 worktree 本机执行，重套件经整机槽位包装，不允许重复套锁；不提供把循环内测试提交到判读机的通道。
- **full 是上述通用执行方式的例外**：pandu 的 full 只能由判读机 Gitea Actions 执行；本机任何 worktree 也不得为了补证据直接调用 `tools/check.py` 全量入口。
- 同一 commit、base、测试范围和环境标识重复提交时，应能定位或复用同一次运行记录，避免并发重复执行。

#### R1.2 核心判决契约

执行器输出一份机器可读 JSON，核心字段至少包括：

| 字段 | 含义 |
|---|---|
| `pass` | 所有必跑项完成且没有本变更引入的新失败时才为 `true` |
| `new_failures` | 新失败清单，每项包含测试标识与规范化失败签名 |
| `quarantined_n` | 本轮按隔离清单跳过的用例数 |
| `tree` | 实际执行测试的精确 tree 标识 |

运行记录还必须能关联 commit、base、运行编号、完成状态、原始日志位置和生成时间；这些元数据不得迫使 agent 阅读完整日志。

#### R1.3 自动归因

- 隔离清单内用例按现行 quarantine 语义处理，不进入 `new_failures`。
- 清单外失败由执行器自动在 base 的隔离工作区中补跑同一条测试，不整套重跑。
- branch 失败而 base 同测试通过，判为“本变更引入”，进入 `new_failures`。
- branch 与 base 以同一规范化签名失败，判为“先存失败”，不进入 `new_failures`。
- base 无法执行、环境不一致、签名无法比较或结果不稳定时，判为“无法自动归因”，`pass=false`；不得交由 agent 猜测，也不得默认归为先存失败。
- 测试基础设施错误、执行器自身错误和测试未完整运行均不得输出通过判决。
- 归因过程不得调用 LLM；失败签名规范化必须忽略路径、耗时、临时目录等非稳定文本，同时保留测试标识和有效断言或异常信息。

#### R1.4 消费方改造

- 实现 agent 只读取核心判决、失败测试标识和一句结论；只有修复单项失败时才按需读取该单项日志。
- evidence 由执行器生成测试摘要和原始证据指针，agent 只补充一句结论，不再手抄完整执行记录。
- `process_check.py` 改为核对判决存在性、完成状态、`pass`、`tree` / commit 新鲜度和证据指针，不再要求 agent 手工组装档位门证据。
- 监督循环的验收复跑读取同一判决；需要复跑时直接调用执行器，不重写一套归因逻辑。
- 判决过期、tree 不符或证据文件缺失时，流程核对必须失败。

#### R1.5 完成判定

- 用固定测试样例覆盖：全新失败、base 同签名失败、base 不同签名失败、base 执行失败、隔离项、执行器错误、tree 变化和重复提交。
- 相同输入重复执行时，核心判决语义一致；日志时间戳等元数据可以不同。
- 任一清单外失败均不需要 agent 执行 base 补跑或阅读整套日志。

### R2 套件并行、缓存与低价值冒烟降频（P0，与 R1 并行）

#### R2.1 并行执行

- pandu 的 `tools/check.py` / `tools/check_runtime.py` 已具备并行执行、慢项优先、历史耗时记录、显式串行尾段和整树指纹缓存；本项不是重新开发并行器，而是保留并验证这些能力在判读机 CI 中的一致性。
- CI 必须持久化并复用历史耗时数据，避免每次干净 checkout 都退回无历史权重；耗时数据只用于调度，不作为测试通过证据。
- 并行后的通过、失败、隔离和无法归因语义必须与串行执行一致。
- 测试之间存在共享资源或顺序依赖时必须显式分组，不得为并行而隐藏失败或改变测试行为。
- 并行度继续受整机槽位约束，排队时间、实际运行时间和 worker 数分别记录。
- 无历史耗时时使用统一初始权重，运行后回写实测耗时供后续分片使用。

#### R2.2 结果缓存

- 缓存键至少包含测试标识、测试自身内容、全部已声明依赖文件内容、运行环境标识和影响结果的配置。
- 任一键组成部分变化都必须使旧缓存失效；缓存命中必须在判决中计数并可追溯到原运行。
- 失败、无法归因和未完整运行的结果不得被缓存为通过。
- 缓存不得绕过需求级验收的强制实跑要求；发布或 main 放行若要求当轮实跑，必须显式禁用相应缓存。
- **依赖顺序**：现有并行分片校准和慢冒烟降频可在第一阶段实施；依赖真实依赖映射的精确缓存，在 R4 映射可用后启用。此前不得用已知不完整的手工映射进行激进缓存。

#### R2.3 慢冒烟降频

- 只有同时满足“耗时高”和“长期未捕获有效回归”的冒烟用例才可移入夜间清单，并记录判定数据。
- 降频用例仍由每日回归执行，不得直接删除或变成永不执行。
- 用例在夜间抓到过回归后，应重新评估是否恢复为队列 full 的常跑项。

### R3 异步合并队列：乐观合入与自动回退（P1，第二阶段）

- 编码票的循环内验证、流程核对、独立评审和需求符合性核对通过后，允许立即合入需求分支，不等待 full。
- 每次需求分支 push 就是合并队列的一次入队；同一分支的 CI full 按 ref 串行，每个判决绑定 push 的精确 commit 与不可变 tree。
- full 的唯一开发流程入口是判读机 Gitea Actions；禁止本机 full、票内 full 与监督循环手工 full。
- 队列可以批量运行 full；迁移、鉴权等高风险票必须单独形成一次需求分支 push，不与其他票混入同一待验证批次。
- full 或需求级验收失败时，队列只对批次内新增失败执行二分定位，不重跑无关通过项。
- 责任提交唯一且可干净 revert 时，队列自动回退该提交，并创建包含失败签名、责任提交、原票和证据指针的回修票。
- 自动回退后先重跑原失败项；随后推送回退提交，由该 push 触发新的 CI full。未恢复通过时继续定位或转人工，不得仅凭 revert 成功宣称问题解决。
- 回退发生争议、冲突或责任不唯一时，保留现场并通知监督循环，不在 main 上试错。
- 回修票由现有监督循环接续处理，重新走循环内验证和队列验证。
- 需求分支只有在同一候选 tree 上 full 与需求级验收均通过时才允许合回 main。
- 票内 full、`once-at-final`、集成票 full 和对应的票内 full 证据规则在本阶段切换完成后删除，不保留长期双路径。

### R4 真实依赖驱动的测试选择（P1，第三阶段）

- 每日 full 或专门夜间任务顺带记录每条测试实际读取的文件和导入模块，生成“测试 → 依赖文件”映射。
- 映射必须记录生成 tree、生成时间、追踪工具与运行环境；来源不明的映射不得使用。
- 循环内选择集至少由 diff 命中的测试、直接变更的测试文件和本票新增单测组成。
- 文件删除或重命名时，必须同时使用变更前映射计算受影响测试，避免因映射键消失而漏选。
- 映射未覆盖、过期或记录不完整时，允许缩小循环内选择集，由队列 full 兜底；不得在 agent 循环内自动升回 full。
- 映射生成失败不得阻断当次每日 full，但必须记录并在下一次运行重试。
- 新选择器连续稳定运行并完成对照验收后，退役 `scoped-map.yaml`、两级触发器、闭包不完整升档或延期特例、`tier-reason` 以及旧 `scoped_select.py` 实现。
- 旧机制删除后，配置、模板、生成文件、测试和宿主分发必须同步，避免规则文本仍引用已不存在的选择器。

### R5 快速行为测试与纯函数边界（P1，持续推进）

- 新增核心逻辑应拆为不依赖数据、配置和外部环境的核心单元，以及负责适配的薄外壳。
- 核心单元测试在标准执行器上单条耗时须低于 1 秒；超过阈值的测试不得进入快速反馈集。
- 循环内快速反馈至少包含 lint、本票单测和受影响单测；票级 DoD 仍按行为要求执行。
- 新单元测试继续遵守 TDD：实现前先因预期行为差异见红，再实现到通过。
- tech-spec 的“预先约定 TDD 接缝”必须明确核心行为边界、输入输出和对应单测，不只写抽象分层名称。
- 存量冒烟不做一次性重写；票修改到某条冒烟覆盖的核心逻辑时，同票抽出可单测边界并补充单测。
- 抽出核心逻辑后不得保留重复的旧实现；无法在本票安全拆分的，记录原因并由后续专门票处理。

### R6 独立需求级黑盒验收测试（P1，随 feature 开工建立）

- feature 开工时，由不参与实现、也不参与编码票测试修改的独立 agent，根据 spec 编写需求级黑盒验收测试。
- 每个 spec 行为点必须映射到至少一条验收测试，或显式列入不可自动化人工核对项；禁止无去向的行为点。
- 验收测试只依赖公开行为入口，不读取实现内部状态，不复制实现算法来证明自身正确。
- 编码实现票不得为通过验收而修改需求级测试；需求变化导致测试更新时，必须保留 spec 变更依据和独立复核记录。
- 需求级验收测试与 full 在同一个判读机 CI workflow 中对同一 push commit 执行，是合回 main 的必要条件。
- 可自动化的 feature 级符合性核对逐步由这些测试承担；未自动化项继续走现有主模型符合性核对，不得留空。
- 验收测试本身失败、无法运行或覆盖映射缺失时，需求分支不得合回 main。

### R7 环境相关验收拆分（P1，规则改造可与 R1 同步）

- 编码票 DoD 只能包含执行器可稳定运行的代码和行为验证，不得依赖远端判读机已部署、生产服务已发布或人工环境已切换。
- 需要部署环境的验收项必须移到 feature 的部署验证阶段，并写明触发条件、责任角色、目标环境和通过标准。
- 环境未就绪时，部署验证状态为“未验证”或“无法验证”，不得记为通过，也不得让编码 agent 反复重试环境。
- 编码票可以凭代码侧判决完成；部署验证是否阻塞 main、预发或生产发布，按 feature 的验收约定执行，不能把生产发布后的检查伪装成编码票前置条件。
- 类似 issue-095 的既有票在迁移时，应拆为编码完成证据与部署验证记录两份，不保留大段环境解释代替验收状态。

### R8 基线红灯硬约束（P1，立即可执行）

- 以当前存量 46 条为初始核对数，实施时先确认真实清单数量并形成只减不增的数量上限；后续不得以刷新清单为由抬高上限。
- 每个新入册条目必须先确认 base 同签名稳定失败，并绑定修复票、责任人和首次登记日期；缺任一项不得入册。
- 达到数量上限时，新先存失败不得静默挤入清单；必须先修复或按永久删除决议清理旧项，否则对应需求级验证保持不通过并交监督循环处理。
- 条目登记满 14 天前必须完成以下二选一：修复测试并移出清单，或经明确决议删除无效测试并移出清单。
- 超过 14 天的条目必须出现在每日回归记录和监督循环报告中；不得继续以“已在清单”视为完成处理。
- 测试修复或删除后，同步移除隔离记录并更新数量；隔离清单刷新不能只增不销。
- 永久删除测试属于独立决议，不由执行器自行决定或自动删除。

## 6. 现行规则迁移清单

| 现行机制 | 目标处理 | 迁移时点 |
|---|---|---|
| light / scoped / full 三档 | 简化为循环内验证 + 队列验证 | R3 完成、R4 切换后 |
| 票内 full、集成票 full | 移到需求分支 push 触发的判读机 CI | R3 |
| 高风险票票内 full | 改为单独需求分支批次，由 CI 优先验证 | R3 |
| `once-at-final`、`full-ref` | 删除，改读 Gitea Actions 同 tree 判决 | R3 |
| 两级触发器、阈值升档 | 删除，不再用人工规则推算测试范围 | R4 |
| `scoped-map.yaml`、静态反向闭包选择器 | 由真实依赖映射替代 | R4 |
| `scoped-deferred`、`tier-reason` | 随旧档位和选择器删除 | R4 |
| evidence 档位门大段记录 | 改为执行器摘要、判决指针和 agent 一句结论 | R1 起逐步切换 |
| 每日回归 | 保留，并兼作依赖记录和慢冒烟执行入口 | 持续 |
| 隔离清单 | 保留并增加数量与期限约束 | R8 |
| 整机槽位 | 保留，循环内本机与 CI 各自经一次预算封装 | 持续 |
| 远端测试协议（SSH 提交测试命令） | 删除：远端只保留 push 触发的 CI full | 已删除（2026-10-29） |
| 主模型符合性核对 | 可自动化部分由 R6 替代，其余保留 | R6 起逐步切换 |

所有替代完成后，应同步清理旧代码、旧配置、旧模板和失效测试；过渡期可以回放对照，但不保留永久兼容层。

## 7. 分阶段落地与依赖

### 阶段 0：基线与契约准备

- 采集最近一个完整统计窗口的票内等待时间、full 墙钟、归因 token、失败类型和隔离数量。
- 冻结判决字段、失败签名规范、tree 新鲜度规则和自动归因边界样例。
- 试点仓确定为 pandu agent，先选一个已有 feature 集成分支与 `query` 影子运行，不直接全仓切换。

### 阶段 1：R1 执行器 + R2 可独立部分

- 先上线执行器与判决，保持现有流程做影子回放，对照 agent 原归因结果。
- 同时校准现有并行分片、历史耗时记录和慢冒烟夜间化在判读机 CI 中的运行参数。
- 影子对照一致后，`process_check.py`、evidence 与监督循环统一切换到判决。

### 阶段 2：R3 push 触发的判读机 CI 合并队列

- 先拆分测试与部署 push 触发边界，再对试点需求分支开放乐观合入。
- 验证 push → CI full → 判决回写 → 自动二分 → 回退 push → 新 CI full → 回修票和 main 放行条件后，取消票内 full。
- main 的放行门必须从切换第一刻就生效，不能先乐观合 main 再补队列。

### 阶段 3：R4 真实依赖选择 + 精确缓存

- 夜间积累依赖映射，与旧选择器对照选择集和漏选情况。
- 切换循环内选择集后删除旧档位、触发器和手工映射。
- 依赖映射稳定后启用精确结果缓存。

### 阶段 4：R5～R8 持续推进

- R5 在新代码和被修改的存量逻辑中逐步落地。
- R6 从新 feature 开始建立独立黑盒验收，旧 feature 不批量补写。
- R7 的规则拆分可与阶段 1 同步，先阻止新的环境依赖进入编码票。
- R8 无需等待执行器完成，可先清理存量隔离项并建立 14 天治理节奏。

## 8. 成功度量

以下指标以阶段 0 新采集的基线为比较对象，统计窗口至少覆盖一个完整 feature：

| 指标 | 口径 | 目标 |
|---|---|---|
| 编码循环内 full 次数 | 实现 agent 和监督循环在票收口前执行的 full 次数 | R3 切换后为 0 |
| 本机 full 次数 | 在本机任何 worktree 中调用全量测试入口的次数 | 全流程为 0；full 只有判读机 CI 记录 |
| full 触发与检出一致率 | 有有效 push / 每日回归 CI 触发，且 checkout SHA 与触发 SHA 一致的 full 运行数 / full 总运行数 | 100% |
| 自动归因覆盖率 | 清单外失败中无需 LLM 即得到确定归因的数量 / 清单外失败总数 | 可确定样例 100%；无法确定样例必须显式上报 |
| 归因 token | 为 base 对照、失败签名判断而消耗的 agent token | 正常自动归因场景为 0 |
| 循环内验证耗时 | 从提交执行到判决可用的墙钟，分 P50 / P90 统计 | 与基线对比；观察目标为常见票几十秒至 2 分钟 |
| full 墙钟 | 队列 full 从实际开始执行到完成的墙钟，不含排队 | 与基线对比；观察目标为并行后 5～10 分钟 |
| main 未验证合入数 | 没有同 tree full 与需求级验收通过判决而合入 main 的次数 | 0 |
| 自动回退闭环率 | 批次新失败中完成定位、回退、失败项复跑和回修票创建的比例 | 可干净定位与回退的场景 100% |
| 隔离超期数 | 登记超过 14 天仍未修复或形成删除决议的条目数 | 稳定期为 0 |

耗时和 token 数据来自执行器运行记录、宿主会话记录与队列记录；不得用 agent 自述估算替代。

## 9. 验收口径

### 9.1 执行器端到端

- Given 目标 commit 有一条清单外失败且 base 同测试通过，When 执行器运行，Then 判决包含该测试及签名，`pass=false`，且无需 agent 补跑 base。
- Given base 以相同签名失败，When 自动归因完成，Then 该项不进入 `new_failures`，但保留先存失败记录。
- Given base 无法运行或签名不可比较，When 自动归因完成，Then 结果为无法自动归因且不得通过。
- Given 判决对应 tree 与候选 tree 不同，When `process_check.py` 或 main 放行核对运行，Then 必须拒绝复用。

### 9.2 并行与缓存

- Given 同一组测试串行和并行各运行一次，Then 两者功能判决一致，并行记录包含分片、耗时和 worker 数。
- Given 任一依赖文件变化，When 再次执行缓存测试，Then 旧通过结果不得命中。
- Given 只有未变化依赖且环境一致，When 再次执行，Then 可复用通过结果并记录缓存来源。
- Given 上次运行失败或未完成，When 再次执行，Then 不得复用为通过。

### 9.3 合并队列

- Given 编码票循环内验证和评审通过，When 合入需求分支，Then agent 无需等待 full 即可结束。
- Given 批次 full 出现一条由某票引入的新失败，When 队列定位成功，Then 自动回退责任提交、复跑原失败项并创建回修票。
- Given 自动回退后原失败仍红或回退冲突，When 队列停止自动处理，Then 监督循环收到完整现场，main 不发生变更。
- Given 需求分支 full 通过后又合入新票，When 尝试合回 main，Then 旧判决失效并被拒绝。
- Given 高风险票进入队列，When 调度批次，Then 它单独成批且优先于普通批次。

### 9.4 依赖选择与快速反馈

- Given 夜间映射记录测试 T 依赖文件 F，When 某票只修改 F，Then 循环内选择集包含 T。
- Given 文件被删除或重命名，When 计算选择集，Then 变更前映射仍能选中相关测试。
- Given 映射缺失，When 循环内验证完成，Then 不触发票内 full，后续队列 full 仍必须执行。
- Given 新增核心逻辑，When 检查 tech-spec 与测试，Then 存在可单测边界、实现前见红证据和单条低于 1 秒的执行记录。

### 9.5 需求级验收与环境拆分

- Given spec 有三个行为点，When feature 开工验收设计完成，Then 每点都有黑盒测试或明确人工核对去向。
- Given 编码实现与验收断言冲突，When 没有 spec 变更依据，Then 实现票不得修改验收测试。
- Given 编码票依赖远端部署状态，When 拆票或流程核对，Then 该项被移出编码票并进入部署验证阶段。

### 9.6 基线治理

- Given 新条目没有修复票或责任人，When 尝试入册，Then 入册失败。
- Given 条目达到 14 天，When 每日回归与监督循环运行，Then 必须列出处理结果或超期项，不得继续静默隔离。
- Given 修复或永久删除完成，When 清单刷新，Then 对应条目销记且总数下降。

## 10. 风险与约束

1. **发现时点延后**：回归从“合入需求分支前”变为“合入后数小时内”。这是已选择的成本交换；main 通过同 tree 放行门保持不受影响。
2. **依赖映射不完备**：真实追踪可能漏掉动态加载和外部输入，因此映射只服务循环内提速，队列 full 与每日回归不可取消。
3. **缓存错误复用**：依赖键或环境键不完整会制造假绿；精确缓存必须等待 R4，并保留缓存来源和强制实跑能力。
4. **自动回退误伤**：责任不唯一或 revert 冲突时不得自动选择提交，应停止并交监督循环。
5. **验收测试同源失真**：验收测试必须由独立 agent 从 spec 生成，并限制实现票修改，否则只是把原有窄 DoD 换成另一种形式。
6. **环境口径回渗**：部署验证独立后，流程核对必须阻止编码票再次加入部署前置条件。
7. **规则双写漂移**：模板、生成后的 SKILL、运行配置和宿主分发必须同步；旧实现切换后删除，不保留长期双路径。
8. **测试 push 触发部署**：pandu 现有 deploy workflow 监听 `feat/**`，不先拆触发边界会把测试传输变成部署动作；这是 full CI 上线前的硬前置。
9. **CI 证据不可复现**：本机 `dev/` 中的未入库隔离清单和 full 记录不会随 push 到 runner，必须迁移为受控输入或 CI artifacts，不能默认 runner 与本机看到同一文件。

## 11. 涉及范围

### 11.1 规则仓

预计涉及：

- `skills/implement/SKILL.md.tmpl`
- `skills/shared/fragments/supervision-loop.md`
- `skills/shared/fragments/dispatch-protocol.md`
- `skills/shared/fragments/dev-config.md`
- `skills/shared/templates/agents-config.md`
- `skills/shared/templates/evidence.md`
- `skills/shared/templates/issue.md`
- `skills/shared/scripts/process_check.py` 及其测试
- `skills/shared/scripts/scoped_select.py` 及其测试（R4 切换后退役）
- `skills/tech-spec/SKILL.md.tmpl`（TDD 接缝与可单测边界）
- 新增确定性执行器、合并队列及各自测试，具体命名和部署形态由 tech-spec 决定

实现时同步更新生成后的 SKILL 与分发产物；实际执行跨宿主 `sync.sh` 前按仓库现行约定另行确认。

### 11.2 下游判读仓

- 复用 `tools/check.py` 现有并行与缓存能力，补统一判决、隔离执行和 CI 元数据。
- 新增判读机 full workflow，并收紧现有 deploy workflow 的分支触发集合。
- 需求分支、`query`、main 放行和自动回退配置。
- `dev/known-failures.yaml` 迁移为 CI 可复现的受控配置，并执行存量清理与硬约束。
- `dev/scoped-map.yaml`、旧 full 运行记录和旧档位配置在对应阶段退役。
- Gitea Actions artifacts / commit status、判读机 runner、夜间 CI 和部署验证流程接入。
- pandu 文件级改造与端到端流程详见第 13 章。

## 12. 已确定与待补充

### 已确定

- 优先实施确定性执行器和套件提速，再上线异步合并队列，之后替换测试选择机制。
- 票内 full 最终取消，高风险票在队列单独优先验证。
- 需求分支可以乐观合入，main 不允许乐观合入。
- 自动回退只发生在需求分支；main 始终只接受已验证需求。
- 每日回归、TDD、独立评审和不可自动化语义核对继续保留。
- 存量冒烟不批量重写，隔离清单不无限期保留。

### 实施前需要由 tech-spec 补齐

- 试点仓库已确定为 pandu agent；优先选择一个 feature 集成分支与 `query` 做影子运行，具体先行 feature 由实施会话确认。
- 失败签名的具体规范化规则及无法归因的复跑次数。
- full 在 Gitea Actions 的并发、夜间时间窗和高风险独立批次配置。
- 依赖追踪工具、映射有效期和缓存强制实跑边界。
- 需求级黑盒验收的独立 agent 入口，以及不可自动化项的复核责任。

这些内容影响实现方式，不改变本需求已确定的行为边界。

## 13. pandu agent 仓库完整落地需求（2026-10-28 补充）

本章是上述通用方案在 `~/Desktop/yufeng-pandu-agent` 的具体化。若通用章节与本章的执行位置描述存在差异，以本章的 **“本机零 full、push 触发、判读机 CI 执行、同提交判决放行”** 为准。

### 13.1 现状核对与改造起点

| 现有资产 / 现状 | 已具备的能力 | 本需求要补的缺口 |
|---|---|---|
| `tools/check.py`、`tools/check_runtime.py` | 已支持 `--jobs`、慢项优先、历史耗时、显式串行项、整树内容与环境指纹缓存 | 增加统一机器判决、CI 元数据、隔离清单实执行、CI artifacts 与放行消费；不重复开发并行器 |
| `tools/test_categories.yaml` | 已有功能、非阻塞、环境依赖、helper、参数化和串行分类 | 增加 CI / 夜间执行策略及必需环境项的明确放行语义 |
| `.gitea/workflows/deploy.yaml` | 判读机 Gitea Actions 已存在，push `main`、`feat/**` 后调用部署同步 | 在新增 full workflow 前拆开测试与部署触发集合，禁止测试 push 意外部署 |
| `configuration/deploy/topology.json` | 已有 judge host、Gitea runner 组件、测试分支 allow / deny 和远端预算寻址 | full CI 复用判读机与预算；部署 allow / deny 不得被当成 CI 测试授权 |
| `dev/agents-config.md` | 已有集成分支、query 基线、quarantine 和测试档位规则 | 改为循环内验证 + CI full；删除本机 full、票内 full、`full-ref` 和本地复跑要求 |
| `dev/known-failures.yaml` | 已有 46 条存量及去向记录 | 文件当前注明未入库，runner 无法从 push commit 获得；必须迁移为 CI 可复现的受控输入 |
| `dev/full-runs/` | 本机历史记录目录 | 不再是 full 真源；真源改为 Gitea Actions 运行、commit status 与 artifacts |
| `tools/scoped_tests.py`、共享 `scoped_select.py` | 当前循环内关联测试选择能力 | 短期保留给循环内验证，R4 真实依赖映射稳定后退役；与 full CI 解耦 |
| `gitlab-ci: manual` | 当前用于评审模式语义 | 不复用、不改写为测试 CI 配置；测试 CI 单独建模，避免影响 OCR 评审 |

pandu 当前在 `query` 分支，且已有 `feat/A1规则配置数据链` 等集成分支。落地不得假设所有票都以 `main` 为 base，也不得把 `query` 当作普通票分支。

### 13.2 分支类型与验证职责

| 分支类型 | pandu 示例 | 循环内验证 | push 后 full | 自动回退 | 部署权限 |
|---|---|---|---|---|---|
| 编码票分支 | `codex/统一能力构建-issue-001`、`feat/A3分析-issue-075` | 必跑，本票 worktree 本机执行 | 默认不跑，避免每个修复 commit 都产生全量成本 | 不适用 | 禁止 |
| feature 集成分支 | `feat/A1规则配置数据链` | 票合入前已完成 | 每次集成分支 push 必跑， checkout 被 push 分支 | 允许，仅限已登记集成分支 | 仅部署授权分支可有；full job 本身无 |
| 独立工作 / 集成分支 | `query` | 以 `query` 为票 base | 每次 `query` push 必跑 | 默认不自动回退；只有显式登记后才开放 | 按既有实例授权，full job 不新增 |
| 主干 | `main` | 不乐观合入 | main push、每日回归 CI 执行 | 禁止自动回退 | 沿用现有部署流程，不因测试 workflow 扩大 |

补充规则：

- “在被 push 分支执行”是指 CI 的 checkout ref、HEAD SHA、tree 和判决都必须对应该次 push，不得统一 checkout `main` 后拿其他分支 diff 冒充分支 full。
- 编码票分支 push 只用于代码传输或评审，不默认触发 full。
- 高风险票要单独形成一次集成分支 push；该批次的 full 不与其他未验证票混批。
- main 的合并与推送仍遵守 pandu 现行人工授权。本需求新增的是验证必要条件，不授予自动合并 main、自动推送 main 或自动部署的权限。

### 13.3 端到端目标流程

#### A. 编码票循环

1. 实现 agent 在票 worktree 中按 TDD 提交代码。
2. 循环内执行器在票 worktree 本机运行 fast、本票单测、受影响测试和票级 DoD；输入必须是已提交 SHA。
3. 清单外失败由执行器在本机 base 隔离工作区补跑单项；不跑 base full，也不由 agent 阅读整套日志归因。
4. `process_check.py` 只要求循环内判决、票级 DoD 和评审材料，不再要求 `tier-executed: full`、`full-commit` 或 `full-ref`。
5. 独立代码评审与需求符合性核对通过后，主会话把票合入其目标集成分支。此时只表示“代码已集成”，不表示“feature 已具备 main 放行条件”。
6. 编码 agent 结束，不等待 full；票分支与集成分支的过程状态分别记录。

#### B. 集成分支 push 与 CI full

7. 主会话在授权范围内对集成分支做普通同名 push，禁止强推。push 是开发流程内 full 的唯一触发入口。
8. Gitea Actions 在判读机 runner 上启动 full workflow，checkout `github.ref` / `github.sha` 对应提交，并核对实际 HEAD 与 tree。
9. CI 使用独立代码目录、独立临时目录和测试专属状态，通过判读机既有预算执行 `tools/check.py` 与需求级黑盒验收；不得调用部署同步、track、服务重启或生产配置写入。
10. CI 生成统一判决：核心 JSON 写 commit status / Checks，完整 JSON、环境摘要、隔离清单版本、日志和原始结果写 Actions artifacts。
11. CI 通过只表示该 SHA / tree 通过；分支随后前进时，旧判决自动失去当前放行效力，但保留历史证据。

#### C. CI 红灯处理

12. full 或需求级验收出现清单外失败时，执行器在同一判读机 CI 环境对 base 补跑失败单项，先区分本批新增、先存失败和无法自动归因。
13. 对本批新增失败，队列在 push 的 `before..after` 提交范围内二分，只执行失败项；不得在二分过程中反复运行 full。
14. 责任提交唯一且可干净 revert 时，自动处理方在已登记的 feature 集成分支创建 revert commit，并开回修票。禁止在 `main` 自动回退，`query` 默认也不在自动回退范围内。
15. 回退提交普通 push 后触发新的 CI full；新 full 通过才恢复该分支的 main 放行资格。禁止在本机执行 full 来缩短这条回路。
16. 回退冲突、责任不唯一、失败项复跑仍红或自动处理达到上限时，停止自动回退并交监督循环；保留 CI run、失败签名和已执行动作。

#### D. main 放行与部署

17. feature 合回 main 前，主会话核对：候选 tree 的 full 为 pass、需求级验收为 pass、不可自动化符合性项已处理、没有未解决的 CI 回退或回修阻断。
18. 合并产生新 commit SHA 但未改变 tree 时，可以按 tree 复用集成分支判决；tree 改变则必须重新在候选分支 push 并通过 CI，禁止只按分支名或旧 SHA 放行。
19. main push 后由 main CI 再做套件级回归，并由现有 deploy workflow 处理部署。两者分别记录，部署成功不能替代 full 通过，full 通过也不能冒充部署验收。
20. 每日回归使用同一 full workflow 在判读机 CI 执行；不提供“本机空闲补跑 full”的兜底。

### 13.4 full CI workflow 需求

新增独立 workflow，目标文件为 `.gitea/workflows/test-full.yaml`；具体名称可在 tech-spec 中调整，但不得并入 deploy job。

- **触发集合**：开发流程内只监听已登记的 feature 集成分支、`query` 和 `main` 的 push；票分支默认排除。每日回归与失败重跑可通过同一 workflow 的 schedule / workflow_dispatch 触发，仍只在判读机执行。
- **精确检出**：每次运行记录触发 ref、触发 SHA、实际 HEAD、tree、push before SHA 和 workflow run id。任一核对失败均为运行无效，不产出 pass。
- **runner 限定**：只能落到判读机 self-hosted runner。不得因 runner 标签漂移跑到本机或其他共享 runner。
- **并发语义**：同一 ref 的 full 串行；被更新 push 取代的旧候选可以取消或标记过期，取消不得记为测试失败或通过。部署 workflow 的 `cancel-in-progress: false` 语义独立保留。
- **资源预算**：runner 内只经过一次判读机 `test_slots` 或等效既有预算封装；禁止本地预算覆盖远端预算，禁止双层套锁。
- **权限最小化**：full job 默认只有读取代码、写 commit status 和上传 artifacts 的权限，不拥有部署、重启、生产配置或 main 写权限。
- **缓存语义**：普通分支 full 可使用整树指纹一致的 CI 缓存；用于 main 放行的 full 必须能证明来源，首期采用 `--no-cache` 实跑，后续若要开放缓存须单独验收。不得把本机 `.local/check-smoke.json` 上传为放行证据。
- **环境隔离**：只使用测试专属凭据和合成 / 冻结测试数据；不得加载生产业务凭据，不得写共享部署配置或在线业务服务。
- **证据保存**：核心判决可被 API 读取；完整日志不进入 agent 上下文。artifacts 保留期须覆盖 feature 验收与复盘周期，过期后只能视为历史摘要，不能作为新放行证据。
- **失败语义**：workflow 基础设施失败、runner 掉线、checkout 失败、artifact 上传失败和超时均为“未得到有效判决”，不得转换成 quarantine 或 pass。

### 13.5 部署 workflow 的硬前置改造

现有 `.gitea/workflows/deploy.yaml` 监听 `main`、`feat/**`，与“push 集成分支跑测试”组合后可能产生非预期部署。上线 full CI 前必须完成：

- 部署 push 触发集合只包含明确允许自动部署的分支，不再用 `feat/**` 同时覆盖测试与票传输场景。
- 编码票分支和仅用于 CI 的分支一律不能进入 deploy workflow；topology 的 allow / deny 只作为部署侧第二道约束，不能替代 workflow 触发隔离。
- full workflow 不得调用 `/opt/pandu/bin/pandu-env-prod sync` 或等效部署入口。
- 自动回退 push 与普通集成分支 push 同样先经过部署触发判定；不能因为是 bot push 就绕过或自动获得部署权限。
- 若某集成分支确需 push 后自动部署，必须单独登记，并分别呈现 full 判决和部署验收，不把两个状态合并为一个绿勾。
- 必须增加静态或行为测试，证明每类分支 push 后只会触发预期的 workflow。

### 13.6 CI 输入、隔离清单与证据真源

- `dev/known-failures.yaml` 当前未入库，不能作为 runner 的隐式输入。实施时迁移为随分支分发的受控测试配置，建议与 `tools/test_categories.yaml` 同级管理；具体路径由 tech-spec 决定。
- 隔离配置必须包含条目签名、owner、destination、first-seen 和版本 / 内容哈希；full 判决记录实际使用的配置哈希。
- 修改隔离配置属于受控变更，不能由 full job 为消除红灯自行写入。新增、销记和永久删除仍按 R8 的修复票与决议流程处理。
- CI 不得读取本机绝对路径下的 `dev/known-failures.yaml`、`.local/check-smoke.json` 或历史 full 日志作为通过前提。
- full 结果真源改为 Gitea Actions：commit status 提供当前门状态，artifact JSON 提供完整判决，workflow run 提供执行环境和日志。
- `dev/full-runs/` 如需保留，只能是由 CI 结果生成的本机只读镜像，不再被 `process_check.py` 当作原始证据，也不允许手工编辑成 pass。
- `process_check.py` 与主会话通过 Gitea API 或同步下来的只读判决核对 run id、ref、SHA、tree、结果和 artifact 完整性；API 暂时不可用时保持未验证，不回退本机 full。
- 环境依赖用例继续区分 pass、failed、skipped-env 和 quarantined。skipped-env 不算通过；feature 的需求行为依赖这些用例时，必须另有部署 / 环境验收，不能凭其余测试绿放行。

### 13.7 base 与批次归因口径

- 普通集成分支 push 的首选归因 base 是 push 事件的 `before` SHA，前提是它存在于当前分支历史中。
- 新分支首次 push 没有有效 `before` 时，使用该 feature 已登记的集成 base 或最近一次同分支有效绿判决；两者都不存在时标记无法自动归因，不默认选 `main`。
- 使用 `feat/A1规则配置数据链` 的票继续以该集成分支为 base；使用 `query` 的票继续以 `query` 为 base，禁止跨分支拿 `main` 失败豁免当前分支。
- base 单项补跑必须在与 branch 相同的判读机运行时、依赖和测试凭据下执行，并使用独立 worktree / 临时状态。
- 强推导致 `before` 不是可用祖先、base 检出失败或环境不一致时，本次归因无效；本方案不开放强推来解决该问题。
- 非冒烟段失败，例如 `config_lint`、`physical_binding_lint`、`skill_lint`、`import_lint`，也必须有稳定测试标识并进入同一判决，不得只处理 `tools/tests/**` 而漏掉 check 前置段。

### 13.8 pandu 仓具体改动清单

| 文件 / 区域 | 需要改动 |
|---|---|
| `.gitea/workflows/test-full.yaml` | 新增：分支 push / 每日回归触发、判读机 runner、精确 checkout、预算、full + 需求验收、判决与 artifacts、同 ref 并发 |
| `.gitea/workflows/deploy.yaml` | 修改：收紧部署分支触发集合，与测试分支隔离；保持部署不可中途取消的现有安全语义 |
| `tools/check.py` | 增加机器可读判决输出、CI 运行元数据和非冒烟段统一失败项；切换完成后增加本机 full 防误触限制，提示通过分支 push 使用 CI |
| `tools/check_runtime.py` | 复用现有并行与缓存；接入真实 quarantine 执行、CI 缓存 / 耗时持久化、判决摘要和输入变化失效 |
| `tools/test_categories.yaml` | 登记 CI 必跑、夜间、测试环境和 main 放行所需分类；保持 perf / display 非阻塞边界 |
| 受控隔离配置（新） | 接收 `dev/known-failures.yaml` 迁移内容，随分支进入 CI，记录配置哈希并执行 R8 硬约束 |
| `dev/known-failures.yaml` | 只作迁移来源；切换后删除本机隐式真源，不与入库配置双写 |
| `tools/scoped_tests.py` | 短期只服务循环内选择，不再具有 full fallback；R4 后随共享旧选择器退役 |
| `dev/agents-config.md` | full 改为“判读机 Gitea Actions，push 触发”；登记分支类型、CI 判决真源和 main 放行；删除票内 full、每日本机补跑、`full-ref` 等旧口径 |
| `protocol/workflow/git约定.md` | 明确授权后的集成分支普通 push 是 CI 测试触发动作；仍禁止评审中 push、强推和未授权 main / 部署操作 |
| `configuration/deploy/topology.json` | 复用 judge / runner / 预算寻址；只在需要补充 runner 或分支登记时修改，不把测试 workflow 当成部署 track 配置 |
| `docs/GLOSSARY.md`、`docs/ARCHITECTURE.md` | 登记 CI full 判决、执行位置、证据真源和分支放行契约；只记现行态，不保留新旧双口径 |
| `tools/tests/**` | 新增判决、隔离、分支触发、CI 环境隔离、tree 新鲜度和回退流程测试；不为让旧断言通过而放宽规则 |

### 13.9 规则仓需要同步改动

- `skills/shared/references/remote-tests.md`、`skills/shared/scripts/remote_tests.py`：删除远端执行通路及其回执校验；远端只保留分支 push 触发的 CI full。
- `skills/shared/fragments/dev-config.md`：通用模板增加“CI-only full”执行模式；pandu 配置不得再把 full 写成可在本机或通用远端任务中运行的命令。
- `skills/implement/SKILL.md.tmpl`：删除 Step 5 / Step 8 的票内 full、最终 full 和本机归因补跑；实现流程只到循环内验证、评审和集成分支合入。
- `skills/shared/fragments/supervision-loop.md`：收口不再复跑 full；新增读取 Gitea 判决、处理红灯、回退后等待新 CI 和 main 同 tree 放行。
- `skills/shared/scripts/process_check.py`：票阶段不再要求 full 字段；feature / main 放行阶段核对 CI run、tree 和验收结果。`full-commit` / `full-ref` 随旧流程退役。
- `skills/shared/templates/evidence.md`：循环内 evidence 只保留本机循环判决；feature 验证记录增加 CI run id、ref、SHA、tree、artifact 和需求验收结果。
- `skills/shared/fragments/dispatch-protocol.md`：回修派发输入改为 CI 核心判决、责任 commit 和单项日志指针，禁止把完整 full 日志塞回实现对话。
- `skills/issue-split/SKILL.md.tmpl`：票面不再设置 full 档；高风险票标记“独立集成批次”，部署依赖拆到独立验证阶段。
- `skills/setup-dev/SKILL.md.tmpl`：增加 Gitea 测试 CI、runner、分支和 artifact 配置检查，与 OCR 的 `gitlab-ci` 配置分开。
- 生成后的 SKILL 与各宿主分发随实现同步；切换完成后删除旧 full 路径，不保留本机兜底。

### 13.10 推荐落地批次

1. **批次 0：触发安全前置**  
   拆 deploy / test 分支触发，登记 feature、`query`、main 和票分支行为；先证明测试 push 不部署。
2. **批次 1：判决与 CI 输入**  
   完成统一 JSON、非冒烟段失败标识、隔离配置入库化、base 单项归因和 artifacts。
3. **批次 2：full workflow 影子运行**  
   在一个 feature 集成分支和 `query` 上运行 CI full，与本机历史结果只读对照；此阶段不据影子结果自动回退。
4. **批次 3：切换消费方并禁止本机 full**  
   `process_check.py`、evidence、监督循环和 main 放行改读 CI；删除票内 full、本机 full 和 `full-ref`。
5. **批次 4：自动二分与回退**  
   先仅在已登记 feature 集成分支开启；`query` 与 `main` 不默认开放自动回退。
6. **批次 5：依赖选择、快速单测和治理持续项**  
   按 R4～R8 推进，不影响已建立的 CI-only full 边界。

### 13.11 pandu 专项验收场景

- **本机零 full**：在本机票 worktree 直接请求 full，流程与入口均拒绝并提示使用集成分支 push CI；fast、单测和受影响测试不受影响。
- **票分支不跑全量**：push `codex/...-issue-...` 或 `feat/...-issue-...`，不触发 full，也不触发 deploy。
- **集成分支精确执行**：push `feat/A1规则配置数据链@SHA-A`，CI 记录的 ref、HEAD 和 tree 均为 SHA-A 对应值，full 在判读机运行且没有部署调用。
- **query 独立 base**：push `query` 后发生清单外失败，base 使用前一个有效 `query` 提交，不使用 `main` 豁免。
- **部署触发隔离**：对只用于测试的 feature push，Actions 中只有 full workflow；对明确登记自动部署的分支，full 与 deploy 的状态和证据分别可查。
- **新失败自动归因**：branch 单项失败、base 同环境通过时，判决进入 `new_failures`；base 同签名失败时不进入；base 不可用时不得 pass。
- **隔离输入可复现**：在全新 runner checkout 中，不挂载本机 `dev/`，仍能读取同版本隔离配置并复现相同 `quarantined_n` 与配置哈希。
- **红灯回退再验**：feature 批次 full 抓到一个责任提交后，自动 revert 并普通 push；必须产生新的 CI full，旧红灯与新判决按 run id 分开。
- **main 不放行旧证据**：feature 在 full 通过后新增一个 commit，即使分支名不变，主会话也拒绝用旧 run 合回 main。
- **merge SHA 变化但 tree 不变**：main 合并只改变 commit SHA 时，可按已验证 tree 通过核对；任何 tree 变化都要求新 CI。
- **环境缺失不冒充通过**：CI 缺少 `PANDU_KB_TEST_*` 时相关用例记 skipped-env；依赖这些行为的 feature 仍有未决环境验收，不能记全量覆盖完成。
- **每日回归不回本机**：定时触发后在判读机 CI 生成 main / query 记录，本机只读摘要，不出现本地补跑任务。
- **CI 不可用**：runner、Gitea API 或 artifact 服务不可用时，feature 保持未验证；任何会话都不得通过本机 full 解除阻塞。

### 13.12 明确不在本次授权内

- 不自动强推、删分支或修改历史。
- 不自动合入或推送 main。
- 不自动部署、重启服务或修改生产实例配置。
- 不让 full job 使用生产业务凭据或写真实业务数据。
- 不在 `main` 上自动 revert；`query` 的自动回退也需另行显式开启。
- 不用 CI 绿勾替代需求级部署验收和人工授权。
