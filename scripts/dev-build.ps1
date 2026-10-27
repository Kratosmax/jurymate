# 开发构建脚本：把所有中间产物与临时文件钉在项目内，不写系统盘。
#
# 为什么需要它：默认行为会把东西写到 C 盘
#   - PyInstaller 用 %TEMP% 做 --workpath，且运行时把依赖解压到 %TEMP%\_MEI*（约 1.1GB/次）
#   - WiX 打 MSI 时在 %TEMP% 解出约 3.8GB 的中间文件
#   - 应用内升级会在 %TEMP% 留 457MB 的安装包
# 这些累积起来能吃掉几十 GB（实测 27.45GB）。本脚本把以上三类全部改道到
# <repo>/temp，退出时可选清理。
#
# 用法：
#   pwsh -File scripts/dev-build.ps1              # 完整构建（前端+sidecar+安装包）
#   pwsh -File scripts/dev-build.ps1 -FrontOnly   # 只构建前端
#   pwsh -File scripts/dev-build.ps1 -Clean       # 构建完清理临时文件
param(
    [switch]$FrontOnly,
    [switch]$Clean,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$tmp = Join-Path $repo "temp"
$logs = Join-Path $tmp "logs"
New-Item -ItemType Directory -Force -Path $tmp, $logs | Out-Null

function Say($msg, $color = "Cyan") { Write-Host "==> $msg" -ForegroundColor $color }

function Show-TempUsage {
    $gb = 0
    if (Test-Path $tmp) {
        $gb = (Get-ChildItem $tmp -Recurse -File -ErrorAction SilentlyContinue |
               Measure-Object Length -Sum).Sum / 1GB
    }
    $c = (Get-PSDrive C -ErrorAction SilentlyContinue).Free / 1GB
    Write-Host ("    temp 占用 {0:N2} GB ｜ C 盘可用 {1:N1} GB" -f $gb, $c) -ForegroundColor DarkGray
}

# ---- 关键：把本进程及其所有子进程的临时目录改道到项目内 ----
# PyInstaller / WiX / 应用内升级都会继承这两个变量。
$env:TEMP = $tmp
$env:TMP = $tmp
# PyInstaller 的 --workpath 已是显式传入，这里再钉一次相关缓存
$env:PYINSTALLER_CONFIG_DIR = Join-Path $tmp "pyinstaller-cache"

Say "临时目录已改道: $tmp"
Show-TempUsage

# ---- 前端 ----
Say "构建前端"
Push-Location $repo
try {
    & npm run build 2>&1 | Tee-Object -FilePath (Join-Path $logs "frontend.log")
    if ($LASTEXITCODE -ne 0) { throw "前端构建失败" }
} finally { Pop-Location }

if ($FrontOnly) {
    Say "仅前端，结束" Green
    Show-TempUsage
    exit 0
}

# ---- 后端 sidecar ----
if (-not $Python) {
    $candidates = @(
        "F:\git\cullmate\.venv\Scripts\python.exe",
        "python"
    )
    $Python = $candidates | Where-Object {
        $_ -eq "python" -or (Test-Path $_)
    } | Select-Object -First 1
}
if (-not $Python) { throw "找不到 Python，请用 -Python 指定" }

$work = Join-Path $repo "build-backend"
$out = Join-Path $repo "dist-backend"
$entry = Join-Path $repo "backend\app.py"
New-Item -ItemType Directory -Force -Path $work, $out | Out-Null

Say "构建 sidecar (Python: $Python)"
# 必须删旧产物：PyInstaller 见到已存在会跳过，导致拿到旧代码
Remove-Item (Join-Path $out "jury-backend.exe") -Force -ErrorAction SilentlyContinue
Push-Location $work
try {
    & $Python -m PyInstaller --name jury-backend --onefile $entry `
        --distpath $out --workpath (Join-Path $work "work") `
        --specpath $work --noconfirm --clean 2>&1 |
        Tee-Object -FilePath (Join-Path $logs "pyinstaller.log") | Select-Object -Last 2
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败" }
} finally { Pop-Location }

$exe = Join-Path $out "jury-backend.exe"
if (-not (Test-Path $exe)) { throw "产物缺失: $exe" }
Say ("sidecar {0:N1} MB" -f ((Get-Item $exe).Length / 1MB)) Green

# ---- 安装包 ----
Say "构建安装包"
Push-Location $repo
try {
    $env:TAURI_SIGNING_PRIVATE_KEY = (Get-Content (Join-Path $repo ".keys\jurymate.key") -Raw).Trim()
    $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = ""
    & npm run tauri build 2>&1 |
        Tee-Object -FilePath (Join-Path $logs "tauri-build.log") |
        Select-String -Pattern 'Finished 2 bundles|error:' | Select-Object -Last 2
    if ($LASTEXITCODE -ne 0) { throw "Tauri 打包失败" }
} finally { Pop-Location }

Say "完成" Green
Show-TempUsage

if ($Clean) {
    Say "清理临时文件"
    Get-ChildItem $tmp -Directory -Filter "_MEI*" -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
    Get-ChildItem $tmp -Directory -Filter "JuryMate-*-updater-*" -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
    Show-TempUsage
}
