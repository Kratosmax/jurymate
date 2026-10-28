"""审美分：MUSIQ（无参考图像质量评估，Google 0-100 分制）映射 5 分制。

- 用 pyiqa 加载，权重缓存由 paths.py 钉到 <数据目录>/torch/hub/pyiqa，
  首次评分时由 pyiqa 自动下载（约 104MB）。
- CPU 推理单张几十 ms，百张分钟级，不构成瓶颈；V1 不上 GPU。
- MUSIQ 是"人类平均审美"的代理分——给的是参考分，最终决策在人。
"""
import logging

import errors
import paths  # noqa: F401  导入即设 TORCH_HOME，必须早于 pyiqa/torch

log = logging.getLogger("jurymate")

_predictor = None
# 预加载线程和扫描线程可能同时进来。没有锁时两边各创建一份 MUSIQ，
# 各自要读 104MB 权重 + 初始化 torch（实测各约 60 秒），
# 用户看到的就是"每次都卡在加载审美模型一分钟"。
_load_lock = __import__("threading").Lock()
_loading = __import__("threading").Event()


# pyiqa 的权重落在 <数据目录>/torch/hub/pyiqa/<文件名>
_MUSIQ_FILE = "musiq_koniq_ckpt-e95806b9.pth"

def _assert_installed() -> None:
    """确认 MUSIQ 权重已落盘，避免 pyiqa 自己联网下载。

    pyiqa.create_metric 在权重缺失时会走 torch.hub 的 load_file_from_url
    直接抓 HuggingFace——只认环境变量代理，不认应用内「网络设置」。
    提前拦住，让用户走应用内下载器（支持镜像、代理、进度）。
    """
    w = paths.torch_dir() / "hub" / "pyiqa" / _MUSIQ_FILE
    if not w.is_file() or w.stat().st_size < 1024:
        raise errors.ModelUnavailable(
            "审美评分 MUSIQ", "权重文件缺失",
            "请在首页「模型就绪检查」里点下载（支持镜像与代理）")

def reset_cache() -> None:
    """丢掉已加载的评分器。

    同 faces.reset_cache：切换数据根后 _predictor 仍绑在旧权重路径上，
    不清就会出现"目录切了、评分还在用旧盘的模型"。
    """
    global _predictor
    _predictor = None


def is_loaded() -> bool:
    """模型是否已就绪（供进度文案区分"加载中"和"评分中"）。"""
    return _predictor is not None


def _get():
    global _predictor
    if _predictor is not None:
        return _predictor
    # 双重检查：只有第一个线程真正加载，其余线程在这里等它加载完，
    # 不会重复创建第二份模型。
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
            "审美评分 MUSIQ（依赖缺失）",
            detail=str(e)[:200],
            hint="请先按 requirements.txt 安装 pyiqa 与 torch",
        ) from e
    _assert_installed()   # 缺失就报错，不让库自己去下载
    log.info("aesthetic: 加载 MUSIQ（CPU）…")
    try:
        _predictor = pyiqa.create_metric("musiq", device=torch.device("cpu"))
    except Exception as e:
        raise errors.ModelUnavailable(
            "审美评分 MUSIQ（约 104MB）",
            detail=str(e)[:200],
            hint="请在首页「模型就绪检查」点下载，或在网络设置里配置代理后重试",
        ) from e
    return _predictor

def score(img_pil) -> float:
    """单张评分（score_batch 的单元素特例）。"""
    r = score_batch([img_pil])
    return r[0]

def score_batch(imgs) -> list:
    """批量评分：同尺寸的张合并成一个 forward（千张场景把 torch 开销摊薄）。

    分组按 (宽, 高)——MUSIQ 对纵横比敏感，同尺寸才保证分数与单张一致。
    返回与输入等长的 5 分制列表，失败位为 None。"""
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
        from torchvision.transforms.functional import to_tensor
        torch.set_num_threads(4)
        m = _get()   # 模型不可用会抛 ModelUnavailable，不吞
        by_idx = dict(prepped)
        for size, idxs in groups.items():
            try:
                t = torch.stack([to_tensor(by_idx[i]) for i in idxs])
                with torch.no_grad():
                    v = m(t)
                for i, raw in zip(idxs, v.flatten().tolist()):
                    out[i] = round(_stretch(float(raw)), 1)
            except Exception as e:
                # 单批推理失败：这一批记 None，其他批次继续（已写进日志）
                log.warning("aesthetic: 批推理失败 size=%s (%s)", size, e)
    except ImportError as e:
        raise errors.ModelUnavailable(
            "审美评分 MUSIQ（依赖缺失）",
            detail=str(e)[:200],
            hint="请先按 requirements.txt 安装 pyiqa 与 torch",
        ) from e
    return out

_STRETCH = [(0, 0.5), (40, 1.0), (55, 2.5), (70, 3.5), (80, 4.5), (90, 5.0), (100, 5.0)]

def _stretch(musiq: float) -> float:
    """分段线性：把 MUSIQ 的经验分布拉伸到 1-5 有区分度的区间。"""
    for (x0, y0), (x1, y1) in zip(_STRETCH, _STRETCH[1:]):
        if musiq <= x1:
            if x1 == x0:
                return y1
            return y0 + (musiq - x0) * (y1 - y0) / (x1 - x0)
    return 5.0

