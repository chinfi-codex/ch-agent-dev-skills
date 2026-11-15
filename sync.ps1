# v2 目前仅支持 macOS / Linux（POSIX flock 与 dev 软链）。
# Windows 不使用本脚本分发：配置、锁与清理必须 fail closed，不能伪装成可用。
$ErrorActionPreference = "Stop"
if ($env:OS -eq "Windows_NT" -or $PSVersionTable.Platform -eq "Win32NT") {
  Write-Error "agent-dev v2 is POSIX-only (macOS/Linux); Windows is not supported. Use a POSIX host."
  exit 2
}

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Pack = Join-Path $Root "skills"

node --experimental-strip-types (Join-Path $Pack "scripts\gen-skill-docs.ts") --check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$PythonCache = Get-ChildItem -Path $Pack -Recurse -Force | Where-Object { $_.Name -eq "__pycache__" -or $_.Name -match '\.py[cod]$' } | Select-Object -First 1
if ($PythonCache) {
  Write-Error "refuse: Python cache artifacts found under $Pack; remove them before sync"
  exit 1
}

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

# DSH 注册 session-title 与 progress-panel；Desktop 运行时只同步文件，退出后重跑注册。
$DSHPackages = @(
  $(if ($env:DSH_TITLE_PACKAGE) { $env:DSH_TITLE_PACKAGE } else { Join-Path $DSHRoot "skills\shared\dsh-session-title" }),
  $(if ($env:DSH_PROGRESS_PACKAGE) { $env:DSH_PROGRESS_PACKAGE } else { Join-Path $DSHRoot "skills\shared\dsh-progress-panel" })
)
foreach ($DSHPackage in $DSHPackages) {
  if (-not (Test-Path $DSHPackage)) {
    Write-Host "skip: DSH 插件目录不存在：$DSHPackage"
    continue
  }
  $DSHProfile = if ($env:DSH_PROFILE) { $env:DSH_PROFILE } else { "desktop" }
  $DSHDesktopRunning = Get-Process -Name "DeepSeek Harness" -ErrorAction SilentlyContinue
  if ($DSHProfile -eq "desktop" -and $DSHDesktopRunning) {
    Write-Host "pending: DeepSeek Harness Desktop 正在运行；完全退出后重跑以注册 $DSHPackage"
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
