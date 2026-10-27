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
    # npm/vite 会把无害的警告写进 stderr（如 INEFFECTIVE_DYNAMIC_IMPORT），
    # 而 PowerShell 5.1 在 $ErrorActionPreference='Stop' 下会把 stderr 输出
    # 当成 NativeCommandError 终止整个脚本。改成重定向到文件并按退出码判断。
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & npm run build *> (Join-Path $logs "frontend.log")
        $npmCode = $LASTEXITCODE
    } finally { $ErrorActionPreference = $prevEap }
    if ($npmCode -ne 0) {
        Get-Content (Join-Path $logs "frontend.log") -Tail 20 | ForEach-Object { "    $_" }
        throw "前端构建失败（退出码 $npmCode）"
    }
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
    # --windowed 与 CI 保持一致：正式版绝不能弹控制台黑窗。
    # 这里如果和 CI 不一致，就会出现"本地测没问题、用户装上多一个黑框"
    # ——0.1.8 正是这么漏的（本地 spec 是 console=True）。
    #
    # --hidden-import 收的是**函数内延迟导入**，PyInstaller 静态分析看不到：
    #   mediapipe.tasks.*  faces.py 在函数里 import，漏了打包版会报
    #                      "No module named 'mediapipe.tasks.c'"
    #   insightface.app    人脸检测
    #   pyiqa.archs.musiq_arch  MUSIQ 评分
    # 漏收的后果是"源码跑完全正常、装好的版本一用就崩"，很难查。
    #
    # PyInstaller 把进度日志写到 stderr，PowerShell 5.1 会当成
    # NativeCommandError 终止脚本，所以统一重定向到文件、只看退出码。
    #
    # torchvision 的 C++ 扩展要单独处理：torchvision.extension 用
    # torch.ops.load_library() 按**文件路径**加载 _C_stable.pyd，
    # 而 onefile 把它解压到 _MEIPASS/torchvision 下，PyInstaller 默认
    # 不保证落在那，结果运行时报 "operator torchvision::nms does not exist"
    # ——这是纯运行时错误，源码环境完全正常，极难查。
    $tvPkg = & $Python -c "import torchvision, os; print(os.path.dirname(torchvision.__file__))"
    $tvC = Join-Path $tvPkg.Trim() "_C_stable.pyd"
    if (-not (Test-Path $tvC)) { throw "找不到 torchvision 的 _C_stable.pyd: $tvC" }

    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Python -m PyInstaller --name jury-backend --onefile $entry `
            --distpath $out --workpath (Join-Path $work "work") `
            --specpath $work --noconfirm --clean --windowed `
            --hidden-import=mediapipe.tasks.python `
            --hidden-import=mediapipe.tasks.python.vision `
            --hidden-import=mediapipe.tasks.python.vision.face_landmarker `
            --hidden-import=mediapipe.tasks.c `
            --hidden-import=insightface.app `
            --hidden-import=insightface.utils.face_align `
            --hidden-import=pyiqa.archs.musiq_arch `
            --hidden-import=torchvision.extension `
            --hidden-import=torchvision._internally_replaced_utils `
            --add-binary="$tvC;torchvision" `
            --runtime-hook="$(Join-Path $repo 'scripts\pyi-rthook-torchvision.py')" `
            --hidden-import=onnxruntime.capi.onnxruntime_pybind11_state `
            --collect-binaries=mediapipe `
            --collect-data=mediapipe `
            *> (Join-Path $logs "pyinstaller.log")
        $pyiCode = $LASTEXITCODE
    } finally { $ErrorActionPreference = $prevEap }
    if ($pyiCode -ne 0) {
        Get-Content (Join-Path $logs "pyinstaller.log") -Tail 25 | ForEach-Object { "    $_" }
        throw "PyInstaller 失败（退出码 $pyiCode）"
    }
} finally { Pop-Location }

$exe = Join-Path $out "jury-backend.exe"
if (-not (Test-Path $exe)) { throw "产物缺失: $exe" }

# 防回归：确认产物确实是 windowed（无控制台子系统）。
# PyInstaller 的 --windowed 会写入 PE 头的 Subsystem=2(GUI)，
# 用 .NET 读一下，避免又静默退回 console。
try {
    $fs = [IO.File]::OpenRead($exe)
    $br = New-Object IO.BinaryReader($fs)
    $fs.Position = 0x3C
    $peOff = $br.ReadInt32()
    $fs.Position = $peOff + 0x5C
    $subsystem = $br.ReadUInt16()
    $br.Close(); $fs.Close()
    if ($subsystem -ne 2) {
        throw "sidecar 不是 GUI 子系统（Subsystem=$subsystem，应为 2）。会弹控制台窗口，请检查 --windowed 是否生效。"
    }
    Write-Host "    已验证 Subsystem=2（无控制台）" -ForegroundColor DarkGray
} catch [System.Management.Automation.MethodInvocationException] { throw }
  catch { Write-Host "    （未能校验子系统，跳过）" -ForegroundColor DarkYellow }

Say ("sidecar {0:N1} MB" -f ((Get-Item $exe).Length / 1MB)) Green

# ---- 运行期依赖自检 ----
# 函数内延迟导入的模块（mediapipe.tasks、torchvision._C）PyInstaller 静态
# 分析看不到，源码跑一万遍也发现不了，只有产物自己 import 一次才知道。
# 0.1.9 就是本地全绿、装好一点整理就崩，所以这一步必须在打包安装器之前做。
Say "sidecar 运行期依赖自检"
$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    & $exe --doctor *> (Join-Path $logs "doctor.log")
    $docCode = $LASTEXITCODE
} finally { $ErrorActionPreference = $prevEap }
if ($docCode -ne 0) {
    Get-Content (Join-Path $logs "doctor.log") -Tail 30 | ForEach-Object { "    $_" }
    throw "sidecar --doctor 未通过（退出码 $docCode）：打包缺少运行期依赖，详见 temp\logs\doctor.log"
}
Write-Host "    doctor 通过：运行期依赖齐全" -ForegroundColor DarkGray

# ---- 安装包 ----
Say "构建安装包"
Push-Location $repo
try {
    $env:TAURI_SIGNING_PRIVATE_KEY = (Get-Content (Join-Path $repo ".keys\jurymate.key") -Raw).Trim()
    $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = ""
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & npm run tauri build *> (Join-Path $logs "tauri-build.log")
        $tauriCode = $LASTEXITCODE
    } finally { $ErrorActionPreference = $prevEap }
    Select-String -Path (Join-Path $logs "tauri-build.log") `
        -Pattern 'Finished \d+ bundle|error:' -ErrorAction SilentlyContinue |
        Select-Object -Last 2 | ForEach-Object { "    $($_.Line.Trim())" }
    if ($tauriCode -ne 0) {
        Get-Content (Join-Path $logs "tauri-build.log") -Tail 25 | ForEach-Object { "    $_" }
        throw "Tauri 打包失败（退出码 $tauriCode）"
    }
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
