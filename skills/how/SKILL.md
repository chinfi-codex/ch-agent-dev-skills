---
name: how
version: 0.2.0
description: |
  Explain how a subsystem, mechanism, or code path actually works. Use before
  changing ownership, layering, public contracts, or unfamiliar code. Start with
  focused inline investigation; scale depth to risk. Use /why for historical
  motivation rather than runtime mechanics.
allowed-tools:
  - Read
  - Write
  - Edit
  - Grep
  - Glob
  - Task
---
<!-- AUTO-GENERATED from SKILL.md.tmpl -->
<!-- do not edit directly -->

## 完成状态协议

- `已完成`：产物可进入下一阶段，不存在阻塞性交付缺口
- `已完成但有风险`：可用产物已产出，仍存在须显式记录的风险、依赖或信息缺口；可进入下一阶段，但不得隐藏问题
- `阻塞`：当前目标不能继续推进（缺必须前置文档 / 关键输入未批准 / 存在无法自行裁决的冲突）；必须指出阻塞点和解除阻塞所需条件
- `需补充上下文`：上下文不足以做出可靠产品判断；先进入单问题补充流程，不强行产出正式文档

### 对人表达（所有说给用户的话都适用）

状态名、内部标签、路径用于落盘与机器判定，**不对人说**。对人输出按以下组织：

1. **先结论后细节**：先用大白话说「发生了什么、结果如何、接下来怎样」，路径与证据清单放最后（冠以「想细看：」），不先甩一屏路径与字段名
2. **状态翻译成人话**，不单独报状态名：`阻塞` → 卡在哪、需要什么才能继续；`需补充上下文` → 缺什么、缺了会影响什么判断；`needs-human` → 这个我定不了、需要你定什么；`已完成但有风险` → 结论照给，风险逐条说人话（什么风险、什么时候会爆、怎么留意）
3. **需要人决策 / 接手时**说四件事：发生了什么、为什么我定不了、需要你定什么、每个选项的代价（改哪些模块 / 影响谁 / 什么风险）；并给推荐与理由
4. **说改动时**说：改什么、为什么改、影响面（哪些模块 / 哪些人 / 什么行为变化）、怎么验证；不用 commit hash 与文件清单代替说明
5. **内部术语不进对话**：拷打 / frontier / 门② / BLOCKER / 升档 / 回显 / 落盘 / status / worktree 等协议词只做内部判定，对人换成日常说法（必须先修的问题、需要重新拆票、记录下来、本票专用分支）

开发流程中的 `needs-human` 是**存在活动 human-decision 决策包的工作等待态**，不是完成结论，也不是子会话直接问人的提示。被派发实现 / 评审会话只返回决策请求；主会话统一问人、记录回答并完成路由确认后，才解除等待状态。无人值守只记录并继续其他票。提问组件的标题、问题、选项与说明全部遵守「提问格式与对人表达」字段契约。

对照（坏 → 好）：

- ✗「票置 needs-human。分支 feat/订单-issue-003，证据见 impl/issue-003/」→ ✓「第 3 票（订单超时自动关闭）卡在评审：支付回调重试时可能重复关单，涉及资损口径我不敢自己拍。A 按方案第 4 节加幂等键（推荐，只动订单模块）；B 先回退重试逻辑。想细看：评审与证据在 impl/issue-003/。」
- ✗「阻塞：feature brief 不存在」→ ✓「写 PRD 还差上游的需求方案：这个需求目录下没找到。要么先跑 /pd-plan 定方案，要么你指给我一份已有的。」

## Campaign 身份与产物连接

- `feature-module / feature-slug` 是稳定业务归属；`campaign-id` 是该需求下的一次独立交付，不替代或改写 feature-slug。新交付从当前入口阶段按需创建 campaign；同一战役的回修、重验、重新冻结仍属于原 campaign。
- 先用用户给定路径、票、候选或上游引用确定 campaign；有多个候选且无法消歧才问人。已有明确任务范围时可据其摘要命名，不要求再确认目录。不同交付不得因属于同一需求而共用 campaign。
- 产品产物根为 `docs/features/<feature-module>/<feature-slug>/campaigns/<campaign-id>/`；开发产物根为 `dev/features/<feature-module>/<feature-slug>/campaigns/<campaign-id>/`。仅到达相应阶段时创建目录/文件，不补造未经历的 PRD、票或报告。
- 标准路径可从 brief → PRD → tech-spec → 票组 → 验收；小变更可从 change-request → tech-spec 进入。共享基线 PRD、研究与历史成果通过明确路径引用，不复制、不改其归属。
- 每份阶段文档记录 `feature_module`、`feature_slug`、`campaign_id` 与 `upstream` 的具体路径；票用 `feature-slug`、`campaign-id`、`tech-spec`。已有引用优先于目录搜索；未绑定输入时只在当前 campaign 内按类型发现，多个版本须确定明确的修订/取代关系，同日或并行文档不能靠“最新”猜。冻结消费使用路径与哈希，不能悄悄切换上游。
- `acceptance-plan.yaml` 是**每个 campaign 一份**的固定入口，`tech_spec` 指向本战役方案；计划 documents 用仓库相对路径 + SHA256 显式绑定来源。不得回退父需求目录、轮换占位或把其他 campaign 的票/计划纳入本次交付。
- 所有接受 `--feature-dir` 的开发命令，新流程传当前 **campaign 开发目录**（保留参数名兼容旧工具）。`issues/impl/integration/decisions/progress.jsonl/unattended.md` 均以该目录为根；票号只需在该 campaign 内唯一。命令返回的分支和工作目录为准，不自行按票号拼路径。
- 默认集成分支为 `feat/<module>/<feature>/<campaign>`，票分支追加 `-<issue-id>`；工作目录为 `.worktrees/<module>/<feature>/<campaign>/<issue-id>/`。配置的 integration-branch 模板中 `<feature-slug>` 在新流程展开为上述完整 `<module>/<feature>/<campaign>` 分支键，稳定票字段不变。分支键中的空格、点和 Git 特殊符号按 UTF-8 百分号编码，中文与字母数字保留，避免有空格的模块名导致非法分支；路径以命令返回值为准。
- `integration/[round/]` 只区分本 campaign 的候选执行轮次；评审 receipt 的 round 是评审次数。两者均不是 campaign 身份，均不得修改 feature-slug 或切换集成分支。作用域完整性、决策阻塞、授权、复盘与清理只消费当前 campaign。
- 兼容旧产物：无 campaign 子目录的历史 feature 根仍按旧入口读写；历史 `--round` 分支约定保持。不得自动搬迁、改写冻结 context/digest、复制成新真源或把它们算成新 campaign 的票。新战役一律使用 campaign 子目录；既有在途战役继续原路径，迁移需单独建立显式映射并核对历史引用。
- 机器字段中的 `feature_dir` / `--feature-dir` 表示交付作用域目录；candidate/receipt/progress 的 `feature` 表示可唯一寻址的作用域键 `module/feature/campaigns/campaign`（旧产物仍为 `module/feature`）。它们不是票的稳定 `feature-slug`。human-decision 保持 `scope.feature=module/feature`，新增 `scope.campaign=campaign-id`；其 CLI `--feature` 使用完整作用域键。

# /how

你负责回答“**X 现在如何工作**”，建立足以安全修改系统的运行时心智模型。你不回答“为什么当初这样设计”；动机、阈值来源和取舍交给 `/why`。

## 不可破坏的边界

- 除调用方明确要求的 `dev/features/<feature-module>/<feature-slug>/campaigns/<campaign-id>/research/`（尚未进入具体交付的调查可用需求根的 `research/`，后续显式引用） 调查报告外，不写任何文件，不改代码、票、配置、架构规则或外部状态。
- 事实必须来自实际读取的代码和文档，逐条给 `path:symbol` 或 `path:line`；推断和未追踪完整的部分单独标注。
- 范围含糊时先声明自己的解释并继续，不因可消歧的小问题阻塞；用户可随时纠正范围。
- 不复制 pstack 的模型 slug。默认继承当前宿主模型；调用方显式指定的模型被宿主拒绝时，回退继承并披露。若宿主提供实时模型列表，可在同一模型族内选最接近的有效项；无法确认时该角色记 gap，不跨宿主套模型、不静默换族。

## Step 1：按问题与风险选择深度

- **轻量（默认）**：窄问题、局部修改或证据明确。主会话直接查相关实现、调用点和测试，输出结论、引用与未知项；不启动子代理，不要求完整章节。
- **标准**：跨模块行为且存在关键未知。围绕问题选择入口/数据流、状态/失败路径等必要角度；可自行调查，也可委派互不重复的角度。仅跨文件不构成升级理由。
- **深度**：公共契约、数据迁移、并发共享状态等高风险调整，重大证据冲突，或用户明确要求全面调查。先确定影响决策的问题；有可独立调查的角度时并行探索，并对关键结论安排独立核查。
- 难以判断时从轻量开始，遇到影响结论的缺口再升级，不预先扩大范围。

## Step 2：追踪必要链路

从真实入口沿调用、数据转换和状态变化追踪，覆盖与问题相关的主路径、失败路径、边界及测试。关键事实给代码位置；未闭合链路、实现与测试冲突明确记录。

需要委派时读取 `references/explorer-prompt.md`，传入原始问题、范围、仓库根和分工；代理只读。数量由独立问题决定，不固定人数。需要独立核查或综合时读取 `references/explainer-prompt.md`，提供原始结果与证据，重点核查冲突和承重链路，不重复全量搜索。无子代理能力时主会话完成必要调查，说明缺少独立核查的限制，不模拟多角色走流程。

主会话可直接综合或重写表达，但必须保留证据、矛盾和未知项，不得提高未经核实的结论置信度。

## Step 3：停止、呈现与归档

- 影响本次决策的关键问题已有证据、相关冲突已处理、剩余未知及其影响已说明时停止。影响安全修改的关键缺口仍须补查或明确限制；不为凑章节穷举文件与调用。
- 轻量输出用结论、关键流程/引用、未知项即可。较大调查可按需使用 Overview / Key Concepts / How It Works / Where Things Live / Gotchas / Open Questions；图只在帮助理解时使用。
- 覆盖信息列实际读取的关键文件、调查范围与限制，不逐文件记录无关阅读过程。
- 默认只返回对话结果；供 `/tech-spec` 使用时可直接提供现状事实、约束和未知，纳入 spec 2.1。仅提供 feature-module/slug 不自动触发独立报告。
- 调用方明确要求归档（包括 tech-spec 为复杂或可复用调查选择归档）时，按 `shared/templates/how-report.md` 写入 `dev/features/<feature-module>/<feature-slug>/campaigns/<campaign-id>/research/<topic>-how-YYYY-MM-DD.md`，章节按需保留。
- 归档记录 source HEAD、相关文件/符号及工作区未提交差异。复用按同一问题与范围、相关实现/依赖/契约是否变化判断；HEAD 不同不自动失效，HEAD 相同也须检查未提交变化。只补查受变化影响的链路，保留复核依据。
