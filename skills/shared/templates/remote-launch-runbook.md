# 判读机开发启动 runbook（Herdr 手动管理）

> `/setup-dev --mode remote` 产出；套件不自动 `hc run/send/kill`。

## 前置
- `hc machine add <ssh-target> --name <machine> --install`（只需一次）
- 判读机受管 clone、venv、skills、`~/.dsh/dev.env`（0600）
- dsh `dev`/`dev-web` profile + overlay；OpenCode provider/agents（可选）

## 启动
```sh
# dsh 一次性任务
hc run --on <machine> --keep --bg --name pandu-dev-TASK \
  --cwd <repo> --label plane=remote \
  -- /home/pandu/.dsh/bin/dev-dsh headless "TASK"

# dsh Web UI（交互）
hc run --on <machine> --keep --bg --name pandu-dev-web \
  --cwd <repo> -- /home/pandu/.dsh/bin/dev-dsh-web
# mac 隧道：ssh -N -L 4096:127.0.0.1:4096 -p <port> <user>@<host>

# OpenCode（备选）
hc run --on <machine> --keep --bg --name pandu-oc \
  --cwd <repo> -- /home/pandu/.dsh/bin/dev-opencode
```

## 记录与恢复
- `hc get <machine>/tN --json` 看终端；`hc attach` / `hc read` / `hc send` 由用户手动操作。
- 记录 `herdr_terminal` 与 agent CLI 的 `session_id`；后者是派发/评审回执唯一真源。
- dsh 恢复：`dev-dsh --session-id <id> headless "..."`；OpenCode：`dev-opencode --continue` 或 `-s <id>`。
- 终端活性不等于 agent 会话有效；未知时保留 worktree/分支。
