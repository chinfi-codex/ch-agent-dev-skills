# Error / Exception Tracking Investigation

适用于 Sentry、Rollbar、Bugsnag 等。先定位 organization/project，再按异常类、目标函数、错误原文、路径和 feature 搜索，只读。

## 查询

1. 搜索与目标防御/修复对应的 issue，读取代表 event 的完整 stack、breadcrumbs 和 tags。
2. 按 release、environment、时间窗、trace ID 缩小；检查 first seen、last seen、事件频率和影响版本。
3. 对照代码落地/发布时间：异常是否始于某版本、是否随目标变更停止，是否仍有后续回归。
4. 必要时读取作者评论、resolution note 和关联 incident；其他来源线索只记 Additional Leads。
5. AI 根因分析只能作为假设，真实 event/stack 才是主证据。

## 返回

issue ID/标题/URL、project/org、first/last seen、事件数与采样信息、affected releases、与目标相关的 stack 原文、时间相关性、解决备注和未覆盖窗口。

时间相关本身不是因果；stack 不经过目标或 release 对不上时不得强行关联。
