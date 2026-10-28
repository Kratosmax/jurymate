"""审美 / 画质评分：两种模式，均输出 0-100 的整数分。

- 审美优先：NIMA(InceptionV2, AVA)。评构图、光影、氛围，速度快（实测 37ms/张）
- 画质优先：MUSIQ(KONIQ)。评清晰度/噪点/压缩伪影等技术质量，慢（实测 427ms/张）

**默认不选模式**（_mode 为空）：没选之前不加载、不下载任何评分模型，
由设置页让用户显式二选一。

两种模型权重都由 paths.py 钉在 <数据目录>/torch/hub/pyiqa，由设置页按所选模式下载。
"""
import logging

import errors
import paths  # noqa: F401  导入即设 TORCH_HOME，必须早于 pyiqa/torch

log = logging.getLogger("jurymate")

# mode -> 模型元信息
MODES = {
    "aesthetic": {
        "id": "nima",
        "file": "NIMA_InceptionV2_ava-b0c77c00.pth",
        "label": "审美优先",
        "size_mb": 208,
    },
    "quality": {
        "id": "musiq",
        "file": "musiq_koniq_ckpt-e95806b9.pth",
        "label": "画质优先",
        "size_mb": 104,
    },
}

_mode = ""
_predictor = None
# 预加载线程和扫描线程可能同时进来。没有锁时两边各创建一份模型，
# 各自要读权重 + 初始化 torch（实测各约 60 秒），
# 用户看到的就是"每次都卡在加载模型一分钟"。
_load_lock = __import__("threading").Lock()


def set_mode(mode: str) -> None:
    """切换评分模式（传空字符串 = 取消选择）。已加载的模型作废。"""
    global _mode, _predictor
    if mode and mode not in MODES:
        raise ValueError(f"未知评分模式: {mode}")
    if mode != _mode:
        _mode = mode
        _predictor = None
        if mode:
            log.info("aesthetic: 评分模式切换为 %s（%s）", mode, MODES[mode]["label"])
        else:
            log.info("aesthetic: 评分模式已清空")


def get_mode() -> str:
    return _mode


def has_mode() -> bool:
    return _mode in MODES


def model_file(mode: str | None = None) -> str:
    return MODES[mode or _mode]["file"]


def _assert_installed() -> None:
    """确认所选模式的权重已落盘，避免 pyiqa 自己联网下载。

    pyiqa.create_metric 在权重缺失时会走 torch.hub 的 load_file_from_url
    直接抓 HuggingFace——只认环境变量代理，不认应用内「网络设置」。
    提前拦住，让用户走应用内下载器（支持镜像、代理、进度）。
    """
    if not has_mode():
        raise errors.ModelUnavailable(
            "评分模型（未选择）", "还没有选择评分模式",
            "请在「设置 → 评分模型」里选择「审美优先」或「画质优先」")
    meta = MODES[_mode]
    w = paths.torch_dir() / "hub" / "pyiqa" / meta["file"]
    if not w.is_file() or w.stat().st_size < 1024:
        raise errors.ModelUnavailable(
            f"{meta['label']}模型（{meta['id']}）", "权重文件缺失",
            f"请在「设置 → 模型」里选择「{meta['label']}」并点下载（支持镜像与代理）")


def reset_cache() -> None:
    """丢掉已加载的评分器（切换数据根后必须调用）。"""
    global _predictor
    _predictor = None


def is_loaded() -> bool:
    return _predictor is not None


def _get():
    global _predictor
    if _predictor is not None:
        return _predictor
    with _load_lock:
        if _predictor is not None:
            return _predictor
        _predictor = _load_locked()
    return _predictor


def _load_locked():
    global _predictor
    try:
        import pyiqa
        import torch
    except ImportError as e:
        raise errors.ModelUnavailable(
            "评分模型（依赖缺失）",
            detail=str(e)[:200],
            hint="请先按 requirements.txt 安装 pyiqa 与 torch",
        ) from e
    _assert_installed()
    meta = MODES[_mode]
    log.info("aesthetic: 加载 %s（%s，CPU）…", meta["id"], meta["label"])
    try:
        _predictor = pyiqa.create_metric(meta["id"], device=torch.device("cpu"))
    except Exception as e:
        raise errors.ModelUnavailable(
            f"{meta['label']}模型（约 {meta['size_mb']}MB）",
            detail=str(e)[:200],
            hint="请在「设置 → 模型」点下载，或在网络设置里配置镜像/代理后重试",
        ) from e
    return _predictor


def score(img_pil) -> float:
    r = score_batch([img_pil])
    return r[0]


def score_batch(imgs, progress=None) -> list:
    """批量评分，返回 0-100 的**整数**列表（失败位 None）。

    同尺寸的分到一个 forward（模型对纵横比敏感）。
    progress(done, total) 每 8 张回调一次，用于真实进度。
    """
    from PIL import Image as _Img
    groups: dict[tuple, list[int]] = {}
    out: list = [None] * len(imgs)
    prepped = []
    for i, im in enumerate(imgs):
        try:
            im = im.convert("RGB")
            im.thumbnail((1024, 1024), _Img.LANCZOS)
            prepped.append((i, im))
            groups.setdefault(im.size, []).append(i)
        except Exception as e:
            log.warning("aesthetic: 预处理失败 #%d (%s)", i, e)
    try:
        import torch
        import os as _os
        from torchvision.transforms.functional import to_tensor
        # 实测（24 张）4 线程 618ms/张，8 线程 458ms，12 线程 423ms，16 线程 433ms
        torch.set_num_threads(max(4, min(_os.cpu_count() or 4, 12)))
        m = _get()
        mode = _mode
        by_idx = dict(prepped)
        total_imgs = len(prepped)
        done = 0
        CHUNK = 8   # 实测比整组一次 forward 更快，且进度粒度够细
        for size, idxs in groups.items():
            for k in range(0, len(idxs), CHUNK):
                part = idxs[k:k + CHUNK]
                try:
                    t = torch.stack([to_tensor(by_idx[i]) for i in part])
                    with torch.no_grad():
                        v = m(t)
                    for i, raw in zip(part, v.flatten().tolist()):
                        out[i] = int(round(_to100(float(raw), mode)))
                except Exception as e:
                    log.warning("aesthetic: 批推理失败 size=%s (%s)", size, e)
                done += len(part)
                if progress:
                    try:
                        progress(done, total_imgs)
                    except Exception:
                        pass
    except ImportError as e:
        raise errors.ModelUnavailable(
            "评分模型（依赖缺失）",
            detail=str(e)[:200],
            hint="请先按 requirements.txt 安装 pyiqa 与 torch",
        ) from e
    return out


# 原始分 -> 0-100：两个模型各用各的尺子，不做人为拉伸。
#
# NIMA：pyiqa 的 dist_to_mos 是 sum(softmax(p_i) * i), i=1..10，理论范围 1-10。
#       ×10 让「10 分 = 100」，语义是模型自己的满分，不是旧版的 5 分制。
# MUSIQ：KONIQ MOS 本身就是 0-100，原值直接用。
# 两者都不截断：NIMA 顶天 100，MUSIQ 实测 17-77，不会溢出。
_SCALE = {"aesthetic": 10.0, "quality": 1.0}


def _to100(raw: float, mode: str | None = None) -> float:
    return float(raw) * _SCALE.get(mode or _mode, 10.0)
