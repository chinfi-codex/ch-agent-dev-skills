# ch-agent-dev-skills

<video src="ch-agent-dev-skills-intro.mp4" controls muted playsinline width="100%"></video>

把开发流水线装进 13 个 agent skill：方向判断 → PRD → 拆票 → 实现 → 评审 → 合并 → 复盘。

> 🎬 [打不开点此](ch-agent-dev-skills-intro.mp4)。实施思路参考 [gstack](https://github.com/garrytan/gstack)，开发 skill 参考 [mattpocock/skills](https://github.com/mattpocock/skills)（MIT）；安装与逐 skill 说明见 [使用指南.md](使用指南.md)。

**产品**：/ceo-office 判断值不值得做 · /pd-plan 拷打澄清出 feature brief · /issue 小变更（change-request）· /prd 写正式 PRD · /pd-review 审查补齐至可交付

**开发**：/setup-dev 仓库初始化 · /tech-spec 技术方案 · /issue-split 拆票（阻塞 DAG、可判定 DoD、改动边界与粒度红线）· /implement 一票一 worktree 跑 TDD · /ai-review ocr 独立评审（delegate/local/ci）

**横切**：/prototype 不可拷打问题出一次性原型 · /review 发布后复盘沉淀两级经验 · /handoff 会话交接提示词

**主线**：/prd → /pd-review → /setup-dev → /tech-spec → /issue-split → 门②人审 → 逐票派发 /implement → /ai-review → 合并 → /review；小变更从 /issue 直达 /tech-spec。门②是唯一例行人工确认；异常通过 human-decision 单点处理。测试由 `verify_issue.py` 真实执行，评审由独立实例生成回执，合并只走 `delivery.py`，清场只走 `close-and-clean / reconcile`。产物归档在 ./docs/features/<feature-module>/<feature-slug>/ 与 ./dev/features/<feature-module>/<feature-slug>/。

**v2 硬约束**：票开工冻结 base / target / scope / 输入哈希；不接受手写测试通过、commit 前缀复用、同会话自评或本地 CI 自证。服务端强制、协作式 CI 与本地人工模式明确分离，能力未知即停止或保留资源。需要人裁决时由主会话统一问人，子会话只提交决策请求，无人值守只记录并继续其他票。

**设计原则**：先判断方向再谈功能，先定义问题再定义方案；GLOSSARY 统一口径，拷打减少遗漏。

## 仓库结构

skills/ 下每个 skill 一个目录：SKILL.md.tmpl 是源码，SKILL.md 是生成物；shared/fragments/ 构建时内联，shared/templates/ 运行时读取。改后先用 `node --experimental-strip-types skills/scripts/gen-skill-docs.ts` 显式生成，再用 `--check` 只读核对。`bash ./sync.sh` 只在检查通过后平铺分发到 Claude Code / ZCode / Kimi Code / Codex / DeepSeek Harness（DSH），不会隐式重写生成物；同时为 CC / Codex / ZCode 安装按 docs/dev 阶段自动命名顶层 session 的 hooks（合并且保留已有配置），并注册 DSH session-title 与 progress-panel bundle（Desktop 需先完全退出）。
