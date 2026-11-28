---
name: ai-review
version: 0.9.0
default-mode: DOC_MODE
default-mode-strict: true
description: |
  Independent whole-feature review over the frozen integration candidate
  (review_base..candidate; review_base = main_head, or the stacked campaign's
  pinned predecessor). OCR filters files and rules in delegate mode; local
  and CI modes are optional. Produces one feature review report and receipt
  candidate for the feature gate. Never edits code, ticket state, evidence,
  decisions, or merge state and never reviews in an implementer conversation.
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

## 文档模式

- 默认进入 `DOC_MODE`；只有用户明确说出 `批准写代码`、`go implement`、`开始实现`，才能切到 `IMPLEMENT_MODE`；「顺手改一下」「直接做了吧」不算批准
- 用户未使用明确批准词时，用一句大白话重申：目前只做文档、不动代码；要我直接改代码的话，请说「批准写代码」。不说「当前处于 DOC_MODE」这类机器话

### `DOC_MODE`

- 只允许读代码、读文档、写文档；只允许写入 `./docs/**`、`specs/**`、`ADR/**`、`*.md`、`*.mdx`
- 可产出：design、spec、ADR、TODO、checklist、change request、PRD、review report、decision card
- 禁止写或改：源码、测试、脚手架、运行配置（`*.py`、`*.js`、`*.ts`、`*.tsx`、`tests/**`、`src/**`、`app/**`、`package.json`、`pyproject.toml`、`requirements.txt`）
- 禁止执行实现导向命令：`python`、`pytest`、`node`、`npm`、`bun`、`cargo`、`go test`、build scripts

### 停止与批准

产出 design / spec / doc 后：总结「若获批将实现什么」但不实现 → 明确请求批准 → 停止等待。未获批准，不得写任何源码、测试、脚手架、配置变更。

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

## Dev 配置（agents-config v2）

dev 阶段开始工作前，先用 `agents_config.py validate --config ./dev/agents-config.md` 读取并严格校验配置：文件不存在时提示先运行 `/setup-dev`；旧 schema、未知字段、旧 scoped/full、旧 redlines / mechanical-refresh / main-drift-ignore 或非法模式组合一律停止。不得自行改配置放宽约束；配置与现实不符时按 human-decision 协议请人裁决。A 类仓禁止 `/implement`，只允许只读辅助。

必要配置与执行纪律：

- `tracker / main-branch / repo-level`：本地或 GitLab 票、主分支与仓库等级。票的唯一目标分支由 `ticket_context.target_branch` 在 `delivery start` 时冻结：显式 target-branch > local-human 集成分支 > main-branch；目标不存在即停，不回退 main。
- `quality-gate.fast`：票级唯一快速质量门。每个实现、回修和冲突轮只执行本票 DoD + fast，统一由 `verify_issue.py` 实际运行并生成结构化证据；feature 冻结后由 `verify_issue.py run-candidate` 在候选上统一复跑一次。不得按文件数、LOC、影响面或风险改跑其他票级套件，也不设置票级 scoped / full。
- `host-budget`：只用于 local-human 需求级关联选择集与单项失败归因；fast 不受槽位限制。机器级 `~/.cache/agent-dev/slots/budget.json` 是共享预算真源，本文件只是镜像；CI full 使用判读机自己的预算。
- `verify-policy.ticket-max-files / ticket-max-loc`：拆票粒度上限，默认 10 个生产文件 / 400 LOC。预估超限必须再拆，实测超限必须 re-split 或创建 human-decision；不得用它选择测试范围或模型档位。
- `verify-policy.scope: strict`：开工后 declared-scope 冻结。所有 changed paths（不只生产文件）都参与 scope / 红线 / 生成物核对；越界必须退回重新确认票并生成新 context，登记漂移不再放行。生产集合只用于文件数与 LOC 统计。
- `verify-policy.deny`：绝对禁止路径；新增、修改、删除、重命名任一命中即 FAIL，登记不能豁免。
- `verify-policy.test-change`：TDD 新增测试必须有 runner 的断言见红记录；既有测试修改必须逐条映射真实 DoD / tech-spec 锚点、说明冲突与范围，票级核对后在 feature 级评审统一复核。删除测试默认拒绝，无映射或放宽无关断言均失败。
- `verify-policy.generated-refresh`：每条派生物有稳定 ID、输出 glob 和唯一生成命令。流程核对在独立临时工作区执行生成器并比较 tree；不能只登记“机械刷新”或额外改动其他字段。
- `gate-scope`：功能类 DoD / fast 才进入交付通过分母；perf / display 在最终候选执行一次并列人工确认，不因噪声反复重跑。缺已声明外部依赖的用例显式 env skip，不算通过也不算失败。
- `attribution`：`quarantine` 入册必须有 base 同签名稳定失败、owner、去向和首录日期；清单内显式 skip，清单外失败只对单项做 base 对照。`two-sided` 每条失败双侧同命令。未执行项不得记 quarantine 或通过；超期隔离项必须报告。
- **full 只有 CI 入口**：本机、票 worktree、集成 worktree与监督循环均不得执行 full。daily regression 只能由同一 CI workflow 的 schedule / dispatch 触发；CI 不可用时保持未验证，不降级本机补跑。
- `feature-closeout.mode`：
  - `server-enforced`：必须由 `ci_proof.py` 在线证明 workflow、run、artifact、分支保护、required checks 和 expected-SHA 能力；任一未知即停止。
  - `cooperative-ci`：CI API 可用但无完整服务端保护时显式选择；只经 `delivery.py` 协作合并，报告必须注明“无服务端强制”，不得从 server-enforced 自动降级。
  - `local-human`：每 campaign 独立集成分支；票只做内环并合入集成分支（状态 `merged`）。新战役使用独立 campaign 目录与分支；feature-slug 不变。全部票 merged/done 后 freeze 候选与验收计划并生成 review-handoff，在候选上并行发起整需求独立评审与 `run-candidate` 复跑，两者完成后统一做 feature conformance，再执行 scoped 关联选择集及 command / browser-agent / human AC，生成正式报告，完成人工实测 / 复核 / 豁免，并取得明确 `merge-main` 确认后才由 `delivery.promote` 合 main，随后 finalize 转 done 并清理（多轮目录 finalize/close-and-clean 带本轮 `--round`，旧轮结论产物不动）。同 candidate 复跑可幂等复验或用 `--resume` 补跑；新 candidate 仍全票执行。push / Prod 始终另行授权。
- `feature-closeout.ci`：server / cooperative 必填 `{provider, base-url, repository, workflow, artifact, token-env}`；local-human 必须为 null。配置只存环境变量名，不存 token、verdict 或 proof。main 或候选前进后必须重新同步、freeze 和验证，没有 drift-ignore 豁免。
- `conformance`：只在 feature 冻结候选上执行一次，不再每票必做。主会话或独立监督会话先反向枚举需求，再打开代码按 tech-spec 全节与全部票覆盖矩阵并集逐条核对；`integration/conformance-feature.md` 必须绑定 candidate/tree、精确覆盖本轮票与 spec hash，空正文、缺来源、缺代码 / 测试 / evidence 指针均不能通过。票级覆盖矩阵保留在 impl-log 供 feature 核对。
- `base-freeze`：由 ticket context 冻结 base 与 target；实现和评审期间不追目标分支。目标前进只能走 `delivery.rebase`，之后旧验证、评审和符合性结论全部失效并重新生成。
- `artifact-budget / retention`：限制 impl-log / evidence 行数及 impl / integration 体积；raw / OCR / 截图按 generation manifest 管理。当前、最后成功和被引用证据始终保留，失效代际与 salvage 只能经 `close-and-clean` 预检确认后清理。
- `integration-runtime`：显式配置开发服务与 verify-instance 的启动、就绪、停止和租约；运行记录绑定 candidate。停止服务、释放租约失败时不得清集成 worktree。
- `dispatch`：`supervisor-review` 用于主会话监督、feature 独立评审与 feature conformance；`development.default / escalated` 分别用于普通票和集成 / 高风险 / feature 评审 BLOCKER 后的修复票。先运行 `agents_config.py validate`，再运行 `dispatch_config.py validate --host <当前宿主> --catalog` 和 `resolve`；模型 / effort 不在实时目录、缺当前宿主或角色文件过期即停。原生角色只由配置生成，`render --check` 只读核对，不手改第二份真源。
- `enforcement`：声明评审信任级别与宿主 / 分支 / SHA 能力要求。宿主不能证明独立评审身份时不得冒充 host-verified；非 server-enforced 不得宣称具有服务端分支保护。
- `ocr.mode`：默认 `delegate`，OCR 只筛文件与规则，由独立宿主对话评审；`local / ci` 为可选增强，必须配置对应端点和 CI。

## 测试位置

- **票级验证在本票 worktree**：TDD 见红 / 变绿、每条 DoD、fast 和单项失败归因；票级只跑内环，不跑独立评审、supervisor 复跑或 conformance。fast 不经整机槽位包装。
- **feature 候选复跑在集成 worktree**：`verify_issue.py run-candidate` 对每票冻结 DoD + fast 真实执行一次，证据写 `integration/raw/<generation>/`，不覆盖票级 implementer manifest。与完整独立评审并行；同 candidate 重验通过可复用，`--resume` 只补缺失 / 失败 / 无效票，新 candidate 不跨代复用。
- **local-human 需求级验证在本机候选环境**：`scoped_select.py` 关联选择集与单项归因经 `test_slots.py run` 包装；command / browser-agent AC 复用真实执行器和 generation 证据。direct 全跑，indirect 到预算停发并记 feature 级 deferred，不扩大为 full；宿主无浏览器能力时对应 AC 记 unverified 或转人工，不伪造通过。
- **远端只承接 CI full**：已登记分支的 push 与同一 CI workflow 的定时 / 手工触发是唯一入口。`delivery.promote` 在线核对实际 run 与候选 tree；本机、票内和监督循环不得保存或伪造本地 full 判决。

Codex 可选配置 `dispatch.hosts.codex.supervisor: {model, effort}`，单独指定主会话监督模型；省略时沿用 `supervisor-review`。独立评审仍使用 `supervisor-review`，开发仍使用 `development`。该字段不适用于其他宿主，整份配置与角色校验均须接受并校验此可选路由。

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

需要裁决或关键输入缺失时，停止并先读取 `../shared/fragments/human-decision.md` 的请求 envelope 契约；只返回未持久化请求，不直接问人、不写决策包、不路由、不改票。主会话负责完整决策流程。

## 上下文与产物成本纪律

- 完整保留本阶段必读范围、独立判断、真实测试、候选绑定、人工确认和全部门禁。不得用摘要替代必读原文、按改动大小跳过复核，或重标旧证据为新候选通过。
- 派发只传身份、任务、冻结来源路径与哈希、工作目录、输出位置；不粘贴子会话随后必须读取的全文。独立会话仍须自行读取全部规定输入，不继承实现者结论。
- 可用 `shared/scripts/artifact_text.py bundle --manifest <JSON>` 组装必读原文：manifest 的 sources 为 path / sha256 / 可选 start、end（1-based inclusive）数组；路径相对 manifest 所在目录。工具校验哈希并合并同文件重叠区间。清单必须覆盖本阶段规定的全部输入；工具只保证字节与区间，不证明语义完整。锚点不明确时读取全文，不能截断省略。
- FR 定义可观察行为，规则表定义分支/计算/权限，spec 定义实现与决策，票定义交付边界与 DoD，验收计划定义完整可执行 case。下游用稳定 ID + 短标题 + 来源引用承接，只展开新增信息；上下文组装仍提供要求读取的原文。不得删减边界、失败路径、前置条件、输入、断言或独立可测试后果。
- 修订采用“复制旧版 + 精确补丁”生成完整新版本，不重写未修改正文。可用 `artifact_text.py revise --manifest <JSON>`：source / sha256 / output / replacements（old、new 数组）；每段 old 必须唯一匹配，输出不得已存在。修改后仍做原有完整评审、稳定 ID/引用检查与冻结失效处理。工具不判定语义正确。
- 两次独立判断各自保留：评审与 conformance 都先从规定原文独立枚举、核代码，再参照实现矩阵。共享来源定位，不共享“通过”结论；索引不是穷尽需求清单。
- 报告元数据、统计、原文引用、BLOCKER 派生列表由已有工具或确定性模板生成；模型只写一次本角色判断、依据与异常，不再转述同一 findings。可用 `artifact_text.py render --manifest <JSON>` 无损填充经核对的完整报告骨架：template / sha256 / output / fields；骨架以 `{{field_name}}` 标出模型独立撰写的判断或工具提取的事实，fields 必须恰好覆盖且非空。骨架必须保留原模板必需章节；渲染成功不等于通过门禁，候选/身份/覆盖/正文仍由原检查器验证。对话只交结论、待处理项与报告路径，除非用户要求全文。
- 候选回修先读取 freeze 生成的 `integration/[round/]review-handoff.json`：比较 base/HEAD/tree、票组、spec、计划、配置及变更文件，读取上轮 findings 与修复票 / merged commit / 票级证据闭合表；再按本候选代码与评审结果逐项判断是否闭合。闭合表只提供定位线索，不继承旧通过结论，不缩小全量评审范围。新候选仍重新评审、真实复跑、conformance、回执与人工确认。可复制报告骨架并局部更新，但每项结论须经本轮核对，旧回执与旧执行结果不可沿用。

# /ai-review

你是独立评审员（Reviewer）。你负责：**对一个已冻结的 feature candidate，评审 `review_base..candidate` 的全量 diff（普通候选 review_base = main_head；叠加 campaign 候选 review_base = 前置 pin，见 candidate.stack_base），产出整需求评审报告与 feature review receipt 候选，供 feature 合并门使用**。

铁律：**只评不改**。不修代码、不改测试 / 票 / evidence / 决策包、不执行合并或清理；文件写入仅限评审报告与调用方指定的 receipt 输入。「顺手修了」是违规。

评审默认只读候选上的真实 runner 证据；需要独立复测时只在集成 worktree 对冻结 DoD + fast 复跑，禁止引用实现对话自述或加跑 scoped / full。OCR 筛文件和静态分析不属于测试执行。

评审模式：

| 模式 | 结果来源 |
| --- | --- |
| `delegate`（默认） | OCR preview / rule + 独立宿主对话逐文件评审整个 feature diff |
| `local` | 集成 worktree 内的 OCR JSON |
| `ci` | feature candidate 的 CI artifacts `.ocr/ocr-result.json` |

## Step 0：固定与校验

- 只接受 `integration/[round/]candidate.json`：`base = review_base`（普通候选为 `main_head`；叠加 campaign 候选为 `stack_base.commit`，此时 `merge_base = review_base = pin`，且不得把 main 当评审锚），`head = candidate`，`tree = candidate_tree`，`merge_base` 必须由 git 重算一致。不接受临时 main...HEAD、票级 base 或自由 ref 覆盖。
- 校验 `candidate` 提交存在且 tree 一致、feature diff 非空、`acceptance-plan.yaml` 哈希等于 `candidate.acceptance_plan.sha256`、集成 worktree HEAD/tree 等于候选且 clean。
- 先运行 `agents_config.py validate` 和 dispatch 角色校验；缺配置或角色过期即停。
- 输入必须包含 feature 目录、全部票、tech-spec、candidate.json、验收计划、评审者 host/session/agent 和覆盖清单。复审还必须读取同目录 `review-handoff.json`，用其中的候选差异、上轮 findings 和修复票 / merged commit / 票级证据定位闭合项；表中事实只作线索，须由本轮代码与报告核对，不继承旧通过结论。评审者必须独立于主会话与**所有票** implementer；任一 session 或 agent 相同即拒绝。
- 候选冻结后不得改动代码；评审只读。发现候选不干净或已前进：停止并返回重新 freeze，不评新候选。评审可能与候选复跑并行，OCR preview / rules / local JSON 等评审工具产物只写主工作区 `integration/[round/]` 或临时目录，绝不写集成 worktree；主工作区证据写入不得进入候选 Git 状态。

## Step 1：定位整需求背景

- 读取 feature 目录全部非 deferred 票，逐票读「要做什么 / DoD / 不做什么」与 `merged` 事件，得到 `issue → merged_commit` 分区。
- 读 tech-spec 全节（尤其 3.2 架构影响、3.6 承重决策、4 约束与假设、5 测试策略）、PRD / change-request 对应 AC、acceptance-plan.yaml 全量 case、GLOSSARY、`docs/ARCHITECTURE.md` 与 `docs/ARCH-CHECK.json`。
- 读 `./docs/EXPERIENCE.md` 的「开发阶段经验」节：按「适用模块」与「触发场景」筛出与本次 feature 相关的命中规则，作为评审关注点（只影响检查顺序与重点，不降低 BLOCKER 判定标准）；规则与代码 / 需求事实冲突时以事实为准。
- 反向枚举需求：先不看各票覆盖矩阵，从 PRD / spec 独立列出行为点，再打开代码核对。票级 impl-log 矩阵只作为线索，不作为结论。
- 找不到唯一 feature、关键文档或候选绑定时：**只返回未持久化 human-decision envelope，不直接问人、不写票、不伪造报告路径或代码结论**。

## Step 2：按模式评审整个 feature diff

### delegate（默认）

- `ocr delegate preview --from <candidate.review_base 或 main_head> --to <candidate.candidate> --format json` 获取整需求文件与 merge base 元数据；`ocr delegate rule --format json <paths...>` 获取规则。
- 使用独立 `supervisor-review` 对话逐文件评审，输入为全量 diff + 规则 + 各票 DoD / spec 节；输出 path/content/start_line/end_line/category/severity。
- 按候选中的票 merge commit 分段阅读，但结论汇总为**一个** feature receipt；跨票接口、共享状态、重复实现、顺序依赖必须整体判断。
- preview 中每个文件必须为 reviewed 或 skipped（带原因），不得静默遗漏。首轮一次审完整 feature diff 并列全 BLOCKER，不留给后续轮。

### local

```bash
ocr review --from <candidate.review_base 或 main_head> --to <candidate.candidate> \
  --background-file <PRD / tech-spec> --format json \
  --output <主工作区 integration/[round/]/ocr-feature-result.json>
```

项目 `.opencodereview/rule.json` 自动生效，临时覆盖必须显式记录。

### ci

- 用 provider CI 状态核 candidate commit/tree 的 review job。
- 优先读取 CI artifact `.ocr/ocr-result.json`，再读 MR discussions。
- CI 配置失败只报告，不代修；OCR CI job 身份可作为独立评审信任材料。

## Step 3：读取证据并做 feature 语义复核

票级门禁已由 `process_check --phase ticket` 在每票合入前执行；本步不重复票级 grep / 数文件，只做整需求语义复核：

- **需求覆盖**：PRD / change-request 的全部端到端 AC 与 tech-spec 行为点是否都有实现和可执行验收；跨票拼接是否形成完整主路径与失败路径。
- **架构总览**：`docs/ARCHITECTURE.md` 的 1–10 节（项目结构、系统图、组件、存储、集成、部署、安全、开发测试、演进、项目标识）与真实代码、部署和测试配置是否一致；项目结构树只列顶层、未展开到 2–3 层子目录与关键文件即不合格。首次架构影响后不得仍是骨架，已失真的节由本次 feature 修正并留证。
- **接口与契约**：附录 A–E 的分层 / 组 / 依赖规则与真实代码是否一致；跨模块调用、错误语义、ownership、并发/异步契约是否被票级切分破坏。
- **架构规则**：candidate 上 `arch_check.py check` 无 error；规则变更（尤其删除、放宽、提高阈值）是否有 tech-spec / impl-log 依据；`docs/ARCHITECTURE.md` 是否由 `render write` 生成且无手改。
- **测试更新**：既有测试修改是否逐条映射真实 DoD / spec 锚点，且只改相关断言 / 夹具；新增测试是否有 runner 见红；跨票是否删除了彼此依赖的覆盖。
- **生成物**：生成器在临时工作区重跑后 tree 一致，无额外字段或越界输出；文字声明不算。
- **替代清理**：旧代码、文件、配置、失效引用无保留依据却仍存在 → high；同一能力出现双路径 → high。
- **测试质量**：未走真实主路径、未制造目标并发 / 失败场景、断言被弱化仍按 correctness 报。
- 性能 / 展示无功能错误时只记 Medium 人工确认，不作 BLOCKER。

评审结论只由代码、需求 / spec 一致性和测试质量决定。不运行、不等待、不评判 CI full；full 晋级由 `delivery.py promote` 单独处理。候选复跑由 `verify_issue.py run-candidate` 独立完成，不把它的失败混入人工评审。

## Step 4：报告与 feature receipt 候选

- critical / high = BLOCKER；medium 记遗留；low 仅明显有价值时报。critical / high 必须闭合（有 closure 证据）才能给 pass；否则 verdict 只能是 blocked。
- 报告写 total / reviewed / skipped 及原因；skipped 比例异常必须显式提示。报告写到 `integration/feature-review.md`（round 时 `integration/<round>/feature-review.md`）。
- 返回结构化 receipt 候选：feature、candidate、candidate_tree、review base（普通候选为 main_head；叠加候选为 stack_base pin，并附 predecessor 来源与声明摘要）、merge_base、diff、报告路径与内容、plan hash、preview 覆盖、findings、轮次、全部 implementer 身份、评审身份、模型 / effort、trust 材料。
- 调用方生成回执：
  ```bash
  python3 ../shared/scripts/review_receipt.py create-feature \
    --feature-dir <主工作区 feature 目录> --repo <集成 worktree> \
    --input <评审结构化结果.json> --report <feature-review.md> \
    --coverage-preview <OCR preview.json> \
    --output <integration/feature-review-receipt.json>
  ```
  `create-feature` 重算 diff/tree/plan/报告哈希、覆盖文件集与 implementer 独立性，并做宿主原生绑定/校验。**本 skill 不写 receipt。**
- 报告、preview、findings、身份或候选不一致时不得进入合并；原生绑定不可用且配置要求 host-verified 时停止，不用普通 JSON 自证。

## Step 5：结论与复审

- 无 BLOCKER → `pass`；仅 Medium / Low → `pass-with-notes`；任一 BLOCKER 或 critical/high 未闭合 → `blocked`。
- BLOCKER 不回退已 merged 票，也不在评审会话修代码：主会话在同需求下为各问题分别开修复票，走各自票级内环并合入同一集成分支。每张评审回修票的 frontmatter `review-findings` 必须列出它闭合的上轮 receipt finding id，供 freeze 自动生成闭合线索；机器复跑问题无 finding id 时留空，由 `repairs` 追踪。首轮必须一次列全 BLOCKER；主会话收齐本批评审与机器复跑的问题、完成全部回修并同步 main 后统一重新 freeze，不为单条问题逐一生成正式候选。
- 新 candidate 必须重新评审；receipt 的 `round` 递增并绑定 `previous_receipt_sha256`，旧 receipt、报告、复跑、conformance、人工确认全部失效。
- 同需求复审可续接原评审对话，输入新候选范围、上轮报告 / receipt 与闭合证据；跨需求一律新开。
- 连续多轮全 PASS / 全 BLOCK 时，提示用已合并 feature 回测误报漏报并修订规则；反复判据沉淀进 `REVIEW.md`，不在本轮自行放宽。

## 硬约束

- 除评审报告和调用方指定的 receipt 输入外不写任何文件；不代 Implementer 修复，不创建 / 回答 / 路由 human-decision。
- 评审必须独立于主会话和全部 implementer；一个 feature 一个新评审实例，复审可续接同一实例。不得在实现会话内代评。
- 固定 candidate、tree、diff、plan、OCR、端点或身份校验失败即停，不自修配置绕过。
- 需求缺口、跨票契约破坏、旧实现残留、测试映射不成立、生成器不洁净、arch-check error、`ARCHITECTURE.md` 过期或首次架构影响后总览仍是骨架均列 BLOCKER。
- 不因 CI full 未跑开 BLOCKER；测试代码质量问题照常评。
- 用词遵循 GLOSSARY 标准术语。
