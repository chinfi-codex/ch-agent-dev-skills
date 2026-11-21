# ch-agent-dev-skills

<video src="ch-agent-dev-skills-intro.mp4" controls muted playsinline width="100%"></video>

把开发流水线装进 15 个 agent skill：方向判断 → PRD → 拆票 → 实现 → 评审 → 合并 → 复盘。

> 🎬 [打不开点此](ch-agent-dev-skills-intro.mp4)。实施思路参考 [gstack](https://github.com/garrytan/gstack)，开发 skill 参考 [mattpocock/skills](https://github.com/mattpocock/skills)（MIT）；/how、/why 方法参考并改编 [Lauren Tan 的 pstack](https://github.com/backnotprop/pstack)（MIT，见 `skills/shared/references/pstack-how-why-LICENSE.md`）；安装与逐 skill 说明见 [使用指南.md](使用指南.md)。

**产品**：/ceo-office 判断值不值得做 · /pd-plan 拷打澄清出 feature brief · /issue 小变更（change-request）· /prd 写正式 PRD · /pd-review 审查补齐至可交付

**开发**：/setup-dev 仓库初始化 · /how 按需还原当前机制 · /why 定向调查历史动机 · /tech-spec 技术方案 · /issue-split 拆票（阻塞 DAG、可判定 DoD、改动边界与粒度红线）· /implement 一票一 worktree 跑 TDD · /ai-review ocr 独立评审（delegate/local/ci）

**横切**：/prototype 不可拷打问题出一次性原型 · /review 发布后复盘沉淀两级经验 · /handoff 会话交接提示词

**主线**：/prd → /pd-review → /setup-dev → 现状查证（按需 /how、/why）→ /tech-spec → /issue-split → 门②人审 → 逐票派发 /implement → 票级内环 → merged → feature freeze → /ai-review（整需求一次）→ 候选复跑 + conformance + arch-check → promote + finalize → /review；小变更从 /issue 直达 /tech-spec。门②是唯一例行人工确认；异常通过 human-decision 单点处理。How/Why 默认主会话轻量调查，按风险升级标准/深度调查；七类来源作为检索菜单，保留 Direct / Supported / Inferred / Speculative / Unknown 分级；轻量证据进入 spec，复杂或可复用调查按需归档；实现证伪承重决策时走 redesign 重新调查与确认。测试由 `verify_issue.py` 真实执行，feature 评审由独立实例生成候选回执，票级只做内环、不在每票做评审 / 复跑 / conformance / 架构检查；架构契约由 `docs/ARCH-CHECK.json` 定义、`docs/ARCHITECTURE.md` 确定性生成（11 节架构总览 + 规则附录），`/setup-dev` 只落空骨架，首次架构影响由 `/tech-spec` 3.2 按证据补全，feature candidate 强制 arch-check，pre-push 只做提前预警；合并只走 `delivery.py`，清场只走 `finalize / close-and-clean / reconcile`。需求归属保持 module / feature 两级；交付产物分别归档在 docs、dev 的 features/<module>/<feature>/campaigns/<campaign-id>/，各阶段按需建立并通过明确路径连接。

**v2 硬约束**：票开工冻结 base / target / scope / 输入哈希；不接受手写测试通过、commit 前缀复用、同会话自评或本地 CI 自证。架构总览与规则唯一真源是 `docs/ARCH-CHECK.json`，`docs/ARCHITECTURE.md` 只能由 `arch_check.py render write` 生成，禁止手改或只填一半骨架；票级不设架构门，feature candidate 强制 arch-check，本地 pre-push 只做预警。服务端强制、协作式 CI 与本地人工模式明确分离，能力未知即停止或保留资源。需要人裁决时由主会话统一问人，子会话只提交决策请求，无人值守只记录并继续其他票。

**设计原则**：先判断方向再谈功能，先定义问题再定义方案；GLOSSARY 统一口径，拷打减少遗漏。

## 仓库结构

skills/ 下每个 skill 一个目录：SKILL.md.tmpl 是源码，SKILL.md 是生成物；shared/fragments/ 构建时内联，shared/templates/ 运行时读取。改后先用 `node --experimental-strip-types skills/scripts/gen-skill-docs.ts` 显式生成，再用 `--check` 只读核对。`bash ./sync.sh` 只在检查通过后平铺分发到 Claude Code / ZCode / Kimi Code / Codex / DeepSeek Harness（DSH），不会隐式重写生成物；同时为 CC / Codex / ZCode 安装按 docs/dev 阶段自动命名顶层 session 的 hooks（合并且保留已有配置），并注册 DSH session-title 与 progress-panel bundle（Desktop 需先完全退出）。
