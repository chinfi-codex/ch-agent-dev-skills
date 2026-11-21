# Why Source Playbooks

按当前未决问题读取相关来源的 playbook；同一调查者可沿关联线索跨来源追踪。示例工具名不是强制接口；先检查真实 MCP schema，适配同类别来源。

| Category | Playbook | Typical sources |
| --- | --- | --- |
| Source control history | `sources/code-archaeology.md` | Git、真实存在的 PR/review、仓内 ADR/测试 |
| Issue / ticket tracker | `sources/linear.md` | 本地 issue/change-request/decision、Linear、Jira、GitLab |
| Long-form documents | `sources/notion.md` | 本地 PRD/spec/review/verdict、Notion、Confluence、Lark Doc |
| Real-time team chat | `sources/slack.md` | Lark、Slack、Discord、Teams |
| Infrastructure observability | `sources/datadog.md` | Datadog、Grafana、New Relic、Splunk |
| Error / exception tracking | `sources/sentry.md` | Sentry、Rollbar、Bugsnag |
| Product analytics warehouse | `sources/databricks.md` | Databricks、Snowflake、BigQuery、ClickHouse |

有事故线索或风险需要时读取 `sources/incident-postmortem.md`，按分配的范围追查相关记录。
