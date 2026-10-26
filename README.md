<div align="center">

# ⚖️ JuryMate

**AI 选片搭档 —— AI 陪审，你是法官**

*A local-first AI photo culling companion. The AI nominates, you judge.*

</div>

---

JuryMate 把一次拍摄中的几十上百张照片自动完成**技术初筛（失焦/闭眼/过曝/欠曝）、
连拍归组、默认选片与胜出图评分**；只在「AI 拿不准」的地方请你拍板，
落选片全部保留、随时复查召回。

> 全程本地推理，**零 LLM token**。AMD / NVIDIA / Apple Silicon / CPU 全硬件支持。

## 为什么是「陪审团」

大多数 AI 选片工具替你做决定。JuryMate 的哲学相反：
**AI 是陪审团，负责初筛和提名；你是法官，只裁决「AI 分差小」的组。**
每张落选片都保留着 AI 给出的淘汰理由，复查、改判、召回，由你说了算。

## 功能

- 📥 **导入**：拖入文件夹，RAW（ARW/CR3/NEF/RAF/DNG…）毫秒级提取内嵌预览
- 🔬 **技术初筛**：失焦（脸部区域锐度）、闭眼（MediaPipe EAR）、过曝/欠曝；
  无脸照片自动豁免"失焦"硬判（背景虚化的背影人像不会被误杀）
- 🗂️ **连拍归组**：拍摄时间 + 感知哈希双信号，同场景连拍归入同组
- 🏆 **默认选片**：组内综合分最高者暂定胜出，**只有前两名分差 <0.35 分才需要你**
- ⚔️ **PK 决策流**：全键盘 `←→` 选图 / `Enter` 定胜者 / `↑↓` 全要全不要 / `Z` 撤销
- 📊 **5 分制审美评分**：MUSIQ 无参考质量模型，胜出图自动打分
- 🔁 **复查召回**：落选片按原因分组、按分排序，一键召回（补评分）
- 📦 **一键归类**：winners/ 与 rejects/ 双目录，RAW+JPG+XMP 整组随迁
- 🚫 **隐私**：不上传任何照片，不调用任何云端 API

## 硬件支持

首次启动自动检测显卡并推荐推理后端：

| 硬件 | 后端 |
|---|---|
| NVIDIA（CUDA） | onnxruntime-gpu |
| AMD / Intel（Windows） | onnxruntime-directml |
| Apple Silicon | CoreML |
| 无独显 | CPU |

模型按需下载（约 440MB），**不会捆绑在任何安装包里**。下载源为各模型的原仓库，
默认直连；若网络不通，可在「网络设置」里按需开启 GitHub 镜像站或填入 HTTP 代理。

## 下载与安装

前往 [Releases](https://github.com/Kratosmax/jurymate/releases/latest) 下载：

| 文件 | 适用 |
|---|---|
| `JuryMate_x.y.z_x64-setup.exe` | 大多数用户。安装到系统，带卸载，支持自动更新 |
| `JuryMate_x.y.z_x64.zip` | 免安装便携版。解压即用 |

**只支持 Windows x64。** 本版不提供 Lite 包：后端是 Python sidecar
（Flask + insightface + pyiqa + MediaPipe），依赖约 2GB，无法形成有意义的轻量包；
安装包已内置运行时，不需要你单独装 Python。

**系统要求**：Windows 10 1809 及以上 / Windows 11。

### 首次使用

1. 启动后首页出现「模型就绪检查」，三个模型共约 440MB 需按需下载
   （闭眼检测 3.7MB / 人脸 330MB / 审美评分 104MB）。
2. 国内网络若失败，在「网络设置」勾选镜像站或填 HTTP 代理。
   **模型缺失时程序会明确报错引导下载，不会静默产出错误结果。**
3. 填入照片文件夹路径 → 开始整理。

### 自动更新

应用内「检查更新」会读取 GitHub Release 上的清单并升级。

- **更新器访问 GitHub 用的是系统代理**，不是应用内的「网络设置」。
  若检查更新失败，先确认系统代理已开启（或能直连 github.com）。
- 应用内「网络设置」里的代理与镜像只作用于**模型下载**，两者互不影响。
- 升级过程中请勿手动结束安装程序；若提示某文件被占用，
  在任务管理器结束 `jury-backend.exe` 后重试。

### 卸载

安装版通过「设置 → 应用」卸载。用户数据在 `%LOCALAPPDATA%/JuryMate/`（模型、日志、网络设置），
卸载不会自动删除；照片目录下的 `.jurymate/` 也需手动清理。两者删掉即彻底重置。

## 开发

```bash
npm install
npm run tauri dev      # 桌面开发模式（需手动另起 backend，见下）
```

Python 后端（sidecar）：

```bash
cd backend
pip install -r ../requirements.txt   # 需要 Python 3.11
python app.py --port 5090 --no-browser
```

> 直接跑 `backend/app.py` 时，若仓库根有 `dist/`（`npm run build` 产物），
> 浏览器访问 <http://127.0.0.1:5090> 即可看到界面；没有 `dist/` 时 Flask 只提供 API。

### 数据目录

运行时产物统一在 `%LOCALAPPDATA%/JuryMate/`（其他平台回落 `~/.cache/JuryMate/`）：
日志、网络设置、以及按需下载的模型权重。照片目录下另有一个 `.jurymate/`
存该目录的整理进度（`state.json` / 缩略图），删掉它即重置该目录。

## 自行编译

工具链：Node.js 20+、Python 3.11、Rust stable（Tauri 2 要求）、
[WebView2](https://developer.microsoft.com/microsoft-edge/webview2/)（Win11 自带）。

```bash
# 1. 前端依赖与构建
npm install
npm run build                 # vue-tsc 类型检查 + vite build → dist/

# 2. 后端依赖
cd backend
pip install -r ../requirements.txt
cd ..

# 3. 打 sidecar（生成 dist-backend/jury-backend.exe，约 479MB）
pip install pyinstaller
cd build-backend && pyinstaller --name jury-backend --onefile ../backend/app.py && cd ..

# 4. 打安装包（需签名环境变量，见下）
npm run tauri build
```

产物位置：`src-tauri/target/release/bundle/nsis/` 与 `bundle/msi/`。

**测试**：

```bash
python selftest.py      # 端到端 18 项（端口 5091）
python verify_eyes.py   # 闭眼检测正例
npm run build           # 前端类型检查
```

样图目录用 `JURYMATE_TEST_DIR` / `JURYMATE_EYE_DIR` 指定，
解释器用 `JURYMATE_PY` 指定；未设置时取 `<工作区>/temp/jurymate-*` 与当前解释器。

## 许可证与致谢

- 本项目代码：MIT（见 [LICENSE](LICENSE)）
- 灵感与交互借鉴 [pianke（片刻）](https://github.com/woshidandan/pianke)（MIT）
- 人脸模型 [buffalo_l](https://github.com/deepinsight/insightface)（insightface model zoo）为**非商业许可**，
  因此本项目的安装包**不包含任何模型权重**，模型由用户按需下载、自行承担其许可约束
- [MediaPipe](https://developers.google.com/mediapipe)（Apache-2.0）、
  [pyiqa](https://github.com/chaofengc/IQA-PyTorch)（Apache-2.0）、
  [rawpy](https://github.com/letmaik/rawpy)（LGPL）

## AI 继续开发

新会话/新设备接手开发时，**第一读取入口是根目录的 [`CODEX_PROGRESS.md`](CODEX_PROGRESS.md)**：
先完整读取并运行其中的「接手校准」命令校准现状，再按需读源码。
每次可交付改动收尾时，必须同步更新该文件（最后校准时间、提交基线、
未验证项、下一步），并删除已失效状态。

**发布授权边界**：提交、推送、打标签、创建 Release 是四个独立动作，需分别授权。
正式签名私钥在本地 `.keys/`（已 gitignore），不得入库、入日志、入产物或 Release；
CI 使用 GitHub Secret `TAURI_SIGNING_PRIVATE_KEY`。
