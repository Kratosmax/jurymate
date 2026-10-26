"""管线编排：扫描 → 信号 → 人脸 → 审美 → 分组 → 决策引擎。

三条编排共享的阶段函数（复用边界）：
- pair_files / _prepare_base：文件配对与线程池预处理（解码/缩略图/信号/phash）
- _face_stage：insightface + FaceMesh 主线程串行推理（模型单例的必然串行段）
- score_candidates：硬淘汰豁免筛选 + view.jpg 批量评分
- decide_group：单组决策（默认胜者/分差/是否需人 PK），全量与增量共用语义

决策引擎（2026-10-25 与用户确认）：
- 每组 AI 默认胜者 = 综合分最高者；只有前两名分差 < pk_gap 的组才需要人 PK。
- 落选片保留（带原因标签），人工复查可召回。
- 5 分制审美分只给胜出图；硬淘汰片召回时补算。
"""
import io
import json
import logging
from datetime import datetime
from pathlib import Path

import cv2
import imagehash
import numpy as np
from PIL import Image

import signals
import faces as faces_mod
import aesthetic
import group as group_mod

log = logging.getLogger("cullmate")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp", ".heic", ".heif"}
RAW_EXTS = {".arw", ".cr2", ".cr3", ".nef", ".raf", ".orf", ".rw2", ".dng", ".erf"}

HARD_REASONS = frozenset(("失焦", "闭眼", "过曝", "欠曝"))

PENALTY = {
    "失焦": 2.5, "闭眼": 2.0, "过曝": 0.8, "欠曝": 0.8,
    "偏软": 0.3, "低分": 1.5,
}


# ---------------------------------------------------------------- 基础工具

def load_preview(path: str) -> Image.Image | None:
    """RAW 提内嵌预览（毫秒级），普通图直接打开。

    必须应用 EXIF orientation（竖拍的 ARW 内嵌预览是横幅数据+旋转标记，
    不 transpose 会整张躺倒——GUI 实测发现）。"""
    from PIL import ImageOps
    p = Path(path)
    try:
        if p.suffix.lower() in RAW_EXTS:
            import rawpy
            with rawpy.imread(path) as raw:
                thumb = raw.extract_thumb()
            if thumb.format == rawpy.ThumbFormat.JPEG:
                img = Image.open(io.BytesIO(thumb.data))
            else:
                img = Image.fromarray(thumb.data)
        else:
            img = Image.open(path)
            img.load()
        return ImageOps.exif_transpose(img)
    except Exception as e:
        log.warning("预览失败 %s: %s", p.name, e)
        return None

def phash_of(img: Image.Image):
    try:
        return str(imagehash.phash(img.convert("RGB")))
    except Exception:
        return None

def shot_time(path: Path, is_jpeg: bool) -> float:
    """拍摄时间：JPEG 读 EXIF DateTimeOriginal（复制文件不改 EXIF）；
    RAW 多为 TIFF 系，piexif 不保证可读，用 mtime（相机直传=mtime≈拍摄时刻）。"""
    if is_jpeg:
        try:
            import piexif
            exif = piexif.load(str(path)).get("Exif") or {}
            v = exif.get(36867)  # DateTimeOriginal
            if v:
                return datetime.strptime(v.decode().strip(), "%Y:%m:%d %H:%M:%S").timestamp()
        except Exception:
            pass
    return path.stat().st_mtime

def classify(photo: dict, settings: dict) -> list[str]:
    """按信号给淘汰原因（有脸豁免无脸失焦误杀，见 signals.py 设计要点）。"""
    reasons = []
    th = dict(signals.TH)
    th.update({k: v for k, v in settings.items() if k in th})
    n_faces = len(photo.get("faces") or [])
    if n_faces:
        face_sharp = photo.get("face_sharpness")
        if face_sharp is not None and face_sharp < th["face_very_blur"]:
            reasons.append("失焦")
        if any(f.get("closed") for f in photo["faces"]):
            reasons.append("闭眼")
    else:
        if photo["sharpness"] < th["very_blur"]:
            reasons.append("失焦")
        elif photo["sharpness"] < th["soft_blur"]:
            reasons.append("偏软")
    if photo["overexp_ratio"] > th["overexp_ratio"]:
        reasons.append("过曝")
    if photo["underexp_ratio"] > th["underexp_ratio"]:
        reasons.append("欠曝")
    return reasons

def composite_score(photo: dict, aesthetic_score: float | None, reasons: list[str]) -> float:
    """综合分 = 审美分 - 淘汰原因惩罚。审美缺失时以 3.0 中性起步。"""
    s = aesthetic_score if aesthetic_score is not None else 3.0
    return round(max(0.0, s - sum(PENALTY.get(r, 0.0) for r in reasons)), 2)


# ---------------------------------------------------------------- 共享阶段

def pair_files(files) -> tuple:
    """RAW+JPG 双拍配对：同 stem 的 RAW/JPEG 视为同一张（JPG 分析面、
    RAW 搬运主体）；独立 JPG/其他图片各自成 primary。
    返回 (primary 顺序 key 列表, primaries 字典)。"""
    primaries: dict = {}
    order: list = []
    for p in files:
        ext = p.suffix.lower()
        k = p.stem.upper()
        ent = primaries.get(k)
        if ent is None:
            ent = {"raw": None, "jpg": None, "companions": []}
            primaries[k] = ent
            order.append(k)
        if ext in RAW_EXTS:
            if ent["raw"] is None:
                ent["raw"] = p
            else:
                ent["companions"].append(p)
        elif ext in (".jpg", ".jpeg"):
            if ent["raw"] is None and ent["jpg"] is None:
                ent["jpg"] = p
            else:
                ent["companions"].append(p)
        else:
            if ent["raw"] is None and ent["jpg"] is None:
                ent["jpg"] = p
            else:
                ent["companions"].append(p)
    return order, primaries

def _prepare_base(k: str, ent: dict, thumbs_dir: Path, settings: dict):
    """线程池 worker：解码/缩略图/信号/phash——rawpy、JPEG 解码、numpy 都
    释放 GIL 真并行；返回 (photo 基础 dict, gray 数组, BGR 数组)，
    gray/BGR 供主线程人脸检测用，不进 state。"""
    primary = ent["raw"] or ent["jpg"]
    analyzer = ent["jpg"] or ent["raw"]
    img = load_preview(str(analyzer))
    if img is None and analyzer != primary:
        img = load_preview(str(primary))
    if img is None:
        return None
    shot_t = shot_time(primary, primary.suffix.lower() in (".jpg", ".jpeg"))
    photo = {
        "path": str(primary),
        "name": primary.name,
        "is_raw": primary.suffix.lower() in RAW_EXTS,
        "size": primary.stat().st_size,
        "mtime": shot_t,
        "companions": [str(c) for c in ent["companions"]],
        "has_jpg_twin": ent["jpg"] is not None and ent["raw"] is not None,
        "width": img.width,
        "height": img.height,
        "mtime_str": datetime.fromtimestamp(shot_t).strftime("%m-%d %H:%M:%S"),
    }
    # 缩略图（PK 大图 1600px + 网格小图 400px）；命名必须用 primary——
    # 配对循环的残留变量曾让 9 张照片互相覆盖（视觉实测发现）
    tdir = thumbs_dir / (primary.stem + primary.suffix.lower().replace(".", "_"))
    tdir.mkdir(parents=True, exist_ok=True)
    big, small = tdir / "view.jpg", tdir / "grid.jpg"
    if not big.exists():
        im2 = img.copy()
        im2.thumbnail((1600, 1600), Image.LANCZOS)
        im2.save(big, quality=88)
    if not small.exists():
        im3 = img.copy()
        im3.thumbnail((400, 400), Image.LANCZOS)
        im3.save(small, quality=85)
    photo["thumb_view"] = f"/thumbs/{tdir.name}/view.jpg"
    photo["thumb_grid"] = f"/thumbs/{tdir.name}/grid.jpg"
    photo["phash"] = phash_of(img)

    # 分析统一在 1600px 缩图上（6192px 全尺寸会饿死 HTTP 线程——实测教训）
    ana = img.copy()
    ana.thumbnail((1600, 1600), Image.LANCZOS)
    rgb = np.asarray(ana.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    sig = signals.compute(gray, settings)
    photo.update({k2: v for k2, v in sig.items() if not k2.startswith("_")})
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    return photo, gray, bgr

def _parallel_prepare(tasks, thumbs_dir: Path, settings: dict, progress=None) -> dict:
    """阶段一：解码/缩略图/信号并行执行，返回 {key: (photo, gray, bgr)}。"""
    from concurrent.futures import ThreadPoolExecutor
    results = {}
    if not tasks:
        return results
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_prepare_base, k, ent, thumbs_dir, settings): k
                   for k, ent in tasks}
        for n_done, fut in enumerate(futures, 1):
            k = futures[fut]
            try:
                results[k] = fut.result()
            except Exception as e:
                log.warning("预处理失败 %s: %s", k, e)
            if progress:
                progress(n_done, len(tasks) * 2, k)
    return results

def _face_stage(order: list, base_results: dict, settings: dict,
                progress=None, progress_offset: int = 0) -> list[dict]:
    """阶段二：人脸模型（insightface DML + FaceMesh）主线程串行推理。"""
    photos = []
    for ki, k in enumerate(order):
        base = base_results.get(k)
        if base is None:
            continue
        photo, gray, bgr = base
        fl = faces_mod.analyze(bgr, settings)
        photo["faces"] = [{k2: (v.tolist() if hasattr(v, "tolist") else v)
                           for k2, v in f.items() if k2 != "embedding"} for f in fl]
        photo["n_faces"] = len(fl)
        if fl:
            x1, y1, x2, y2 = [int(v) for v in fl[0]["bbox"]]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(gray.shape[1], x2), min(gray.shape[0], y2)
            if x2 - x1 > 8 and y2 - y1 > 8:
                photo["face_sharpness"] = signals.sharpness(gray[y1:y2, x1:x2])
        photos.append(photo)
        if progress:
            progress(progress_offset + ki + 1, progress_offset + len(order),
                     photo["name"])
    return photos

def score_candidates(photos: list[dict], state_dir: Path, settings: dict,
                     progress=None, report_all: int | None = None) -> None:
    """阶段三：无硬淘汰原因的候选批量评分（原地写 aesthetic）。

    评分直接用 scan 阶段生成的 1600px view.jpg——重复走 rawpy 解码是纯浪费。
    report_all：进度总口径（全量=2N；增量场景无对应前半段，传 None 只报阶段名）。
    """
    n = report_all if report_all is not None else len(photos)
    todo = [ph for ph in photos
            if not (set(classify(ph, settings)) & HARD_REASONS)]
    if not todo:
        if progress and report_all is not None:
            for idx, ph in enumerate(photos):
                progress(n + idx + 1, n * 2, ph["name"])
        return
    if progress:
        progress(n, n * 2, "加载审美模型（首次约 20 秒）…")
    images = []
    for ph in todo:
        # thumb_view 形如 /thumbs/<stem>_<ext>/view.jpg，已含 thumbs 段，
        # 相对 state_dir 直接解析（再拼一层 thumbs 会命中不存在的路径，
        # 导致每张都退回原始 RAW 重解码）
        view = Path(ph["thumb_view"].lstrip("/"))
        cached = state_dir / view
        images.append(load_preview(str(cached) if cached.exists() else ph["path"]))
    for ph, s in zip(todo, aesthetic.score_batch(images)):
        ph["aesthetic"] = s
    if progress and report_all is not None:
        for idx, ph in enumerate(photos):
            progress(n + idx + 1, n * 2, ph["name"])

def decide_group(members: list[int], photos: list[dict], settings: dict,
                 gid: int, id_offset: int = 0) -> dict:
    """单组决策：默认胜者/分差/是否需人 PK；组内落选打标。

    members 是 photos 的局部索引（增量场景传 new_photos，id_offset 把
    winner/members 换算回全局索引）。"""
    pk_gap = float(settings.get("pk_gap", 0.35))
    min_score = float(settings.get("min_score", 2.0))
    for i in members:
        ph = photos[i]
        ph["group_id"] = gid
        ph.setdefault("manual_winner", False)   # fresh 图显式落字段，消除 state 歧义
        if ph["aesthetic"] is not None and ph["aesthetic"] < min_score \
                and "低分" not in ph["reject_reasons"]:
            ph["reject_reasons"].append("低分")
            ph["score"] = composite_score(ph, ph["aesthetic"], ph["reject_reasons"])
    ranked = sorted(members, key=lambda i: -photos[i]["score"])
    winner = ranked[0]
    gap = (photos[ranked[0]]["score"] - photos[ranked[1]]["score"]) \
        if len(ranked) > 1 else 99.0
    for i in members:
        if i != winner and not photos[i]["reject_reasons"]:
            photos[i]["reject_reasons"] = ["组内落选"]
    return {"id": gid,
            "members": [id_offset + i for i in members],
            "winner": id_offset + winner,
            "gap": round(gap, 2),
            "needs_pk": len(members) > 1 and gap < pk_gap}


# ---------------------------------------------------------------- 编排层

def scan_folder(folder: str, thumbs_dir: Path, settings: dict,
                progress=None) -> list[dict]:
    folder_path = Path(folder)
    files = sorted(p for p in folder_path.iterdir()
                   if p.is_file() and p.suffix.lower() in (IMAGE_EXTS | RAW_EXTS))
    order, primaries = pair_files(files)
    tasks = [(k, primaries[k]) for k in order
             if primaries[k]["raw"] is not None or primaries[k]["jpg"] is not None]
    base_results = _parallel_prepare(tasks, thumbs_dir, settings, progress)
    return _face_stage(order, base_results, settings, progress, len(order))

def decide(photos: list[dict], settings: dict) -> list[dict]:
    """全量决策：逐图定原因/综合分；逐组定默认胜者与是否需要人 PK。"""
    for ph in photos:
        ph["reject_reasons"] = classify(ph, settings)
        ph["score"] = composite_score(ph, ph.get("aesthetic"), ph["reject_reasons"])
    groups_out = []
    for gid, members in enumerate(group_mod.group(photos, settings)):
        groups_out.append(decide_group(members, photos, settings, gid))
    return groups_out

def run(folder: str, state_dir: Path, settings: dict, progress=None) -> dict:
    photos = scan_folder(folder, state_dir / "thumbs", settings, progress)
    # 评分阶段的进度由 score_candidates 统一推进（含模型加载提示与逐张回执）
    score_candidates(photos, state_dir, settings, progress, report_all=len(photos))
    groups = decide(photos, settings)
    return {"photos": photos, "groups": groups,
            "folder": folder, "settings": settings}

def add_new_photos(folder: str, photos: list[dict], groups: list[dict],
                   state_dir: Path, settings: dict, progress=None) -> int:
    """增量扫描：只处理目录里新增的照片（已有 path 跳过），新图独立分组、
    独立走 AI 判定；已有的人工决策（PK/召回）一律保留。返回新增数。
    photos/groups 原地扩展。
    局限：新图不会与旧图重组连拍组（burst 按批次内时间聚簇，跨批不合并）。"""
    folder_path = Path(folder)
    files = sorted(p for p in folder_path.iterdir()
                   if p.is_file() and p.suffix.lower() in (IMAGE_EXTS | RAW_EXTS))
    order, primaries = pair_files(files)
    known = {Path(p["path"]).resolve().as_posix().lower() for p in photos}
    tasks = [(k, primaries[k]) for k in order
             if (primaries[k]["raw"] or primaries[k]["jpg"]) is not None
             and (primaries[k]["raw"] or primaries[k]["jpg"]).resolve()
                 .as_posix().lower() not in known]
    if not tasks:
        return 0
    base_results = _parallel_prepare(tasks, state_dir / "thumbs", settings, progress)
    new_photos = _face_stage([k for k, _ in tasks], base_results, settings,
                             progress, len(tasks))
    score_candidates(new_photos, state_dir, settings, None)
    for ph in new_photos:
        ph["reject_reasons"] = classify(ph, settings)
        ph["score"] = composite_score(ph, ph.get("aesthetic"), ph["reject_reasons"])
    offset = len(photos)
    for gi, members in enumerate(group_mod.group(new_photos, settings)):
        groups.append(decide_group(members, new_photos, settings,
                                   offset + gi, id_offset=offset))
    photos.extend(new_photos)
    return len(new_photos)
