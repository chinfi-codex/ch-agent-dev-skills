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

# DeepSeek Harness（DSH）只同步 skills：dsh-skill-filesystem 默认扫描 ~/.dsh/skills，
# 设置 DSH_HOME 时扫描 $DSH_HOME/skills；目录变化在下一次模型步骤即时生效，无需重启。
DSH_ROOT="${DSH_HOME:-$HOME/.dsh}"
if [ -d "$DSH_ROOT" ]; then
  sync_flat "$DSH_ROOT/skills"
else
  echo "skip: $DSH_ROOT 不存在（宿主未安装）"
fi

# 安装三宿主 session 自动命名 hooks；合并配置，不覆盖已有 hooks（DSH 不安装本套 hooks）。
install_title_hooks() {
  local host="$1"
  local target_root="$2"
  python3 "$target_root/shared/scripts/install_host_hooks.py" \
    --host "$host" --script "$target_root/shared/scripts/session_title.py"
}
if [ -d "$HOME/.claude" ]; then install_title_hooks claude "$HOME/.claude/skills"; fi
if [ -d "$HOME/.zcode" ]; then install_title_hooks zcode "$HOME/.zcode/skills"; fi
if [ -d "$HOME/.codex" ]; then install_title_hooks codex "$HOME/.codex/skills"; fi
