# Incident / Postmortem Addendum

目标含 null guard、retry、timeout、rate limit、circuit breaker、feature flag、egress/OOM guard 等防御形态时，在自己的来源内追加 incident 调查：

- Ticket：搜索 `incident`、`sev-*`、`postmortem-action-item`、`reliability` 及对应错误。
- Chat：搜索 incident/sev 频道、目标添加前后的错误原文和应急讨论。
- Git：搜索 `fix for incident`、`defensive`、`revert`、`re-apply`、hotfix 序列和相关测试。
- Observability/Error：查事故时间线、首次/最后出现、告警与目标代码发布的关系。
- Documents：查 postmortem、runbook、action item 是否明确指向该目标。

事故与代码时间相邻只是间接证据；incident 文档明确说“因此增加 X”才可成为 Direct。没有找到事故不等于该防御没有事故动机。
