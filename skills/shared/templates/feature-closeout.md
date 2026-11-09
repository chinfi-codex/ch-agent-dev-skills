# 需求收口产物模板（feature-closeout: local-human）

全部产物写**主工作区** `./dev/features/<feature-module>/<feature-slug>/integration/`，不写集成 worktree、不提交到集成分支。`candidate.json` 由 `feature_closeout.py freeze` 生成，其余由主会话按下列结构填写；`feature_closeout.py check` 只读这些文件判门。

## 1. evidence.md（自动证据）

````markdown
# 需求收口自动证据：<feature-slug>

```yaml
candidate_tree: <candidate.json 的 candidate_tree>
all-passed: true            # 仅指实际执行项；有清单外失败未归因 = false
ran:
  direct: 0                 # 需求 diff 直接测试 + 新增测试（全跑、不受预算）
  indirect: 0               # 反向闭包 + scoped-map 映射中实际执行的
  quarantined: 0
  skipped-env: 0
  deferred: 0               # 预算截断 / 闭包不可枚举，由 main push CI full 兜底
deferred:
  - <测试或数据路径>：<原因>
scoped-report: integration/raw/scoped.json
full-deferred: ci           # 本机不跑 full，不得写 tier-executed: full
```

## 结论
一句话：选择集多大、跑了什么、失败如何归因（指针指向 verify-log.md）。
````

## 2. business-results.json（每条 AC 一条）

```json
{
  "schema": "agent-dev.feature-business-results/v1",
  "candidate_tree": "<tree>",
  "items": [
    {
      "id": "AC-1", "title": "<行为点>", "oracle": "auto",
      "status": "pass",
      "command": "<实际执行命令>", "exit": 0,
      "assertions": "<关键业务断言及结果>",
      "evidence_level": "synthetic",
      "artifacts": [{"path": "integration/artifacts/x.json", "sha256": "<hex>"}]
    },
    {
      "id": "AC-2", "title": "<行为点>", "oracle": "auto+human-review",
      "status": "pass", "command": "<…>",
      "steps": "<人怎么看>", "expect": "<期望>",
      "human": {"status": "confirmed", "by": "<人>", "at": "<时间>", "note": ""}
    },
    {
      "id": "AC-3", "title": "<行为点>", "oracle": "human",
      "steps": "<测试实例上的操作步骤>", "expect": "<期望>",
      "human": {"status": "pending"}
    },
    {
      "id": "AC-4", "title": "<真库行为点>", "oracle": "auto",
      "status": "unverified", "reason": "真库无授权",
      "human": {"status": "waived", "by": "<人>", "note": "合 main 后另行安排"}
    }
  ]
}
```

字段规则（`check` 机器把关）：

- `oracle`：`auto` / `auto+human-review` / `human`，取自 tech-spec §5.1；
- 自动部分 `status`：`pass`（必须带 `command`）/ `fail` / `unverified`（必须带 `reason`，合并前只能由人 `waived`，不能记 `confirmed` 冒充通过）；
- `human.status`：`pending` / `confirmed` / `waived`（必须带 `note`）/ `fail`；`confirmed` / `waived` 必须带 `by`；
- `evidence_level`：`synthetic | frozen-host | test-db | production`；截图必须配行为断言，单独截图不构成通过；
- 已在关联选择集中执行的业务测试直接采集产物，不为出证据重复执行。

## 3. human-checklist.md（一页纸，给人看）

由 `feature_closeout.py summary` 生成骨架，主会话补全测试环境与预截图：结果先行一句话 → 候选（分支 @ commit，tree）→ 测试环境（实例地址或启动命令）→ 人工清单（逐条：步骤 → 期望 → 预截图路径）→ 需要你判断的风险（未验证 AC / 延期兜底 / 遗留 Medium-Low / 超期 quarantine）→ 想细看 → 「确认后我将：合 main（--no-ff）+ 发布 Prod 并冒烟；push 另等指示」。说人话，不甩字段名。

## 4. confirmation.md（人显式确认）

````markdown
# 需求收口人工确认：<feature-slug>

```yaml
candidate_tree: <tree>
confirmed-by: <人>
confirmed-at: YYYY-MM-DD HH:MM
quote: "<人在对话里的原话>"
conditions: []        # 附条件确认时逐条列出，落地后再合并
scope: [merge-main, deploy-prod]   # 本次确认覆盖的动作；push 不在默认范围
```
````

沉默、超时、「看着行」之类非确认语不算确认；确认后候选 tree 变化须重新确认。

## 5. verify-log.md（候选变更与回修轨迹）

| 时间 | 候选 tree | 触发 | 跑了什么 | 结果 | 下一步 |
|---|---|---|---|---|---|

## 6. conformance-feature.md

复用 `shared/templates/conformance.md`，`issue: feature`：对 tech-spec 全节枚举需求点，对照各票覆盖矩阵并集，抓票间接缝缺口。
