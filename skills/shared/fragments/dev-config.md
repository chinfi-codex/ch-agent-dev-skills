## Dev 配置（agents-config）

dev 阶段 skill 开始工作前，先读取 `./dev/agents-config.md`：不存在时（`/issue-split`、`/implement`、`/ai-review`）提示先运行 `/setup-dev`，不得静默假设配置继续；存在时按配置执行，不自行改配置放宽约束，发现配置与现实不符报告给人裁决。

必要字段（结构见 `shared/templates/agents-config.md`）：

- `tracker`：`local`（markdown 票文件）或 `gitlab`（glab CLI）
- `main-branch`：主分支名（worktree 与 merge request 的基准）
- `repo-level`：A / B / C 仓库等级；A 类仓（飞行软件 / 涉密）禁止进入 `/implement`，只允许只读辅助
- `quality-gate`：质量门命令，三档——快速档（fast：秒级，类型 / lint / 单文件测试）、关联档（scoped：按最终 diff 换算的受影响测试集）、全量档（full：完整测试套件）。**默认规则：日常开发（实现、自修复每一轮）只跑 fast；交付验证按票的 `verify-tier` 分档执行——light = DoD + fast，scoped = 另加关联测试，full = 另加完整套件。full 不再每票必跑，但 full 档票与集成票必跑**
- `verify-policy`：验证档位策略（阈值 / 两级升档触发器 / 漂移规则 / 选择机制 / 先在失败归因）。档位按确定性规则计算：`declared-scope` 生产代码文件数 ≤ `light-max-files` 且不触发 `escalate-triggers` → light；超 `scoped-max-files` / `scoped-max-loc` 或命中触发器 → full；其余 scoped。触发器分两级——full 级（公共接口签名 / 数据迁移 schema / 鉴权权限）直接 full；scoped+ 级（共享模块 / 工具库、红线邻接）先判影响面可枚举性：静态反向闭包完整可枚举 → scoped（选择集 = 全闭包），不可枚举 → full。选择集大小不是升档理由（闭包大跑大闭包，占比 ≥ 50% 仅记录为评审信号）；DAG 无后继的集成票一律 full，动态依赖与跨模块交互风险由它统一兜底——唯一例外「模块隔离认定」：feature 全程只动不被外部依赖的叶子模块时，集成票按 module-scoped 跑该模块全闭包（不机械全仓），判定依据须可机器复核、有疑义仍 full。实测 diff 超出 `declared-scope`：同模块小漂移登记即可，命中触发器或超阈值则**只升不降**并补验证。先在失败归因按 `attribution`：默认 `known-failures`（失败比对落盘清单，仅清单外单项补跑 base 侧对照，不整套重跑）；`two-sided` 为保守档（每条失败双侧同命令对照）。**预计与实测分工**：拆票时**预估**文件数超 `scoped-max-files` 的票必须再拆、不得定档（`/issue-split` 粒度红线）；实现期**实测**超阈值才升档。**测试职责边界（`gate-scope`）**：AI 交付门只保证功能实现度（DoD 行为、契约、回归）；`non-gating` 类（默认性能基准 perf、展示样式 display）只执行一次并落盘记录，不阻塞交付、不进 `all-passed` 分母，由人工验收确认；缺外部依赖（环境变量 / 外部服务）的用例按分类清单显式 skip 并在 evidence 列出，不算失败也不算通过。**full 次数（`full-runs: once-at-final`）**：full 档每票只在评审通过后的最终 commit 跑一次；评审回修轮只跑 DoD + fast + 回修 diff 的 scoped；无冲突 rebase 且本票文件 blob 不变时复用证据不重跑
- `max-fix-rounds`：自修复轮次上限，默认 99；`review-round-alert`（默认 3）：同票评审达此轮数时主会话介入判断拆票 / 缩范围 / 升级模型（预警，不是硬停）
- `base-freeze`：票开工时固定 base，实现与评审期间不追集成分支，只在合入前 rebase 一次
- `mechanical-refresh`：预授权机械刷新白名单（路径 glob + 字段 / 生成块）；diff 可机器证明只动所列字段时直接刷新并登记，不停等人；白名单外的既有测试 / 夹具改动仍按红线处理
- `artifact-budget`：impl-log / evidence 正文行数与 impl 目录体积上限；正文只留结论与指针，过程放 raw，中间轮原始产物只留最终轮
- `redlines`：红线文件 glob 清单（协议文件、密钥配置、验收判定文件等），命中即停，不得绕过
- `dispatch`：执行派发方式。默认 `executor: subagent`（主会话通过宿主「新开独立对话」逐票自动派发并监督到合并）；`model` **按宿主 agent 分别配置**，每个宿主条目分 `default`（light / scoped 票）与 `escalated`（full 档 / 集成 / 高风险票、评审达 `review-round-alert` 轮后）两档优先级清单，均由人在 `/setup-dev` 确认；主会话取自身所在宿主的条目，缺条目则停下请人确认，不跨宿主套用；`parallel-max`：frontier 中 declared-scope 互不相交的票可并行派发的上限
- `ocr.mode`：评审模式。默认 `delegate`——ocr 只做文件筛选与规则解析（不调 LLM），评审由宿主 agent 内新开的独立对话执行；`local` / `ci` 为可选增强，需配置 LLM 端点
