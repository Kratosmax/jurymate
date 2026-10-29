# ONNX 迁移：已完成部分与剩余步骤

> 状态（2026-10-29 16:45）：模型导出、ONNX 分发、打包与安装验收已完成；双包更新已完成，engine 通道已发布并通过真实下载验证，待应用 v0.3.3。
> 所有实测数据见 `temp/onnx_spike_notes.md`（不入库）。

## 已完成

| 项 | 位置 | 验证结果 |
|---|---|---|
| NIMA ONNX 导出 | `temp/nima299.onnx`（207.3 MiB / 217,340,947 B） | 124/124 张，最大 \|Δ\| = 0.0000 |
| MUSIQ 横向导出 | `temp/musiq_1024x683.onnx`（103.9 MiB / 108,926,867 B） | 44/44 张，Δ ≤ 0.0001 |
| MUSIQ 纵向导出 | `temp/musiq_683x1024.onnx`（103.9 MiB / 108,926,867 B） | 80/80 张，Δ ≤ 0.0001 |
| 推理模块 | `backend/onnx_score.py` | 与 torch 整数分数完全一致 |
| 接线 | `backend/aesthetic.py` | 有 ONNX 走 ONNX，无则回退 torch |

## 导出方法（复现用）

```python
# NIMA：导出 base_model -> global_pool -> classifier
# 输入固定 299x299；预处理在外部做：短边缩 299 -> 中心裁 299 -> (x-0.5)/0.5
# dist 已是 softmax 概率，MOS = dist @ arange(1..10)
import torchvision.transforms.functional as _F
_F.resize = lambda img, size, **kw: img       # 输入已是 299，恒等
_F.center_crop = lambda img, size, **kw: img
torch.onnx.export(net, (x,), out, opset_version=17, dynamo=False)
```

MUSIQ 还要额外 patch：

```python
import pyiqa.data.multiscale_trans_util as mtu
def patched(image, h, w, longer_side_length):
    h, w = int(h.item()) if torch.is_tensor(h) else int(h), \
           int(w.item()) if torch.is_tensor(w) else int(w)
    ratio = longer_side_length / max(h, w)
    return F.interpolate(image, (round(h*ratio), round(w*ratio)),
                         mode="bicubic", align_corners=False), round(h*ratio), round(w*ratio)
mtu.resize_preserve_aspect_ratio = patched
```

## 执行步骤（2026-10-29 已全部完成）

### 1. 上传模型资产

三个 `.onnx` 传到新的 Release（或独立 `models` tag）：

```
nima.onnx            217 MB
musiq_land.onnx      109 MB
musiq_port.onnx      109 MB
```

### 2. 下载器支持 ONNX

`backend/models.py` 的模型目录增加三条，下载后落到 `<数据目录>/onnx/<名字>`。
注意现有的 NIMA/MUSIQ 条目是给 torch 用的，ONNX 是**另一套文件**，
建议沿用同一个「已就绪」判断：`<数据目录>/onnx/nima.onnx` 存在即视为可用。

### 3. 打包排除 torch

`scripts/dev-build.ps1` 与 `.github/workflows/release.yml` 的 PyInstaller 命令加：

```
--exclude-module torch --exclude-module torchvision --exclude-module pyiqa
```

同时 `requirements.txt` 里的 torch/torchvision/pyiqa 只保留给「导出 ONNX 用」的开发环境，
CI 构建不再需要它们——这一步是 CI 从 25 分钟降到 12 分钟的关键。

### 4. 验收

1. 清空 `D:\AppData\JuryMate\onnx`，确认应用仍能用 torch 跑（回退路径）
2. 放入三个 onnx，确认设置页显示就绪、扫描正常
3. 扫 124 张，对比新旧分数：单张 \|Δ\| ≤ 0.5、组内排序 0 翻转、胜出 0 变化
4. 检查安装包体积（目标 90~120 MB）

## 已知限制

- MUSIQ 的 ONNX 是**固定尺寸**导出的（1024×683 / 683×1024）。
  非 3:2 的图会被拉伸，分数会有偏差。当前素材全是 3:2，无损；
  将来支持其它比例需要按比例再导模型。
- NIMA 预处理已改为逐通道 float32 PIL 双线性，实测与 torchvision Tensor resize
  完全一致（80 张 max=0.0000），不再有 8-bit 量化误差。

## 完成记录（2026-10-29 14:00）

- **模型资产**：GitHub Release `models-v1`（非 Latest，`/releases/latest` 仍为 v0.3.2）
  - `nima.onnx` 217,340,947 B，SHA256 `6ad8153ac82215bc10fc3ff21b0450d08ada570fda16bb14d61425d09c4d5b16`
  - `musiq_land.onnx` 108,926,867 B，SHA256 `6c356ccef2dd867f324739f7043621808bde9fcf8bf62d97ac04a051d7714ced`
  - `musiq_port.onnx` 108,926,867 B，SHA256 `91d84cc5fa6c12fe36d6c98c1f005c934981330c41377a5c7c036bcac2fb0e25`
- **下载器**：`backend/models.py` 的 `nima` / `musiq` 改为 `<数据目录>/onnx/` 三个
  ONNX 文件；`musiq` 横竖两个文件都就绪才算安装完成，下载进度按整模型累计。
- **打包**：`scripts/dev-build.ps1` 与 `.github/workflows/release.yml` 排除
  `torch/torchvision/pyiqa`（另排除非运行时的 `pandas/pyarrow/numba/llvmlite`）；
  `app.py --doctor` 改查 `onnxruntime` / `onnx_score`。
- **体积**：sidecar 423.9 MB；NSIS 安装包 130,435,789 B（124.4 MiB，旧包 337.1 MiB）。
  略高于 90~120 MiB 目标上限，剩余大头是 cv2 137 MB、onnxruntime 63 MB、scipy 52 MB。
- **安装**：NSIS `NSIS_HOOK_PREINSTALL` 清理旧 `dist-backend\jury-backend`，
  覆盖安装后无 torch 等旧依赖残留。
- **124 张验收**：当前源码 torch 基线 vs 安装版 ONNX：80 张有分，最大 \|Δ\| =
  **0.0000**（整数分），49 组不变，status 变化 0，组内胜出变化 0，needs_pk 20=20。
- **回退**：源码环境临时数据根无 ONNX、有 torch 权重时，`score_batch` 正常走
  torch，6 张输出与 ONNX 完全一致。
- **回归**：`selftest.py` 18 项 ALL PASS（124 张，含 PK/召回/撤销/organize）。
