---
feature-slug: <feature-slug>
issue-id: issue-001
title: <一句话标题，动词开头>
status: proposed        # proposed / confirmed / in-progress / in-review / mr-submitted / needs-human / done
blocked-by: []          # 例如 [issue-001]；frontier 票此处为空
verify-tier: scoped     # 交付验证档位：light / scoped / full；由 /issue-split 按 verify-policy 计算并过门②确认，
                        # 实现期只升不降（升档规则见 agents-config.verify-policy）；集成票（无后继票）一律 full
declared-scope: []      # 声明将触碰的生产代码路径 / glob 清单；实测漂移按 verify-policy 漂移规则处理
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

## 不做什么

本票明确不覆盖的部分（防止 scope creep，评审 Spec 轴据此判越界）。

## 备注

上游：tech-spec `<文件名>`；PRD `<文件名>`。
