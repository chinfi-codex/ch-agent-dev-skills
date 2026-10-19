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
  Write-Host "synced (flat)   -> $TargetRoot"
}

if (Test-Path "$HOME\.claude") { Sync-Flat "$HOME\.claude\skills" } else { Write-Host "skip: ~/.claude 不存在（宿主未安装）" }
if (Test-Path "$HOME\.zcode")  { Sync-Flat "$HOME\.zcode\skills" }  else { Write-Host "skip: ~/.zcode 不存在（宿主未安装）" }
if (Test-Path "$HOME\.kimi-code") { Sync-Flat "$HOME\.kimi-code\skills" } else { Write-Host "skip: ~/.kimi-code 不存在（宿主未安装）" }
if (Test-Path "$HOME\.codex")  { Sync-Flat "$HOME\.codex\skills" }  else { Write-Host "skip: ~/.codex 不存在（宿主未安装）" }

# DeepSeek Harness（DSH）同步 skills 与原生 session 标题插件。
$DSHRoot = Join-Path $HOME ".dsh"
if ($env:DSH_HOME) { $DSHRoot = $env:DSH_HOME }
if (Test-Path $DSHRoot) { Sync-Flat (Join-Path $DSHRoot "skills") } else { Write-Host "skip: $DSHRoot 不存在（宿主未安装）" }

# 安装三宿主 session 自动命名 hooks；合并配置，不覆盖已有 hooks。
function Install-TitleHooks($HostName, $TargetRoot) {
  $ScriptDir = Join-Path $TargetRoot "shared\scripts"
  python (Join-Path $ScriptDir "install_host_hooks.py") --host $HostName --script (Join-Path $ScriptDir "session_title.py")
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
if (Test-Path "$HOME\.claude") { Install-TitleHooks "claude" "$HOME\.claude\skills" }
if (Test-Path "$HOME\.zcode") { Install-TitleHooks "zcode" "$HOME\.zcode\skills" }
if (Test-Path "$HOME\.codex") { Install-TitleHooks "codex" "$HOME\.codex\skills" }

# DSH 使用原生 bundle 调 sessionTitle.rename；Desktop 运行时只同步文件，退出后重跑注册。
$DSHPackage = Join-Path $DSHRoot "skills\shared\dsh-session-title"
if ($env:DSH_TITLE_PACKAGE) { $DSHPackage = $env:DSH_TITLE_PACKAGE }
if (Test-Path $DSHPackage) {
  $DSHProfile = if ($env:DSH_PROFILE) { $env:DSH_PROFILE } else { "desktop" }
  $DSHDesktopRunning = Get-Process -Name "DeepSeek Harness" -ErrorAction SilentlyContinue
  if ($DSHProfile -eq "desktop" -and $DSHDesktopRunning) {
    Write-Host "skip: DeepSeek Harness Desktop 正在运行；完全退出后执行本脚本以注册 session 标题插件"
  } elseif ($env:DSH_CLI) {
    & $env:DSH_CLI plugin --profile $DSHProfile add $DSHPackage
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  } elseif (Get-Command dsh -ErrorAction SilentlyContinue) {
    dsh plugin --profile $DSHProfile add $DSHPackage
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  } else {
    Write-Host "skip: 找不到 dsh CLI；仅同步 $DSHPackage，未注册 profile bundle"
  }
}
