# 同步 skills/product-workflow 到本机各 agent 宿主的 skills 目录（Windows 版，等价 sync.sh）。
# 用法：在仓库根目录执行 powershell -ExecutionPolicy Bypass -File .\sync.ps1
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Pack = Join-Path $Root "skills\product-workflow"

node --experimental-strip-types (Join-Path $Pack "scripts\gen-skill-docs.ts")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$FlatItems = @("ceo-office","pd-plan","issue","prd","pd-review","review","tech-spec","issue-split","implement","ai-review","setup-dev","prototype","shared")

function Sync-Flat($TargetRoot) {
  New-Item -ItemType Directory -Force $TargetRoot | Out-Null
  foreach ($Item in $FlatItems) {
    $Dst = Join-Path $TargetRoot $Item
    if (Test-Path $Dst) { Remove-Item -Recurse -Force $Dst }
    Copy-Item -Recurse (Join-Path $Pack $Item) $Dst
  }
  Write-Host "synced (flat)   -> $TargetRoot"
}

if (Test-Path "$HOME\.claude") { Sync-Flat "$HOME\.claude\skills" } else { Write-Host "skip: ~/.claude 不存在（宿主未安装）" }
if (Test-Path "$HOME\.zcode")  { Sync-Flat "$HOME\.zcode\skills" }  else { Write-Host "skip: ~/.zcode 不存在（宿主未安装）" }

if (Test-Path "$HOME\.codex") {
  $Dst = "$HOME\.codex\skills\product-workflow"
  if (Test-Path $Dst) { Remove-Item -Recurse -Force $Dst }
  New-Item -ItemType Directory -Force (Split-Path -Parent $Dst) | Out-Null
  Copy-Item -Recurse $Pack $Dst
  Write-Host "synced (nested) -> $Dst"
} else {
  Write-Host "skip: ~/.codex 不存在（宿主未安装）"
}
