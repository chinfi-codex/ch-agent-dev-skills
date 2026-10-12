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
  # 被派发对话模型：当前宿主 agent 可选模型清单中的具体模型，按人确认的优先级排序——
  # 派发时取清单中第一个当前可用的模型，首选不可用按序降级；高风险 / 复杂票显式升级（升级目标也取自本清单）
  model: [<setup-dev 时枚举宿主可选模型、由人确认优先级后填写具体模型名；推荐首选取可选范围内经济性最高的一档>]

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
# 不变式（任何档位不得缩水）：红线检查 / DoD 验收命令 / 独立评审 / 每 feature 至少一次 full——
# 唯一例外是集成票的「模块隔离认定」（见 verify-policy 集成票条）：认定成立时按 module-scoped 跑涉及模块的全闭包，
# 不再机械全仓；认定不成立或有疑义时 full 不变。
verify-policy:
  light-max-files: 3        # declared-scope 生产代码文件数 ≤ 此值、且不触发 escalate-triggers，才允许 light
  scoped-max-files: 10      # 实测生产代码文件数 > 此值 → 直接 full；拆票时预估 > 此值 → 票必须再拆（/issue-split 粒度红线）
  scoped-max-loc: 400       # 实测 diff LOC > 此值 → 直接 full
  escalate-triggers:        # 命中任一（声明或实测）→ 升级验证；按两级分别处理，不一律全量
    # —— full 级：影响面难以静态枚举、或错误代价高的变更，直接 full
    - "公共接口 / API 签名变更"                     # full 级
    - "数据迁移 / schema 兼容变更"                  # full 级
    - "鉴权 / 权限逻辑改动"                         # full 级
    # —— scoped+ 级：影响面通常可静态枚举的变更，先判可枚举性，不盲目全量
    - "共享模块 / 工具库改动"                       # scoped+ 级
    - "红线邻接（redlines glob 命中文件的同目录）"   # scoped+ 级
  # scoped+ 级处理：按 selection 反查变更文件的反向依赖闭包（含调用方测试）——
  #   闭包完整可枚举（无动态加载 / 反射 / subprocess 间接调用变更模块）→ scoped，选择集 = 全闭包；
  #   闭包不可枚举（存在上述动态依赖）→ full。
  # 选择集大小不是升档理由：闭包即精确选择集，大闭包跑大闭包；占比 ≥ 50% 时在 evidence 记录
  # （作为「本票影响面广」的评审信号），不升 full——动态依赖与跨模块交互风险由集成票
  # （DAG 终点票）的 full 统一兜底，不由每票重复全量。
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
                           #   module-scoped = 按需求涉及模块选择该模块的全部测试（模块目录映射 + 该模块的反向闭包测试），
                           #                不跑全仓——用于「模块隔离认定」成立的集成票与 C 类仓末票；
                           #                选择面比 scoped 宽（整个模块而非本票 diff）、比 full 窄（不跨无关模块）
  # 先在失败归因：套件基线长期存在与本票无关的失败时，档位门只对可归因于本票 diff 的失败判红
  attribution: known-failures
    # known-failures（推荐默认）：维护落盘的既有失败清单（测试名 + 失败签名 + 首录日期 + 来源 evidence）；
    #   失败先比对清单——在清单且签名一致 = 先在失败，evidence 显式列出但不进 all-passed 分母；
    #   不在清单或签名不符 = 疑似本票引入，只对这几个单项补跑 base 侧对照，不整套重跑；
    #   清单由 full 档执行顺带刷新（全量结果即新基线），条目修复后销记
    # two-sided（保守档）：每条失败 branch / base 同命令各跑一遍对照——归因成本翻倍（全套 ×2），
    #   仅套件小或清单机制未建时使用
  # 执行形态（耗时契约，档位语义不因此缩水）：
  #   - 独立用例型套件应并行执行（并发度按机器自定；共享端口 / 文件等资源的用例标记 serial 除外）
  #   - 单项超时分层：默认上限 + slow 白名单（实测头部慢项登记）——避免个别卡死项烧满统一上限
  #   - full 档仍全量跑（slow 项不剔），慢项排前起跑压墙钟；同 tree 已跑过 full 的证据可复用，不重复执行

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

- 验证档位制：交付验证按票 frontmatter 的 `verify-tier` 分档执行（规则见上方 `verify-policy`），full 不再每票必跑；触发器分两级（full 级直接全量，scoped+ 级按影响面可枚举性定档），选择集大小不是升档理由；每 feature 至少一次 full 由集成票（DAG 终点票）保证，动态依赖与跨模块交互风险由它统一兜底；集成票唯一例外是「模块隔离认定」——feature 全程只动不被外部依赖的叶子模块时按 module-scoped 跑该模块全闭包，不机械全仓。
- `ocr.mode: delegate` 是**默认模式而非降级**：OCR 侧不调 LLM（只做文件筛选 + 规则解析），评审智能由宿主 agent 内新开的独立对话承担；无 LLM 端点、未接 CI 也能获得同等纪律的独立评审
- 评审与实现的被派发对话模型默认取 `dispatch.model` 清单中第一个当前可用的宿主模型（清单由 `/setup-dev` 枚举宿主可选模型后由人确认优先级）；评审严格性由 rule.json、票 DoD 与红线复核保证，不依赖模型档位
- 规则文件 `.opencodereview/rule.json` 结构：`include[]` / `exclude[]`（glob，优先级最高）/ `rules[{path, rule}]`（第一条匹配生效）；评审规则文本从 `REVIEW.md` 提炼维护
- 本文件不进 GLOSSARY；术语口径仍以 `./docs/GLOSSARY.md` 为准
