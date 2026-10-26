"""审美分：MUSIQ（无参考图像质量评估，Google 0-100 分制）映射 5 分制。

- 用 pyiqa 加载，权重缓存由 paths.py 钉到 <数据目录>/torch/hub/pyiqa，
  首次评分时由 pyiqa 自动下载（约 104MB）。
- CPU 推理单张几十 ms，百张分钟级，不构成瓶颈；V1 不上 GPU。
- MUSIQ 是"人类平均审美"的代理分——给的是参考分，最终决策在人。
"""
import logging

import paths  # noqa: F401  导入即设 TORCH_HOME，必须早于 pyiqa/torch

log = logging.getLogger("jurymate")

_predictor = None

def _get():
    global _predictor
    if _predictor is not None:
        return _predictor
    import pyiqa
    import torch
    log.info("aesthetic: 加载 MUSIQ（CPU）…")
    _predictor = pyiqa.create_metric("musiq", device=torch.device("cpu"))
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
        m = _get()
        by_idx = dict(prepped)
        for size, idxs in groups.items():
            try:
                t = torch.stack([to_tensor(by_idx[i]) for i in idxs])
                with torch.no_grad():
                    v = m(t)
                for i, raw in zip(idxs, v.flatten().tolist()):
                    out[i] = round(_stretch(float(raw)), 1)
            except Exception as e:
                log.warning("aesthetic: 批推理失败 size=%s (%s)", size, e)
    except ImportError as e:
        log.warning("aesthetic: torch 缺失(%s)", e)
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

