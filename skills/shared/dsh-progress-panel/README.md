# @ch-agent/dsh-progress-panel

DSH 插件：多宿主开发进度面板，挂在右侧栏「进度」页签。按分支嵌套的票树（git 祖先链定父子）+ 活性探针 + 宿主会话绑定徽章；已完成票的残留分支提供「清理」操作。

## 结构

- `index.js` 服务端半端：
  - `GET /api/ch-progress/payload` → `../scripts/progress.py payload`（20s 进程内缓存）
  - `POST /api/ch-progress/cleanup/preview` → 只读预检
  - `POST /api/ch-progress/cleanup/apply` → 确认指纹执行（要求 JSON 与 `x-ch-progress-action: apply`）
- `client.js` 浏览器半端：`window.__ModuleLoader__.load` 自注册；右侧栏 tab 类型 + body + title 三件套；tab 定义的 `guide` 数组在「开始」页提供「开发进度」工具卡（点击原生切换到进度页签）；切换对话时继承右侧栏展开状态（用户主动折叠则不强制重开）；10s 轮询，任务行折叠、票详情、清理确认弹窗
- `../scripts/progress_cleanup.py`：清理原语与确认指纹；`tests/test_progress_cleanup.py` 覆盖安全边界

## 清理边界

- 只给 `status=done` 的残留分支入口；在途票、needs-human、评审中与集成分支不能从面板清理。若分支仍被在途/待派发票延展（git 祖先链判定），预检同样拒绝执行，保护基线冻结中的工作。
- 执行链：预检 → 用户确认 → 重新计算 HEAD/文件内容/worktree 状态并比对 confirm → salvage → `git worktree remove --force` → `git worktree prune` → 已并入授权目标的本地分支才删除 → dev 过程文件清理。任何变化先停止。
- 合并目标支持 `main-branch`、agents-config 的特性集成分支和明确定义的 `query` 工作分支；未合入的分支保留。
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
