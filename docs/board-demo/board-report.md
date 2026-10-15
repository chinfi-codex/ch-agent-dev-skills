# 跨项目看板（2026-10-14 16:23）

## ⚠ 告警
- [pandu] issue-003 盯盘列表页 — 验收复跑失败×1
- [pandu] issue-003 盯盘列表页 — 评审第3轮
- [pandu] issue-003 盯盘列表页 — 停滞3天
- [pandu] issue-004 告警规则引擎 — 停滞5天
- [pandu] issue-006 快照导出 — 集成票档位非 full
- [nova] issue-002 评论列表页 — 需人工裁决
- [nova] issue-002 评论列表页 — 验收复跑失败×2
- [nova] issue-002 评论列表页 — 集成票档位非 full

## pandu

### 需求看板

| 阶段 | 卡片 | 标记 |
|---|---|---|
| 技术方案 | `stock/earnings-sub` 财报订阅 | – |

### 开发看板

| 泳道（执行 agent） | 阶段 | 卡片 | 档位 | DoD | 标记 |
|---|---|---|---|---|---|
| ⚙codex·gpt-5.4-codex | 实现中 | `issue-004`〔stock/realtime-watch〕 告警规则引擎 | full | 1/3 | ⚠停滞5天 |
| ⚙codex·gpt-5.4-codex | 已完成 | `issue-002`〔stock/realtime-watch〕 推送通道 | scoped | 2/2 | – |
| ⚙zcode·glm-4.7 | 评审/合并 | `issue-003`〔stock/realtime-watch〕 盯盘列表页 | scoped | 3/3 | ⚠验收复跑失败×1；⚠评审第3轮；⚠停滞3天 |
| ⚙zcode·glm-4.7 | 已完成 | `issue-001`〔stock/realtime-watch〕 行情快照存储 | scoped | 3/3 | – |
| ⚙未派发 | 待执行 | `issue-005`〔stock/realtime-watch〕 端到端集成与回归 | full | 0/3 | blocked-by: issue-003,issue-004；集成票 |
| ⚙未派发 | 待执行 | `issue-006`〔stock/realtime-watch〕 快照导出 | light | 0/3 | 可派发；集成票；⚠集成票档位非 full |

## nova

### 需求看板

| 阶段 | 卡片 | 标记 |
|---|---|---|
| 需求澄清 | `cms/markdown-import` Markdown 导入 | 需补充上下文 |

### 开发看板

| 泳道（执行 agent） | 阶段 | 卡片 | 档位 | DoD | 标记 |
|---|---|---|---|---|---|
| ⚙claude·opus-4.1 | 评审/合并 | `issue-001`〔cms/comments〕 评论模型与接口 | scoped | 3/3 | 待合并 |
| ⚙zcode·deepseek-v4 | 阻塞 | `issue-002`〔cms/comments〕 评论列表页 | scoped | 2/3 | 集成票；⚠需人工裁决；⚠验收复跑失败×2；⚠集成票档位非 full |
| ⚙zcode·deepseek-v4 | 已完成 | `issue-003`〔cms/comments〕 表情回复 | light | 2/2 | – |
