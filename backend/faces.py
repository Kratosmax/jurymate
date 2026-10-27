"""人脸信号：insightface（检测/ArcFace）+ MediaPipe FaceLandmarker（闭眼 EAR）。

架构分工（2026-10-25 实测定版）：
- 检测/脸部锐度/同人嵌入：insightface buffalo_l（onnxruntime，DML 优先）
- 闭眼判定：MediaPipe FaceLandmarker EAR（468 点+虹膜细化）。
  **不用 insightface landmark 算 EAR**——68/106 点回归对闭眼都会漂回
  "睁眼先验"位置（实测正面闭眼图 EAR 输出 0.27-0.30 睁眼量级，漏检 3/4）；
  MediaPipe 闭眼 0.05-0.14 / 睁眼 0.39，判别间隔清晰。
- 两源合并：FaceMesh 能检出 insightface 漏掉的脸（如闭眼特写），按中心点
  最近匹配挂 EAR；insightface 没有的脸补录（bbox 从 landmarks 推）。
- 模型缓存：<数据目录>/insightface/（buffalo_l）与 <数据目录>/face_landmarker.task，
  数据目录见 paths.py（Windows 为 %LOCALAPPDATA%/JuryMate）。
"""
import logging

import cv2
import numpy as np

import errors
import hardware
import paths

try:
    import mediapipe as _mp
except ImportError:            # mediapipe 可选（缺失时仅闭眼检测降级）
    _mp = None

log = logging.getLogger("jurymate")

_EAR_CLOSED = 0.18   # EAR 低于此判闭眼（MediaPipe 量纲；闭眼实测 0.05-0.14，睁眼 0.39）
_EAR_OPEN = 0.25     # 两者之间算"半闭"，不确定不淘汰
_L_EAR = [33, 160, 158, 133, 153, 144]
_R_EAR = [362, 385, 387, 263, 373, 380]

_FACES = None
_MESH = None

def _model_root() -> str:
    """insightface 模型根：统一放 JuryMate 自己的数据目录。

    布局为 <root>/models/buffalo_l/（insightface 的约定）。"""
    return str(paths.ensure("insightface"))

def _assert_installed() -> None:
    """确认 buffalo_l 已落盘，避免 insightface 自行联网下载。

    insightface 的 FaceAnalysis 在模型缺失时会调用 ensure_available() 直接
    从 GitHub 拉 340MB——它只认环境变量代理，不认应用内「网络设置」的镜像
    与代理。结果就是：用户明明配好了镜像，库还是慢/失败，且完全没有进度。
    这里提前拦住，让用户走应用内的下载器（支持镜像、代理、进度）。
    """
    want = paths.ensure("insightface") / "models" / "buffalo_l"
    if not want.is_dir() or not any(want.iterdir()):
        raise errors.ModelUnavailable(
            "buffalo_l", "模型文件缺失",
            "请在首页「模型就绪检查」里点下载（支持镜像与代理），或手动放入 "
            f"{want}")

def _providers() -> list[str]:
    """EP 顺序由显卡厂商决定（见 hardware.py），CPU 兜底。"""
    return hardware.ranked_providers()

def _get():
    global _FACES
    if _FACES is not None:
        return _FACES
    from insightface.app import FaceAnalysis
    _assert_installed()   # 缺失就报错，不让库自己去下载
    root = _model_root()
    prov = _providers()
    log.info("faces: 初始化 buffalo_l @ %s, providers=%s", root, prov)
    try:
        # 注意：insightface 2.1 的 taskname 是动态格式 'landmark_3d_68'
        # （landmark_%dd_%d），0.7 的 'landmark_3d68' 匹配不上会被静默过滤
        app = FaceAnalysis(name="buffalo_l", root=root,
                           allowed_modules=["detection", "landmark_3d_68", "recognition"],
                           providers=prov)
        app.prepare(ctx_id=0, det_size=(640, 640))
    except Exception as e:
        # 首次运行会在此自动下载 340MB；失败通常是网络/许可问题。
        # 不能吞掉——吞掉会让整批照片变成"无人脸"，失焦与闭眼判定全失效。
        raise errors.ModelUnavailable(
            "人脸检测 buffalo_l（约 340MB）",
            detail=str(e)[:200],
            hint="请在首页「模型就绪检查」点下载，或在网络设置里配置代理/镜像后重试",
        ) from e
    _FACES = app
    return _FACES

def _get_mesh():
    global _MESH
    if _MESH is not None:
        return _MESH
    mp_model = paths.data_dir() / "face_landmarker.task"
    if not mp_model.exists():
        # 闭眼检测依赖它；缺失时明确失败，不静默跳过闭眼判定
        raise errors.ModelUnavailable(
            "闭眼检测 FaceLandmarker（3.7MB）",
            detail=f"未找到 {mp_model}",
            hint="请在首页「模型就绪检查」点下载后重试",
        )
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision
    try:
        base = mp.tasks.BaseOptions(model_asset_path=str(mp_model))
        opts = vision.FaceLandmarkerOptions(
            base_options=base, running_mode=vision.RunningMode.IMAGE,
            num_faces=12, min_face_detection_confidence=0.3)
        _MESH = vision.FaceLandmarker.create_from_options(opts)
    except Exception as e:
        raise errors.ModelUnavailable(
            "闭眼检测 FaceLandmarker（3.7MB）",
            detail=str(e)[:200],
            hint="模型文件可能损坏，请删除后重新下载",
        ) from e
    return _MESH

def _downscale(img_bgr: np.ndarray, long_edge: int = 1600):
    h, w = img_bgr.shape[:2]
    scale = min(1.0, long_edge / max(h, w))
    if scale < 1.0:
        img_bgr = cv2.resize(img_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    return img_bgr, scale

def _ear_from_mp(landmarks, idx) -> float:
    p = np.array([(landmarks[i].x, landmarks[i].y) for i in idx])
    return float((np.linalg.norm(p[1]-p[5]) + np.linalg.norm(p[2]-p[4]))
                 / (2 * np.linalg.norm(p[0]-p[3]) + 1e-9))

def _ear(pts: np.ndarray) -> float:
    """68 点版 EAR（保留供调试对比；生产闭眼判定走 _ear_from_mp）。"""
    p = pts[:, :2].astype(np.float64)
    return float((np.linalg.norm(p[1]-p[5]) + np.linalg.norm(p[2]-p[4]))
                 / (2 * np.linalg.norm(p[0]-p[3]) + 1e-9))

def _apply_ear(rec: dict, ear_l: float, ear_r: float,
               th_closed: float, th_open: float) -> None:
    """按 EAR 阈值写 closed/half（analyze 内三处共用）。"""
    lo = min(ear_l, ear_r)
    rec["closed"] = lo < th_closed
    rec["half"] = (not rec["closed"]) and lo < th_open

def _iou(a: list[float], b: list[float]) -> float:
    """两 bbox（x1,y1,x2,y2）的 IoU。"""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = ((a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter)
    return inter / union if union > 1e-6 else 0.0

def analyze(img_bgr: np.ndarray, settings: dict | None = None) -> list[dict]:
    """返回每张脸的 {bbox, ear_l, ear_r, closed, half, tiny, source}。

    脸宽 < 40px（1600px 缩图上）跳过 EAR——远景路人脸眼睛只有几个像素，
    闭眼判定交给"这张脸太小"而非瞎猜。
    """
    th = (settings or {}).get("ear_closed", _EAR_CLOSED)
    th_open = (settings or {}).get("ear_open", _EAR_OPEN)
    img, scale = _downscale(img_bgr)
    h, w = img.shape[:2]
    # 模型不可用必须冒泡（ModelUnavailable）；单张图的推理异常才算可降级，
    # 但也要留日志，不能像以前那样静默变成"无人脸"。
    #
    # insightface 的 landmark 阶段有个已知崩溃：检测框能出、但 5 点关键点
    # 估计失败时内部会把 None 传进 estimate_affine_matrix_3d23d，抛
    # AttributeError: 'NoneType' object has no attribute 'shape'。
    # 这出现在纯风景/严重虚化的照片上，属于单图可降级，不该让整批任务失败。
    try:
        det_faces = _get().get(img)
    except AttributeError as e:
        if "NoneType" not in str(e):
            raise
        log.warning("faces: 关键点估计失败，本图按无人脸处理（%s）", e)
        det_faces = []

    out = []
    def push(bbox, ear_l=None, ear_r=None, source="insightface"):
        rec = {"bbox": [round(v / scale, 1) for v in bbox],
               "ear_l": ear_l, "ear_r": ear_r, "source": source,
               # FaceMesh 的 bbox 是 landmark 包围盒（不含额头/耳），比真实
               # 脸宽小 ~30%——tiny 判定按来源分别取界
               "tiny": (bbox[2]-bbox[0]) < (56 if source == "facemesh" else 40)}
        if ear_l is not None:
            _apply_ear(rec, ear_l, ear_r, th, th_open)
        out.append(rec)

    for f in det_faces:
        push(f.bbox.tolist())

    # MediaPipe：给 insightface 的脸挂 EAR；insightface 漏的脸补录
    mesh = _get_mesh()   # 缺失即抛 ModelUnavailable，不静默跳过闭眼判定
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    try:
        res = mesh.detect(_mp.Image(image_format=_mp.ImageFormat.SRGB, data=rgb))
    except Exception as e:
        # 单张图推理失败可接受（返回无 EAR 的脸），但必须留痕
        log.warning("faces: FaceMesh 推理失败(%s)，本图无 EAR 信号", e)
        res = None
    if res is not None:
        for mp_lm in res.face_landmarks or []:
            xs = [p.x * w for p in mp_lm]
            ys = [p.y * h for p in mp_lm]
            bbox = [min(xs), min(ys), max(xs), max(ys)]
            el, er = _ear_from_mp(mp_lm, _L_EAR), _ear_from_mp(mp_lm, _R_EAR)
            # 按 IoU 挂到 insightface 的脸（中心距在合影贴脸时会错配）
            best, best_iou = None, 0.0
            for rec in out:
                iou = _iou(bbox, rec["bbox"])
                if iou > best_iou:
                    best, best_iou = rec, iou
            if best is not None and best_iou > 0.3 and best.get("ear_l") is None:
                best["ear_l"], best["ear_r"] = round(el, 3), round(er, 3)
                _apply_ear(best, el, er, th, th_open)
            elif best_iou <= 0.3:
                if bbox[2] - bbox[0] >= 56:   # insightface 漏检的脸补录
                    push(bbox, round(el, 3), round(er, 3), source="facemesh")

    out = [r for r in out if not r.get("tiny", (r["bbox"][2]-r["bbox"][0]) < 40)]
    return out
