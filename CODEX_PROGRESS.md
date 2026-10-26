# CODEX_PROGRESS — JuryMate

> AI 跨会话接续索引。新会话先完整读本文件并运行「接手校准」命令校准，
> 再按需读源码。本文件是导航索引，不是 Git/源码/测试的替代真相。

## 1. 接手校准（只读命令）

```bash
git status --short && git branch --show-current && git log --oneline -5
npm run build                 # 前端构建（vue-tsc + vite）
python -m py_compile backend/*.py
python selftest.py            # 全量回归 18 项（端口 5091，勿与 5090 服务并行）
python verify_eyes.py         # 闭眼正例回归（需样图目录与模型缓存）
```

解释器：`JURYMATE_PY` 环境变量 → 本仓库 `.venv` → 当前解释器。
样图目录：`JURYMATE_TEST_DIR`（selftest，需 9 张 ARW）、
`JURYMATE_EYE_DIR`（verify_eyes）；未设时取 `<工作区>/temp/jurymate-*`。

## 1.1 数据目录（唯一根）

所有运行时产物都在 **`%LOCALAPPDATA%/JuryMate/`**，由 `backend/paths.py` 统一派生：

| 子路径 | 内容 |
|---|---|
| `logs/backend.log` | 滚动日志（5×2MB） |
| `net_settings.json` | 网络策略：`use_mirror` / `github_mirrors` / `http_proxy` |
| `face_landmarker.task` | 闭眼检测模型（3.7MB） |
| `insightface/models/buffalo_l/` | 人脸检测/识别（340MB，非商业许可，按需下载） |
| `torch/hub/pyiqa/` | MUSIQ 权重（104MB） |
| `settings.json` | Rust 侧 debug 开关（`app_config_dir`） |

`paths.py` 导入时强制设 `TORCH_HOME=<数据目录>/torch`，因此 pyiqa 的权重位置
由它决定——**任何模块都必须先 `import paths` 再 import pyiqa/torch**。

照片目录下的会话状态在 **`.jurymate/`**（state.json / thumbs / recall_stats.json），
用户删掉它 = 重置该目录进度。

## 1.2 网络策略（全部由用户决定）

`net_settings.json` 三个字段，默认全关：

| 字段 | 默认 | 作用 |
|---|---|---|
| `use_mirror` | `false` | 开启后 GitHub 来源的模型才走镜像前缀，失败回落直连 |
| `github_mirrors` | 两条常见镜像 | 镜像前缀表，仅 `use_mirror=true` 时生效 |
| `http_proxy` | 空 | 独立生效，与镜像开关无关；填了就用于源站下载 |

代理会同时写入进程环境变量（`HTTP_PROXY`/`HTTPS_PROXY`），因为
insightface（`requests`）和 torch.hub（`urlopen`）的**自动下载只认环境变量**——
不写这一步，用户填的代理对"库自动下载"完全无效。后端启动时即应用一次
（`app.py` 顶部），保存设置时再应用一次。

三条模型都从各自**原仓库**下载，不经过任何第三方选片项目：
MediaPipe 官方 storage / insightface 官方 GitHub release / pyiqa 作者的 HuggingFace。

## 1.3 推理后端（EP）选择

EP 优先级**不写死**，由 `hardware.py` 按显卡厂商决定，再与 onnxruntime
实际可用的 EP 求交集（没装的 EP 传进去会直接报错）：

| 显卡厂商 | 首选 → 次选 |
|---|---|
| NVIDIA | CUDA → DML → CoreML |
| AMD / Intel | DML → CUDA → CoreML |
| Apple | CoreML → DML → CUDA |
| 识别不出 | CUDA → DML → CoreML |

CPU 永远兜底。首选 EP 没装时自动降级（N 卡只装了 DML 包仍会走 DML），
`describe_backend()` 会在提示里补一句该装哪个包。
`models.detect()` 只转述 `hardware` 的结论，不再自己判断——避免出现
"界面提示推荐 X、实际跑 Y"的分裂。

## 2. 当前快照（最后核验 2026-10-26 21:xx）

- 版本：**0.1.3**（版本源 `src-tauri/tauri.conf.json`；`package.json`、
  `Cargo.toml`、`Cargo.lock` 必须同步，CI 会校验与标签一致）
- 分支：master；remote：`origin` → https://github.com/Kratosmax/jurymate
- 仓库：**PUBLIC**，标签已到 `v0.1.3`
- 线上状态：**v0.1.3 已发布**
  https://github.com/Kratosmax/jurymate/releases/tag/v0.1.3
  - CI run 40324214220 success；7 个资产齐全（Setup/ZIP/MSI + 2×sig +
    latest.json + SHA256SUMS.txt）
  - latest.json: version=0.1.3，载体为 NSIS setup.exe，签名 key id
    fad7706de1435f20 与 tauri.conf.json 公钥一致（已核验）
  - releases/latest 实际指向 v0.1.3
- 自动更新：0.1.2 起文件锁问题已修，0.1.2 及以后可应用内升级；
  **0.1.0/0.1.1 必须手动装一次**（历史遗留，服务端无法修复）
- **待用户**：提供 2-3 张正面人像含闭眼样本（闭眼阈值真数据校准）
### 发布方式

- 标签触发：推送 `v*` 标签 → `.github/workflows/release.yml` 自动构建并发布
- 版本三处一致（tauri.conf.json / package.json / Cargo.toml）+ 标签一致，
  任一不符 CI 直接失败
- 签名私钥在 GitHub Secret `TAURI_SIGNING_PRIVATE_KEY`（本地副本在 `.keys/`，
  已 gitignore）。**私钥不入库、不入日志、不入产物**
- 资产矩阵：Full Setup（NSIS）+ Full ZIP（便携）。**不提供 Lite**：
  后端是 Python sidecar（Flask + insightface + pyiqa + MediaPipe），依赖约
  2GB 且需 Python 3.11，无法形成有意义的轻量包——此结论已写入 README 与
  Release Notes，不是静默省略
- 自动更新清单 `latest.json` 只收 NSIS 安装包（Tauri 只给它签名；手工压的
  ZIP 无 `.sig`，写进清单会导致客户端验签失败）

### 仓库体积约束

历史曾误提交 457MB 的 sidecar exe（`5bff22e`、`13f2dff`），`.git` 一度达
2.9GB。已用 `filter-branch` 清除并 gc，现 `.git` 约 0.4MB。
**`dist-backend/`、`build-backend/`、`temp/` 永远不得入库**（已在 .gitignore）。

## 3. 当前待办

1. 【待用户】正面人像含闭眼样本 → EAR 阈值真数据校准
2. debug 后端（`jury-backend-debug.exe`）未随包分发，`bundle.resources`
   只映射正式版；lib.rs 已做"debug 缺失回退正式版"，若要真正用 debug
   模式需补资源映射
3. `%TEMP%\_MEI*` 残留：onefile 每次启动解压约 1.1GB，被强杀时不清理。
   实测累积 27 个 / 27.45GB 挤爆系统盘（C 盘只剩 0.1GB）。
   **尝试过的方案失败了**：在 `paths.py` 加启动时 `_cleanup_mei_dirs()`，
   导致后端起不来（推测误删了自身正在使用的 `_MEIPASS`），已回退。
   根治方向：改 `--onedir` 打包，或让 Rust 壳在退出时清理；
   不要用运行中的 Python 进程去猜哪些 `_MEI` 可删
4. P1：胜出清单联动 LightCraft 调色（CSV 已有，缺导入自动化）；
   recall_stats 阈值自校准；千张并行（信号/评分多进程）
5. P2：EXIF 相机水印；ArcFace 同人归组

没有其他已授权未完成事项；上述之外等待用户需求，不自行扩大范围。

## 4. 关键入口

| 职责 | 文件 |
|---|---|
| API 服务（Flask，端口 5090，CORS） | `backend/app.py` |
| 管线编排（扫描/信号/人脸/评分/分组/决策） | `backend/pipeline.py` |
| 人脸双源（insightface 检测 + MediaPipe EAR） | `backend/faces.py` |
| MUSIQ 审美评分（分段映射 5 分制） | `backend/aesthetic.py` |
| 连拍分组（burst + phash） | `backend/group.py` |
| 锐度/曝光信号与阈值 | `backend/signals.py` |
| 模型清单/下载器（镜像与代理策略） | `backend/models.py` |
| 显卡检测与 EP 优先级 | `backend/hardware.py` |
| 数据目录唯一根（含 TORCH_HOME 钉定） | `backend/paths.py` |
| 前端四页（总览/决策/胜出/复查） | `src/views/*.vue` + `src/App.vue` |
| API 客户端（全站唯一 fetch 出口） | `src/api.ts` |
| Rust 壳（sidecar 生命周期 + updater） | `src-tauri/src/lib.rs` |
| 回归测试 | `selftest.py`（18 项）、`verify_eyes.py`（闭眼 4 正例） |
| 风格模板（四家族 + 已确认预设） | `styles/`——**不在本仓库**，路径待核 |

## 5. 不得破坏的约束

- **评委制**：AI 只提名与初筛；分差 < pk_gap 的组必须人裁决；
  精修与最终决策永远由用户触发
- **状态源**：磁盘 state.json 是唯一事实源；用户删它 = 重置，
  增量前必须先从磁盘同步内存（幽灵召回 bug 的根因，勿回退）
- **无脸豁免**：无脸图不做"失焦"硬判（背影人像误杀教训）；
  tiny 脸 <40px 丢弃；FaceMesh 补录脸 tiny 界 56px
- **闭眼检测走 MediaPipe EAR**（阈值 0.18/0.25），不用 insightface
  landmark 算 EAR（网图实测漂回睁眼先验，漏检 3/4）
- **许可红线**：insightface buffalo_l 权重非商业许可——不随安装包
  分发，由用户按需下载
- **签名私钥**：本地 `.keys/`（已 gitignore），不入库/不入日志/不入产物
- **全本地零 token**：不引入任何云端 API 依赖
- **状态透明**：人工召回的卡片必须带角标与 AI 胜出区分

## 6. 验证矩阵

| 层 | 命令 | 预期 |
|---|---|---|
| 普通逻辑 | `python selftest.py` | 18 项全 PASS（端口 5091） |
| 普通逻辑 | `python verify_eyes.py` | 闭眼 4/4 + 远景 1 豁免 |
| 后端编译 | `python -m py_compile backend/*.py` | 无错误 |
| 前端 | `npx vue-tsc --noEmit && npm run build` | 零类型错误，构建成功 |
| 打包 | `npm run tauri build`（需签名环境变量与代理） | NSIS+MSI+updater 签名 |
| 发布 | 未建立 | GitHub 仓库/Release 流程待建 |

## 7. 协作约定

- 自然语言一律中文；回复言简意赅；证据优先（结论附可复现命令与落盘路径）
- 评委制流程：家族级评审（12 家族菜单）→ 细化级评审（12 单轴变体，
  **用户必须看到原图级画质**，JPEG 4:4:4）→ LightCraft 精修（单变量 before/after）
- 目录边界：仓库与开发产物在工作目录；临时产物在 temp；
  最终版交付物放用户指定位置；**用户安装目录与用户数据目录不可写入**
- 依赖解释器可用 `JURYMATE_PY` 指定；本机开发环境为姊妹仓库 cullmate 的 venv
  （该 venv 装好了全部依赖，仅为开发便利，不是运行时依赖）
- 网络策略由用户决定：镜像站默认关闭、代理独立可选，不写死任何加速方案
- 「压缩版 agent 自查 / 高清版用户评审」双版本交付铁律
- GUI 验证用 browser-use 黑盒测试（视觉证据 + DOM 双确认）

## 8. 维护规则

- 每轮可交付改动结束：更新「当前快照」的时间/基线提交/工作内容/未验证项
- 只记当前状态与稳定事实，不记逐轮聊天记录（历史交给 Git）
- 测试记录必须含命令与结果；没运行就写未运行
- 架构、入口、安全边界、验证命令变化时同步更新对应章节
- 与 Git/源码/用户指示冲突时，以更新的可验证证据为准并修正本文件


## 9. 会话交接（2026-10-26 21:xx）

### 当前状态

- **线上最新版本：v0.1.3**（已发布，资产 7 项齐全，签名 key id 匹配）
- **master 最新提交：82dc06**（0.1.3 的发布提交是 36e3f47）
- **工作区干净**

### 本会话完成的三项

| 版本 / 提交 | 内容 |
|---|---|
| v0.1.1 | 更新进度显示、版本号常驻、前后端版本一致性检查、首页崩溃修复 |
| v0.1.2 (d06fe8) | **修复 sidecar 文件锁导致自动更新失败**——更新器报 "Error opening file for writing" |
| v0.1.3 (36e3f47) | **更新检查接入应用内代理**；修复代理读取失败被永久缓存为空 |
| 82dc06（未发版） | **禁止 insightface/pyiqa 自行联网下载模型**，缺失时明确报错引导用户走应用内下载器 |

82dc06 已在 master 但未包含在任何 Release 里，下一个版本会自动带上。

### 更新器代理：验证方法与结论

改动的正确性用**客观证据**确认，不是看界面有没有反应：

1. 起一个记录请求的 TCP 转发代理（监听 10809）
2. 应用「网络设置」填 http://127.0.0.1:10809，**系统代理保持关闭**
3. 点「检查更新」，代理端日志出现：
   CONNECT github.com:443、CONNECT release-assets.githubusercontent.com:443
4. 反向对照：把代理改成死端口 59999，检查更新报
   error sending request for url (...)，证明参数确实被使用而非被忽略

### 踩过的坑（务必先读）

1. **本地 
pm run tauri build 不会重建 sidecar**，会复用 dist-backend/ 里的旧 exe，
   得到"新后端 + 旧前端"。打包前必须手动跑 PyInstaller。
   且 PyInstaller 见到产物存在会跳过，要先 Remove-Item dist-backend\jury-backend.exe。
2. **%TEMP%\_MEI* 会吃光 C 盘**。onefile 每次启动解压约 1.1GB，被强杀时不清理。
   实测累积 27 个共 27.45GB。开发期间频繁重启应用时，每隔一段时间清一次；
   排查异常的 ENOSPC 报错时先看这里。**不要试图用运行中的 Python 进程去清理**
   （试过，导致后端起不来，已回退，见待办 #3）。
3. **Tauri 窗口标题改不动 document.title**。前端调试想输出信息，写进页面可见区域
   （例如侧栏小字），不要用 document.title。
4. **UIA 点击坐标要用 get_app_state(mode='text') 拿到的 rect**，别按截图估。
   截图是缩放后的（1568×1026），实际窗口坐标可能是 1702×1114。
5. **PowerShell 控制台中文乱码**是显示问题，文件本身正常。判断内容用 ead 工具
   或 [IO.File]::ReadAllText(..., UTF8)。
6. **git 历史不能带大文件**：dist-backend/、uild-backend/、	emp/ 已在 .gitignore。
   历史曾因 457MB exe 膨胀到 2.9GB，用 ilter-branch 清理过。

### 剩余待办

见 §3。其中 #3（_MEI 残留根治）是有明确失败记录的，重试前先读那条记录。