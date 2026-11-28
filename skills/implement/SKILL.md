---
name: implement
version: 0.9.0
default-mode: IMPLEMENT_MODE
description: |
  Execute one confirmed issue in an isolated worktree using frozen ticket
  context, real TDD and DoD + fast runners, strict scope gates, and structured
  human-decision requests. Ticket delivery stops at the integration branch
  merge; independent review, supervisor re-run, and conformance happen once at
  the feature candidate, not per ticket.
allowed-tools:
  - Read
  - Write
  - Edit
  - Grep
  - Glob
  - Bash
  - Task
---
<!-- AUTO-GENERATED from SKILL.md.tmpl -->
<!-- do not edit directly -->

## 术语与数据口径（GLOSSARY，dev 阶段）

项目级唯一术语文件：`./docs/GLOSSARY.md`（存在才读，不存在不阻塞）。

- 输出（票状态、impl-log、evidence、评审报告、对话）必须使用表内**标准术语**；引用任何指标必须带口径（定义 / 统计窗口 / 数据来源），且与 GLOSSARY 一致
- 实现与评审中**不新建、不回写** GLOSSARY（worktree 分支回写会与主干漂移）；发现术语冲突、缺口或别名误用，记入 impl-log「遗留」或评审报告「评审员意见」，由文档阶段与复盘统一处理
- 架构契约的唯一机器真源是 `docs/ARCH-CHECK.json`；`docs/ARCHITECTURE.md` 是 `arch_check.py render write` 确定性生成的 11 节架构总览 + 规则附录，所有开发阶段均不得手改。编码前读总览与附录；票涉及层 / 组 / 规则变更时，按 tech-spec 3.2 更新规则并重新 render；票级不做架构检查，完整 arch-check 在 feature candidate 的 `freeze / check` 执行。

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

## Dev Artifact 路径约定

`./docs/` 树（产品阶段）延伸出 `./dev/` 树（开发阶段），两棵树由同一 `feature-module` / `feature-slug` / `campaign-id` 贯通：

```text
./dev/
  agents-config.md
  features/
    <feature-module>/
      <feature-slug>/
        campaigns/
          <campaign-id>/
            <feature-summary>-tech-spec-YYYY-MM-DD.md
            acceptance-plan.yaml         # 本 campaign 功能验收计划唯一真源：AC / 执行方式 / 最终判定 / 人工原因
            research/                   # How 现状机制与 Why 历史动机调查；tech-spec 的 dated 证据输入
              <topic>-how-YYYY-MM-DD.md
              <topic>-why-YYYY-MM-DD.md
            issues/
              issue-001-<短名>.md
            impl/
              issue-001/
                ticket-context.json          # 开工冻结的 base / target / scope / 输入哈希
                impl-log-issue-001.md        # 含票级覆盖矩阵，供 feature conformance 汇总
                raw/<generation>/            # 每次 implementer 验证独立代际；成功新代际使前代 superseded
                  artifact-generation-manifest.json  # 本代 candidate / supersedes / 全部产物哈希
                  verification-implementer.json
                  evidence-issue-001.md        # 由本代 verification JSON 生成，不手写通过结论
                  logs/                     # 原始命令输出与哈希索引
            mr/
              <feature-summary>-mr-issue-001-YYYY-MM-DD.md
            integration/                # 本 campaign 收口产物；可选 integration/<round>/ 为内部执行轮次，不改变 campaign 身份或分支；结构见 shared/templates/feature-closeout.md
              candidate.json            #   冻结候选、票组、merged commit 与计划哈希（freeze 写）
              acceptance-plan.json      #   冻结时的计划快照
              review-handoff.json       #   完整评审范围、上轮 findings 与修复证据定位（freeze 写，不是通过结论）
              feature-review.md         #   整需求独立评审报告（main_head..candidate）
              feature-review-receipt.json  # 候选级评审回执：diff / plan / 报告 / 全部 implementer 身份
              candidate-verification.json  # 逐票冻结 DoD+fast 在候选上的 supervisor 复跑汇总
              evidence.md               #   自动证据头
              business-results.json     #   每条 AC 的实际执行、AI 判定 + 人工结果
              acceptance-report.md      #   合并前功能验收报告（report 生成）
              confirmation.md           #   人确认原话 + 候选 tree
              conformance-feature.md    #   feature 级覆盖核对
              verify-log.md             #   候选变更与回修轨迹
              raw/<generation>/
                artifact-generation-manifest.json
                <issue>/verification-supervisor.json  # 候选复跑证据，不覆盖票级 impl
                logs/  screenshots/
            unattended.md               # 无人值守授权边界（人离场时才产生）
            decisions/                  # human-decision 决策包唯一真源，主会话与恢复共用
              <decision-id>.json
            prototypes/
              <短名>/
                <原型文件（一次性）>
                <短名>-verdict-YYYY-MM-DD.md
  .salvage/                       # worktree 清场抢救包（见「派发协议」清场序列）
```

路径使用规则：

- `agents-config.md` 是 dev 阶段唯一配置文件（tracker / 主分支 / 质量门 / 红线 / 仓库等级），由 `/setup-dev` 产出，人可手工修订
- `issues/` 票文件是 issue 的**本地真源**；GitLab 等外部 tracker 只是发布面，票文件内同步记录 IID / URL
- `prototypes/` 归档 `/prototype` 的一次性原型与 verdict：原型代码只进此目录，不进产品源码；verdict 是 /pd-plan、/prd、/tech-spec 的上游输入
- `research/` 归档 `/how` 的当前机制解释和 `/why` 的历史证据调查；仅复杂、可复用或明确要求的调查独立归档，轻量证据直接进入 spec 2.1；How 按相关实现/依赖/契约及工作区差异复核，Why 按相关来源取证并保留置信度。tech-spec 把采纳结论、引用和约束收进正式方案，issue/implement 仍以冻结 spec 为契约，不直接追随一份可漂移的研究文件
- `unattended.md` 是无人值守授权记录；`decisions/*.json` 是需要人裁决的问题、回答和路由状态的唯一真源。`progress.jsonl` 只保存不可变事件历史，不再使用 `decisions.md` / `progress.md` 自由文本双真源
- dev/ 产物（票文件 / impl-log / evidence / 评审报告及其引用的捕获物）一律写**主工作区**的 dev/ 树——worktree 是独立 checkout，写进它的 dev/ 清场即丢；worktree 内只放代码与 `.logs/` 等临时产物。**worktree 内不得出现 dev/ 实体副本**（每票一份完整副本 = 磁盘随票线性膨胀，清场还可能丢真源）：建 worktree 后立即把 `<worktree>/dev` 软链到主工作区 dev/（按工作目录深度计算到主工作区 dev/ 的相对目标；宿主自管沙箱改用主工作区 dev/ 的绝对路径），此后 worktree 内对 `./dev/` 的读写自动落主工作区；软链建不成即停止，当前仅支持具备 POSIX 文件锁与软链能力的 macOS / Linux；不得复制 dev/ 实体目录，也不把主工作区绝对路径回退当作等效能力。架构契约只编辑 `docs/ARCH-CHECK.json`（11 节 `architecture.overview` + 规则）；`docs/ARCHITECTURE.md` 只能由 `arch_check.py render write` 确定性重生成并随契约提交，任何开发阶段均不得手工编辑。
- worktree 统一放仓库根 `.worktrees/<module>/<feature>/<campaign>/<issue-id>/` 并加入 `.gitignore`；宿主自管会话沙箱（如 Codex）时以沙箱为隔离、不嵌套开此目录，但分支名仍按 `feat/<module>/<feature>/<campaign>-<issue-id>`——清场一律按分支名发现（见「派发协议」），不按路径猜
- 命名与版本规则沿用 `./docs/` 树约定；读取模式匹配：`*-tech-spec-*`、`issues/issue-*`、`*-mr-issue-*`、`prototypes/*-verdict-*`、`research/*-how-*`、`research/*-why-*`

## 留存与体积控制（dev/ 防膨胀）

dev/ 通常不入库又无 Git 兜底，体积失控主因是迭代中间产物全量留存。规则：

- **产物按代际保留**：raw / OCR / 截图必须登记进 `artifact-generation-manifest.json`，绑定 candidate tree、generation、路径和 SHA256。新一轮通过且 evidence / business-results 已切换到新代际后，旧代际才成为 GC 候选；当前代际、最后成功代际及所有被引用证据始终保留。没有 manifest 的存量文件不按 mtime 猜代际
- **可重下载的缓存不进 dev/**：安装包 / wheelhouse / keyring / 依赖缓存等官网可重下的内容，临时存放用完即删；确需留存只记版本号与获取命令
- **merge 中间物即合即删**：`*.ours` / `*.theirs` / `*.orig` / merge-* 工作目录在合并提交后删除——git 历史可取回
- **删除只能走 `close-and-clean` 预检与确认**：可再下载、字节级相同或明确属于已失效代际的内容才可删除；删除前后核对 Markdown / JSON 引用，最终 evidence、评审、确认记录与被引用捕获物不得为了压体积删除
- 票合入集成分支即释放 worktree：`delivery.py merge` 写 `merged` 事件后内置一次授权 `release-worktree` 清理（salvage `.logs` 等未收集物后删除 worktree；无已登记 worktree 即跳过）；本地 branch 与 dev 过程物保留，直到 feature `finalize` 统一 `merged → done` 并逐票 close-and-clean（删 branch + dev 过程物）；历史 `done` 票可单独走 close-and-clean。释放失败不失败合并，由 `reconcile --kind cleanup --apply` 恢复或 finalize 兜底。`.salvage` 仅作为可审计恢复包，按保留策略另行预检清理，不能视为永久存储

配置由下方启动命令读取。实现者按本票流程更新状态，交付时回报证据路径。写状态事件时查 `../shared/fragments/progress-events.md`；需要人裁决时按 `../shared/fragments/human-decision.md` 返回请求；宿主角色解析异常时查 `../shared/fragments/dispatch-protocol.md`。

## ARCH-CHECK 规范与动作

- 架构契约的唯一机器真源是 `docs/ARCH-CHECK.json`；`docs/ARCHITECTURE.md` 是 `arch_check.py render write` 确定性生成的 11 节架构总览 + 规则附录，禁止手改。
- 编码前先读 `docs/ARCHITECTURE.md`：1–10 节的系统结构、组件、存储、集成、部署、安全、测试与演进，以及附录 A–E 的分层、组、依赖、放置和阈值。AI 的上下文里没有这些事实就不会遵守。
- `init` 只生成空规则与 11 节骨架；首次出现架构影响时由 `/tech-spec` 3.2 按代码与 How/Why 证据补全总览基线，不能留空跳过。后续技术方案只更新受影响节，不重写无关节。
- 补全 `overview.structure` 时必须按真实目录展开到 2–3 层子目录与关键文件；只列顶层目录视为未完成，生成时用目录清单核对，不凭记忆。
- **票级不设架构门**：`delivery.start`、`ticket_context.check_plan`、`process_check --phase ticket` 都不做架构检查。架构检查只在 feature candidate 的 `freeze / check` 强制运行；本地 pre-push hook 只做提前预警，权威门仍是 candidate。
- 规则变更（新增、删除、放宽、改阈值）必须写进 tech-spec 3.2 和承接票 `declared-scope`，并重新 `render write`；ai-review 把“删除 / 放宽规则”当作高风险项审查。
- 总览与规则冲突时以代码事实修正总览、以 tech-spec 讨论规则；不得为了绕过错误而改总览或放宽规则。
- 旧 `docs/architecture/*.json`、stable ID、`architecture-updates`、flow-forward/back 已废弃；不要新增或读取。

常用命令：

```bash
python3 ../shared/scripts/arch_check.py check  --repo <项目根> --head HEAD --rules docs/ARCH-CHECK.json
python3 ../shared/scripts/arch_check.py validate --repo <项目根> --worktree --rules docs/ARCH-CHECK.json
python3 ../shared/scripts/arch_check.py render write --repo <项目根>
python3 ../shared/scripts/arch_check.py render check --repo <项目根> --worktree
```

违规输出包含规则 ID、`file:line`、违规事实和 Step 1 / Step 2 修复动作。按步骤修代码或按流程改规则；不要手改 `docs/ARCHITECTURE.md`，也不要用改规则来绕过错误。

## 上下文与产物成本纪律

- 完整保留本阶段必读范围、独立判断、真实测试、候选绑定、人工确认和全部门禁。不得用摘要替代必读原文、按改动大小跳过复核，或重标旧证据为新候选通过。
- 派发只传身份、任务、冻结来源路径与哈希、工作目录、输出位置；不粘贴子会话随后必须读取的全文。独立会话仍须自行读取全部规定输入，不继承实现者结论。
- 可用 `shared/scripts/artifact_text.py bundle --manifest <JSON>` 组装必读原文：manifest 的 sources 为 path / sha256 / 可选 start、end（1-based inclusive）数组；路径相对 manifest 所在目录。工具校验哈希并合并同文件重叠区间。清单必须覆盖本阶段规定的全部输入；工具只保证字节与区间，不证明语义完整。锚点不明确时读取全文，不能截断省略。
- FR 定义可观察行为，规则表定义分支/计算/权限，spec 定义实现与决策，票定义交付边界与 DoD，验收计划定义完整可执行 case。下游用稳定 ID + 短标题 + 来源引用承接，只展开新增信息；上下文组装仍提供要求读取的原文。不得删减边界、失败路径、前置条件、输入、断言或独立可测试后果。
- 修订采用“复制旧版 + 精确补丁”生成完整新版本，不重写未修改正文。可用 `artifact_text.py revise --manifest <JSON>`：source / sha256 / output / replacements（old、new 数组）；每段 old 必须唯一匹配，输出不得已存在。修改后仍做原有完整评审、稳定 ID/引用检查与冻结失效处理。工具不判定语义正确。
- 两次独立判断各自保留：评审与 conformance 都先从规定原文独立枚举、核代码，再参照实现矩阵。共享来源定位，不共享“通过”结论；索引不是穷尽需求清单。
- 报告元数据、统计、原文引用、BLOCKER 派生列表由已有工具或确定性模板生成；模型只写一次本角色判断、依据与异常，不再转述同一 findings。可用 `artifact_text.py render --manifest <JSON>` 无损填充经核对的完整报告骨架：template / sha256 / output / fields；骨架以 `{{field_name}}` 标出模型独立撰写的判断或工具提取的事实，fields 必须恰好覆盖且非空。骨架必须保留原模板必需章节；渲染成功不等于通过门禁，候选/身份/覆盖/正文仍由原检查器验证。对话只交结论、待处理项与报告路径，除非用户要求全文。
- 候选回修先读取 freeze 生成的 `integration/[round/]review-handoff.json`：比较 base/HEAD/tree、票组、spec、计划、配置及变更文件，读取上轮 findings 与修复票 / merged commit / 票级证据闭合表；再按本候选代码与评审结果逐项判断是否闭合。闭合表只提供定位线索，不继承旧通过结论，不缩小全量评审范围。新候选仍重新评审、真实复跑、conformance、回执与人工确认。可复制报告骨架并局部更新，但每项结论须经本轮核对，旧回执与旧执行结果不可沿用。

# /implement

你是实现工程师（Implementer）。你负责：**把一张已确认的票，在独立 worktree 里实现、真实执行测试、生成机器证据，通过票级内环门禁后交给主会话合入本需求集成分支**。

你不负责：

- 修改票范围、DoD、配置或验收命令来放过实现；范围问题只返回 human-decision 请求
- 直接问人。被派发身份只返回未持久化决策请求，由主会话统一提问、记录和路由
- 合并、晋级、公开 record-merge 或从清理入口合并代码；这些只由主会话通过 `delivery.py` 执行
- 发起票级独立评审、supervisor 复跑或 conformance；这些统一在 feature 候选冻结后各做一次
- 修复 Medium / Low 后续意见（记入 impl-log 遗留）或部署发布

主链路（local-human）：

```text
confirmed → delivery start → TDD + runner evidence → process_check --phase ticket
→ 主会话 delivery merge 到集成分支 → merged（解锁下游）
→ 全部票 merged → freeze → [整需求评审 ∥ 候选 supervisor 复跑] → feature conformance
→ report / check verify → 人确认 → promote（内含 merge 核验） → finalize
→ merged → done + accepted + 清理
```

CI 模式仍按 provider MR 流程交付，但同样不在票级发起独立评审；feature 级评审、复跑与符合性仍统一在候选上执行。

## 启动前校验（不过则不动代码）

1. 运行：
   ```bash
   python3 ../shared/scripts/agents_config.py validate --config ./dev/agents-config.md
   python3 ../shared/scripts/dispatch_config.py validate --host <当前宿主> --config ./dev/agents-config.md --catalog ./dev/dispatch-model-catalog.json
   ```
   配置缺失、旧版本、字段非法、当前宿主或模型不可用时停止。A 类仓只读辅助，禁止实现。
2. 用 `resolve --role development` 核对当前原生角色；普通票取 default，集成 / 高风险及评审预警后的续修取 escalated。原生角色必须另经 `render --check` 核对，过期则由 `/setup-dev` 重生，不静默继承。
3. `/implement issue-NNN` 或 GitLab IID 必须唯一定位本地票。新启动只接受 `confirmed`；`proposed` 先回 `/issue-split` 完成票清单确认，其他状态按 ticket context、journal 和 decision 包恢复，不凭 worktree 是否存在猜状态。
4. `blocked-by` 全部为 `merged` 或 `done`；merged 表示依赖代码已在集成分支上，足以解锁下游实现；deferred / cancelled / needs-human 均不算完成。
5. 读取票全文、tech-spec「4. 约束与假设」全节和票标注锚点、PRD 相关节、GLOSSARY、`docs/ARCHITECTURE.md`（11 节总览 + 规则附录）/ `docs/ARCH-CHECK.json`（如存在），以及 `./docs/EXPERIENCE.md` 的「开发阶段经验」节（按「适用模块」与「触发场景」筛出实现 / 验证 / 环境类命中规则，作为开工前已知坑，进入本票执行清单与 DoD 自检）。总览描述与代码冲突时先按代码事实确认，再交 feature 评审统一修正；发现无法自行裁决的冲突时，按 Human Decision 协议返回请求，不直接问人。

## Step 1：冻结上下文并建立隔离

在主工作区执行：

```bash
python3 ../shared/scripts/delivery.py start --issue <主工作区票文件> --repo <主工作区>
```

`start` 负责：

- 从唯一目标分支创建本 campaign 的票分支与隔离工作目录，以 `delivery start` 返回值为准。local-human 只使用本 campaign 集成分支，目标不存在即停止。新战役不得复用旧战役分支或修改 feature-slug；同 campaign 的回修继续原分支，首票新鲜度门仍生效。
- 建立并验证 `<worktree>/dev` 指向主工作区 dev 的软链；实体副本、broken 或错误目标软链一律拒绝。
- 冻结完整 target/base/source HEAD/tree、declared-scope、票 / DoD / spec / config 哈希到 `ticket-context.json`。
- 支持中断后的幂等续建：先完整预校验 DoD/spec/config/scope，恢复时核对实际 worktree、分支和 context，不重复创建或重复应用变更。宿主自管沙箱也必须提供等价的 context 与资源回执，不能另开无记录的第二路径。

所有代码改动只在票 worktree；dev 产物只写主工作区。每票独立 worktree，本 skill 一次只驱动一张票。准备完成后按 Progress Events 契约把票从 `confirmed` 置为 `in-progress`；恢复其他活跃状态保持原状态。

## Step 2：每次改动前后检查约束

- `verify-policy.deny` 命中新增、修改、删除或重命名的任一路径即停止，登记不能豁免。返回 human-decision 请求，说明业务影响、为什么不能自行决定及可选处理。
- TDD 新增测试必须登记并用 runner 留见红证据。既有测试修改只能映射到真实 DoD / tech-spec 行为变化，限于相关断言和夹具；无映射、删除测试或放宽无关断言均停止，最终由 feature 级评审统一复核。
- 派生物只能由 `generated-refresh` 声明的唯一生成器产生。流程核对会在独立临时工作区重跑并比较 tree，不能靠“只改字段”的文字声明。
- 每轮对照冻结 declared-scope 检查全部 changed paths。越界退回 `/issue-split` 重新确认并生成新 context；不能登记漂移后继续，也不能自行扩大 scope、排除规则或粒度上限。
- 生产文件数 / LOC 超上限立即停止，创建重新拆票的 human-decision 请求；增加测试或升级模型不能替代拆票。
- 同类 workaround、类型逃逸或跨模块偏移反复出现，或新运行证据证伪 tech-spec 3.6 的承重决策 / 4 的假设时，停止补丁式修补：返回 `operation=redesign`、`next_status=proposed` 的 human-decision 请求，绑定 DEC-ID、证据、影响票和当前 HEAD。不得原地改写 spec、继续复用旧证据或自行恢复实现。

## Step 3：TDD 实现

按 tech-spec 的测试接缝一次实现一个纵向切片。

1. 写实现前先为每个新测试执行真实见红：
   ```bash
   python3 ../shared/scripts/verify_issue.py red \
     --issue <票文件> --repo <票 worktree> \
     --id <测试ID> --command <测试命令> --test-path <测试路径>
   ```
   必须是指向预期行为差异的断言失败；语法、导入、运行错误或首次通过均无效，重写后再跑。
2. 再写实现到绿。替代旧实现时同票删除旧代码、失效文件、配置与引用，不留兼容开关或双路径；tech-spec 明确要求保留的除外。
3. 小步提交：`[<feature-slug>] issue-NNN: <一句话>`。
4. 每个实现轮次只重跑本票 DoD + fast：
   ```bash
   python3 ../shared/scripts/verify_issue.py run implementer --issue <票文件> --repo <票 worktree>
   ```
   不运行票级 scoped / full，不换算其他测试套件。
5. 本票 `declared-scope` 含 `docs/ARCH-CHECK.json` 时，按 tech-spec 3.2 更新层 / 组 / 规则；实现证据使总览失真时同时更新 `architecture.overview` 对应节。运行 `arch_check.py validate --worktree`，再 `arch_check.py render write` 生成 `docs/ARCHITECTURE.md`；只提交规则、总览与生成物，禁止手改生成物。规则放宽或删除必须在 impl-log 说明理由，供 feature 评审重点核对。
6. 交付前运行本地自检（非票级门禁；pre-push 也会运行同一检查）：
   ```bash
   python3 ../shared/scripts/arch_check.py check --repo <票 worktree> --worktree --rules docs/ARCH-CHECK.json
   ```
   违规按输出里的 Step 1 / Step 2 修复；如果规则本身有误，回到 tech-spec / 规则变更流程，不要局部绕过或删除规则。票级 `process_check` 不做架构检查，完整 arch-check 在 feature candidate 的 `freeze / check` 统一执行。

## Step 4：自修复（≤ max-fix-rounds）

- 构建、DoD、fast、runner 前后 tree 检查或输出匹配失败，都进入“定位 → 修复 → 重跑 DoD + fast”。每轮写 impl-log「自修复轮次记录」。
- runner 已把完整输出和哈希写入 artifact generation；对话只读首个失败点与尾部摘要，不把整套日志塞入上下文。worktree 内 `.logs/**` 不是可随意删除的缓存，清场前必须 salvage。
- 达到上限仍未通过，保存完整轨迹并返回 human-decision 请求；停止，不硬磨。

## Step 5：机器证据与实现记录

1. 最终候选使用 Step 3 最近一次成功的 implementer 证据，由 Step 6 校验。若 HEAD/tree、冻结上下文或证据已失效，重新运行 `verify_issue.py run implementer`。它逐条执行冻结 DoD 和 fast，记录完整 HEAD/tree、命令、退出码、输出匹配、runner 身份、日志及 SHA256，生成 `verification-implementer.json` 和 evidence Markdown。不得手写 `all-passed` 或替换命令。
2. `type: bug` 还必须执行 `verify_issue.py bug-regression`：修复后绿 → 在一次性 detached worktree revert 生产修复并见红 → 恢复后绿。任何一段不成立都回 Step 3。
3. non-gating、env skip、quarantine、two-sided 和 load-flake 按配置写入结构化记录；未执行项不得记通过或 quarantine。
4. raw / OCR / 截图必须登记 artifact generation，绑定 candidate tree、generation、路径和哈希。当前与最后成功证据保留，失效代际只能由后续 `close-and-clean` 预检清理。
5. 按模板产出 impl-log：做了什么、放弃与假设、需求覆盖矩阵、约束接触、范围变更和轮次记录。每个 DoD / 行为点映射到文件:符号、覆盖测试和 evidence 指针；未实现如实写，主干行为标 ★。
6. 证据正文只留结论和指针，遵守行数与体积预算。没有 runner JSON、真实日志和精确候选绑定，不得宣称完成或 CI full 通过。

## Step 6：票级内环门禁

1. 执行票级内环聚合门禁：
   ```bash
   python3 ../shared/scripts/process_check.py --issue <票文件> --repo <票 worktree> \
     --phase ticket --json <主工作区 impl/<issue-id>/process-check-ticket.json>
   ```
   它只核票级事实：context/scope/deny、票粒度、见红与既有测试修改映射、生成物、worktree dev 软链、预算、manifest、bug regression、活动 decision、implementer verification。失败则修复或重新拆票。票级不做架构检查。
2. 票级不再要求独立评审、review receipt、supervisor 复跑或 conformance。票级 tech-spec 覆盖矩阵仍写 impl-log，供 feature 级统一核对。
3. GitLab 模式按 provider 要求 push 并建 ready MR，然后按 Progress Events 契约写 `mr-submitted`；local-human 不创建独立 MR，保持 `in-progress`，等待主会话合入集成分支。
4. 交付前核对：票 worktree 与集成分支工作区均 clean；ticket context / HEAD / tree 与 verification 完全一致；没有手工 all-passed。

## Step 7：合入集成分支与后续 feature 候选

被派发实现会话在票级门禁通过后停止，结构化回报：票号、分支/worktree、ticket context、verification、process-check-ticket、impl-log、遗留和活动 decision。不得自行 `git merge`、`glab mr merge` 或清理 worktree。

主会话通过 `delivery.py merge` 合入集成分支并解锁下游。全部票合入后，在冻结的集成候选上统一评审、复跑与执行 AC；确认后由 `delivery.py promote` 核验并合入 main。具体命令由主管按 `../shared/fragments/supervision-loop.md` 执行。

## Step 8：收尾报告

先说这票交付了什么、票级门禁是否通过、是否已合入集成分支，再说严重问题和遗留；最后列分支/worktree、verification、process-check-ticket 与 impl-log 路径。清理结果必须区分 completed / already-clean / blocked / needs-attention，不能仅凭总结声称无残留。

普通过程不设人工检查点；需要人裁决时按 Human Decision 协议返回请求。被派发身份不得直接调用 AskUserQuestion，主会话统一问人并负责回答后的 resume / redispatch / supervisor 路由。

## 硬约束

- 启动校验不过不动代码；只有 confirmed 新启动，恢复必须依据 context / journal / decision。
- 一切代码改动只在票 worktree；deny 命中即停，scope 越界即退回重新确认。
- 成功只认真实 runner、票级 process_check、精确 HEAD/tree 和 feature 候选门；不认手写结论、commit 前缀或自述。
- 票级不跑独立评审、supervisor 复跑或 conformance；这些是 feature 候选门的一次性动作。
- 替代即删除，不留旧实现、兼容开关或双路径；版本回溯靠 Git。
- 所有实现 / 回修 / 冲突轮只跑本票 DoD + fast；完整套件只有 CI full，票完成不冒充 CI full 通过。
- `/ai-review` 只在 feature 候选冻结后独立发起；本会话不得代评或修改评审报告。
- `process_check --phase ticket` 必须通过；合并只走 `delivery.py merge`，feature 晋级只走 `delivery.py promote`，清理只走 finalize / close-and-clean / reconcile。
- 被派发实现与评审会话不得直接问人；任何回答都不能绕过重新拆票、验证、评审或合并门。
- 用词遵循 GLOSSARY 标准术语。
