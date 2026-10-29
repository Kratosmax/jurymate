# 双包更新（UI + Engine）

> 状态（2026-10-29 17:40）：engine 通道已发布，UI-only 安装版已真实完成下载、验签、
> 解压、原子切换、启动与 124 张回归；应用 v0.4.0 Release 已发布。

## 目标

- **UI 包**：Tauri 壳 + 前端，约 2.4 MB；常更新。
- **Engine 包**：Python sidecar（PyInstaller onedir），约 173.7 MiB；只在算法改动时更新。
- 两者独立版本、独立资产、独立更新路径，UI 更新不重复下载 180MB 引擎。

## 运行时布局

```
<数据根>/engine/current.json          {"version":"0.4.0"}
<数据根>/engine/0.4.0/engine.json
<数据根>/engine/0.4.0/jury-backend/jury-backend.exe
```

Rust `backend_exe()` 查找顺序：数据目录 engine → 旧版 bundle 资源 → 开发目录
`dist-backend/jury-backend/`。UI 安装目录不再携带 sidecar；NSIS 升级会清理旧
`dist-backend`，不会误删数据目录里的 engine。

## 引擎清单与校验

- 清单地址：`https://github.com/Kratosmax/jurymate/releases/download/engine/engine.json`
  （独立 `engine` 通道，非应用 latest）。
- 清单沿用 Tauri updater 格式，`platforms.windows-x86_64` 含 `url` / `signature` / `sha256`。
- 引擎 ZIP 用与 UI 相同的 Tauri 签名私钥签名（`tauri signer sign --app-version <engineVersion>`）。
- Rust `engine_install`：下载 → SHA256 → `Update::verify`（同一公钥）→ ZIP 路径穿越检查
  → 解压到 `.staging-*` → 原子切换 `engine/<version>` + 写 `current.json`；失败保留旧版本。
- 下载线路复用网络设置里的镜像前缀/HTTP 代理，和模型下载共用一套策略。

## 版本与构建

- `package.json.version` = UI 版本；`package.json.engineVersion` = 引擎版本。
- 引擎算法改动时只改 `engineVersion`，重新构建 Engine ZIP；UI 版本可以不动。
- `scripts/dev-build.ps1` 同时产出：
  - UI：`src-tauri/target/release/bundle/nsis/JuryMate_<ver>_x64-setup.exe`
  - Engine：`dist-engine/JuryMate_Engine_<engineVer>_x64.zip` + `.zip.sig` + `engine.json`

## 发布

1. UI Release 使用原有 `latest.json`，只含 UI setup。
2. Engine ZIP/清单由 CI 的 `Publish engine channel` 步骤发布到 `engine` 标签；
   引擎版本变化时更新该通道的 `engine.json` 与 ZIP。
3. 旧客户端兼容：旧版完整安装包（sidecar 随安装目录）仍能启动；新 UI 安装会清理旧
   `dist-backend`，首次启动走引擎下载门禁。

## 本地验证（2026-10-29）

- UI setup：2.35 MiB；Engine ZIP：182,173,308 B（173.73 MiB）。
  引擎 ZIP 高于 150MB 目标，剩余大头与 sidecar 相同（cv2 / onnxruntime / scipy）。
- 无引擎启动 UI：显示“首次使用需要下载引擎”门禁，未启动 backend。
- **真实下载通路**：0.3.2 首次安装时从 GitHub [engine 通道](https://github.com/Kratosmax/jurymate/releases/tag/engine)
  点击“下载并安装引擎”，下载进度到 100% 后自动安装并启动；0.4.0 UI 安装后再从
  0.3.2 检查到 0.4.0 并完成更新，落盘
  `D:\AppData\JuryMate\engine\0.4.0\jury-backend\jury-backend.exe`，`current.json` 为
  `{"version":"0.4.0"}`，进程路径已在运行中核对。
- 线上资产与本地哈希一致：0.4.0 Engine ZIP `1fd66843…05486`、`engine.json` `dfbbe3d6…09dbe8`。
- 124 张扫描与 torch 基线：80 张有分、**Δ=0.0000**、49 组、胜出/status 变化 0。
- **边界**：应用 Release `latest` 已是 v0.4.0；本文记录 engine 通道与本地运行时的验证，不替代应用 Release 的安装包校验。
