# 会话召回设计：journal + recall（参考 claude-mem 渐进式披露）

> 状态：设计稿（2026-10-25）。上游依据：progress.jsonl 写侧契约（supervision-loop.md「状态与事件写入契约」，未提交）与 token 成本分析中唯一遗留项「渐进式披露」。参考项目：[thedotmack/claude-mem](https://github.com/thedotmack/claude-mem)（截至 2026-10 文档）。

## 1. 痛点

长期项目、多轮会话反复进出同一个仓库、上下文重建成本高，具体拆成三笔账：

1. **每次进仓全量重读**。新会话按读取顺序约定重建上下文：GLOSSARY 全文、票文件、ARCHITECTURE 现行态、impl-log（复审时全文）、EXPERIENCE grep、progress.jsonl replay（issue/SKILL.md:48-49、implement/SKILL.md:29-31、supervision-loop.md:44）。前两项成本随**项目规模**线性增长——长期项目里每次进入都重付一遍，与会话数无关。
2. **没有增量视图**。「上次离开之后这个仓发生了什么」今天要靠 replay 全量事件 + 逐票对账拼出来；知识性变化（坑、决策、契约变更）没有单独的廉价入口，散在 impl-log / 评审报告 / EXPERIENCE 三级容器里，前两级只在复盘时才被回收（review/SKILL.md:300-304）。
3. **检索无成本分层**。EXPERIENCE 超 50 行 grep 滤行（issue/SKILL.md:49）是唯一雏形；GLOSSARY / ARCHITECTURE / 评审报告都是「读就读全文」，没有「先看索引行、判断要不要读、再定位」的中间层。这正是 token 成本分析（2026-10）五项落地后仅剩的「渐进式披露」。

监督循环的既定原则放大了这个问题：续跑一律另起新会话靠落盘文件接力、不唤醒长会话（supervision-loop.md:44）——**新会话冷启动是常态而非例外**，冷启动成本就是每轮都在付的固定税。

## 2. claude-mem 机制拆解与借鉴判定

claude-mem 的做法：lifecycle hooks（SessionStart / UserPromptSubmit / PostToolUse / Stop / SessionEnd）捕获全部工具执行，worker 异步 AI 压缩成带类型的 observation（gotcha / problem-solution / how-it-works / trade-off 等），存 SQLite FTS5 + 向量混合索引；SessionStart 注入紧凑索引（最近 10 条会话摘要 + 50 条 observation，约 800–1000 tokens）；需要时按 4 层渐进披露取详情。关键实测数字：L1 索引每条 50–100 tokens、L3 详情约 155 tokens/条；对比传统 RAG 预注入 35,000 tokens 只有 6% 相关（94% 浪费），渐进式 920 tokens 相关性 100%；50 条索引通常只下钻 2–3 条；找上下文时间 90s → 30s。

| claude-mem 机制 | 本套件判定 | 理由 |
|---|---|---|
| 4 层渐进披露：索引 → 时间线 → 详情 → 原始证据，ID 门控（无上层 ID 禁止直接取 L4） | **采纳为统一纪律** | 直接纳入查读三级；L4 门控对应「没有指针不读 evidence 全文」 |
| SessionStart 注入紧凑索引；resume/compact 跳过注入（保 prompt cache） | **采纳，分两步**：P0 做成查询入口命令（宿主无关），P1 在 CC/ZCode/Codex 已有 hook 面真注入 | 套件哲学是落盘文件 + 主动读；注入是锦上添花，不能是唯一通道 |
| Stop hook 结构化会话摘要（request / investigated / learned / completed / next_steps） | **采纳 chokepoint 版**：不做 hook 全捕获，在已有写点（监督循环收尾、digest、/review Step 5）多落一笔 | 全捕获 token 成本高、噪声大（claude-mem 自己也要跳过 TodoWrite 类低价值工具）；五宿主 hook 面不齐 |
| observation 类型词表 + 每条 ~token 成本提示 | **采纳形态**：行类型标签 + 指针 | 指针（票号 / commit / 路径#节）本身就是成本提示——指明去哪读、读多少可估 |
| 每月一个 markdown log 追加（append-only） | **直接采纳**：`journal/YYYY-MM.md` | 与 dev/ 树纯文本文化一致，grep 即索引 |
| SQLite FTS5 + Chroma 向量混合检索 | **P0 不做；P2 可选 FTS5**（python stdlib，派生索引可从真源重建）；**向量明确不做** | 五宿主零依赖原则；真源必须保持纯文本可 grep |
| 常驻 worker / HTTP 服务 / 云同步 | **不做** | 与「宿主无关、无常驻」架构冲突 |
| per-project 隔离 | 已有（dev/ 树按 feature 隔离；progress.projects.yml 多仓注册） | 无需动作 |

## 3. 现状层映射：缺的不是存储，是索引和入口

把套件现有产物按 claude-mem 四层对齐：

| 层 | 套件现状 | 缺口 |
|---|---|---|
| L1 索引 | EXPERIENCE grep 滤行（issue/SKILL.md:49，唯一雏形） | **缺**：无仓级统一索引，无「自上次以来」窗口 |
| L2 时间线 | `progress.py replay`（工作流事件回放） | 半缺：工作流时间线有，**知识时间线无** |
| L3 详情 | impl-log / 评审报告（头部收口）/ EXPERIENCE / tech-spec supersedes 链 | 不缺：全部落盘且路径可寻址 |
| L4 原始 | git 历史 + evidence 文件 | 不缺 |

结论：L3/L4 存储已经很完备（这套件从不缺落盘），缺的是（a）一个**仓级知识流水 journal**（现在知识只在复盘级 EXPerence 和票级 impl-log 两级，没有自动、廉价、仓级的第三级），（b）一条**召回入口命令**把散落的 L1 素材合并输出，（c）**会话进入纪律**把「先索引后详情」变成文字契约。本方案即 progress.jsonl 写侧契约落地后的读侧闭环。

## 4. 方案

### 4.1 journal：仓级会话日志（写侧）

- **位置**：`./dev/journal/YYYY-MM.md`，dev 树根（仓级、跨 feature）、月度滚动、append-only。树形图补进 dev-artifact-paths.md。
- **行格式**：`- MM-DD [类型] 一句话结论 → 指针`。类型词表固定 6 个，不发明新词：`决策` / `坑` / `解法` / `契约` / `风险` / `摘要`。指针 = 票号 | commit | 路径#节。单行一句话（≤40 字），只写结论不写过程——过程归 impl-log。
- **写点全部挂在已有写点上，不新增 hook、不新增写者角色**：
  1. 监督循环第 6 步收尾总结的逐票大白话句 → journal（supervision-loop.md:26；同一段文字多落一处，零额外生成成本）；
  2. 停止 / 熔断 / 评审轮次预警的 `digest` 事件 note → journal 摘要行（supervision-loop.md:38,40）；
  3. /review Step 5 交付时「需求侧与开发侧最重要的发现各三条」→ journal（review/SKILL.md:310）。
  4. （P1）handoff 技能加 `--save`：当前只输出不落盘（handoff/SKILL.md:5-11），加一个落 journal 变体。
- **写者纪律**：实现对话不写 journal（并行票 append 交错风险；其结论经 impl-log 由收口/复盘回收）——保持「监督循环是事件流唯一写者」的既有格局（supervision-loop.md:5）。
- **体积**：月度滚动 + 单行一句话天然封顶（一年量级 < 2k tokens），不做归档机制；对齐 dev 留存规则「过程结论以文本留存」精神（dev-artifact-paths.md:47）。

### 4.2 recall：召回入口（读侧，progress.py 新子命令）

```
progress.py recall [--since 日期|auto] [--topic 关键词] [--detail 指针]
```

- **L1 输出**（默认，目标 ≤1k tokens），全部带指针：
  - journal 自 `--since` 起的行（知识）；
  - progress.jsonl 自 `--since` 起的事件（复用 replay 解析，工作流）；
  - EXPERIENCE.md 滤行（复用既有 grep 规则，行自带分类列）；
  - ARCHITECTURE.md 现行态一行（版本/日期 + supersedes 链尾指针）；
  - quarantine-list.yaml 超期条目计数一行；
  - 输出头部一句话下钻提示（`--detail` 用法）。
- **L2/L3 下钻**：`--detail <指针>` 展开——票 → 票全文 + impl-log 头部；日期 → journal 当日块 + 事件窗口；路径#节 → 该节。
- **`--since auto`**：P0 默认 7 天；P1 从四宿主 session-title.json 本仓最近绑定推 last-seen（progress.py:568-603 已读该文件；绑定记录当前无时间戳字段，P1 写侧补 `last-seen`）。
- **原则**（借 claude-mem）：索引只回答「有什么、去哪取」，取不取由会话按当前任务决定；指针即成本提示。

### 4.3 会话进入协议（文字纪律）

- 新 fragment `session-entry.md`（≤15 行）三级查读纪律：**索引行 → 节定位 → 才全文**；无指针不读 L4（evidence / 全量代码）。
- 注入面（改前查注入面，不全注入）：issue / implement 的读取顺序节、review Step 1、supervision-loop.md:44「恢复」条——把「读 unattended.md + decisions.md + progress.jsonl + 票 frontmatter 四件」改为「先 `recall`，再按指针取」。
- 与派发协议不冲突：被派发对话仍不继承主会话上下文（dispatch-protocol.md:7-8 不变），recall 是它们自读落盘文件的廉价化，写查分离不动。

### 4.4 宿主自适应注入（P1）

- CC / ZCode / Codex：已有 SessionStart hook 面（install_host_hooks.py:16-18，现仅会话命名）→ 扩同链路输出 recall 索引（CC `additionalContext` / ZCode SessionStart / Codex 同理）。**续跑不重复注入**（保 prompt cache）；compact 后补注入一次（对齐 claude-mem 的 Codex post-compact pending-marker 方案——本套件宿主同样存在压缩后丢上文问题）。
- DSH：dsh-session-title 原生 bundle 机制可挂侧栏入口。
- Kimi 等无 hook 面宿主：靠 4.3 文字纪律兜底。
- 与「可视化契约能力自适应」同一宿主分层模式（有原生能力的走原生，没有的走文字 + 脚本）。

### 4.5 与既有记忆机制的分层（不替代）

| 层 | 容器 | 写者 | 性质 |
|---|---|---|---|
| 仓级廉史（L1/L2） | **journal**（新） | 监督循环 / 复盘 / handoff | 自动、廉价、按时间 |
| 评审过的教训（L3 精选） | EXPERIENCE.md | /review 复盘 | 有合并 / 晋升 / 90 天归档（review/SKILL.md:300-304） |
| 票级过程 | impl-log / 评审报告 | 实现对话 / 评审对话 | 头部收口可续接 |
| 工作流状态 | progress.jsonl | 监督循环 | 8 词表事件流 |
| 设计决策史 | tech-spec supersedes 链 | /tech-spec | 追溯用 |
| 现行态 | GLOSSARY / ARCHITECTURE | 文档阶段 + 契约票 | 只记现行 |

- 晋升路径：journal 的 `坑` / `解法` 行在 /review 复盘时并入 EXPERIENCE（走既有「相似条目合并、复现次数 +1」规则），journal 永不删改。
- 宿主自动记忆（如 ZCode auto-memory）是宿主私有、不随仓走——journal 是仓内真源，两者互补。

## 5. 分期

- **P0（纯文本 + 脚本，一次 sync 分发五宿主）**：journal 契约三处写点（supervision-loop.md / review Step 5 / dev-artifact-paths 树形图）+ `progress.py recall` + `session-entry.md` fragment + 三处注入面改写。顺手修正 dev-artifact-paths.md:25/:38 的 `progress.md` 残留（本次未提交改动把监督循环切到了 progress.jsonl:39，此文件漏改）。
- **P1**：三宿主 SessionStart 注入 + session-title 绑定补 `last-seen` 时间戳 + ARCHITECTURE / quarantine 进索引 + handoff `--save`。
- **P2（可选，按触发条件）**：journal 累计 >500 行 → sqlite3 FTS5 派生索引（stdlib、可从真源重建，journal/JSONL 仍是唯一真源）；跨仓 recall 聚合（progress.projects.yml 已多仓注册）。
- **明确不做**：PostToolUse 全捕获、向量检索、常驻服务、云同步。

## 6. 验收指标（借 claude-mem 同款口径）

- **冷启动恢复 token**：pandu 仓 A/B——现状路径（GLOSSARY 全文 + 票 + impl-log + replay）vs recall 路径（索引 + 按需 ≤3 条详情）实测对比。
- **相关 token 占比 >80%**；50 条索引下钻详情 ≤3 条。
- **长期不退化**：项目六个月后 `recall --since 7d` 输出仍 ≤1k tokens（月度滚动 + 窗口保证）。

## 7. 参考

- thedotmack/claude-mem：`docs/public/progressive-disclosure.mdx`（4 层设计与实测数字）、`hooks-architecture.mdx`（生命周期捕获、注入预算、Codex post-compact）、`context-engineering.mdx`（源自 Anthropic "Effective context engineering for AI agents", 2025-09：最小高信号 token 集、Just-In-Time 检索、结构化笔记、子代理隔离）。
- 本仓依据：supervision-loop.md 状态与事件写入契约 / 恢复条；dispatch-protocol.md 写查分离；review/SKILL.md Step 4-5 经验分级沉淀；token 成本分析 2026-10（渐进式披露遗留项）。
