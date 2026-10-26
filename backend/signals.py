"""技术信号：清晰度（拉普拉斯方差）+ 曝光统计。纯 numpy/cv2，CPU 毫秒级。

设计要点（2026-10-25 用户实测教训）：
- 无脸图只做"极低锐度"硬淘汰（全图真糊），背景虚化的背影人像不许误杀。
- 人脸区域的锐度单独算（有脸时判失焦更准）。
"""
import cv2
import numpy as np

# 阈值（可被 state.settings 覆盖）
# 实测校准（2026-10-25，9 张 A6700 ARW）：大面积平滑背景（湖面+天空）会把
# 全图拉普拉斯方差拉到 40~50，而主体清楚——very_blur 必须低于该量级，
# 否则背影人像被误杀（selftest 的"背影人像未被误判失焦"就是守这条线）。
TH = {
    "very_blur": 30.0,      # 无脸图：低于此 = 真失焦（硬淘汰）
    "soft_blur": 80.0,      # 无脸图：30~80 = 焦点偏软（警告，不淘汰）
    "face_very_blur": 80.0, # 有脸图：脸部区域低于此 = 人脸失焦（硬淘汰）
    "overexp_ratio": 0.15,  # 过曝像素占比硬阈值
    "underexp_ratio": 0.30, # 欠曝像素占比硬阈值
}

def sharpness(img_gray: np.ndarray) -> float:
    """全图拉普拉斯方差。"""
    return float(cv2.Laplacian(img_gray, cv2.CV_64F).var())

def sharpness_center(img_gray: np.ndarray, frac: float = 0.6) -> float:
    """中心区域拉普拉斯方差（主体通常在中心）。"""
    h, w = img_gray.shape
    ch, cw = int(h * frac), int(w * frac)
    y0, x0 = (h - ch) // 2, (w - cw) // 2
    return sharpness(img_gray[y0:y0 + ch, x0:x0 + cw])

def exposure_stats(img_gray: np.ndarray) -> dict:
    """过曝/欠曝像素占比（灰度 250+/5-，近似高光修剪/死黑）。"""
    total = img_gray.size
    return {
        "overexp_ratio": float((img_gray >= 250).sum() / total),
        "underexp_ratio": float((img_gray <= 5).sum() / total),
        "mean_luma": float(img_gray.mean()),
    }

def compute(img_gray: np.ndarray, settings: dict | None = None) -> dict:
    th = dict(TH)
    if settings:
        th.update({k: v for k, v in settings.items() if k in th})
    out = {
        "sharpness": sharpness(img_gray),
        "sharpness_center": sharpness_center(img_gray),
    }
    out.update(exposure_stats(img_gray))
    out["_th"] = th
    return out
