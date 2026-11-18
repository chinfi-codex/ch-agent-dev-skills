# Feature 符合性核对：<feature-slug>（仅候选级）

> 只在 feature candidate 冻结后执行一次（整需求评审与候选复跑之后、报告与合并之前）。判「要的有没有、偏没偏」——与 `/ai-review` 的代码质量评审正交。只判不改；deviate / missing 走修复票并重新 freeze。票级不再生成 conformance。

```yaml
issue: feature
candidate: <candidate.json 的 candidate 完整 commit>
candidate_tree: <candidate.json 的 candidate_tree>
verdict: conform        # conform / deviate / missing
checker: main-session   # main-session / dispatched
checker_session: <实际宿主会话或 agent ID；不能自报为独立会话>
date: YYYY-MM-DD
coverage:
  issues: [issue-001, issue-002]       # 与 candidate 冻结票组精确一致
  specs:
    - {path: dev/features/<module>/<slug>/<spec>.md, sha256: <当前 spec SHA256>}
```

## 需求点清单（反向枚举）

**先不看各票 impl-log 覆盖矩阵**，从 PRD / change-request 的全部端到端 AC、tech-spec 全节与票「要做什么 / 不做什么」独立枚举需求点，再打开代码逐条判定——防止被实现方叙事带偏。跨票接缝、共享状态、接口顺序和失败路径都在核对范围；`architecture-updates` 契约逐稳定 ID 核对 tech-spec 3.2、票声明、`docs/architecture/*.json` 分片与真实代码四者一致。

| # | 需求点 | 来源（PRD AC / spec 节锚点） | 票 | 判定（已实现 / 语义偏差 / 未实现） | 核对依据（candidate 代码:符号、测试、verification-supervisor.json#DoD） |
|---|---|---|---|---|---|

## 偏差与缺口详情

verdict ≠ conform 时逐条写：需求要什么、实现做了什么、偏在哪 / 缺什么；每条标打回原因分类（未实现 / 语义偏差 / DoD 本身窄），并开修复票。conform 时写「无」。

## 抽查记录

实际打开核对过的 candidate 代码位置与结论——至少覆盖全部 ★ 关键路径与跨票接缝；只照抄票级矩阵不打开代码，等于没核。

## 与票级矩阵的关系

票级 `impl-log` 覆盖矩阵是线索，不是结论。本表覆盖全部票与 tech-spec 全节，`coverage.issues` 必须精确等于 candidate 票组，`coverage.specs` 必须精确覆盖本轮全部 tech-spec 及当前 SHA256；只有 YAML 头、漏票 / 漏 spec、空表、未知票或不存在的代码 / 测试 / verification fragment 均不得通过。
