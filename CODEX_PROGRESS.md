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

## 1.1 数据目录（唯一根，且可自定义）

所有运行时产物都由 `backend/paths.py` 统一派生。**根目录可以由用户改**：

优先序（高到低）：
1. 环境变量 `JURYMATE_DATA_DIR`
2. `%LOCALAPPDATA%/JuryMate/paths.json` 的 `data_root`（界面「存储位置」写入）
3. 默认 `%LOCALAPPDATA%/JuryMate`

配置文件本身固定在默认位置——它是"用户选了哪个根"的唯一来源，
不能跟着一起搬，否则改完就找不到了。

| 子路径 | 内容 |
|---|---|
| `logs/backend.log` | 滚动日志（5×2MB） |
| `net_settings.json` | 网络策略，**每次按当前根重新解析**（不能存成常量） |
| `face_landmarker.task` | 闭眼检测模型（3.7MB） |
| `insightface/models/buffalo_l/` | 人脸检测/识别（340MB，非商业许可，按需下载） |
| `torch/hub/pyiqa/` | MUSIQ 权重（104MB） |
| `tmp/` | **PyInstaller 的 `_MEI*` 解压目标**（每次约 1.1GB） |
| `settings.json` | Rust 侧 debug 开关（`app_config_dir`） |

`paths.py` 导入时：
- 强制设 `TORCH_HOME=<数据目录>/torch`，所以**任何模块都必须先 `import paths`
  再 import pyiqa/torch**
- 仅在 `sys.frozen`（打包版）下把 `TEMP`/`TMP` 指向 `<数据目录>/tmp`；
  源码直跑时不动开发者环境

Rust 壳（`lib.rs` 的 `data_root()`）用同样的优先序解析，并在 spawn sidecar
时注入 `TEMP`——这是关键：PyInstaller 在启动瞬间就解压，只有父进程提前
设好环境变量才能改变落点。

照片目录下的会话状态在 **`.jurymate/`**（state.json / thumbs / recall_stats）。
缩略图刻意跟随照片目录而非数据根：搬移照片时缓存一起走，不留孤儿文件。
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

## 2. 当前快照（最后核验 2026-10-29 12:30）

- 本地版本：**0.3.2**（`tauri.conf.json` / `package.json` / `Cargo.toml` 一致）
- 已发布版本：**v0.3.2**（GitHub Release，5 个资产）
- 分支 master，**有 7 个提交未推送**（见 §9），工作区干净
- **注意版本撞车**：本地 0.3.2 含「网络设置重做/持久化/onedir/色彩」等修复，
  线上 v0.3.2 不含。下一次发版用 **0.3.3**。

### 本轮（2026-10-29）已完成

| 项 | 结果 | 证据 |
|---|---|---|
| sidecar `--onefile` → `--onedir` | 冷启动 **60~90s → 7.7s** | 装包后实测两次 |
| 安装包 | **494 MB → 337 MB** | 构建产物 |
| 色彩整形（A 方案） | **11~19 ms/张** | `backend/grade.py`，缩略图与分析共用 |
| NIMA → ONNX | 124/124 张，最大 Δ = **0.0000** | `temp/onnx_spike_notes.md` |
| MUSIQ → ONNX | 124/124 张，Δ ≤ **0.0001** | 同上 |
| ONNX 推理接线 | 两模式整数分数与 torch **差 0** | `backend/onnx_score.py` + `aesthetic.py` |

### 仓库体积约束

历史曾误提交 457MB sidecar（`5bff22e`、`13f2dff`），`.git` 一度 2.9GB，
已用 `filter-branch` 清除。**`dist-backend/`、`build-backend/`、`temp/` 不入库**。
本地 `src-tauri/target` 约 21GB，在 F 盘，勿删（删了要重编 15 分钟）。

## 3. 当前待办

1. **【下一步·必做】按 `docs/ONNX_MIGRATION.md` 完成 ONNX 分发与打包**：
   上传 3 个 `.onnx` 资产 → `models.py` 加下载条目 → PyInstaller 排除
   `torch/torchvision/pyiqa` → 打包 → 扫 124 张验收。四步必须一次做完。
2. 双包更新（界面包 ~15MB / 引擎包 ~150MB 分离）——ONNX 完成后再做。
3. 【待用户】正面人像含闭眼样本 → EAR 阈值校准（用户未确认重视此项，优先级低）。

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
| 前端五页（总览/决策/胜出/复查/设置） | `src/views/*.vue` + `src/App.vue` |
| 设置页（更新/存储位置/网络） | `src/views/SettingsView.vue` |
| 安装器中文文案（27 条） | `src-tauri/nsis/SimpChinese.nsh` |
| 开发构建（临时文件钉在项目内） | `scripts/dev-build.ps1` |
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

## 6.5 开发环境铁律（防止占满系统盘）

本项目的构建与测试会产生大量临时文件，**必须全部落在项目内**，
不许写系统盘。历史事故：作者机器上累积了 27 个 `_MEI*` 目录共
27.45GB，把 C 盘挤到只剩 0.1GB。

三类大头与对应做法：

| 来源 | 单次体积 | 做法 |
|---|---|---|
| PyInstaller 运行时解压 `_MEI*` | 约 1.1GB | 构建/测试前把 `C:\Users\11726\AppData\Local\Temp` 指到 `<repo>\temp` |
| WiX 打 MSI 的中间文件 | 约 3.8GB | 同上（`npm run tauri build` 继承该变量） |
| 应用内升级下载的安装包 | 457MB/次 | 同上，或用后即删 |

**统一用 `scripts/dev-build.ps1`**，它已把这些变量设好：

```powershell
pwsh -File scripts/dev-build.ps1              # 完整构建
pwsh -File scripts/dev-build.ps1 -FrontOnly   # 只构建前端
pwsh -File scripts/dev-build.ps1 -Clean       # 构建完顺便清理临时文件
```

手工构建时至少要：

```powershell
C:\Users\11726\AppData\Local\Temp = "F:\git\jurymate\temp"; C:\Users\11726\AppData\Local\Temp = C:\Users\11726\AppData\Local\Temp
```

其它注意：

- `temp/` 已在 .gitignore，可以放心往里写
- 每次验证完顺手清 `_MEI*`，不要等积起来再处理
- 用户侧已由代码解决：Rust 壳启动 sidecar 时把 `TEMP` 注入为
  数据根下的 `tmp`，所以最终用户的 `_MEI*` 不会再落在系统盘
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


## 9. 会话交接（2026-10-29 12:30）

### 未推送的提交（7 个，master）

```
2253e15 docs: ONNX 迁移交接文档
261946f feat(score): aesthetic 接入 ONNX 分支（有模型走 ONNX，否则回退 torch）
6ea564e feat(score): 新增 ONNX 推理后端（未接线）
f89d92a perf(packaging): sidecar 改 onedir，冷启动 60~90s -> 7.7s；新增色彩整形
c58e9a3 fix(settings): 评分模式/权重改为应用级持久化
8f842a7 feat(settings): 网络设置重做，保存即生效并当场可见
11f52ec build: npm/pip 缓存改道到项目 temp，构建不再写 C 盘
```

### 下一会话从这里开始

1. 先跑 §1 的接手校准命令
2. 读 `docs/ONNX_MIGRATION.md`（含可复现的导出脚本）
3. 读 `temp/onnx_spike_notes.md`（全部实测数据与踩坑记录，注意 temp 不入库但本机还在）
4. 按 `docs/ONNX_MIGRATION.md` 的「剩余步骤 1→4」一次做完

### 现成的 ONNX 产物（本机，未入库）

| 文件 | 路径 |
|---|---|
| NIMA 299 | `F:\git\jurymate\temp\nima299.onnx`（207MB） |
| MUSIQ 横 | `F:\git\jurymate\temp\musiq_1024x683.onnx`（104MB） |
| MUSIQ 竖 | `F:\git\jurymate\temp\musiq_683x1024.onnx`（104MB） |
| 已就位副本 | `D:\AppData\JuryMate\onnx\{nima,musiq_land,musiq_port}.onnx` |

> 注意：`temp/` 有多个中间产物（nima1/nima224/nima_clean/musiq），
> 只有上表三个是最终版；其余是失败或早期试验，可删。

### 踩过的坑（不要再犯）

1. **NIMA 输入是 299×299，不是 224** —— 按 224 导出会让分数差到 1.3
2. **NIMA 内部 resize 用双线性** —— 用 LANCZOS 会差 0.498；`onnx_score.py` 已用 BILINEAR
3. **pyiqa 的 `center_crop` 追踪时会崩**（对 Tensor 调 round）——导出期把 resize/center_crop 换成恒等
4. **MUSIQ 的 `resize_preserve_aspect_ratio` 同样问题** —— 把 h/w 先 int() 再 round
5. **MUSIQ 是变长输入** —— 已按实际素材导出横竖两版（1024×683 / 683×1024），非 3:2 的图会被拉伸
6. **`--onedir` 后安装目录多一层** `dist-backend/jury-backend/jury-backend.exe`
7. **PowerShell 5.1 的 `-Encoding UTF8` 写文件带 BOM**，会让 package.json/JSON 解析失败；必须用 `UTF8Encoding($false)`

### 用户偏好与边界（重要）

- 改代码前先给 why + before/after，同意后再改（用户明确要求过）
- 用户不接受的：没证据下结论、把没验证的东西说成"已验证"
- 用户对「闭眼检测」**没有**表达过重视（此前我误称"用户重视闭眼"，已更正）
- 发版必须用户明确同意；本次用户选择「不发」，只记录进度

## 9.1 路径与校验（新会话务必先核对这一节）

### 三个"根"分别是什么

| 名称 | 绝对路径 | 怎么来的 |
|---|---|---|
| 仓库根 | `F:\git\jurymate` | 固定，git 仓库所在 |
| 数据根 | `D:\AppData\JuryMate` | `%LOCALAPPDATA%\JuryMate\paths.json` 里的 `data_root` |
| 构建临时 | `F:\git\jurymate\temp` | `dev-build.ps1` 把 `TEMP/TMP` 指到这里；探针产物也放这里 |

**ONNX 模型不是随便放哪都能读到。** 代码里的查找规则是固定的：

```python
# backend/aesthetic.py
def _onnx_dir():
    return paths.data_dir() / "onnx"      # = D:\AppData\JuryMate\onnx
```

`paths.data_dir()` 的优先序（见 §1.1）：
1. 环境变量 `JURYMATE_DATA_DIR`（若设置，以它为准）
2. `%LOCALAPPDATA%\JuryMate\paths.json` 的 `data_root` ← 当前就是这条
3. `%LOCALAPPDATA%\JuryMate`

所以**文件名和位置必须严格是**：

```
D:\AppData\JuryMate\onnx\nima.onnx              ← 审美优先（NIMA）
D:\AppData\JuryMate\onnx\musiq_land.onnx        ← 画质优先（横向）
D:\AppData\JuryMate\onnx\musiq_port.onnx        ← 画质优先（纵向）
```

名字写错（例如 `nima299.onnx`）= 代码找不到 = 静默回退 torch，不会报错。

### 一份文件两个位置（当前状态）

`temp\` 里是**源文件**，`D:\AppData\JuryMate\onnx\` 里是**运行时副本**。
两边内容必须完全一致——用哈希核对：

| 文件 | 大小 | SHA256 |
|---|---|---|
| `temp\nima299.onnx` = `D:\...\onnx\nima.onnx` | 207.3 MB | `6ad8153ac82215bc10fc3ff21b0450d08ada570fda16bb14d61425d09c4d5b16` |
| `temp\musiq_1024x683.onnx` = `D:\...\onnx\musiq_land.onnx` | 103.9 MB | `6c356ccef2dd867f324739f7043621808bde9fcf8bf62d97ac04a051d7714ced` |
| `temp\musiq_683x1024.onnx` = `D:\...\onnx\musiq_port.onnx` | 103.9 MB | `91d84cc5fa6c12fe36d6c98c1f005c934981330c41377a5c7c036bcac2fb0e25` |

### 核对命令（PowerShell，可直接跑）

```powershell
# 1) 数据根是不是这个
Get-Content "$env:LOCALAPPDATA\JuryMate\paths.json"

# 2) 三个文件是否就位、大小对不对
Get-ChildItem "D:\AppData\JuryMate\onnx" |
  Select-Object Name, Length, LastWriteTime

# 3) 哈希是否与上表一致（最关键的一步）
Get-ChildItem "D:\AppData\JuryMate\onnx\*.onnx" | ForEach-Object {
  "{0}  {1}" -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower(), $_.Name
}
```

预期输出三行，分别对应上表三个哈希。**任何一个对不上，就说明文件被替换或复制不完整，
不要继续后续步骤。**

### 哪些文件不要用

`temp\` 里还有早期试验产物，**全部作废**：

| 文件 | 状态 |
|---|---|
| `nima1.onnx` / `nima224.onnx` / `nima_clean.onnx` | 错误输入尺寸（224），分数差 1.3 |
| `musiq.onnx` | 首次导出，未按横竖拆分 |
| `musiq_1024x683.onnx` / `musiq_683x1024.onnx` | ✅ 正确，是最终版 |

### 代码侧怎么确认走的是 ONNX 而不是 torch

`backend/aesthetic.py` 的逻辑：`_onnx_models(mode)` 检查文件存在 → 存在就走
`_score_batch_onnx`，不存在或抛异常就 `log.warning("...回退 torch")` 并走
`_score_batch_torch`。

所以验证方法：
```powershell
# 看后端日志里有没有这行（有 = 走了 ONNX 但失败了，需要排查）
Select-String "D:\AppData\JuryMate\logs\backend.log" -Pattern "ONNX|回退 torch"
```
**没有这行** = 正常走 ONNX（或根本没触发回退）。有这行 = 检查上面三个文件。