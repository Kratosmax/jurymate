"""模型管理：硬件检测 → 推荐后端；模型清单/安装状态；带代理的下载器。

三条模型的获取方式各不同（都已被管线隐式依赖）：
- face_landmarker.task：闭眼检测（MediaPipe Tasks）——**需手动下载**（本模块）
- buffalo_l：insightface 人脸检测/识别——首次运行管线时 insightface 自动下载
- musiq 权重：pyiqa 美学评分——首次评分时 pyiqa 自动下载（HF）
发布红线：buffalo_l 权重为 insightface 非商业许可，**不随安装包分发**，
由用户按需下载（本模块即该下载入口的雏形）。
"""
import json
import logging
import threading
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("cullmate.models")

DL = {"running": False, "done": 0, "total": 1, "id": "", "error": None}

def _home_cache() -> Path:
    return Path.home() / ".cache" / "cullmate"

MODELS = [
    {
        "id": "face_landmarker",
        "name": "FaceLandmarker（闭眼检测）",
        "size_mb": 3.7,
        "path": _home_cache() / "face_landmarker.task",
        "url": ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
                "face_landmarker/float16/1/face_landmarker.task"),
        "mode": "manual",
    },
    {
        "id": "buffalo_l",
        "name": "人脸检测 / 识别（insightface）",
        "size_mb": 330,
        "path": Path.home() / ".cache" / "pic_selecter" / "insightface" / "models" / "buffalo_l",
        "url": "auto-by-insightface",
        "mode": "auto",
    },
    {
        "id": "musiq",
        "name": "MUSIQ 审美评分权重",
        "size_mb": 104,
        "path": Path.home() / ".cache" / "torch" / "hub" / "pyiqa",
        "url": "auto-by-pyiqa",
        "mode": "auto",
    },
]

def detect() -> dict:
    """硬件与推理后端检测 + 模型安装状态。"""
    providers, gpu = [], ""
    try:
        import onnxruntime as ort
        providers = ort.get_available_providers()
    except Exception as e:
        log.warning("detect: onnxruntime 不可用 %s", e)
    try:
        import subprocess
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_VideoController).Name"],
            capture_output=True, text=True, timeout=15)
        names = [l.strip() for l in out.stdout.splitlines() if l.strip()]
        real = [n for n in names if "virtual" not in n.lower()]
        gpu = " / ".join(real or names)
    except Exception:
        gpu = ""
    if "CUDAExecutionProvider" in providers:
        backend, hint = "cuda", "N 卡：CUDA EP（推荐）"
    elif "DmlExecutionProvider" in providers:
        backend, hint = "dml", "AMD/Intel 显卡：DirectML EP（推荐）"
    elif "CoreMLExecutionProvider" in providers:
        backend, hint = "coreml", "Apple：CoreML EP"
    else:
        backend, hint = "cpu", "纯 CPU 推理"
    models = []
    for m in MODELS:
        installed = None if m["mode"] == "auto" else bool(
            m["path"].exists() and (m["path"].is_file() or any(m["path"].iterdir())))
        models.append({"id": m["id"], "name": m["name"], "size_mb": m["size_mb"],
                       "installed": bool(installed), "mode": m["mode"]})
    return {"providers": providers, "gpu": gpu, "backend": backend,
            "backend_hint": hint, "models": models}

def download(model_id: str, proxy: str = "") -> None:
    """流式下载 face_landmarker（manual 模式的模型）；支持代理设置。"""
    m = next((m for m in MODELS if m["id"] == model_id), None)
    if not m:
        DL.update(error=f"未知模型 {model_id}")
        return
    if m["path"].exists():
        DL.update(running=False, done=1, total=1, error=None)
        return
    handlers = urllib.request.build_opener()
    if proxy:
        handlers = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    req = urllib.request.Request(m["url"], headers={"User-Agent": "jurymate/0.1"})
    tmp = m["path"].with_suffix(".part")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    with handlers.open(req, timeout=60) as resp, tmp.open("wb") as fh:
        total = int(resp.headers.get("Content-Length") or m["size_mb"] * 1e6)
        got = 0
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            fh.write(chunk)
            got += len(chunk)
            DL.update(done=got, total=total)
    tmp.replace(m["path"])

def start_download(model_id: str, proxy: str = ""):
    if DL["running"]:
        return False
    DL.update(running=True, done=0, total=1, id=model_id, error=None)
    def worker():
        try:
            download(model_id, proxy)
        except Exception as e:
            log.exception("模型下载失败")
            DL["error"] = str(e)
        finally:
            DL["running"] = False
    threading.Thread(target=worker, daemon=True).start()
    return True

def progress() -> dict:
    return {k: DL[k] for k in ("running", "done", "total", "id", "error")}
