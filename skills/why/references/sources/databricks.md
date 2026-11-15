# Product Analytics Warehouse Investigation

适用于 Databricks、Snowflake、BigQuery、ClickHouse、Redshift 等只读 SQL/MCP。回答使用量、实验、迁移和数值阈值依据，不与基础设施遥测混为一类。

## 查询纪律

1. 先 `SHOW TABLES` / information schema / `DESCRIBE` 确认真实表、字段、事件和时间列；不得凭示例猜 schema。
2. 所有查询带明确时间窗和必要 partition/filter，通常覆盖目标变更前后约 30 天；大范围扫描必须有理由。
3. 优先去重、类型化的分析模型；使用原始事件时处理重复、refresh lag 和属性演进。
4. 查询返回 statement/job ID 时按工具协议读取结果，不重复发起相同重型查询。只允许只读 SQL。

## 有效调查模式

- **使用轨迹**：按日统计 feature/event，观察上线、衰减或删除，但排除新埋点造成的假阶跃。
- **阈值来源**：变更前相关字段的 count/median/p99/max 与代码常量对照；吻合是间接证据，不是作者动机 Direct。
- **实验/feature flag**：确认 exposure/outcome 表，按 flag key 和 variant 查曝光、结论和时间。
- **迁移/回填/性能**：查 query history 的 statement、duration/read bytes、失败和时间，或 dbt lineage 的上下游变化。
- **成本/用量决策**：使用量、计费或审计表要有完整表名、过滤和数值摘要。

## 陷阱

埋点存在不等于代码因其产生；使用量变化可能来自埋点变更、schema drift、模型刷新延迟或 retention cliff。查询工具看不到 notebook/离线分析时，将潜在理由记 gap。未确认存在的表不得出现在证据里。

## 返回

完整表名、实际 SQL、时间窗、计数/分位/first/last-seen 等紧凑数值、与代码日期的关系、数据质量限制、替代解释和 direct/circumstantial/weak 强度。不要倾倒原始用户数据。
