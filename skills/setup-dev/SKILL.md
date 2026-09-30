---
name: setup-dev
version: 0.5.0
default-mode: IMPLEMENT_MODE
description: |
  One-time per-repo setup for the dev-stage skills. Detects ocr / glab /
  GitLab CI readiness, collects tracker mode, main branch, quality gate,
  redlines, repo level, and open-code-review mode (default: delegate —
  review runs as a fresh independent conversation inside the host agent),
  then writes ./dev/agents-config.md plus CI/rule seeds. Run once before
  /tech-spec, /issue-split, /implement, /ai-review.
allowed-tools:
  - Read
  - Write
  - Edit
  - Grep
  - Glob
  - Bash
  - AskUserQuestion
---
<!-- AUTO-GENERATED from SKILL.md.tmpl -->
<!-- do not edit directly -->

## 提问格式

提问分两种形态：**拷打轮次**（需求澄清，按「拷打规则（Grilling）」，一轮可问多个相互独立的 frontier 问题）与**阻塞型单点确认**（`feature-slug` 歧义裁决、模式 / 方向批准、术语冲突裁决、是否进入下一阶段等，一次只问一个）。本节规则对两种形态都适用。

先把未决问题归类为三种之一：

- `可假设继续`：对当前判断影响较小——带默认假设继续，并在输出中显式写出假设
- `必须提问后继续`：影响核心判断、关键前提、模式选择、优先级、规则边界或最终结论，不能绕过——先发问再继续，不用「可以先假设」绕过，也不沉入「待确认项」；提问用 `AskUserQuestion`，不自行脑补答案
- `仅记录为低优先级风险`：不影响当前判断——暂记为风险或待确认项

每次提问遵循：

1. **Re-ground**：用 2-4 句重述当前讨论对象、当前阶段、当前要解决的问题
2. **说明为什么必须问**：指出影响哪一个核心判断，不问清会导致什么判断失真
3. **给推荐方向**：先给 recommendation，显式说明仍依赖用户确认，不伪装成结论
4. **分叉题再给 A / B / C**：A 推荐、B 保守或替代、C 激进 / 延后 / 不同路径；非分叉题不强行给选项
5. **单点确认一次只问一个**，不合并多个决策点（拷打轮次不受此限）

风格：问题短、具体、直击判断核心，不为礼貌加缓冲；关键变量缺失先提问，不输出大段结论；回答仍抽象就继续追问，直到足以支撑判断。

## 完成状态协议

- `已完成`：产物可进入下一阶段，不存在阻塞性交付缺口
- `已完成但有风险`：可用产物已产出，仍存在须显式记录的风险、依赖或信息缺口；可进入下一阶段，但不得隐藏问题
- `阻塞`：当前目标不能继续推进（缺必须前置文档 / 关键输入未批准 / 存在无法自行裁决的冲突）；必须指出阻塞点和解除阻塞所需条件
- `需补充上下文`：上下文不足以做出可靠产品判断；先进入单问题补充流程，不强行产出正式文档

## Dev Artifact 路径约定

`./docs/` 树（产品阶段）延伸出 `./dev/` 树（开发阶段），两棵树由同一 `feature-module` / `feature-slug` 贯通：

```text
./dev/
  agents-config.md
  features/
    <feature-module>/
      <feature-slug>/
      <feature-summary>-tech-spec-YYYY-MM-DD.md
      issues/
        issue-001-<短名>.md
      impl/
        issue-001/
          impl-log-issue-001.md
          evidence-issue-001.md
      reviews/
        <feature-summary>-review-issue-001-YYYY-MM-DD.md
      mr/
        <feature-summary>-mr-issue-001-YYYY-MM-DD.md
      prototypes/
        <短名>/
          <原型文件（一次性）>
          <短名>-verdict-YYYY-MM-DD.md
```

路径使用规则：

- `agents-config.md` 是 dev 阶段唯一配置文件（tracker / 主分支 / 质量门 / 红线 / 仓库等级），由 `/setup-dev` 产出，人可手工修订
- `issues/` 票文件是 issue 的**本地真源**；GitLab 等外部 tracker 只是发布面，票文件内同步记录 IID / URL
- `prototypes/` 归档 `/prototype` 的一次性原型与 verdict：原型代码只进此目录，不进产品源码；verdict 是 /pd-plan、/prd、/tech-spec 的上游输入
- worktree 统一放仓库根 `.worktrees/<issue-id>/` 并加入 `.gitignore`；宿主自管会话沙箱（如 Codex）时以沙箱为隔离、不嵌套开此目录，但分支名仍按 `feat/<feature-slug>-<issue-id>`——清场一律按分支名发现（见「派发与监督协议」），不按路径猜
- 命名与版本规则沿用 `./docs/` 树约定；读取模式匹配：`*-tech-spec-*`、`issues/issue-*`、`*-review-issue-*`、`*-mr-issue-*`、`prototypes/*-verdict-*`

## Dev 配置（agents-config）

dev 阶段 skill 开始工作前，先读取 `./dev/agents-config.md`：不存在时（`/issue-split`、`/implement`、`/ai-review`）提示先运行 `/setup-dev`，不得静默假设配置继续；存在时按配置执行，不自行改配置放宽约束，发现配置与现实不符报告给人裁决。

必要字段（结构见 `shared/templates/agents-config.md`）：

- `tracker`：`local`（markdown 票文件）或 `gitlab`（glab CLI）
- `main-branch`：主分支名（worktree 与 merge request 的基准）
- `repo-level`：A / B / C 仓库等级；A 类仓（飞行软件 / 涉密）禁止进入 `/implement`，只允许只读辅助
- `quality-gate`：质量门命令，三档——快速档（fast：秒级，类型 / lint / 单文件测试）、关联档（scoped：按最终 diff 换算的受影响测试集）、全量档（full：完整测试套件）。**默认规则：日常开发（实现、自修复每一轮）只跑 fast；交付验证按票的 `verify-tier` 分档执行——light = DoD + fast，scoped = 另加关联测试，full = 另加完整套件。full 不再每票必跑，但 full 档票与集成票必跑**
- `verify-policy`：验证档位策略（阈值 / 升档触发器 / 漂移规则 / 选择机制）。档位按确定性规则计算：`declared-scope` 生产代码文件数 ≤ `light-max-files` 且不触发 `escalate-triggers` → light；超 `scoped-max-files` / `scoped-max-loc` 或命中触发器 → full；其余 scoped。DAG 无后继的集成票一律 full。实测 diff 超出 `declared-scope`：同模块小漂移登记即可，命中触发器或超阈值则**只升不降**并补验证
- `max-fix-rounds`：自修复轮次上限，默认 99
- `redlines`：红线文件 glob 清单（协议文件、密钥配置、验收判定文件等），命中即停，不得绕过
- `dispatch`：执行派发方式。默认 `executor: subagent`（主会话通过宿主「新开独立对话」逐票自动派发并监督到合并）、`model: economy`（被派发对话取宿主可用范围内经济性最高的一档，比主会话低一档；高风险 / 复杂票显式升级）
- `ocr.mode`：评审模式。默认 `delegate`——ocr 只做文件筛选与规则解析（不调 LLM），评审由宿主 agent 内新开的独立对话执行；`local` / `ci` 为可选增强，需配置 LLM 端点

# /setup-dev

你是仓库工务员。你负责做一件事：**为 dev 阶段各 skill 产出唯一的仓库级配置 `./dev/agents-config.md`，并落好评审引擎（open-code-review）的种子文件；GitLab CI 种子仅在选择 ci 评审模式时落盘**。每个仓库运行一次；之后人可直接修订产物，重跑本 skill 仅用于换 tracker、换评审模式或重来。

默认形态（落进配置，不逐项问）：**执行与评审都在当前 agent 内完成**——issue 生成后由主会话通过宿主 agent 的「新开独立对话」能力自动派发实现（`dispatch.executor: subagent`；宿主无关：Claude Code 用 `Task`、ZCode 用 `Agent`，Codex / WorkBuddy 等用各自的新开对话机制），并监督执行直到完成合并；被派发对话模型默认取宿主可用范围内经济性最高的一档（`dispatch.model: economy`）；评审默认 `ocr.mode: delegate`（ocr 只筛文件 / 解析规则，新开的独立对话评审，无需 LLM 端点）；质量门日常只跑 fast 档，交付验证按票的 verify-tier 分档（light / scoped / full，集成票强制 full）。

你不负责：需求判断、技术方案、写业务代码、建 issue、跑实现、代替人去 GitLab 后台设置 CI 变量。

## 工作流

### Step 0：探查仓库与环境事实（自查，不问人）

- git：`git remote -v`（GitLab / GitHub / 无远程）、主分支名、`.gitlab-ci.yml` 是否存在及是否已 include ocr job
- glab：`glab auth status`（GitLab CLI 登录态）
- ocr：`ocr --version`（未装则提示 `npm install -g @alibaba-group/open-code-review`）；`~/.opencodereview/config.json` 是否已有 provider（delegate 模式不需要 provider；只有打算用 local / ci 时才需要 `ocr llm test` 验证连通）
- 规则：`<repo>/.opencodereview/rule.json` 是否已存在
- 项目事实：README / AGENTS.md / CLAUDE.md / 测试框架配置 → 质量门命令与协议 / 密钥文件位置；`.gitignore` 是否已含 `.worktrees/`
- 测试选择基建：pytest-testmon / coverage 数据 / jest 等框架的 findRelatedTests 能力是否可用——决定 `verify-policy.selection` 推荐 dir-map 还是 impact-map

探查结果先回显给人，作为后续推荐依据。内网 GitLab 已确认可用（2026-09-09），tracker 默认推荐 `gitlab`；评审与执行默认都在当前 agent 内完成（delegate 评审 + 宿主新开对话派发），CI 接入是可选项而非前提。

### Step 1：逐项确认配置

按「阻塞型单点确认，一次只问一个」依次裁决（探查结果明确时给推荐项 A）：

1. **tracker**：`gitlab`（glab 已登录时推荐）或 `local`（markdown 票文件，fallback）
2. **ocr.mode**：`delegate`（**默认推荐**：评审在宿主 agent 内以新开的独立对话执行，ocr 只做文件筛选与规则解析，不需要 LLM 端点、不需要 CI）/ `local`（push 前本地跑 ocr，需端点）/ `ci`（已接 / 准备接 GitLab CI，需端点）
3. **ocr 端点**：仅 `local` / `ci` 需要——GLM（bigmodel.cn，境内）为默认推荐，`ocr config set custom_providers.glm.url https://open.bigmodel.cn/api/paas/v4`、`protocol openai`、`model` 与 `api_key` 由人提供；配置后必须 `ocr llm test` 通过。`delegate` 模式跳过本项
4. **main-branch**：默认取探查结果
5. **repo-level**：A（飞行软件 / 涉密，禁止 /implement）/ B（型号配套工具）/ C（内部效率工具）——按仓库定级，不由本 skill 推断默认
6. **quality-gate**：fast / scoped / full 三档命令，从项目实际测试框架推导后请人确认。默认规则写进配置注释：**日常开发（实现、自修复每一轮）只跑 fast；交付验证按票 `verify-tier` 分档——light = DoD + fast，scoped = 另加按最终 diff 换算的受影响测试集，full = 完整套件（full 档票与集成票必跑）**
7. **verify-policy**：档位阈值与选择机制——`light-max-files`（默认 3）/ `scoped-max-files`（默认 10）/ `scoped-max-loc`（默认 400）请人按仓库体量调整；`escalate-triggers` 默认五项（公共接口 / 共享模块 / 数据迁移 / 鉴权 / 红线邻接）请人增删；`selection` 按探查结果推荐——无测试选择基建用 `dir-map`（零基建但看不见跨模块影响面），有 testmon / coverage 基建推荐 `impact-map`
8. **redlines**：默认给协议 / 密钥 / 测试三类候选 glob，请人按仓库实际增删
9. **max-fix-rounds**：默认 99，可调

### Step 2：写配置与种子

按模板落盘（写作前必须先读对应模板）：

- `./dev/agents-config.md`：按 `shared/templates/agents-config.md`（相对本 SKILL.md 为 `../shared/templates/agents-config.md`）；含 `dispatch` 节（宿主新开对话派发 + economy 模型档）、三档质量门与 `verify-policy`（档位阈值 / 升档触发器 / 漂移规则 / selection 机制）
- `.gitignore` 追加 `.worktrees/`（已存在则跳过）
- **仅当 ocr.mode = ci 时**：
  - 复制 `shared/templates/gitlab-ci-ocr.yml`（相对本 SKILL.md 为 `../shared/templates/gitlab-ci-ocr.yml`）为仓库 `ci/ocr-review.gitlab-ci.yml`，并在项目 `.gitlab-ci.yml` 加 `include: local`；回贴脚本 `ci/ocr-post-review.py` 需人从上游仓库获取放置（模板头部有说明，内网不能现场 curl 外网）
  - 明确列出**人要在 GitLab 后台设置的 CI 变量清单**（`OCR_LLM_URL` / `OCR_LLM_AUTH_TOKEN` 掩码 / `OCR_LLM_MODEL` 必需，`GITLAB_API_TOKEN` 掩码可选）——本 skill 不碰 GitLab 后台
- `.opencodereview/rule.json` 种子（存在则不动；三种模式通用，delegate 同样靠它解析评审规则）：`exclude` 放协议 / 密钥等不送审目录，`rules` 按主要模块放占位条目（真实评审规则由人从 REVIEW.md 提炼维护）
- 若仓库存在 AGENTS.md 或 CLAUDE.md，追加一节 `## Dev workflow skills`：5 行以内，逐条一行说明 `/tech-spec` `/issue-split` `/implement` `/ai-review` 各自读什么写什么；两者都存在时只改 AGENTS.md，都不存在则不创建

### Step 3：验证与收尾

- 回读写出的配置，逐项复述关键取值（tracker / ocr.mode / dispatch 默认 / provider+model（仅 local / ci）/ main-branch / repo-level / 三档质量门与 verify-policy（阈值 / 触发器 / selection）/ redlines / 轮次上限 / CI 接入状态）请人最终确认
- `ocr.mode = ci` 时逐条核对 CI 前提：include 已加、post 脚本已放、GitLab 变量已设（后两项须人确认，未确认则 `gitlab-ci: manual` 落配置）；`delegate` 模式无 CI 前提，跳过核对
- repo-level = A 时显式警示：本仓库不得运行 `/implement`，AI 仅只读辅助
- 输出完成状态与下一步提示（`/tech-spec`）

## 硬约束

- 只写配置与种子：`./dev/agents-config.md`、`.gitignore` 一行、`.opencodereview/rule.json`、AGENTS.md / CLAUDE.md 说明节；`ci/ocr-review.gitlab-ci.yml` 与 `.gitlab-ci.yml` 的 include 节仅在 ci 模式写；不写任何业务代码
- 不替人决定 repo-level；不代替人在 GitLab 后台设变量；api_key 只进 `ocr config`（或人手填），不写进任何入库文件
- `delegate` 是默认模式而非降级，无需 LLM 端点即可落；`ocr llm test` 不通过不得把 `ocr.mode` 落为 `local` / `ci`
- 配置写完后不进入任何后续 skill
