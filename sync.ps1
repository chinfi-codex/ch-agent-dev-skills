# 同步 skills/ 到本机各 agent 宿主的 skills 目录（Windows 版，等价 sync.sh，全部平铺分发）。
# 用法：在仓库根目录执行 powershell -ExecutionPolicy Bypass -File .\sync.ps1
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Pack = Join-Path $Root "skills"

node --experimental-strip-types (Join-Path $Pack "scripts\gen-skill-docs.ts")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$FlatItems = @("ceo-office","pd-plan","issue","prd","pd-review","review","tech-spec","issue-split","implement","ai-review","setup-dev","prototype","handoff","shared")

function Sync-Flat($TargetRoot) {
  New-Item -ItemType Directory -Force $TargetRoot | Out-Null
  foreach ($Item in $FlatItems) {
    $Dst = Join-Path $TargetRoot $Item
    if (Test-Path $Dst) { Remove-Item -Recurse -Force $Dst }
    Copy-Item -Recurse (Join-Path $Pack $Item) $Dst
  }
  $ScriptDir = Join-Path $TargetRoot "shared\scripts"
  if (Test-Path (Join-Path $ScriptDir "package.json")) {
    Push-Location $ScriptDir
    try {
      npm ci --omit=dev --no-audit --no-fund
      if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } finally {
      Pop-Location
    }
  }
  Write-Host "synced (flat)   -> $TargetRoot"
}

if (Test-Path "$HOME\.claude") { Sync-Flat "$HOME\.claude\skills" } else { Write-Host "skip: ~/.claude 不存在（宿主未安装）" }
if (Test-Path "$HOME\.zcode")  { Sync-Flat "$HOME\.zcode\skills" }  else { Write-Host "skip: ~/.zcode 不存在（宿主未安装）" }
if (Test-Path "$HOME\.kimi-code") { Sync-Flat "$HOME\.kimi-code\skills" } else { Write-Host "skip: ~/.kimi-code 不存在（宿主未安装）" }
if (Test-Path "$HOME\.codex")  { Sync-Flat "$HOME\.codex\skills" }  else { Write-Host "skip: ~/.codex 不存在（宿主未安装）" }

# 安装 CC 原生 session 自动命名 hooks；Codex/ZCode 未提供可附着命令时不注册空转 hooks。
function Install-TitleHooks($HostName, $TargetRoot) {
  $ScriptDir = Join-Path $TargetRoot "shared\scripts"
  python (Join-Path $ScriptDir "install_host_hooks.py") --host $HostName --script (Join-Path $ScriptDir "session_title.py")
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
if (Test-Path "$HOME\.claude") { Install-TitleHooks "claude" "$HOME\.claude\skills" }
