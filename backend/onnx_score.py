"""ONNX 推理后端：替代 pyiqa + torch 做评分。

为什么要它：torch 占了安装包约 250MB（压缩后），而安装包里模型本来就不带
（按需下载）。把推理换成 onnxruntime 后，torch/torchvision/pyiqa 都可以不打包。

已用 124 张实拍 ARW 回归验证（详见 temp/onnx_spike_notes.md）：
  NIMA        124/124，最大 |Δ| = 0.0000（百分制）
  MUSIQ 横     44/44， 最大 |Δ| = 0.0001
  MUSIQ 竖     80/80， 最大 |Δ| = 0.0001

预处理必须与 pyiqa 完全一致，否则分数会偏（踩过：把 NIMA 的 299 写成 224，
分数差到 1.3）：
  NIMA  : 短边缩到 299 -> 中心裁 299 -> (x-0.5)/0.5 -> dist(已 softmax) @ [1..10]
  MUSIQ : 按横竖选模型 -> 缩放到模型固定尺寸 -> 直接喂（模型内部自己多尺度缩放）

限制：MUSIQ 的 ONNX 是固定尺寸导出的，非 3:2 的图会被拉伸到 3:2。
当前素材（Sony ARW）全是 3:2，所以无损；将来要支持其它比例需要按比例再导模型。
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

log = logging.getLogger("jurymate")

NIMA_SIZE = 299
MUSIQ_LANDSCAPE = (1024, 683)
MUSIQ_PORTRAIT = (683, 1024)

_SESS: dict[str, ort.InferenceSession] = {}


def _session(path: Path) -> ort.InferenceSession:
    key = str(path)
    if key not in _SESS:
        so = ort.SessionOptions()
        so.intra_op_num_threads = max(4, min(12, (__import__("os").cpu_count() or 4)))
        _SESS[key] = ort.InferenceSession(key, so, providers=["CPUExecutionProvider"])
    return _SESS[key]


def _nima_input(im: Image.Image) -> np.ndarray:
    im = im.convert("RGB")
    w, h = im.size
    s = NIMA_SIZE / min(w, h)
    im2 = im.resize((max(NIMA_SIZE, round(w * s)), max(NIMA_SIZE, round(h * s))), Image.BILINEAR)
    x0 = (im2.width - NIMA_SIZE) // 2
    y0 = (im2.height - NIMA_SIZE) // 2
    crop = im2.crop((x0, y0, x0 + NIMA_SIZE, y0 + NIMA_SIZE))
    a = np.asarray(crop, dtype=np.float32) / 255.0
    return ((a - 0.5) / 0.5).transpose(2, 0, 1)[None]


def nima_scores(imgs: list[Image.Image], model_path: Path) -> list[float]:
    """返回 NIMA 原始分（1~10 量纲，未 ×10）。"""
    sess = _session(model_path)
    ar = np.arange(1, 11, dtype=np.float32)
    out = []
    for im in imgs:
        d = sess.run(None, {"input": _nima_input(im)})[0][0]
        out.append(float((d * ar).sum()))
    return out


def musiq_scores(imgs: list[Image.Image], land_path: Path, port_path: Path) -> list[float]:
    """返回 MUSIQ 原始分（0~100 量纲）。按横竖选模型。"""
    out = []
    for im in imgs:
        im = im.convert("RGB")
        landscape = im.width >= im.height
        size = MUSIQ_LANDSCAPE if landscape else MUSIQ_PORTRAIT
        sess = _session(land_path if landscape else port_path)
        x = np.asarray(im.resize(size, Image.LANCZOS), dtype=np.float32) / 255.0
        out.append(float(sess.run(None, {"input": x.transpose(2, 0, 1)[None]})[0].flatten()[0]))
    return out
