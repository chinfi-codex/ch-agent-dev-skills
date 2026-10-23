# agents-config

> 由 `/setup-dev` 产出；人可直接修订本文件。dev 阶段各 skill 的唯一配置真源。
> 事实背景：内网 GitLab 已确认可用（2026-09-09）；GLM 端点走 bigmodel.cn（境内，满足数据不出境）。
> 默认形态：**执行与评审都在当前 agent 内完成**——issue 生成后由主会话通过宿主 agent 的「新开独立对话」能力自动派发实现（宿主无关：Claude Code `Task` / ZCode `Agent` / Codex / WorkBuddy 等各自的新开对话机制），并监督执行直到完成合并；评审走 ocr `delegate` 模式；GitLab CI 与 LLM 端点均为可选增强，不是前提。

```yaml
# issue 发布面：gitlab = glab CLI 建 issue / draft MR（内网 GitLab 已确认，首选）
#              local  = ./dev/features/<feature-module>/<feature-slug>/issues/ 票文件（无 glab 环境的 fallback）
tracker: gitlab

# 主分支名：worktree 与 MR 的基准分支
main-branch: main

# 执行派发（issue 生成后由主会话自动派发并监督到合并，无需人工触发）
dispatch:
  executor: subagent   # subagent = 主会话通过宿主 agent 的新开独立对话能力执行 /implement
                       # （宿主无关：Claude Code = Task，ZCode = Agent，Codex / WorkBuddy =
                       #  各自的新会话 / 派发机制；宿主无此能力时按派发协议兜底）
  # 被派发对话模型：按宿主 agent 分别配置（各宿主可选模型阵容不同），每个宿主条目由人在 /setup-dev 确认。
  # 主会话按自身所在宿主取对应条目；当前宿主无条目 → 停下请人确认后补写，不跨宿主套用、不自行挑选。
  # 按票分档：default 用于 light / scoped 票的实现与评审；escalated 用于 full 档票、集成票、高风险票，
  #   以及评审达 review-round-alert 轮后的续修。清单内按优先级排序，取第一个当前可用的模型。
  model:
    <宿主名，如 claude-code / zcode / codex>:
      default: [<宿主具体模型，推荐经济性最高、速度快的一档>]
      escalated: [<宿主具体模型，推荐能力最强的一档>]
      reasoning-effort: {default: medium, escalated: high}   # 宿主支持推理强度时填写，否则删去
  parallel-max: 2      # frontier 中 declared-scope 互不相交的票可并行派发的上限；1 = 串行

# 质量门命令
quality-gate:
  fast: "pytest -q <相关测试文件>"      # 日常开发默认档：实现 / 自修复每一轮只跑 fast（秒级）；fast 不受整机槽位限制
  scoped: "pytest -q -n $JOBS <按最终 diff 换算的受影响测试集>"   # scoped 档票的交付门：选择集从 merge-base..HEAD 的实际 diff 重算；经 test_slots.py run 包装执行，并发度 = 分得槽位数 $JOBS
  full: "pytest -q -n $JOBS"            # full 档票与集成票的交付门：完整套件；同样经 test_slots.py run 包装

# 整机测试算力预算（跨项目 / 跨会话共享，防多 agent 任务超卖）
# 真源是机器级文件 ~/.cache/agent-dev/slots/budget.json（同机各仓共用一份），此处为镜像记录；
# /setup-dev 写入并核对两者一致，日常执行只读机器级文件
host-budget:
  total: 8            # 整机同时运行的重测试 worker 总数上限；默认 ncpu - 2（P+E 混合机型按实测调低）
  max-runs: 2         # 整机同时运行的重套件（scoped / full / 归因补跑）套数上限；超出排队
  queue-timeout: 1200 # 排队秒数上限（大于 full 套件 P90）；超时报告而非静默等待

# 验证档位策略：交付验证按票的 verify-tier 分档，不再每票一刀切跑全量
# 档位含义（累计向下包含）：
#   light  = DoD 验收命令 + fast                （单点小改：文案 / 阈值 / 默认值 / 局部样式）
#   scoped = light + 关联测试选择                （改动集中在 1-2 个模块的常规票）
#   full   = scoped + 完整测试套件               （迁移 / 鉴权票、超阈值票与集成票，票内跑）
# 套件级回归三层（C+D+F）：①集成票（feature 收尾）票内 full；②main 每个工作日定时 full（daily-regression，
#   兜底跨 feature 回归与票内未跑 full 的风险，最长延迟 1 个工作日）；③迁移 / 鉴权票票内 full。其余票不跑票内 full。
# 不变式（任何档位不得缩水）：红线检查 / DoD 验收命令 / 独立评审 / 每 feature 至少一次 full——
# 唯一例外是集成票的「模块隔离认定」（见 verify-policy 集成票条）：认定成立时按 module-scoped 跑涉及模块的全闭包，
# 不再机械全仓；认定不成立或有疑义时 full 不变。
verify-policy:
  light-max-files: 3        # declared-scope 生产代码文件数 ≤ 此值、且不触发 escalate-triggers，才允许 light
  scoped-max-files: 10      # 实测生产代码文件数 > 此值 → 直接 full；拆票时预估 > 此值 → 票必须再拆（/issue-split 粒度红线）
  scoped-max-loc: 400       # 实测 diff LOC > 此值 → 直接 full
  # production-exclude: ["tests/**", "docs/**", "dev/**", "**/*.md"]   # 可选：不计入生产文件数 / LOC 的 glob；缺省用 process_check.py 内置清单
  escalate-triggers:        # 命中任一（声明或实测）→ 升级验证；按两级分别处理，不一律全量
    # —— full 级：错误代价高的变更，票内 full（不等每日回归）
    - "数据迁移 / schema 兼容变更"                  # full 级
    - "鉴权 / 权限逻辑改动"                         # full 级
    # —— scoped+ 级：先判可枚举性，不盲目全量；跨模块剩余风险由每日回归与集成票兜底
    - "公共接口 / API 签名变更"                     # scoped+ 级（选择集 = 调用方反向闭包）
    - "共享模块 / 工具库改动"                       # scoped+ 级
    - "红线邻接（redlines glob 命中文件的同目录）"   # scoped+ 级
  # scoped+ 级处理：按 selection 反查变更文件的反向依赖闭包（含调用方测试）——
  #   闭包完整可枚举（无动态加载 / 反射 / subprocess 间接调用变更模块）→ scoped，选择集 = 全闭包；
  #   闭包不可枚举（存在上述动态依赖）→ 仍 scoped（可枚举部分 + 按目录约定补齐），evidence 标
  #   「闭包不完整」，不可见的动态依赖由每日回归兜底，不为此跑票内 full。
  # 选择集大小不是升档理由：闭包即精确选择集，大闭包跑大闭包；占比 ≥ 50% 时在 evidence 记录
  # （作为「本票影响面广」的评审信号），不升 full——动态依赖与跨模块交互风险由集成票
  # （DAG 终点票）的 full 与每日回归统一兜底，不由每票重复全量。
  # 漂移规则（实测 diff 超出票的 declared-scope 时）：
  #   仍同模块、未触发 escalate-triggers、未超阈值 → 不升档，impl-log「漂移与升档记录」登记漂移文件清单即可
  #   触发 escalate-triggers 或超 scoped-max-* 阈值 → 升档（只升不降），立即按新档补验证并在 evidence 记录
  # 集成票：DAG 中无后继（没有其他票 blocked-by 它）的票一律 verify-tier: full——
  #   唯一例外「模块隔离认定」（前提：selection 具备反向闭包能力即 impact-map；dir-map 仓无闭包工具、
  #   无法机器判空，不得做隔离认定，集成票仍 full）：本 feature 全部票的生产 diff 都落在同一叶子模块内
  #   （该模块不被本 feature 外的生产代码 import，按 selection 的反向闭包判定为空）、未命中任何 full 级触发器、
  #   不触碰共享模块 / 公共接口 → 集成票按 module-scoped 跑该模块的全闭包（不是只跑本票 diff），
  #   并在 evidence 记录隔离判定依据（反向闭包为空 + 涉及模块清单）。
  #   判定有疑义（闭包工具不全 / 依赖图有动态边）→ 仍 full。隔离认定的代价是把「跨模块回归」
  #   推迟到下一次非隔离 feature 的集成票，故该认定必须可机器复核，不得凭感觉。
  # repo-level 平移：C 类仓可在计算结果上再降一档（下限 light）；B 类按规则执行；A 类不进 /implement。
  #   C 类仓集成票若被降为 light / scoped，须在本 feature 末票交付时按 module-scoped 跑一次
  #   涉及模块的全闭包（模块隔离的等价安全网），记录同模块隔离认定。
  selection: dir-map        # scoped 档的关联测试选择机制：
                           #   dir-map    = 目录约定映射（源码路径 → 测试路径，零基建；看不见跨模块影响面，只配低风险 scoped 票）
                           #   impact-map = coverage / 静态导入闭包反查（diff → 依赖这些模块的测试，含调用方模块的测试；
                           #                hub 形态仓库必选本档——dir-map 会漏掉沿 import 链传导的影响面，给出全绿假信号；
                           #                scoped+ 级触发器处理也依赖本档的闭包）
                           #   impact-map + dir-map 组合（推荐）：代码变更走反向闭包，数据 / 配置文件走仓内声明映射表
                           #                （默认 dev/scoped-map.yaml，命中即视为可证明覆盖）；仓库元信息 / dev/ / docs/
                           #                等无行为消费者的文件不要求覆盖证明
                           #   module-scoped = 按需求涉及模块选择该模块的全部测试（模块目录映射 + 该模块的反向闭包测试），
                           #                不跑全仓——用于「模块隔离认定」成立的集成票与 C 类仓末票；
                           #                选择面比 scoped 宽（整个模块而非本票 diff）、比 full 窄（不跨无关模块）
  selection-map: null       # dir-map 映射表路径（仓内相对路径，默认 dev/scoped-map.yaml）；
                           #   选择器为共享脚本 shared/scripts/scoped_select.py（--map 覆盖、--scan 扫描根、--test-dir 测试目录）
  # scoped 执行条件：选择集可枚举（闭包可证明）才票内跑；不可枚举（full fallback，跑了等于变相每票 full）
  #   → 票内不跑选择集，evidence 头记 `scoped-deferred: closure-incomplete` + 原因，tier-executed 如实记 light，
  #   套件级验证由集成票 full 与每日回归兜底；不因此升档、tier-target 不变，DoD + fast + 评审照常
  # 先在失败隔离：套件基线长期存在与本票无关的失败时，隔离清单内用例 skip 执行，
  # 不产生 exit 1——套件退出码恢复语义（exit 1 = 有新东西坏了），档位门只对清单外失败归因
  attribution: quarantine
    # quarantine（推荐默认）：维护落盘隔离清单（默认 dev/known-failures.yaml）——
    #   清单内用例在 full / scoped 中以 skip（标 quarantined）执行，不进 all-passed 分母，evidence 只列隔离数；
    #   入册条件：base 侧同签名稳定失败；每条必须写 name + returncode + signature + owner +
    #   去向（修复票 / 永久删除决议）+ 首录日期，无去向不入册；
    #   清单由每日 full 顺带刷新（转绿销记、新先在追加），env-skip 项不做整体替换（环境缺失的轮次保留历史条目）；
    #   每日 full 另以「只跑隔离项、不判红」抽查签名漂移（隔离项转绿该销记 / 失败形态变了该重新评估，都要可见）；
    #   超 quarantine-max-age-days 未处理的条目在每日回归记录列出，主会话监督循环呈现给人；
    #   清单外失败 = 疑似本票引入，只对这几个单项补跑 base 侧对照，不整套重跑
    # two-sided（保守档）：每条失败 branch / base 同命令各跑一遍对照——归因成本翻倍（全套 ×2），
    #   仅套件小或清单机制未建时使用
  quarantine-max-age-days: 14        # 隔离条目超期告警天数（进每日回归记录）
  # 测试职责边界：AI 交付门只保证功能实现度；性能与展示由人工确认
  gate-scope:
    categories-file: null     # 测试分类清单路径（仓库内，测试名 → 类别）；未配置时全部用例视为 functional
    non-gating: [perf, display]
      # perf    = 断言墙钟 / 吞吐阈值或以性能为主要目的的用例与 DoD
      # display = 断言渲染样式 / CSS / 版式 / 展示字节（报告「内容是否正确」——计数、结论、章节存在——属功能，仍在门内）
      # non-gating 类：交付时在最终 commit 执行一次并落盘原始输出，不阻塞、不进 all-passed 分母、不因噪声反复重跑；
      #   结果列入 evidence「人工确认项」，由人在验收时判定。阈值不达标不是 BLOCKER，除非同时伴随功能错误
    env-skip: true            # 分类清单中声明了外部依赖（环境变量 / 外部服务）的用例，依赖缺失时显式 skip，
                              #   evidence 列出 skip 清单与缺失项；不算失败（免归因），也不算通过
  # 档位原因：定档 / 升档时写入票 frontmatter `tier-reason`（命中的触发器名 / 阈值项 / integration），
  #   process_check.py 输出与每日回归记录统计，用于观察并调整触发器
  # full 次数：同一张票的 full 只在「评审通过后的最终 commit」上跑一次（仅 full 档票：迁移 / 鉴权 / 超阈值 / 集成票）
  full-runs: once-at-final
    # Step 5 先跑 DoD + fast + scoped 进评审；评审通过后在最终 commit 跑一次 full（full 档票 / 集成票）
    # 评审回修轮：只跑 DoD + fast + 回修 diff 的 scoped，不重跑 full
    # full 失败 → 回修 → 回修 diff 的 scoped + 同一评审对话复审回修范围 → 最终 commit 再跑一次 full
    # rebase / 合并 main：无冲突且本票改动文件 blob 不变 → 复用证据；有冲突 → 跑冲突文件闭包的 scoped
    # 集成票复用：其合并候选 tree 与 dev/full-runs/ 中某次通过的每日回归 tree 相同 → 复用该记录（full-ref），不重跑
  # 每日回归：main HEAD 每个工作日一次 full，由人自设的定时任务触发（/setup-dev 给出任务 prompt）
  daily-regression:
    enabled: true
    record-dir: dev/full-runs      # 每次落盘 <date>-<tree8>.md：tree / 上次通过以来的合入区间 / 结果 / 归因 / 二分结论 / 耗时 / jobs
                                   #   / 隔离数与签名漂移抽查结果 / 超期隔离条目清单（超 quarantine-max-age-days）
    worktree: .worktrees/daily-full
    skip-if-unchanged: true        # main tree 与最近一次记录相同 → 当日跳过
    max-gap-days: 3                # 存在进行中 feature 且最近记录早于此天数 → 主会话在空闲时补跑一次（定时漏跑兜底）
    # 新失败（清单外）：在「上次通过 tree..本次 tree」合入区间内二分（每步只跑失败项）→ 定位到合入提交 → 其票置
    #   needs-human 并开回修票；合入不暂停。清单内用例 skip（quarantine），清单由本次结果顺带刷新（转绿销记、
    #   新先在追加须带 owner / 去向 / 首录日期），并以「只跑隔离项、不判红」抽查签名漂移
  # 基线冻结：票开工时固定 base（记 impl-log），实现与评审期间不追集成分支；只在合入前 rebase 一次
  #   （无冲突且本票文件 blob 不变 → 复用证据；有冲突 → 冲突文件闭包 scoped）
  base-freeze: true
  # 执行形态（耗时契约，档位语义不因此缩水）：
  #   - 独立用例型套件应并行执行（并发度按机器自定；共享端口 / 文件等资源的用例标记 serial 除外）
  #   - 单项超时分层：默认上限 + slow 白名单（实测头部慢项登记）——避免个别卡死项烧满统一上限
  #   - full 档仍全量跑（slow 项不剔），慢项排前起跑压墙钟；同 tree 已跑过 full 的证据可复用，不重复执行
  #   - 重套件（scoped / full / 归因补跑）经 shared/scripts/test_slots.py run 包装拿整机槽位
  #     （上限见 host-budget），并发度 = 分得槽位数（命令内用 $JOBS）；一律后台执行、输出与
  #     退出码落文件后轮询（full 墙钟可超宿主单次命令上限）；fast 不受限
  #   - 负载感知超时：单项超时且包装器记录的运行期 load-peak > ncpu → 先串行单独重跑该项一次；
  #     通过记 load-flake（evidence 列出，不算失败、不进归因），仍败才进归因流程

# 自修复轮次上限（构建失败或评审 BLOCKER 的回修循环，超出打回人工）
max-fix-rounds: 99
# 评审轮次预警：同票评审达到此轮数 → 主会话介入判断（拆票 / 缩范围 / 升级模型），不是硬停
review-round-alert: 3

# 需求符合性核对（主模型校验）：主会话在合并前核对「要的有没有、偏没偏」——与 ai-review 正交，
# 评审判代码质量（同档模型够用），本核对判需求语义符合性（必须主模型，实现 / 评审同档模型有同源盲区）。
# 方法：先独立枚举需求点（反向枚举先行，不看 impl-log 覆盖矩阵），再对照矩阵逐条核映射、打开代码验证语义；
# 结论落盘 impl/<issue-id>/conformance-<issue-id>.md，process_check.py --phase close 机器把关。
conformance:
  mode: always            # always = 每票合并前必核；sampled = 实测 light 档票抽查（未抽中写 verdict: skipped
                          #   落盘留痕），scoped / full / 集成票仍一律必核（按实测档位 tier-target 判定，升档票不免核）
  light-sample: 1/3       # sampled 模式下 light 票的抽查比例
  checker: main-session   # main-session = 主会话亲核（默认，输入有界不读全量代码）；
                          #   dispatched = 上下文紧张时按派发协议开独立对话核对（模型取 dispatch.model escalated 首选）

# 预授权机械刷新：随生产文件变更必然联动、可由机器证明「只动这些字段」的派生物，
# 刷新不视为「修改既有测试 / 红线绕过验收」，无需停下等人授权；须在 impl-log 逐文件登记，评审独立复核 diff 只含所列字段
mechanical-refresh:
  - "<路径 glob> :: <JSON 指针或生成块标记>"   # 例：golden/**/*.json :: /fingerprint ；SKILL.md :: <!-- generated:task-catalog -->

# 产物体积预算（证据瘦身）：超出由 fast 档 lint 报错
artifact-budget:
  impl-log-max-lines: 120      # impl-log / evidence 正文行数上限；详细过程放 raw 文件，正文只留结论与指针
  impl-dir-max-mb: 20          # impl/<issue-id>/ 体积上限；中间轮原始产物只留最终轮

# 红线：命中任一 glob 即停止并报告，不得绕过
# 注意：redlines 是「不允许改」；ocr exclude 是「不送审」，两者不可互替
redlines:
  - "protocols/**"        # 协议文件（示例，按仓库实际填写）
  - "**/secrets*"
  - "**/*.key"
  - "tests/**"            # 禁止为通过验收而修改测试；TDD 新增测试除外，须在 impl-log 中说明；票 DoD / tech-spec 明确改变行为导致的旧测试更新（需求驱动更新）登记映射后自动执行，不算命中

# 仓库等级：A = 飞行软件 / 涉密（只读辅助，禁止 /implement）
#           B = 型号配套工具（全流程）
#           C = 内部效率工具（轻速径）
repo-level: B

# open-code-review 评审引擎
ocr:
  mode: delegate          # delegate（默认）= ocr 只筛文件 / 解析规则，评审由宿主 agent 内新开的独立对话执行，无需 LLM 端点
                          # local = 本地跑 ocr review（需 LLM 端点）| ci = GitLab CI 自动评审（需端点 + CI 接入）
  provider: glm           # 仅 local / ci 需要：~/.opencodereview/config.json 中 custom_providers.<name>
  model: <按公司 GLM 账号实际模型填写>   # 仅 local / ci 需要
  rule-file: .opencodereview/rule.json   # 项目级规则（可提交进仓库）

# GitLab CI 自动评审接入状态（仅 ocr.mode = ci 时需要）：
#   enabled = .gitlab-ci.yml 已 include ocr-review job，MR push 自动评审
#   manual  = 未接 CI（默认）
gitlab-ci: manual
```

## GitLab CI 接入清单（仅当把 ocr.mode 切到 ci 时才需要完成）

1. `.gitlab-ci.yml` include 评审 job（种子见 `shared/templates/gitlab-ci-ocr.yml`，回贴脚本需另行放置，见该模板头部说明）
2. GitLab 项目 Settings → CI/CD → Variables 设置：
   - `OCR_LLM_URL`（必需）：GLM OpenAI 兼容端点，如 `https://open.bigmodel.cn/api/paas/v4`
   - `OCR_LLM_AUTH_TOKEN`（必需，掩码）：GLM API key
   - `OCR_LLM_MODEL`（必需）：模型名，无默认值
   - `GITLAB_API_TOKEN`（可选，掩码，`api` scope）：回贴内联评论用；缺省回退内置 `CI_JOB_TOKEN`
3. 本机验证：`ocr llm test`（端点连通）；`glab auth status`（CLI 登录）

## 说明

- 整机算力预算：scoped / full / 归因补跑经 `shared/scripts/test_slots.py run` 包装执行（预算真源是机器级 `~/.cache/agent-dev/slots/budget.json`，`host-budget` 节为镜像）；单项超时撞高负载（load-peak > ncpu）先串行重跑，通过记 `load-flake`，不直接归因
- 验证档位制：交付验证按票 frontmatter 的 `verify-tier` 分档执行（规则见上方 `verify-policy`），full 不再每票必跑；触发器分两级（full 级 = 迁移 / 鉴权，票内全量；scoped+ 级含公共接口签名，跑反向闭包），选择集大小不是升档理由；每 feature 至少一次 full 由集成票（DAG 终点票）保证，跨 feature 回归由 `daily-regression`（main 每工作日一次 full）兜底；集成票唯一例外是「模块隔离认定」——feature 全程只动不被外部依赖的叶子模块时按 module-scoped 跑该模块全闭包，不机械全仓。
- `ocr.mode: delegate` 是**默认模式而非降级**：OCR 侧不调 LLM（只做文件筛选 + 规则解析），评审智能由宿主 agent 内新开的独立对话承担；无 LLM 端点、未接 CI 也能获得同等纪律的独立评审
- 评审与实现的被派发对话模型取 `dispatch.model.<当前宿主>` 按票档位取 default / escalated 清单的第一个当前可用模型（各宿主条目由 `/setup-dev` 逐宿主请人确认）；评审严格性由 rule.json、票 DoD 与红线复核保证，不依赖模型档位
- 规则文件 `.opencodereview/rule.json` 结构：`include[]` / `exclude[]`（glob，优先级最高）/ `rules[{path, rule}]`（第一条匹配生效）；评审规则文本从 `REVIEW.md` 提炼维护
- 本文件不进 GLOSSARY；术语口径仍以 `./docs/GLOSSARY.md` 为准
