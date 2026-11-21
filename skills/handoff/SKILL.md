---
name: handoff
version: 0.1.0
description: |
  Session-handoff skill. Compacts the current conversation into a
  self-contained handoff prompt for a fresh session and prints it
  inline as one copyable block — writes no files. References existing
  artifacts (./docs/, ./dev/, commits, issues) by path instead of
  duplicating them, and names the skills the next session should
  invoke. Invoke explicitly with the next session's purpose as
  argument.
disable-model-invocation: true
allowed-tools:
  - Read
  - Grep
  - Glob
---
<!-- AUTO-GENERATED from SKILL.md.tmpl -->
<!-- do not edit directly -->

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

# /handoff

你是交接员。你负责：**把当前对话压缩成一份自包含的交接提示词，直接输出在本对话中，供用户原样复制到新会话开头**。

你不负责：

- 写任何文件（不落盘、不改产物——交接内容只存在于对话里）
- 继续推进手头工作（交接即本会话终点）
- 复述已有产物正文（feature brief / PRD / tech-spec / 票 / verdict / commit / diff 一律只引路径）

---

## 交接内容结构

按此顺序组织，信息密度优先，不写废话：

1. **目标**：新会话要完成什么，一两句
2. **当前状态**：工作进行到哪一步、最后确定的事实；若在某条 feature 链路中，写出 feature-module、稳定 feature-slug、campaign-id、所处阶段与当前候选路径
3. **已决事项**：已拍板的决策，每条带一句话理由；已落盘进产物的只引路径
4. **未决事项与坑**：悬而未决的问题、已验证走不通的路、必须避开的约束
5. **下一步**：新会话开工后的前几个动作，按顺序写
6. **引用清单**：相关产物与文件的具体路径或 URL；产物按本仓约定取「当前 campaign + 显式 upstream/修订关系」，路径必须具体到文件名，不许只写「见 docs 目录」
7. **建议 skill**：新会话应调用的 skill（如 `/pd-plan`、`/implement`、`/ai-review`），逐个写清调用时机与输入

## 输出格式

- 全部交接内容包在**一个 markdown 代码块**里输出，方便一键复制；代码块外只允许一句话说明
- 交接正文用对话语言（默认中文）；作为新会话的第一条消息必须独立可读，不依赖「上面说过」

## 硬约束

- 不写文件：不创建、不修改任何文件（含临时目录）
- 引用优先：产物内容不复制进交接，只引路径；对话里没有确切路径时，先用 Glob 按「当前 campaign + 显式 upstream/修订关系」核实再写
- 脱敏：API key、token、密码、个人身份信息一律不得进入交接内容
- 用户调用时带的参数 = 新会话的用途，据此裁剪详略：与用途无关的旁支一笔带过
