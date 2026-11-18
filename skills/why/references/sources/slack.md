# Real-time Team Chat Investigation

适用于 Lark、Slack、Discord、Teams 等真实可用 MCP。先检查工具 schema 和认证，不执行发帖、 reactions、删除等写操作。

## 查询

1. 按目标作者和代码落地前后时间窗搜索。
2. 搜索真实 PR/ticket/spec URL、issue ID、ARCH-CHECK 组 / 规则 ID、关键符号和业务词。
3. 防御性目标搜索异常类、错误原文、timeout/retry/rate-limit 等关键词和 incident 频道。
4. 命中单条消息后读取完整 thread 和必要上下文；频道决策还要查是否有后续修正或正式文档链接。
5. 记录频道、日期范围、查询、保留期和未搜索的私域范围。

## 证据与陷阱

作者在完整讨论中的明确决策可作为 Direct；玩笑、表情、单句“先这么做”通常不是。旧消息可能受保留期影响，DM/私有频道可能不可搜索，认证失败是 gap。孤立的单条消息容易 context collapse，不得脱离 thread 引用。

## 返回

频道/thread/permalink、参与者、日期、原文、上下文、后续修正、搜索范围和不可访问范围。其他来源链接放 Additional Leads。
