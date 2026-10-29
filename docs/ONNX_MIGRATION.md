# ONNX 迁移：已完成部分与剩余步骤

> 状态（2026-10-29）：模型导出与推理接线已完成并验证；分发与打包未做。
> 所有实测数据见 `temp/onnx_spike_notes.md`（不入库）。

## 已完成

| 项 | 位置 | 验证结果 |
|---|---|---|
| NIMA ONNX 导出 | `temp/nima299.onnx`（217 MB） | 124/124 张，最大 \|Δ\| = 0.0000 |
| MUSIQ 横向导出 | `temp/musiq_1024x683.onnx`（109 MB） | 44/44 张，Δ ≤ 0.0001 |
| MUSIQ 纵向导出 | `temp/musiq_683x1024.onnx`（109 MB） | 80/80 张，Δ ≤ 0.0001 |
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

## 剩余步骤（必须一次做完）

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
- NIMA 走 PIL 双线性 vs torchvision 双线性，实测差 0.133（百分制），在门禁内。
  若要更精确，可用 numpy 复刻 torchvision 的采样公式。
