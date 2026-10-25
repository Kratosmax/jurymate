"""管线编排：扫描 → 缩略图 → 信号 → 人脸 → 审美 → 分组 → 决策引擎。

决策引擎（2026-10-25 与用户确认）：
- 每组 AI 默认胜者 = 综合分最高者；只有前两名分差 < pk_gap 的组才需要人 PK。
- 落选片保留（进 rejects，带原因标签），人工复查可召回。
- 5 分制审美分只给胜出图（评分范围按用户选择）。
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

PENALTY = {
    "失焦": 2.5, "闭眼": 2.0, "过曝": 0.8, "欠曝": 0.8,
    "偏软": 0.3, "低分": 1.5,
}

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

def classify(photo: dict, settings: dict) -> list[str]:
    """按信号给淘汰原因（有脸豁免无脸失焦误杀，见 signals.py 设计要点）。"""
    reasons = []
    th = signals.TH
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
    # 缩略图（PK 大图 1600px + 网格小图 400px）
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

def scan_folder(folder: str, thumbs_dir: Path, settings: dict,
                progress=None) -> list[dict]:
    folder_path = Path(folder)
    files = sorted(p for p in folder_path.iterdir()
                   if p.is_file() and p.suffix.lower() in (IMAGE_EXTS | RAW_EXTS))
    order, primaries = pair_files(files)

    tasks = [(k, primaries[k]) for k in order
             if primaries[k]["raw"] is not None or primaries[k]["jpg"] is not None]
    # 阶段一：解码/缩略图/信号——rawpy 与 JPEG 解码、numpy 都释放 GIL，
    # ThreadPool 真并行（千张场景的大头）；模型推理（人脸/审美）保持主线程。
    from concurrent.futures import ThreadPoolExecutor
    base_results = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_prepare_base, k, ent, thumbs_dir, settings): k
                   for k, ent in tasks}
        for n_done, fut in enumerate(futures, 1):
            k = futures[fut]
            try:
                base_results[k] = fut.result()
            except Exception as e:
                log.warning("预处理失败 %s: %s", k, e)
            if progress:
                progress(n_done, len(tasks) * 2, k)

    # 阶段二：人脸模型（insightface DML + FaceMesh）主线程串行
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
            progress(len(order) + ki + 1, len(order) * 2, photo["name"])
    return photos

def decide(photos: list[dict], settings: dict) -> list[dict]:
    """逐图定原因/综合分；逐组定默认胜者与是否需要人 PK。"""
    for ph in photos:
        ph["reject_reasons"] = classify(ph, settings)
        ph["aesthetic"] = ph.get("aesthetic")  # 保留
        ph["score"] = composite_score(ph, ph.get("aesthetic"), ph["reject_reasons"])

    group_ids = group_mod.group(photos, settings)
    pk_gap = float(settings.get("pk_gap", 0.35))
    min_score = float(settings.get("min_score", 2.0))
    groups_out = []
    for gid, members in enumerate(group_ids):
        for i in members:
            photos[i]["group_id"] = gid
            if photos[i]["aesthetic"] is not None and photos[i]["aesthetic"] < min_score \
               and "低分" not in photos[i]["reject_reasons"]:
                photos[i]["reject_reasons"].append("低分")
                photos[i]["score"] = composite_score(photos[i], photos[i]["aesthetic"],
                                                     photos[i]["reject_reasons"])
        ranked = sorted(members, key=lambda i: -photos[i]["score"])
        winner = ranked[0]
        gap = (photos[ranked[0]]["score"] - photos[ranked[1]]["score"]) if len(ranked) > 1 else 99.0
        needs_pk = len(members) > 1 and gap < pk_gap
        groups_out.append({"id": gid, "members": members, "winner": winner,
                           "gap": round(gap, 2), "needs_pk": needs_pk})
        for i in members:
            if i != winner and not photos[i]["reject_reasons"]:
                photos[i]["reject_reasons"] = ["组内落选"]
    return groups_out

def run(folder: str, state_dir: Path, settings: dict, progress=None) -> dict:
    photos = scan_folder(folder, state_dir / "thumbs", settings, progress)
    # 审美分：只给无硬淘汰原因的候选算（省算力；落选片复查召回时补算）。
    # 评分直接用 scan 阶段生成的 1600px view.jpg——重复走 rawpy 提取是纯浪费。
    n = len(photos)
    todo = []   # (idx, ph) —— 无硬淘汰原因的候选才评分
    for ph in photos:
        reasons = classify(ph, settings)
        ph["_hard"] = [r for r in reasons if r in ("失焦", "闭眼", "过曝", "欠曝")]
        if not ph["_hard"]:
            todo.append(ph)
    if todo and progress:
        progress(n, n * 2, "加载审美模型（首次约 20 秒）…")
    # 评分直接用 scan 生成的 1600px view.jpg——重复走 rawpy 解码是纯浪费
    def _src(ph):
        view = Path(ph["thumb_view"].lstrip("/"))
        cached = state_dir / "thumbs" / view
        return str(cached) if cached.exists() else ph["path"]
    images = [load_preview(_src(ph)) for ph in todo]
    scores = aesthetic.score_batch(images)
    for ph, s in zip(todo, scores):
        ph["aesthetic"] = s
    for idx, ph in enumerate(photos):
        ph.pop("_hard", None)
        if progress:
            progress(n + idx + 1, n * 2, ph["name"])
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
             and (primaries[k]["raw"] or primaries[k]["jpg"]).resolve().as_posix().lower() not in known]
    if not tasks:
        return 0
    from concurrent.futures import ThreadPoolExecutor
    base_results = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_prepare_base, k, ent, state_dir / "thumbs", settings): k
                   for k, ent in tasks}
        for n_done, fut in enumerate(futures, 1):
            k = futures[fut]
            try:
                base_results[k] = fut.result()
            except Exception as e:
                log.warning("预处理失败 %s: %s", k, e)
            if progress:
                progress(n_done, len(tasks) * 2, k)
    new_photos = []
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
        new_photos.append(photo)
        if progress:
            progress(len(tasks) + ki + 1, len(tasks) * 2, photo["name"])
    # 评分（新图内部批处理）；人工决策不涉及
    todo = []
    for ph in new_photos:
        reasons = classify(ph, settings)
        if not [r for r in reasons if r in ("失焦", "闭眼", "过曝", "欠曝")]:
            todo.append(ph)
    if todo:
        images = [load_preview(str(state_dir / "thumbs"
                    / Path(ph["thumb_view"].lstrip("/")))) for ph in todo]
        for ph, s in zip(todo, aesthetic.score_batch(images)):
            ph["aesthetic"] = s
    for ph in new_photos:
        ph["reject_reasons"] = classify(ph, settings)
        ph["score"] = composite_score(ph, ph.get("aesthetic"), ph["reject_reasons"])
    new_groups = group_mod.group(new_photos, settings)
    pk_gap = float(settings.get("pk_gap", 0.35))
    min_score = float(settings.get("min_score", 2.0))
    for gi, members in enumerate(new_groups):
        gid = len(photos) + gi          # 组 id 唯一即可（前端按 id 查找）
        for i in members:
            new_photos[i]["group_id"] = gid
            if new_photos[i]["aesthetic"] is not None and new_photos[i]["aesthetic"] < min_score                and "低分" not in new_photos[i]["reject_reasons"]:
                new_photos[i]["reject_reasons"].append("低分")
                new_photos[i]["score"] = composite_score(new_photos[i],
                                                         new_photos[i]["aesthetic"],
                                                         new_photos[i]["reject_reasons"])
        ranked = sorted(members, key=lambda i: -new_photos[i]["score"])
        gap = (new_photos[ranked[0]]["score"] - new_photos[ranked[1]]["score"])             if len(ranked) > 1 else 99.0
        groups.append({"id": gid, "members": [len(photos) + i for i in members],
                       "winner": len(photos) + ranked[0],
                       "gap": round(gap, 2), "needs_pk": len(members) > 1 and gap < pk_gap})
        for i in members:
            if i != ranked[0] and not new_photos[i]["reject_reasons"]:
                new_photos[i]["reject_reasons"] = ["组内落选"]
    photos.extend(new_photos)
    return len(new_photos)
