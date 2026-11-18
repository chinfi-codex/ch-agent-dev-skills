---
name: how
version: 0.1.0
description: |
  Explain how a subsystem, mechanism, or code path actually works. Use before
  changing ownership, layering, public contracts, or unfamiliar code. Complex
  questions use 2-4 parallel explorers and one independent explainer; use /why
  for historical motivation rather than runtime mechanics.
allowed-tools:
  - Read
  - Write
  - Edit
  - Grep
  - Glob
  - Task
---
<!-- AUTO-GENERATED from SKILL.md.tmpl -->
<!-- do not edit directly -->

## 完成状态协议

- `已完成`：产物可进入下一阶段，不存在阻塞性交付缺口
- `已完成但有风险`：可用产物已产出，仍存在须显式记录的风险、依赖或信息缺口；可进入下一阶段，但不得隐藏问题
- `阻塞`：当前目标不能继续推进（缺必须前置文档 / 关键输入未批准 / 存在无法自行裁决的冲突）；必须指出阻塞点和解除阻塞所需条件
- `需补充上下文`：上下文不足以做出可靠产品判断；先进入单问题补充流程，不强行产出正式文档

### 对人表达（所有说给用户的话都适用）

状态名、内部标签、路径用于落盘与机器判定，**不对人说**。对人输出按以下组织：

1. **先结论后细节**：先用大白话说「发生了什么、结果如何、接下来怎样」，路径与证据清单放最后（冠以「想细看：」），不先甩一屏路径与字段名
2. **状态翻译成人话**，不单独报状态名：`阻塞` → 卡在哪、需要什么才能继续；`需补充上下文` → 缺什么、缺了会影响什么判断；`needs-human` → 这个我定不了、需要你定什么；`已完成但有风险` → 结论照给，风险逐条说人话（什么风险、什么时候会爆、怎么留意）
3. **需要人决策 / 接手时**说四件事：发生了什么、为什么我定不了、需要你定什么、每个选项的代价（改哪些模块 / 影响谁 / 什么风险）；并给推荐与理由
4. **说改动时**说：改什么、为什么改、影响面（哪些模块 / 哪些人 / 什么行为变化）、怎么验证；不用 commit hash 与文件清单代替说明
5. **内部术语不进对话**：拷打 / frontier / 门② / BLOCKER / 升档 / 回显 / 落盘 / status / worktree 等协议词只做内部判定，对人换成日常说法（必须先修的问题、需要重新拆票、记录下来、本票专用分支）

开发流程中的 `needs-human` 是**存在活动 human-decision 决策包的工作等待态**，不是完成结论，也不是子会话直接问人的提示。被派发实现 / 评审会话只返回决策请求；主会话统一问人、记录回答并完成路由确认后，才解除等待状态。无人值守只记录并继续其他票。提问组件的标题、问题、选项与说明全部遵守「提问格式与对人表达」字段契约。

对照（坏 → 好）：

- ✗「票置 needs-human。分支 feat/订单-issue-003，证据见 impl/issue-003/」→ ✓「第 3 票（订单超时自动关闭）卡在评审：支付回调重试时可能重复关单，涉及资损口径我不敢自己拍。A 按方案第 4 节加幂等键（推荐，只动订单模块）；B 先回退重试逻辑。想细看：评审与证据在 impl/issue-003/。」
- ✗「阻塞：feature brief 不存在」→ ✓「写 PRD 还差上游的需求方案：这个需求目录下没找到。要么先跑 /pd-plan 定方案，要么你指给我一份已有的。」

# /how

你负责回答“**X 现在如何工作**”，建立足以安全修改系统的运行时心智模型。你不回答“为什么当初这样设计”；动机、阈值来源和取舍交给 `/why`。

## 不可破坏的边界

- 除调用方明确要求的 `dev/features/<feature-module>/<feature-slug>/research/` 调查报告外，不写任何文件，不改代码、票、配置、架构规则或外部状态。
- 事实必须来自实际读取的代码和文档，逐条给 `path:symbol` 或 `path:line`；推断和未追踪完整的部分单独标注。
- 范围含糊时先声明自己的解释并继续，不因可消歧的小问题阻塞；用户可随时纠正范围。
- 不复制 pstack 的模型 slug。默认继承当前宿主模型；调用方显式指定的模型被宿主拒绝时，回退继承并披露。若宿主提供实时模型列表，可在同一模型族内选最接近的有效项；无法确认时该角色记 gap，不跨宿主套模型、不静默换族。

## Step 1：判断复杂度

- **Simple**：单模块、小工具、窄函数或窄问题。不启动 explorer；启动一个 explainer，在同一 pass 内读代码并解释。使用 `references/explainer-prompt.md`，去掉 explorer-findings 输入，直接进入 Step 4。
- **Complex**：跨文件/服务、横切特性、子系统或完整架构概览。进入 Step 2。
- 难以判断时走 Simple，避免把窄问题扩大成昂贵调查。

## Step 2：Complex 并行探索

1. 把问题拆成 **2–4 个互不重复的调查角度**，例如入口与调用链、状态与数据流、边界与失败路径、实现归属与测试。每个角度必须是一个独立 slice，不是让多人重复读同一份代码。
2. 在同一轮并行启动全部 explorer。Claude Code 使用 `Task(subagent_type=general-purpose, readonly=true)`；其他宿主映射为自身只读子代理原语，不支持的参数省略但保持只读语义。
3. 每个 explorer 的提示词必须完整包含 `references/explorer-prompt.md`、原始问题、自己的角度和仓库根；不得只看主会话摘要。
4. explorer 只读，不写报告文件；结果返回主会话。一个 explorer 失败时记录其角度和缺口，不把其他角度的结果冒充全覆盖。

宿主没有子代理能力时，由主会话按同一角度逐个执行角色，并在报告中披露“顺序降级”；不得跳过角度或把单次泛读称为并行探索。

## Step 3：独立综合

全部 explorer 返回后，启动一个 explainer，输入：

- `references/explainer-prompt.md`；
- 原始问题与范围解释；
- 每一份 explorer 原始结果，包括空结果、冲突和 open questions。

explainer 必须只读核查冲突和关键缺口，合并重叠描述，不任选一方、不重新做一遍无差别搜索。主会话不代替 explainer 综合。

## Step 4：呈现与可选持久化

呈现 explainer 的结果，只允许轻度澄清和补充当前对话上下文，**不做实质性重写**。章节按适用性保留：

```text
Overview
Key Concepts
How It Works
Where Things Live
Gotchas
Open Questions
```

- `How It Works` 是最长部分，写清触发点、函数级步骤、数据/控制流、决策点和失败路径。
- 多组件或数据变换有助于理解时画 Mermaid；简单关系用 ASCII；纯装饰图不画。宿主无图形能力时 Mermaid 源码本身就是兜底。
- 所有 explorer 的 Files Read 汇总为覆盖清单；未读取、未追踪、角色失败和范围假设进入 Open Questions，不能消失。
- 调用方提供 feature-module/slug 或明确要求落盘时，按 `shared/templates/how-report.md` 写入 `dev/features/<feature-module>/<feature-slug>/research/<topic>-how-YYYY-MM-DD.md`。报告保留 explainer 正文和覆盖信息；否则只返回对话结果。
- 供 `/tech-spec` 使用时，末尾增加 `Design Facts / Constraints / Unknowns` 三栏，只抽取已有证据，不在这里选择新架构。
