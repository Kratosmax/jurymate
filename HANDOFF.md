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

