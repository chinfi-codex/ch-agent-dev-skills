#!/usr/bin/env bash
# 同步 skills/product-workflow 到本机各 agent 宿主的 skills 目录。
# 两件事：1) 重新生成全部 SKILL.md；2) 按各宿主布局分发。
#   Claude Code / ZCode：平铺（skill 目录 + shared）-> ~/.claude/skills/、~/.zcode/skills/
#   Codex：整棵嵌套 -> ~/.codex/skills/product-workflow/
# 用法：在仓库根目录执行 bash ./sync.sh（维护者环境需 Node >= 22.6）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACK="$ROOT/skills/product-workflow"

node --experimental-strip-types "$PACK/scripts/gen-skill-docs.ts"

FLAT_ITEMS=(ceo-office pd-plan issue prd pd-review review tech-spec issue-split implement ai-review setup-dev prototype shared)

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

if [ -d "$HOME/.codex" ]; then
  mkdir -p "$HOME/.codex/skills"
  rsync -a --delete "$PACK" "$HOME/.codex/skills/"
  echo "synced (nested) -> $HOME/.codex/skills/product-workflow"
else
  echo "skip: ~/.codex 不存在（宿主未安装）"
fi
