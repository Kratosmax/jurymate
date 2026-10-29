"""色彩整形（A 方案）：把 ARW 内嵌预览的「灰片感」压回去。

问题：相机内嵌的 JPEG 预览偏亮偏灰（实测 mean 0.68~0.87、sat 0.026~0.12），
rawpy 全量解码是 mean 0.60~0.79、sat 0.066~0.15。但全量解码 460ms/张，
124 张要多 47 秒，不值得。

做法（1600px 上 10~15ms）：
  1) 高光滚降：只压最亮的部分，不动中间调，去掉"蒙了一层灰"
  2) 自适应饱和度：按当前饱和度决定加多少，本来就鲜艳的图少加甚至不加
     ——固定倍率会把本来就浓的图推到过饱和

不追求还原真实色彩，它是固定风格。介意的话把 settings.color_grade 关掉。
"""
import numpy as np
from PIL import Image, ImageEnhance

_HIGHLIGHT = 0.06      # 高光滚降强度
_SAT_TARGET = 0.10     # 目标平均饱和度（参考 rawpy 解码的实测区间）
_SAT_MIN, _SAT_MAX = 1.0, 1.7   # 饱和度倍率的上下限


def _rolloff_lut() -> list[int]:
    lut = []
    for i in range(256):
        x = i / 255.0
        x = x - _HIGHLIGHT * (x ** 3)
        lut.append(int(round(min(max(x, 0.0), 1.0) * 255)))
    return lut


_LUT = _rolloff_lut() * 3          # RGB 三通道共用


def _mean_saturation(img: Image.Image) -> float:
    a = np.asarray(img.resize((200, 200))).astype(np.float32) / 255.0
    mx = a.max(axis=2)
    mn = a.min(axis=2)
    return float(np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0).mean())


def apply(img: Image.Image) -> Image.Image:
    """对已缩到 1600px 的图做色彩整形，返回新图（不修改入参）。"""
    out = img.convert("RGB").point(_LUT)
    sat = _mean_saturation(out)
    factor = _SAT_TARGET / max(sat, 0.02)
    factor = min(max(factor, _SAT_MIN), _SAT_MAX)
    if factor > 1.01:
        out = ImageEnhance.Color(out).enhance(factor)
    return out
