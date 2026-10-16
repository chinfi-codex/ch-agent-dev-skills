#!/usr/bin/env bash
# 同步 skills/ 到本机各 agent 宿主的 skills 目录。
# 两件事：1) 重新生成全部 SKILL.md；2) 按各宿主布局分发（平铺：skill 目录 + shared）。
#   Claude Code / ZCode / Kimi Code / Codex：平铺 -> ~/.claude/skills/、~/.zcode/skills/、~/.kimi-code/skills/、~/.codex/skills/
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
  if [ -f "$PACK/shared/scripts/package.json" ]; then
    cp "$PACK/shared/scripts/package.json" "$PACK/shared/scripts/package-lock.json" "$target_root/shared/scripts/"
    (cd "$target_root/shared/scripts" && npm ci --omit=dev --no-audit --no-fund)
  fi
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

# 安装 CC 原生 session 自动命名 hooks；Codex/ZCode 保留共享投影与可选适配器，
# 但未提供可附着当前会话的命令时不注册空转 hooks。
install_title_hooks() {
  local host="$1"
  local target_root="$2"
  python3 "$target_root/shared/scripts/install_host_hooks.py" \
    --host "$host" --script "$target_root/shared/scripts/session_title.py"
}
if [ -d "$HOME/.claude" ]; then install_title_hooks claude "$HOME/.claude/skills"; fi
