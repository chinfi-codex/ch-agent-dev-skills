# @ch-agent/dsh-progress-panel

DSH 插件：多宿主开发进度面板，挂在右侧栏「进度」页签。按分支嵌套的票树（git 祖先链定父子）+ 活性探针 + 宿主会话绑定徽章；残留分支与中途搁置需求均提供「清理」操作。

## 结构

- `index.js` 服务端半端：
  - `GET /api/ch-progress/payload` → `../scripts/progress.py payload`（20s 进程内缓存）
  - `POST /api/ch-progress/cleanup/preview` → 只读预检
  - `POST /api/ch-progress/cleanup/apply` → 确认指纹执行（要求 JSON 与 `x-ch-progress-action: apply`）
- `client.js` 浏览器半端：`window.__ModuleLoader__.load` 自注册；右侧栏 tab 类型 + body + title 三件套；tab 定义的 `guide` 数组在「开始」页提供「开发进度」工具卡（点击原生切换到进度页签）；切换对话时继承右侧栏展开状态（用户主动折叠则不强制重开）；10s 轮询，任务行折叠、票详情、清理确认弹窗
- `../scripts/progress_cleanup.py`：清理原语与确认指纹；`tests/test_progress_cleanup.py` 覆盖安全边界

## 工作区与会话联动

- 客户端通过 `uiWorkspace.selection` + `workspaces/sessions` 确定当前工作区；未选择工作区时不加载、不展示进度数据。切换工作区会重建面板，切换会话只移动焦点。
- 服务端仅接受 `config.repo` / `config.repos` 中登记的仓库（本机 profile 配置，不写死在插件包内），按 repo 缓存 payload；清理请求也必须带同一 repo，未登记路径返回 403。
- 当前会话先按 session id 匹配 session-title 绑定，再用标题中的 issue / feature 兜底；命中后展开祖先、滚动到条目并高亮。
- 「复制下钻提问」与「清理」统一放在票详情动作排；清理入口不再按状态隐藏，所有票/分支都可预检，但执行仍受下方安全边界约束。

- **关闭需求（独立生命周期操作）**：票详情提供「关闭需求」，确认后仅把 `status` 改为 `deferred` 并追加 `digest`（含原因），不删除分支、worktree 或 dev 文件；`deferred/cancelled/superseded` 不作为 `blocked-by` 的完成依据，避免误解锁下游。

## 清理边界

- 清理入口全状态可见。`done` 走普通收尾；非 done（除 `proposed` 未开工）按「中途搁置」提供两个选择：**丢弃开发**（保存补丁后删除 worktree 和本地分支）或**合并保留**（先尝试合入已提交内容，成功后再清理）。活跃后代分支仍禁止清理。
- **无本地分支（issue-only）**：只清理该票 `dev/impl/<issue>/` 中明确可判的过程文件，不删除代码、分支或票状态；`proposed` 仍不可清理，dev 无安全候选时提示无可清理文件，不生成空清理。
- 共同前置：confirm 指纹重验 + salvage。中途搁置未合入时额外保存完整 `branch.patch`；未提交改动保存 `uncommitted.patch` 和文件副本，不参与合并。合并保留要求目标 worktree 干净，冲突自动 abort，原 worktree 和分支保留；合并成功不自动把票置为 done，后续仍按正常验收收口。
- 执行链：预检 → 用户选择 → 后台重验 → salvage → 按选择合并或直接进入 worktree 删除 → `git worktree prune` → 本地分支处理 → dev 过程文件清理。普通 done 未合入的分支仍保留，不删远端分支。
- dev 软链不跟随。实体 dev 与主工作区逐文件比对，未收录内容写入 `dev/.salvage/<issue>-<时间>/`，含 `_manifest.json` 和未提交补丁。内容去重；同设备硬链接，跨设备才复制。
- dev 只清可再生缓存、merge 中间物等明确候选；被 impl-log / evidence / 评审等 Markdown 引用的文件保留。票文件、impl-log、评审报告不在删除范围。
- 成功后向本 feature 的 `progress.jsonl` 追加一条 `digest` 留痕。

## 安装（profile: ~/.dsh/profiles/desktop）

1. `package.json` 依赖加 `"@ch-agent/dsh-progress-panel": "link:<skills仓>/skills/shared/dsh-progress-panel"`（sync.sh 已铺到 `~/.dsh/skills/shared/`，用该路径）；`dsh.profile.bundles` 加 `"@ch-agent/dsh-progress-panel"`
2. bundle 自带 `cordis.patch.yml` 声明挂载；要改默认仓库，在 profile 的 `cordis.patch.yml` 同 id 覆盖 `config.repo`
3. 重启 DSH

## 数据契约与性能

票 frontmatter `status` / `dispatch` 块 + `dev/features/<m>/<s>/progress.jsonl` 事件流（见 `supervision-loop.md`「状态与事件写入契约」）；活性不落盘，查询时采样。活性遍历不扫 dev；完整 ahead/dirty 只算在途与集成分支；祖先链只给在途/集成子节点找父，避免大仓 done 残留两两检查。

验证：

```bash
python3 -m unittest skills/shared/scripts/tests/test_progress_cleanup.py -v
python3 skills/shared/scripts/progress.py cleanup --repo /path/to/repo --branch feat/slug-issue-001  # 只读预检
```
