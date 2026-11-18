---
doc_type: change-request
feature_module: <feature-module>
feature_slug: <feature-slug>
feature_summary: <feature-summary>
version: YYYY-MM-DD
upstream:
  prd: <baseline PRD 文件名；无则留空>
baseline_ac:
  retain: []             # 本轮仍受影响、必须在 candidate 重验的 baseline AC
  replace: {}            # 旧 AC -> 替代它的 AC-Dxx；旧 AC 不再单独验收
  retire: []             # 明确退出本轮及现行验收的 baseline AC
---

# Change Request 输出模板

> 本文件是 `/issue` 的输出格式模板，由 agent 在产出文档前读取。
> 修改本文件即时生效，无需重新生成 SKILL.md。
> 产出时必须严格遵循章节结构，不自行增删一级章节。

最终必须输出一份 **Change Request / 需求变更单**。

# Change Request

## 基本信息
- **对应 feature-slug**：
- **变更名称**：
- **变更类型**：规则调整 / 交互优化 / 文案更新 / 入口调整 / 阈值调整 / 流程修补 / 其他

## 改动说明
### 本次改动范围
- …

### 本次明确不改
- …

### 功能变化
- …

### 代码改动清单（文档树）

> 以文档树列出本次改动对应的全部代码模块与文件，逐文件标注动作（`[新增]` / `[修改]` / `[删除]`）和一句话计划变动逻辑（改哪些符号、什么行为变化）。与「本次改动范围」「本次明确不改」保持一致：范围外文件不得入树；被替代的旧实现及其失效引用以 `[删除]` 入树，不留兼容残留；配置 / 规则 / 派生物变更（如 `docs/ARCH-CHECK.json`）同样入树。纯文档 / 外部配置变更没有代码文件时，明确写「无代码改动」及原因，不得留空。

```text
<repo>/
  services/order/                      # 订单模块：本次改动主场
    order_service.py      [修改] close_order() 增加幂等键校验，回调重试不再重复关单
    idempotency.py        [新增] 幂等键生成与校验工具
  tests/order/
    test_order_close.py   [修改] 新增幂等重试场景 2 条
```

### Delta 验收场景

> 每个 delta 使用 feature 内唯一且不复用的 `AC-D<序号>`。acceptance plan 必须覆盖全部 delta 与 retain baseline；replace / retire 的旧 AC 不得混入。
- AC-D01：Given <前置状态>，When <动作>，Then <量化预期>

### 异常与边界
- …

### 影响面分析
- **前端页面 / 组件**：
- **后端服务 / 接口 / 配置**：
- **数据 / 埋点 / 指标口径**：
- **通知 / 消息 / 外部依赖**：
- **协作角色影响**：

### 术语与口径对齐
- **本文使用的标准术语**（须与 GLOSSARY 一致）：
- **口径是否变化**：否 / 是（若是，须已同步更新 GLOSSARY 并说明新旧口径差异）
- **架构规则是否变化**：否 / 是（若是，走 tech-spec 小变更路径时须在 3.2 节列出层 / 组 / 规则变更并指定承接票；承接票只编辑 `docs/ARCH-CHECK.json` 并重新 `render write`，变更单不直接改规则）

### 风险与兼容性
- **兼容性风险**：
- **历史数据 / 存量数据风险**：
- **灰度 / 回滚建议**：
- **需要额外验证的点**：
