# Infrastructure Observability Investigation

适用于 Datadog、Grafana、New Relic、Honeycomb、Splunk 等。它们记录运行时实际发生的情况，可与计划/讨论互证。

## 查询

1. 确定 owning service、upstream/downstream、环境和代码落地时间。
2. 先搜 dashboard、monitor、alert 和 SLO：记录查询、阈值、 owner、创建/修改时间。
3. 搜相关 metric 的 metadata、单位、tags 和时间序列，观察变更前后是否出现异常/恢复。
4. 日志按 service/tag/error/symbol 和窄时间窗过滤，优先 pattern/aggregate，不倾倒大量原始日志。
5. timeout、retry、慢路径查 APM span/trace；防御性目标查 incident 时间线和 postmortem。
6. 默认覆盖变更前后合理窗口；保留期短于目标时间时明确记 gap。

## 证据与陷阱

监控阈值与代码限制一致可说明团队关注同一条件，但不自动证明代码因此产生。变更前 spike、变更后稳定只是相关，不是因果；检查同窗口其他变更。指标更名、删除、采样、保留期和数据缺口都不能当成“无事故”。

## 返回

资源类型、名称/ID/URL、owner/日期、精确查询/阈值/错误原文、时间窗、聚合数值、与目标的时间关系、替代解释和证据强度。
