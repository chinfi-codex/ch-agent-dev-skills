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

# 执行派发（主会话监督到合并；开发与独立评审使用不同的隔离会话）
dispatch:
  schema-version: 1
  executor: subagent
  parallel-max: 2      # frontier 中 declared-scope 互不相交的票可并行派发的上限；1 = 串行
  # 角色语义：
  # - supervisor-review：主会话监督 / 符合性核对 + 独立 /ai-review；同模型但不同执行上下文。
  # - development：独立 /implement；普通票用 default，集成票 / 高风险票及评审预警后的回修用 escalated；不按测试范围路由。
  # 本模板按 2026-10-29 用户确认阵容填写；development 两档同值表示不另设第三型号。
  # 主会话开工前必须核对 supervisor-review 的 model / effort；不符即停，不静默继承或跨宿主套用。
  hosts:
    codex:
      supervisor-review:
        model: gpt-6-astra
        effort: low
      development:
        default:
          model: gpt-6.1-sol
          effort: medium
        escalated:
          model: gpt-6.1-sol
          effort: medium
    claude-code:
      supervisor-review:
        model: claude-opus-5-5
        effort: medium
      development:
        default:
          model: claude-sonnet-5-5
          effort: medium
        escalated:
          model: claude-sonnet-5-5
          effort: medium
    zcode:
      supervisor-review:
        model: bigmodel-api/GLM-5.3
        effort: high
      development:
        default:
          model: deepseek/deepseek-flash
          effort: high
        escalated:
          model: deepseek/deepseek-flash
          effort: high
    dsh:
      supervisor-review:
        model: deepseek-official/deepseek-v4-pro
        effort: high
      development:
        default:
          model: deepseek-official/deepseek-flash
          effort: high
        escalated:
          model: deepseek-official/deepseek-flash
          effort: high

# 票级质量门命令：每票只执行自己的 DoD + fast，不配置票级 scoped / full
quality-gate:
  fast: "pytest -q <相关测试文件>"      # 实现 / 自修复 / 评审回修 / merge / close 均只跑 fast（秒级）；fast 不受整机槽位限制

# 本机重测试算力预算（跨项目 / 跨会话共享，防多 agent 任务超卖）
# 仅用于 local-human 需求收口的关联选择集与单项失败归因；CI full 使用判读机自己的预算。
# 真源是机器级文件 ~/.cache/agent-dev/slots/budget.json（同机各仓共用一份），此处为镜像记录；
# /setup-dev 写入并核对两者一致，日常执行只读机器级文件
host-budget:
  total: 8            # 整机同时运行的重测试 worker 总数上限；默认 ncpu - 2（P+E 混合机型按实测调低）
  max-runs: 2         # 本机同时运行的需求级关联选择集 / 单项归因任务数上限；超出排队
  queue-timeout: 1200 # 排队秒数上限；超时报告而非静默等待

# 验证策略：票只有 DoD + fast；以下阈值只约束拆票粒度，绝不用于选择测试范围或路由模型
verify-policy:
  ticket-max-files: 10      # 拆票时预估生产代码文件数 > 此值必须再拆；实测超值必须 re-split，无法自动再拆则 needs-human
  ticket-max-loc: 400       # 拆票时预估 diff LOC > 此值必须再拆；实测超值必须 re-split / needs-human
  # production-exclude: ["tests/**", "docs/**", "dev/**", "**/*.md"]   # 可选：不计入生产文件数 / LOC 的 glob；缺省用 process_check.py 内置清单
  # 漂移规则：实测 diff 超出 declared-scope 时在 impl-log「漂移记录」逐文件登记；
  #   未超粒度上限且仍属本票范围，登记后继续；超任一粒度上限只准 re-split / needs-human，不得升级测试或模型档次。
  # full 只有 CI 入口：已登记 feature / branch / main 的 push，以及同一 CI workflow 的定时 / 手工 dispatch。
  #   本机、票 worktree、集成 worktree 与监督循环均不得执行 full，也没有票级 full 证据。
  #   ci-promote 的 feature / branch / main 放行必须另核同 tree CI verdict + run proof：
  #   process_check.py --phase promote --ci-verdict <...> --ci-run-proof <trusted-gitea-run-proof.json>；
  #   分支前进或 tree 改变即重新验证。CI 不可用时保持未验证，不在本机补跑。
  # 先在失败隔离：基线长期存在与本票无关的失败时，隔离清单内用例 skip 执行，不产生 exit 1；
  #   只对清单外失败归因，未执行项不得入册冒充通过。
  attribution: quarantine
    # quarantine（推荐默认）：维护落盘隔离清单（默认 dev/known-failures.yaml）——
    #   清单内用例在 DoD / fast、需求级关联选择集与 CI full 中以 skip（标 quarantined）执行，
    #   不进 all-passed 分母，票级 evidence 只列本次 DoD / fast 的隔离数；
    #   入册条件：base 侧同签名稳定失败；每条必须写 name + returncode + signature + owner +
    #   去向（修复票 / 永久删除决议）+ 首录日期，无去向不入册；
    #   清单由 CI full 报告刷新（转绿销记、新先在追加须带 owner / 去向 / 首录日期），env-skip 项不做整体替换；
    #   CI full 另以「只跑隔离项、不判红」抽查签名漂移；超期条目在 CI 回归记录与主会话监督输出中呈现；
    #   清单外失败 = 疑似本次变更引入，只对这几个单项补跑 base 侧对照，不整套重跑；
    #   需求级关联选择集的未启动 indirect 只记 feature 级 deferred，不算 quarantine。
    # two-sided（保守档）：每条失败 branch / base 同命令各跑一遍对照，仅套件小或清单机制未建时使用
  quarantine-max-age-days: 14        # 隔离条目超期告警天数（进 CI 回归记录）
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
  # 每日回归（可选）：只能由 CI schedule / 手工 dispatch 触发同一个 CI full workflow，不建本机 worktree。
  daily-regression:
    enabled: true
    workflow: "<已登记 CI full workflow 名称>"
    record-dir: dev/ci-regression  # 每次落盘 <date>-<tree8>.md：CI run / verdict / run proof / tree / 合入区间 /
                                   #   结果 / 归因 / 二分结论 / 隔离数与签名漂移 / 超期隔离条目
    skip-if-unchanged: true        # main tree 与最近一次 CI 记录相同 → 当日跳过
    max-gap-days: 3                # 漏跑只能在 CI 补触发同一 workflow，不得本机补跑
    # 清单外新失败由 CI 在合入区间内二分（每步只跑失败项）并生成归因记录；agent 不读整套日志。
  # 基线冻结：票开工时固定 base（记 impl-log），实现与评审期间不追集成分支；只在合入前 rebase 一次
  #   无冲突且本票文件 blob 不变 → 复用 DoD + fast 证据；有冲突 → 重跑受影响 DoD + fast。
  base-freeze: true
  # 执行形态：
  #   - 票级实现 / 自修复 / 评审回修 / merge / close 只跑 DoD + fast，文件数、LOC 与影响面不改变这个边界；
  #   - local-human 需求收口的关联选择集先跑 direct 再跑 indirect，indirect 使用 feature-closeout.verify-budget-seconds；
  #     到点只停发新 indirect，在途项自然结束，机器报告与 deferred 证据写 feature 级 integration/；
  #   - 本机重测试仅限上述需求级选择集与单项归因，经 shared/scripts/test_slots.py run 包装，并发度 = $JOBS；
  #     后台执行、输出与退出码落文件后轮询，fast 不受限；
  #   - 单项超时且包装器记录 load-peak > ncpu 时先串行单独重跑一次；通过记 load-flake，仍败才进归因。


# 自修复轮次上限（构建失败或评审 BLOCKER 的回修循环，超出打回人工）
max-fix-rounds: 99
# 评审轮次预警：同票评审达到此轮数 → 主会话介入判断（拆票 / 缩范围 / 升级模型），不是硬停
review-round-alert: 3

# 需求符合性核对（主模型校验）：主会话在合并前核对「要的有没有、偏没偏」——与 ai-review 正交，
# 评审判代码质量（同档模型够用），本核对判需求语义符合性（必须主模型，实现 / 评审同档模型有同源盲区）。
# 方法：先独立枚举需求点（反向枚举先行，不看 impl-log 覆盖矩阵），再对照矩阵逐条核映射、打开代码验证语义；
# 结论落盘 impl/<issue-id>/conformance-<issue-id>.md，process_check.py --phase close 机器把关。
conformance:
  mode: always            # always = 每票合并前必核；sampled = 所有非集成票均按 sample-rate 抽查
                          #   （未抽中写 verdict: skipped 落盘留痕），集成票一律必核
  sample-rate: 1/3        # sampled 模式下非集成票的抽查比例
  checker: main-session   # main-session = 主会话亲核（默认，输入有界不读全量代码）；
                          #   dispatched = 上下文紧张时按派发协议开独立对话核对（模型取当前宿主 supervisor-review）

# 需求收口：全部票合入集成分支后，如何把需求合进 main（流程见「监督循环 · 需求收口」，门禁 feature_closeout.py）
feature-closeout:
  mode: ci-promote        # ci-promote（默认）= feature / branch / main 放行核同 tree CI verdict + run proof；
                          # local-human = 冻结代码与验收计划 → 自动证据 + 浏览器 / 命令功能验收 → 正式报告 → 人工确认 → 合 main → 发布 Prod，push 后 CI full 作安全网
  integration-branch: "feat/<feature-slug>"   # local-human 要求每需求独立集成分支
  since: null             # YYYY-MM-DD：此前创建的存量票不套用集成分支默认（保持 main-branch）；null = 全部套用
  verify-budget-seconds: 1200                 # 收口关联选择集 indirect 预算；direct 不受限
  selection-map: null     # 可选 dir-map 映射表路径；缺省 / null = dev/scoped-map.yaml；显式配置时 scoped_select.py 追加 --map <路径>
  main-drift-ignore:                          # 冻结后 main 只在这些路径前进时候选仍有效（非行为路径）
    - "dev/**"
    - "docs/**"
  verify-instance: null   # 人工测试实例：null = 在集成 worktree 用开发启动器；有实例时填
                          #   {name, deploy: "<部署命令，可含 {branch} {commit}>", lease: "<租约文件>"}
  prod: null              # 合 main 后的发布：{instance, sync, dry-run, status, rollback} 命令；null = 不在收口内发布

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

- 本机算力预算：只有 local-human 需求收口的关联选择集与单项归因经 `shared/scripts/test_slots.py run` 包装执行（预算真源是机器级 `~/.cache/agent-dev/slots/budget.json`，`host-budget` 节为镜像）；单项超时撞高负载（load-peak > ncpu）先串行重跑，通过记 `load-flake`，不直接归因。票级 fast 不受限，CI full 使用判读机预算
- 票级验证固定为每票 DoD + fast；`ticket-max-files` / `ticket-max-loc` 只约束拆票粒度，实测超限只能 re-split / needs-human。完整套件只能由 CI full 执行；ci-promote 以同 tree CI verdict + run proof 放行，local-human 在 main push 后以 CI full 作安全网；每日回归如启用也只能触发同一 CI workflow
- `ocr.mode: delegate` 是**默认模式而非降级**：OCR 侧不调 LLM（只做文件筛选 + 规则解析），评审智能由宿主 agent 内新开的独立对话承担；无 LLM 端点、未接 CI 也能获得同等纪律的独立评审
- 主会话监督 / 符合性核对与独立评审使用当前宿主 `dispatch.hosts.<宿主>.supervisor-review`，但评审必须另开独立会话；普通票取 `development.default`，集成票 / 高风险票及评审预警后的回修取 `development.escalated`，绝不按测试范围路由。开工前用 `shared/scripts/dispatch_config.py validate` 校验、`resolve` 解析原生参数，缺当前宿主或校验失败即停，不跨宿主套用。评审严格性由 rule.json、票 DoD 与红线复核保证
- 规则文件 `.opencodereview/rule.json` 结构：`include[]` / `exclude[]`（glob，优先级最高）/ `rules[{path, rule}]`（第一条匹配生效）；评审规则文本从 `REVIEW.md` 提炼维护
- 本文件不进 GLOSSARY；术语口径仍以 `./docs/GLOSSARY.md` 为准
