---
doc_type: change-request
feature_module: 开发工具
feature_slug: 历史候选兼容
feature_summary: 历史候选兼容
version: 2026-11-20
upstream:
  prd:
baseline_ac:
  retain: []
  replace: {}
  retire: []
---

# Change Request

## 基本信息
- **对应 feature-slug**：历史候选兼容
- **变更名称**：修复官方候选复跑对历史冻结上下文及配置的兼容性
- **变更类型**：流程修补；用户授权为御风第三批收口实际维护官方开发工具最小兼容修复。

## 改动说明
### 本次改动范围
仅需求候选复跑消费严格 v3/v4 历史上下文；增加显式归档并认证历史配置原始字节的命令。

### 本次明确不改
load_context/create/delivery 继续只允许 v4；不重写历史对象、不升级digest、不修改业务源码或配置、不广播同步其他宿主。

### 功能变化
候选复跑对 v3 使用原始历史schema完整校验。当前配置与冻结摘要不一致时，只允许使用本需求目录内精确内容寻址的原配置快照；缺失、篡改或非法快照均拒绝。

### 代码改动清单（文档树）
```text
skills/shared/
  scripts/ticket_context.py [修改] 增加候选专用严格读取及archive-config CLI
  scripts/verify_issue.py [修改] 仅候选入口消费历史上下文和认证配置快照，日志写来源与摘要
  references/ticket-context-v3.schema.json [新增] 原样保存历史严格schema
  scripts/tests/test_ticket_context.py [修改] 严格读取与归档命令回归
  scripts/tests/test_verify_issue.py [修改] 真实run_candidate混合版本、历史配置与拒绝路径回归
```

### Delta 验收场景
- AC-D01：Given 原始 v3 与当前 v4 冻结上下文，When 真实 run_candidate 执行，Then 两版本均完成冻结 DoD+fast；原上下文字节、对象和digest均不变；默认 load_context 仍拒绝 v3。
- AC-D02：Given 当前配置摘要漂移且存在来源明确、摘要等于冻结值的历史配置，When 显式 archive-config 后执行真实 run_candidate，Then 执行冻结配置内的fast，日志包含实际配置来源路径和SHA256，所有上下文字节不变。
- AC-D03：Given 未知版本/字段、篡改 architecture_updates、DoD、digest、配置摘要、缺失或非法快照，When 读取或复跑，Then 全部拒绝；不回退到当前不同配置、不迁移上下文、不覆盖冲突归档。

### 异常与边界
重复JSON键、非规范路径、顺序DoD和digest防篡改检查沿用；archive-config需显式expected-sha，核验原始字节与配置合法性，同内容幂等，冲突拒绝。

### 影响面分析
- **前端页面 / 组件**：无。
- **后端服务 / 接口 / 配置**：官方开发工具候选读取入口和显式归档命令。
- **数据 / 埋点 / 指标口径**：冻结上下文及配置SHA256口径不变。
- **通知 / 消息 / 外部依赖**：无新增依赖。
- **协作角色影响**：主会话可恢复历史候选复跑；实现及交付默认严格门不变。

### 术语与口径对齐
- **本文使用的标准术语**：冻结上下文、需求候选、内容寻址配置快照。
- **口径是否变化**：否。
- **架构契约是否变化**：否；既有工具职责内新增候选兼容读取，无层、组、规则变化。

### 风险与兼容性
- **兼容性风险**：候选兼容读取错误扩散到默认加载器；用独立入口与负向测试约束。
- **历史数据 / 存量数据风险**：不得删除历史architecture_updates参与digest的材料。
- **灰度 / 回滚建议**：仅精确安装本次必要文件；可回退工具源码，不改历史数据。
- **需要额外验证的点**：真实run_candidate执行而非只测私有helper；归档路径和摘要均不得绕过冻结约束。
