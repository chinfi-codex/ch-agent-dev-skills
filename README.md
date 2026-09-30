# ch-agent-dev-skills

<video src="ch-agent-dev-skills-intro.mp4" controls muted playsinline width="100%"></video>

把开发流水线装进 13 个 agent skill：方向判断 → PRD → 拆票 → 实现 → 评审 → 合并 → 复盘。

> 🎬 [打不开点此](ch-agent-dev-skills-intro.mp4)。实施思路参考 [gstack](https://github.com/garrytan/gstack)，开发 skill 参考 [mattpocock/skills](https://github.com/mattpocock/skills)（MIT）；安装与逐 skill 说明见 [使用指南.md](使用指南.md)。

**产品**：/ceo-office 判断值不值得做 · /pd-plan 拷打澄清出 feature brief · /issue 小变更（change-request）· /prd 写正式 PRD · /pd-review 审查补齐至可交付

**开发**：/setup-dev 仓库初始化 · /tech-spec 技术方案 · /issue-split 拆票（阻塞 DAG、可判定 DoD、verify-tier）· /implement 一票一 worktree 跑 TDD · /ai-review ocr 独立评审（delegate/local/ci）

**横切**：/prototype 不可拷打问题出一次性原型 · /review 发布后复盘沉淀两级经验 · /handoff 会话交接提示词

**主线**：/prd → /pd-review → /setup-dev → /tech-spec → /issue-split → 门②人审 → 逐票派发 /implement → /ai-review → 合并 → /review；小变更从 /issue 直达 /tech-spec。唯一人工门是门②（票清单确认），评审独立、只评不改，之后派发监督到合并全自动。产物归档在 ./docs/features/<feature-module>/<feature-slug>/。

**设计原则**：先判断方向再谈功能，先定义问题再定义方案；GLOSSARY 统一口径，拷打减少遗漏。

## 仓库结构

skills/ 下每个 skill 一个目录：SKILL.md.tmpl 是源码，SKILL.md 是生成物；shared/fragments/ 构建时内联，shared/templates/ 运行时读取。改后跑 bash ./sync.sh 重新生成并平铺分发到 Claude Code / ZCode / Kimi Code / Codex。
