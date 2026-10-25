# JuryMate 交接文档（Tauri 发布版）

> cullmate 的继承者：Tauri 桌面应用 + Vue3 前端 + PyInstaller sidecar。
> 功能/验证细节见 cullmate\HANDOFF.md（同一管线的原型期记录）。



## 安装包体积实验结论（2026-10-25，已定论勿重试）

- 455MB 构成：torch+torchvision ~200MB（只为 MUSIQ 评分）为最大头，
  onnxruntime-directml ~100MB、cv2+insightface ~150MB
- NSIS LZMA 无增益：onefile 内部已 zlib 压缩，高熵数据再压缩无效（实测
  455.08MiB 分毫不变）
- **MUSIQ→ONNX 导出被证伪**：multi-scale patch 采样是数据依赖动态控制流
  （round(h*ratio) 在 tensor 上），torch.onnx 导出直接 TypeError；固定尺寸
  导出可行但等比变拉伸、分数分布偏移、无标注数据重校准——**不换**，
  体积是 torch 依赖的结构性成本
- 未来重开条件：pyiqa 官方 ONNX/量化权重，或拿到标注数据可重校准



## PinNote 逻辑移植 + 自动更新（2026-10-25 晚）

- **模型下载器双通道**（PinNote UpdateNetworkSettings 逻辑移植）：
  GitHub 镜像前缀列表（默认 ghfast.top/gh-proxy.com，优先级降级）+ HTTP 代理 +
  直连兜底；net_settings.json 持久化；前端设置面板（镜像增删/代理）
- **自动更新**：tauri-plugin-updater（对应 PinNote 独立 Updater 的角色）——
  minisign 签名校验（密钥对已生成 .keys/jurymate.key[.pub]，**私钥已 gitignore**）、
  启动静默检查 + 手动升级 UI；端点占位 GitHub Releases latest.json
- 三模型全部转 manual 模式（可走镜像下载）；musiq 源切 hf-mirror
- bundle 产物带 .sig 签名文件；发布流程：签名环境变量
  TAURI_SIGNING_PRIVATE_KEY + latest.json（tauri build 自动产出 updater 产物）
- 首次 build 的坑：`plugins`（复数）字段名；signer generate 参数经 npx 直传
  （`npm run` 的 `--` 分隔符会吞参）；pubkey 必须是真实 minisign 公钥


## 装机实测轮（2026-10-25 晚，用户手动安装+反馈驱动）

- **CORS preflight 失败（用户实测抓到的发布级 bug）**：点"开始整理"只发
  OPTIONS 不发 POST——打包版 sidecar 的 Flask 自动 OPTIONS 响应缺 CORS 头
  （浏览器开发模式同源代理掩盖）。修复：OPTIONS before_request 短路 +
  after_request 全套头（含 Max-Age）。**流程教训：改后端代码后必须重打
  sidecar/安装包再交用户验收**（本次曾让用户装了 CORS 修复前的包）
- **正式版无控制台**（用户需求）：sidecar 打双 exe（默认 --noconsole /
  debug 版带控制台），Rust 按 %APPCONFIG%/settings.json 的 debug 开关选择；
  后端日志落 %LOCALAPPDATA%/JuryMate/logs/backend.log（滚动 5×2MB）
- identifier 改 com.jurymate.desktop（.app 结尾与 macOS 冲突警告）
- **NSIS /S 静默安装挂起**仍未解（UI 模式正常）：现象 /S 后进程挂起无产物、
  且安装路径是 %LOCALAPPDATA%/com.jurymate.desktop（identifier 命名）——
  待 NSIS /LOG 深挖；interim 方案：用户手动 UI 安装（正常）或绿色版
- **装机验证遗留**：CORS 修复后的安装包需要用户重装+复测"开始整理"
