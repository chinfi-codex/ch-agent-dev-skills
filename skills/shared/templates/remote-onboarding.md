# 判读机远端接入检查单（setup-dev --mode remote）

1. 机器与目录：`hc` 可用、判读机已注册；repo 在受管 clone，拒绝 `/opt/pandu/**`。
2. git：CA 生效（`git ls-remote` 无 `-k`）、user.name/email、凭据（token/SSH）、`credential.helper`。
3. 运行时：venv、fast 基线、`test_slots` 预算（total/max-runs）。
4. 技能：`~/.dsh/skills` 与 OpenCode skills 根有你需要的套件；manifest 校验和一致。
5. 模型：dsh/opencode 主机目录实测可用；默认 GLM-5.3 / GLM-5.3-Flash。
6. 角色：`agents_config.py validate`、`dispatch_config.py validate/resolve`、`render --check`。
7. 原生回执：`native_session_command` 可 query/write；`host_adapters` 探测非 unknown。
8. 启动：runbook 可手动启动 dsh/OpenCode；记录 `herdr_terminal` 与 agent session id。
9. 证据：`dev/remote/plane.json` 已写；mac 只留只读镜像。
