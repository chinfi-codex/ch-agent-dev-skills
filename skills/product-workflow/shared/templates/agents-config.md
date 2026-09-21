# agents-config

> 由 `/setup-dev` 产出；人可直接修订本文件。dev 阶段各 skill 的唯一配置真源。
> 事实背景：内网 GitLab 已确认可用（2026-09-09）；GLM 端点走 bigmodel.cn（境内，满足数据不出境）。
> 默认形态：**执行与评审都在当前 agent 内完成**——issue 生成后由主会话通过宿主 agent 的「新开独立对话」能力自动派发实现（宿主无关：Claude Code `Task` / ZCode `Agent` / Codex / WorkBuddy 等各自的新开对话机制），并监督执行直到完成合并；评审走 ocr `delegate` 模式；GitLab CI 与 LLM 端点均为可选增强，不是前提。

```yaml
# issue 发布面：gitlab = glab CLI 建 issue / draft MR（内网 GitLab 已确认，首选）
#              local  = ./dev/features/<feature-slug>/issues/ 票文件（无 glab 环境的 fallback）
tracker: gitlab

# 主分支名：worktree 与 MR 的基准分支
main-branch: main

# 执行派发（issue 生成后由主会话自动派发并监督到合并，无需人工触发）
dispatch:
  executor: subagent   # subagent = 主会话通过宿主 agent 的新开独立对话能力执行 /implement
                       # （宿主无关：Claude Code = Task，ZCode = Agent，Codex / WorkBuddy =
                       #  各自的新会话 / 派发机制；宿主无此能力时按派发协议兜底）
  model: economy       # 被派发对话默认模型：当前宿主可用范围内经济性最高的一档（低主会话一档）；
                       # 仅高风险 / 复杂票显式升级

# 质量门命令
quality-gate:
  fast: "pytest -q <相关测试文件>"      # 日常开发默认档：实现 / 自修复每一轮只跑 fast（秒级）
  scoped: "pytest -q <按最终 diff 换算的受影响测试集>"   # scoped 档票的交付门：选择集从 merge-base..HEAD 的实际 diff 重算
  full: "pytest -q"                     # full 档票与集成票的交付门：完整测试套件

# 验证档位策略：交付验证按票的 verify-tier 分档，不再每票一刀切跑全量
# 档位含义（累计向下包含）：
#   light  = DoD 验收命令 + fast                （单点小改：文案 / 阈值 / 默认值 / 局部样式）
#   scoped = light + 关联测试选择                （改动集中在 1-2 个模块的常规票）
#   full   = scoped + 完整测试套件               （高风险票与集成票）
# 不变式（任何档位不得缩水）：红线检查 / DoD 验收命令 / 独立评审 / 每 feature 至少一次 full（集成票）
verify-policy:
  light-max-files: 3        # declared-scope 生产代码文件数 ≤ 此值、且不触发 escalate-triggers，才允许 light
  scoped-max-files: 10      # 实测生产代码文件数 > 此值 → 直接 full
  scoped-max-loc: 400       # 实测 diff LOC > 此值 → 直接 full
  escalate-triggers:        # 命中任一（声明或实测）→ 至少 full，无论文件数
    - "公共接口 / API 签名变更"
    - "共享模块 / 工具库改动"
    - "数据迁移 / schema 变更"
    - "鉴权 / 权限逻辑改动"
    - "红线邻接（redlines glob 命中文件的同目录）"
  # 漂移规则（实测 diff 超出票的 declared-scope 时）：
  #   仍同模块、未触发 escalate-triggers、未超阈值 → 不升档，impl-log「漂移与升档记录」登记漂移文件清单即可
  #   触发 escalate-triggers 或超 scoped-max-* 阈值 → 升档（只升不降），立即按新档补验证并在 evidence 记录
  # 集成票：DAG 中无后继（没有其他票 blocked-by 它）的票一律 verify-tier: full
  # repo-level 平移：C 类仓可在计算结果上再降一档（下限 light）；B 类按规则执行；A 类不进 /implement
  selection: dir-map        # scoped 档的关联测试选择机制：
                           #   dir-map    = 目录约定映射（源码路径 → 测试路径，零基建；看不见跨模块影响面，只配低风险 scoped 票）
                           #   impact-map = coverage / testmon 反查（diff → 执行过这些行的测试，含调用方模块的测试；
                           #                出现 scoped 漏测反馈后应升级到本档）

# 自修复轮次上限（构建失败或评审 BLOCKER 的回修循环，超出打回人工）
max-fix-rounds: 99

# 红线：命中任一 glob 即停止并报告，不得绕过
# 注意：redlines 是「不允许改」；ocr exclude 是「不送审」，两者不可互替
redlines:
  - "protocols/**"        # 协议文件（示例，按仓库实际填写）
  - "**/secrets*"
  - "**/*.key"
  - "tests/**"            # 禁止为通过验收而修改测试；TDD 新增测试除外，须在 impl-log 中说明

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

- 验证档位制：交付验证按票 frontmatter 的 `verify-tier` 分档执行（规则见上方 `verify-policy`），full 不再每票必跑；每 feature 至少一次 full 由集成票（DAG 终点票）保证
- `ocr.mode: delegate` 是**默认模式而非降级**：OCR 侧不调 LLM（只做文件筛选 + 规则解析），评审智能由宿主 agent 内新开的独立对话承担；无 LLM 端点、未接 CI 也能获得同等纪律的独立评审
- 评审与实现的被派发对话模型默认取 `dispatch.model`（经济性最高档）；评审严格性由 rule.json、票 DoD 与红线复核保证，不依赖模型档位
- 规则文件 `.opencodereview/rule.json` 结构：`include[]` / `exclude[]`（glob，优先级最高）/ `rules[{path, rule}]`（第一条匹配生效）；评审规则文本从 `REVIEW.md` 提炼维护
- 本文件不进 GLOSSARY；术语口径仍以 `./docs/GLOSSARY.md` 为准
