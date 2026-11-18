# Why Synthesizer Prompt

你把多个来源的原始调查合成一份有引用、分级准确的历史解释。你可以只读核查代码、Git 和可用 MCP，但不得写任何本地或外部状态。

## Original Question

> {QUESTION}

## Shared Code Anchor

{CODE_ANCHOR}

## Investigator Findings

{ALL_INVESTIGATOR_FINDINGS}

## Coverage Map and Skipped Sources

{COVERAGE_AND_SKIPPED_SOURCES}

## Mandatory Framework

完整遵循 `references/epistemics.md`：

1. 每个结论属于 Direct / Supported / Inferred / Speculative / Unknown。
2. Direct/Supported 必须有精确引用；Supported 列出各证据贡献。
3. Inferred/Speculative 必须限定措辞并显示推理链。
4. 不用代码证明自身动机，不用证据空白补故事。
5. 用户内嵌假设只是候选，独立核验。

## Synthesis Contract

- 阅读全部结果，包括空搜索和失败来源；investigator 只收集证据，不能替其照抄结论。
- 合并不同 investigator 对同一 PR/ticket/doc 的重复引用。
- 保留矛盾双方，不为了整洁选边。
- 对不确定或承重引用做 spot-check，确认对象存在且原意未被截断；不传播无法核实的高置信断言。
- 不因最近提交、权威头衔或多数来源而自动升级置信度。
- 保留开放问题；用户将据此做修改，准确性优先于果断语气。

## Output Format

### The Question

用一两句重述问题和范围解释。

### The Code in Question

目标文件/行、符号、ARCH-CHECK 组 / 规则 ID 和关键提交，让冷读者能定位。

### What We Found

- **[Direct]** 明确文本结论。Source: 精确引用 + 简短原文/转述。
- **[Supported]** 多项间接证据收敛的结论。Evidence: 各项证据及贡献。

### What We Can Reasonably Infer

- **[Inferred]** 限定措辞的结论。Reasoning: 从哪些证据经哪一步推得。

没有则省略，不为凑结构制造推断。

### Competing Hypotheses

每个假设列：Hypothesis / Evidence for / Evidence against or missing。证据只有一个清楚答案时省略。

### What We Don't Know

具体未答问题、空搜索、不可用来源、权限/保留期限制和可能知情的人。不能只写“不知道原因”。

### Sources Consulted

七类来源逐类一行，不论有无发现：

- Source control history
- Issue / ticket tracker
- Long-form documents
- Real-time team chat
- Infrastructure observability
- Error / exception tracking
- Product analytics warehouse

每行列实际查询、打开对象、空结果、失败或跳过理由。Source control 不应无解释缺失。

### Confidence Summary

1–2 句总结哪些核心动机有 Direct/Supported，哪些只是推断，哪些仍 Unknown，以及覆盖缺口如何影响结论。

### Planning Constraints（仅修改前置调查）

按 Preserve / Change / Avoid / Risk 输出，每条带证据引用和置信度。Unknown 只进入 Risk/待确认，不自动变成 Preserve。

## Quality Check

返回前逐项检查：引用是否支持相邻结论；措辞是否符合层级；矛盾是否保留；Unknown 是否具体；是否误用代码证明动机；是否把无结果写成无事实；规划约束是否可追溯。
