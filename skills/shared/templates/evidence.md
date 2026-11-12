# Evidence：<issue-id>

> 体积预算：正文 ≤ `artifact-budget.impl-log-max-lines` 行，只写结论、表格与 raw 文件指针；过程日志、完整输出放 `raw/`，中间轮只留最终轮。

```yaml
issue: <issue-id>
feature-slug: <feature-slug>
commit: <完成时 HEAD 的 commit hash>
branch: feat/<feature-slug>-<issue-id>
all-passed: true|false    # 仅表示本票所有实际执行的 DoD 验收命令与 fast 均通过；不表示分支 / main 的 CI full 已通过
quarantined: 0            # 本票 DoD / fast 中按隔离清单 skip 的用例数（attribution: quarantine 时必填）
generated: YYYY-MM-DD HH:MM
```

## 验收命令执行记录

> 逐条对应票文件 DoD。命令必须在本票 worktree 内实际执行，禁止只引用 agent 自述。

### DoD-1

- 命令：`<命令>`
- 退出码：`0`
- 关键输出（截取可判定部分）：
  ```text
  <输出>
  ```

### DoD-2

（同上）

## fast 质量门记录

- 命令：`<quality-gate.fast>` → 退出码 / 耗时
- 关键输出：`<可判定部分；完整输出放 raw/>`
- 先在失败隔离（`attribution: quarantine` 时必填）：隔离数（头部 `quarantined` 字段须一致）+ 清单版本或路径；清单外失败补跑记录（疑似本票引入的单项 + base 侧同命令对照结果）。`two-sided` 模式逐条记录 branch / base 退出码与原始日志名。清单外失败必须显式列出：仅 base 侧同签名归因成立的先在失败可不进 `all-passed` 分母；新增或未归因失败必须令 `all-passed: false` 并阻塞。未执行项不属于先在失败，不得记 quarantine 或通过
- load-flake 清单（如有）：超时单项 + 运行期 load-peak + 串行重跑退出码；load-flake 不算失败、不进归因、不进 `all-passed` 分母

> 本票不执行关联选择集或完整套件，也不记录任何票级 scoped / full 档位、延期或 full 证据。完整回归只在分支 / main 的 CI full 中执行，晋级证据由 promote 流程单独核对。

## 回归有效性（仅 `type: bug` 票必填）

> 证明回归测试真的能抓住这个 bug：revert 生产修复（保留测试）须见红，恢复后须绿。revert 后不变红 = 测试从未复现 bug，修复不算完成。

- revert 修复后重跑回归测试：命令 + 退出码（须非 0、断言失败）+ 失败输出要点
- 恢复修复后重跑：命令 + 退出码 `0`

## 人工确认项（non-gating，不进 all-passed）

- 性能 / 展示类结果：命令、原始输出路径、与参考值对比（只陈述，不判通过）
- env skip 清单：用例名 + 缺失依赖
