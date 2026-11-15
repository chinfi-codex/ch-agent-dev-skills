# Source Control Archaeology

## 内容

Git 提交、作者/日期、补丁、代码注释、测试、ADR、CHANGELOG、共同变更文件；真实存在时的 PR body/review。本地无 PR 不妨碍调查，Git 和仓内记录始终是主来源。

## 查询

围绕共同 anchor 扩展，而不是只读最近提交：

```bash
git log --follow --oneline -- <file>
git log -S '<exact_string>' -- <file>
git log -G '<regex>' -- <file>
git blame -L <start>,<end> <file>
git show <commit>
git log <old>..<new> -p -- <file>
```

- 搜索 `TODO/FIXME/HACK/NOTE`、deprecated 注释、相关测试和 architecture ID。
- 查看同一提交修改的其他文件，识别事故修复、迁移和共同约束。
- 提交含真实 PR 编号且 `gh` 可用时，读完整 body/comments/reviews；无 PR 时记录“本地 Git 路径，无外部 PR”，不是覆盖失败。
- 最近 revert/re-apply、fixup、expand/contract 提交要连成序列读。

## 证据与陷阱

提交信息、作者评论和明确代码注释可成为 Direct；测试和共同变更通常只是间接证据。最新提交不一定解释最初动机，代码“看起来合理”不等于历史理由。保留 revert、意见冲突和与现行行为不一致的旧文档。

## 返回

每个关键节点给完整 SHA、作者/日期、原文、补丁位置、关联 ticket/spec/PR、实际读取的相关文件和未追到的历史线索。
