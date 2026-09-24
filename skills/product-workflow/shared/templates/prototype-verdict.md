# 原型 Verdict：<问题一句话>

| 项 | 内容 |
|---|---|
| feature-slug | `<feature-slug>` |
| feature-summary | `<feature-summary>`（关联的需求级文档摘要名） |
| 版本 | YYYY-MM-DD |
| 触发来源 | 哪个 skill 的哪一轮拷打 / 哪份文档的哪一节 |
| 原型类型 | `LOGIC` / `UI` |
| 原型路径 | `./dev/features/<feature-module>/<feature-slug>/prototypes/<短名>/` |

## 1. 要回答的问题

逐字写下原型回答的唯一问题（与原型顶部的问题一致）。

## 2. 场景 / 变体清单

- LOGIC：walkthrough 场景列表（快乐路径 / 尴尬边界 / 应非法操作）及各自的实际表现
- UI：变体清单（各变体的结构性差异）及用户对每个变体的反应

## 3. 裁决

- 用户的一句话结论
- 「等等，这不应该可能」类发现逐条列出（每条：操作路径 → 实际表现 → 应然）

## 4. 决策性片段（可选）

比散文更精确编码决策的片段（状态机 / reducer / schema / type shape），裁剪到决策相关部分。每片标注：来源原型文件、对应裁决条目。供 tech-spec「3.5 决策性片段」收录；无则本节留空。

## 5. 回写建议

该裁决应收进哪份上游文档的哪一节（feature brief / PRD / tech-spec 待确认项），以及回收形式（文字结论 / 决策性片段）。
