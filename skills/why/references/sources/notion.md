# Long-form Document Investigation

## 内容

本地 PRD、tech-spec、feature brief、pd/dev review、prototype verdict、ADR、runbook/postmortem；外部 Notion、Confluence、Lark Doc 等。重点查问题陈述、约束、替代方案、否决理由、共识和后果。

## 查询

1. 用 feature 名、关键符号、ARCH-CHECK 组 / 规则 ID、作者、错误文本、业务词和落地时间做多轮搜索。
2. 先读 commit/ticket 明确链接的文档，再查同 feature 目录、supersedes 链、相关 review 和 prototype verdict。
3. 对候选页面/文件读全文，不用预览代替正文；记录节标题和版本/日期。
4. 沿同一文档来源的父页、子页、backlink 和会议记录查找，不跨到聊天或 ticket 深查，只记 Additional Leads。
5. 多份草稿确认哪份定稿、哪份被取代；日期新不等于内容已生效。

## 证据与陷阱

非模板化的 Alternatives/Rejected/Decision/Consequences 是强证据。文档可能描述计划而非现实；与代码或其他来源不一致时并列披露。样板化 Why、权限受限和未链接文档都属于覆盖问题。

## 返回

标题、路径/URL、节、作者/更新日期、定稿/草稿/被取代状态、动机和取舍原文、相关链接，以及文档与现实漂移。
