#!/usr/bin/env bash
# 同步 skills/ 到本机各 agent 宿主的 skills 目录。
# 两件事：1) 重新生成全部 SKILL.md；2) 按各宿主布局分发（平铺：skill 目录 + shared）。
#   Claude Code / ZCode / Kimi Code / Codex / DeepSeek Harness（DSH）：平铺到各宿主 skills 目录。
#   DSH 默认使用 ~/.dsh/skills/；设置 DSH_HOME 时使用 $DSH_HOME/skills/。
# 用法：在仓库根目录执行 bash ./sync.sh（维护者环境需 Node >= 22.6）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACK="$ROOT/skills"

node --experimental-strip-types "$PACK/scripts/gen-skill-docs.ts"

FLAT_ITEMS=(ceo-office pd-plan issue prd pd-review review tech-spec issue-split implement ai-review setup-dev prototype handoff shared)

sync_flat() {
  local target_root="$1"
  mkdir -p "$target_root"
  local item
  for item in "${FLAT_ITEMS[@]}"; do
    rsync -a --delete "$PACK/$item" "$target_root/"
  done
  echo "synced (flat)   -> $target_root"
}

# 只同步到已安装的宿主（宿主配置根目录存在才分发）
if [ -d "$HOME/.claude" ]; then
  sync_flat "$HOME/.claude/skills"
else
  echo "skip: ~/.claude 不存在（宿主未安装）"
fi

if [ -d "$HOME/.zcode" ]; then
  sync_flat "$HOME/.zcode/skills"
else
  echo "skip: ~/.zcode 不存在（宿主未安装）"
fi

if [ -d "$HOME/.kimi-code" ]; then
  sync_flat "$HOME/.kimi-code/skills"
else
  echo "skip: ~/.kimi-code 不存在（宿主未安装）"
fi

if [ -d "$HOME/.codex" ]; then
  sync_flat "$HOME/.codex/skills"
else
  echo "skip: ~/.codex 不存在（宿主未安装）"
fi

# DeepSeek Harness（DSH）同步 skills 与原生 session 标题插件。
DSH_ROOT="${DSH_HOME:-$HOME/.dsh}"
if [ -d "$DSH_ROOT" ]; then
  sync_flat "$DSH_ROOT/skills"
else
  echo "skip: $DSH_ROOT 不存在（宿主未安装）"
fi

# 安装三宿主 session 自动命名 hooks；合并配置，不覆盖已有 hooks。
install_title_hooks() {
  local host="$1"
  local target_root="$2"
  python3 "$target_root/shared/scripts/install_host_hooks.py" \
    --host "$host" --script "$target_root/shared/scripts/session_title.py"
}
if [ -d "$HOME/.claude" ]; then install_title_hooks claude "$HOME/.claude/skills"; fi
if [ -d "$HOME/.zcode" ]; then install_title_hooks zcode "$HOME/.zcode/skills"; fi
if [ -d "$HOME/.codex" ]; then install_title_hooks codex "$HOME/.codex/skills"; fi

# DSH 使用原生 bundle 调 sessionTitle.rename；Desktop 正在运行时只同步文件，
# 不违反官方“完全退出后改 desktop profile”的要求。其余 profile 可直接注册。
install_dsh_title_plugin() {
  local cli="$1"
  local profile="$2"
  local package="$3"
  if [ "$profile" = "desktop" ] && pgrep -f '/Applications/DeepSeek Harness.app/Contents/MacOS/DeepSeek Harness' >/dev/null; then
    echo "skip: DeepSeek Harness Desktop 正在运行；完全退出后执行本脚本以注册 session 标题插件"
    return 0
  fi
  "$cli" plugin --profile "$profile" add "$package"
}
DSH_PACKAGE="${DSH_TITLE_PACKAGE:-$DSH_ROOT/skills/shared/dsh-session-title}"
if [ -d "$DSH_PACKAGE" ]; then
  if [ -n "${DSH_CLI:-}" ]; then
    install_dsh_title_plugin "$DSH_CLI" "${DSH_PROFILE:-desktop}" "$DSH_PACKAGE"
  elif [ -x "/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh" ]; then
    install_dsh_title_plugin "/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh" desktop "$DSH_PACKAGE"
  elif command -v dsh >/dev/null 2>&1; then
    install_dsh_title_plugin "$(command -v dsh)" "${DSH_PROFILE:-headless}" "$DSH_PACKAGE"
  else
    echo "skip: 找不到 dsh CLI；仅同步 $DSH_PACKAGE，未注册 profile bundle"
  fi
fi
