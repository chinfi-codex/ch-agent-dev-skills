# 需求符合性核对：<issue-id> <title>

> 主会话在合并前执行（验收复跑之后、合并之前），判「要的有没有、偏没偏」——与 ai-review 的代码质量评审正交。只判不改；deviate / missing 走 BLOCKER 回修通道派回实现对话。

```yaml
issue: <issue-id>
context_digest: <ticket-context.gate2_context_digest>
commit: <核对时的完整 HEAD>
tree: <核对时的完整 HEAD tree>
verdict: conform        # conform / deviate / missing；仅确定性未抽中的非集成票可写 skipped
checker: main-session   # main-session / dispatched
checker_session: <实际宿主会话或 agent ID；不能自报为独立会话>
selection:
  algorithm: sha256-v1
  rate: <配置 sample-rate；always 写 1>
  selected: true        # 由 ticket_context.select_conformance 复算，不接受手工跳票
date: YYYY-MM-DD
```

## 需求点清单（反向枚举）

**先不看 impl-log 覆盖矩阵**，从票「要做什么 / 不做什么」+ tech-spec 锚点节独立枚举需求点，再对照矩阵逐条判定——防止被实现方叙事带偏；本表专门抓「缺席」（diff 与命令退出码里看不见的东西）。票的 `architecture-updates` 契约条目也在反向枚举范围：逐稳定 ID 核对 tech-spec 3.2、票声明、`docs/architecture/*.json` 分片 diff 与代码实现四者一致，且未顺手修改无关接口；生成首页必须通过与分片的确定性比对。

| # | 需求点 | 来源（DoD 条目 / 行为点 / spec 节锚点） | 判定（已实现 / 语义偏差 / 未实现） | 核对依据（文件:符号、测试、evidence 行） |
|---|---|---|---|---|

## 偏差与缺口详情

verdict ≠ conform 时逐条写：需求要什么、实现做了什么、偏在哪 / 缺什么；每条标打回原因分类（未实现 / 语义偏差 / DoD 本身窄），供复盘归因。conform 时写「无」。

## 抽查记录

实际打开核对过的代码位置与结论——至少覆盖矩阵中全部 ★ 关键路径行；只照抄矩阵不打开代码，等于没核。

## Feature 级覆盖核对（仅集成票）

对 tech-spec 全节（不只票锚点）枚举 feature 级需求点，对照本 feature 各票覆盖矩阵的并集，抓票间接缝缺口（A 票以为 B 票做的部分）。非集成票删去本节。
