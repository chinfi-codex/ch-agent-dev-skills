---

> 写作成本：保留本模板全部必需内容；事实用稳定 ID 与来源引用承接，不复述上游整段正文。完整行为、边界与断言不得省略。修订只编辑变化处，独立判断不可复用他人结论。
feature-slug: <feature-slug>
campaign-id: <campaign-id>
issue-id: issue-001
title: <一句话标题，动词开头>
type: feature         # feature / bug；bug 票须做回归有效性验证（revert 修复见红），规则见 /implement Step 5
status: proposed        # proposed / confirmed / in-progress / in-review / mr-submitted / merged / needs-human / done / deferred / cancelled
blocked-by: []          # 例如 [issue-001]；merged（已合入集成分支）或 done（feature 已验收）解锁下游
target-branch:          # 可空：由 delivery start 通过 ticket_context 解析并冻结；local-human 不得指向 main
declared-scope: []      # 预计触碰的所有代码 / 测试 / 配置路径；本票修改架构总览或规则时含 docs/ARCH-CHECK.json 与 docs/ARCHITECTURE.md；开工后不可自行扩大
verify-fails: 0         # 票级门禁 / feature 候选门打回计数；达 2 创建 human-decision
dispatch:               # 派发归属，主会话派发时写入（见 Progress Events 契约）；未派发票留空
  host:                 # 执行宿主
  model:                # 实际执行模型
  dispatched-at:        # 最近一次新会话派发时间；resume 不更新
  attempts: 0           # 新会话派发次数；resume 不增，redispatch +1
  dispatch_id:          # 每次新会话派发的稳定 ID
  session_id:           # 宿主返回的真实实现 / 评审会话 ID
  parent_session_id:    # 发起派发的主会话 ID
tracker-iid:            # tracker: gitlab 时回写 GitLab issue IID
tracker-url:
created: YYYY-MM-DD
---

# <title>

## 要做什么（What to build）

纵向切片描述：这张票打通哪些层（数据 / 逻辑 / 界面 / 测试），完成后能演示什么。不写实现细节。

## 验收标准（DoD，可自动判定）

- [ ] `<验收命令>` → 退出码 `<整数>`
- [ ] `<验收命令>` → 退出码 `<整数>`；期望输出 `<关键输出子串>`

（每条必须是可直接执行的命令 + 明确的期望退出码；期望输出可选，省略时不做输出断言，禁止「功能正常」「体验符合预期」类表述。实现后的 DoD 不要求架构巡检；完整 arch-check 在 feature candidate 的 `freeze / check` 统一执行。）

## 人工确认项（不阻塞交付）

- 性能基准 / 展示样式等由人验收确认的项（`verify-policy.gate-scope.non-gating`）；可附采集命令，实现方在最终 commit 执行一次并落盘原始输出，不设 AI 通过门。无则写「无」

## 不做什么

本票明确不覆盖的部分（防止 scope creep，评审 Spec 轴据此判越界）。

## 方案切片

来源：`<tech-spec 文件名>` §3.1
来源 SHA256：`<spec 文件 SHA256>`
切片格式：1

本节由 `spec_tree.py slice` 从 tech-spec 3.1 文档树确定性生成（本票 `declared-scope` 命中的树行，仓库相对路径 + 动作 + 计划变动逻辑原文）；`declared-scope` 变更后必须重新生成，不得手改。本节是施工输入，不等同于本票全部行为——与邻票共享的文件必须在「要做什么 / 不做什么」写清行为分工。

```text
<由脚本写入，例如：services/order/order_service.py [修改] close_order() 增加幂等键校验，回调重试不再重复关单>
```

## 备注

上游：tech-spec `<文件名>`，相关节 `<#节标题，可多个>`；PRD `<文件名>`，相关节 `<#节标题>`。
