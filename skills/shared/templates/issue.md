---
feature-slug: <feature-slug>
issue-id: issue-001
title: <一句话标题，动词开头>
type: feature         # feature / bug；bug 票须做回归有效性验证（revert 修复见红），规则见 /implement Step 5
status: proposed        # proposed / confirmed / in-progress / in-review / mr-submitted / needs-human / done
blocked-by: []          # 例如 [issue-001]；frontier 票此处为空
verify-tier: scoped     # 交付验证档位：light / scoped / full；由 /issue-split 按 verify-policy 计算并过门②确认，
                        # 实现期只升不降（升档规则见 agents-config.verify-policy）；集成票（无后继票）一律 full
tier-reason: default    # 定档原因：命中的触发器名 / 阈值项 / integration / default；升档时更新
declared-scope: []      # 声明将触碰的生产代码路径 / glob 清单；实测漂移按 verify-policy 漂移规则处理
verify-fails: 0         # 主会话收口打回计数（验收复跑失败 / 需求符合性核对未过，见「监督循环」收口）；达 2 票置 needs-human
tracker-iid:            # tracker: gitlab 时回写 GitLab issue IID
tracker-url:
created: YYYY-MM-DD
---

# <title>

## 要做什么（What to build）

纵向切片描述：这张票打通哪些层（数据 / 逻辑 / 界面 / 测试），完成后能演示什么。不写实现细节。

## 验收标准（DoD，可自动判定）

- [ ] `<验收命令>` → 期望输出 `<关键输出 / 退出码 0>`
- [ ] `<验收命令>` → 期望输出 `<...>`

（每条必须是可直接执行的命令 + 可判定的期望，禁止「功能正常」「体验符合预期」类表述。）

## 人工确认项（不阻塞交付）

- 性能基准 / 展示样式等由人验收确认的项（`verify-policy.gate-scope.non-gating`）；可附采集命令，实现方在最终 commit 执行一次并落盘原始输出，不设 AI 通过门。无则写「无」

## 不做什么

本票明确不覆盖的部分（防止 scope creep，评审 Spec 轴据此判越界）。

## 备注

上游：tech-spec `<文件名>`，相关节 `<#节标题，可多个>`；PRD `<文件名>`，相关节 `<#节标题>`。
