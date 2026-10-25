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

