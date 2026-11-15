# Issue / Ticket Investigation

## 内容

本地 `issues/`、`change-request`、feature 进度与 human-decision；外部 Linear/Jira/GitHub Issues/GitLab 等。重点找业务问题、客户/合规要求、范围变化、父子票、评论和关闭原因。

## 查询

1. 先打开 commit/spec/PR 明确引用的 ticket，读正文、评论、状态历史和关联票。
2. 本地按 feature-module/slug、issue ID、稳定 AC、architecture ID、关键符号和业务词搜索；外部工具用多种措辞全文搜索。
3. 从子票向父票/项目查 forcing function，从父票向兄弟票查共同约束和重复/替代关系。
4. 读取关联 PRD/spec/决策包时，本 investigator 只记录文档线索；长文档正文由文档 investigator 深读，避免跨源重复。
5. 检查 labels、milestone、状态变化、延期/取消原因、关闭为重复后的 canonical ticket。

## 证据与陷阱

“客户 X 因审计需要……”或评论中的明确取舍是 Direct；模板化的“提升体验”不是可靠动机。旧票可能反映未实施的计划，需与代码落地日期和其他来源交叉核对。范围重开、重复票和私有不可读内容必须披露。

## 返回

ticket ID/标题、动机原文、精确位置/链接、作者和日期、父子/项目/标签、状态历史、范围是否漂移，以及空查询和不可访问项。
