"""模型管理：硬件检测 → 推荐后端；模型清单/安装状态；双通道下载器。

下载网络策略（参考 PinNote 的设计逻辑）：
- **GitHub 镜像前缀列表**（优先级降级重试）：对 GitHub 域 URL 依次尝试
  用户配置的镜像前缀，全部失败回落直连
- **HTTP 代理**：对任意域生效（含 Google storage 的 FaceLandmarker）
- 直连兜底：未配置任何代理/镜像时直接尝试
网络设置持久化到 ~/.cache/cullmate/net_settings.json。

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

NET_SETTINGS_PATH = _home_cache() / "net_settings.json"

# 国内常见 GitHub 加速镜像（默认表；用户可在设置里增删/调序）
DEFAULT_GITHUB_MIRRORS = [
    "https://ghfast.top",
    "https://gh-proxy.com",
]

def load_net_settings() -> dict:
    """读网络设置；无配置时给默认镜像表+直连兜底（PinNote Normalize 思路）。"""
    try:
        s = json.loads(NET_SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        s = {}
    mirrors = s.get("github_mirrors")
    if not isinstance(mirrors, list):
        mirrors = DEFAULT_GITHUB_MIRRORS
    mirrors = [str(m).rstrip("/") for m in mirrors if str(m).strip()]
    proxy = s.get("http_proxy") or None
    return {"github_mirrors": mirrors, "http_proxy": proxy}

def save_net_settings(mirrors: list[str], http_proxy: str) -> None:
    NET_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    NET_SETTINGS_PATH.write_text(json.dumps(
        {"github_mirrors": [m.rstrip("/") for m in mirrors if str(m).strip()],
         "http_proxy": http_proxy or ""}, ensure_ascii=False, indent=1), encoding="utf-8")

def _routes_for(url: str, net: dict) -> list[tuple[str, str]]:
    """为 URL 生成按序尝试的下载路由列表 [(url, 通道名)]：
    GitHub 域 → 镜像前缀列表 + 直连兜底；其他域 → 直连（代理由 handler 统一处理）。"""
    is_github = "github.com" in url or "raw.githubusercontent.com" in url
    routes = []
    if is_github:
        for m in net.get("github_mirrors", []):
            routes.append((f"{m}/{url}", f"镜像 {m}"))
    routes.append((url, "直连"))
    return routes

def _opener(http_proxy: str | None):
    if http_proxy:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": http_proxy, "https": http_proxy}))
    return urllib.request.build_opener()

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
        "url": "https://github.com/deepinsight/insightface/releases/download/models/buffalo_l.zip",
        "mode": "manual",
    },
    {
        "id": "musiq",
        "name": "MUSIQ 审美评分权重",
        "size_mb": 104,
        "path": Path.home() / ".cache" / "torch" / "hub" / "pyiqa",
        "url": ("https://hf-mirror.com/chaofengc/IQA-PyTorch-Weights/resolve/main/"
                "musiq_koniq_ckpt-e95806b9.pth"),
        "mode": "manual",
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
                       "installed": installed, "mode": m["mode"]})
    net = load_net_settings()
    return {"providers": providers, "gpu": gpu, "backend": backend,
            "backend_hint": hint, "models": models, "net": net}

def _download_via_routes(routes, dest: Path, http_proxy: str | None, dl: dict) -> None:
    """按路由列表降级下载；代理对每条路由统一生效。"""
    opener = _opener(http_proxy)
    last_err = None
    for url, channel in routes:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "jurymate/0.1"})
            tmp = dest.with_suffix(dest.suffix + ".part")
            tmp.parent.mkdir(parents=True, exist_ok=True)
            with opener.open(req, timeout=90) as resp, tmp.open("wb") as fh:
                total = int(resp.headers.get("Content-Length") or 0)
                got = 0
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    fh.write(chunk)
                    got += len(chunk)
                    dl.update(done=got, total=total or got)
            tmp.replace(dest)
            log.info("models: %s 下载成功（%s，%d 字节）", dest.name, channel, got)
            return
        except Exception as e:
            last_err = e
            log.warning("models: 通道 %s 失败: %s", channel, e)
    raise last_err or RuntimeError("所有下载通道均失败")

def download(model_id: str) -> None:
    m = next((m for m in MODELS if m["id"] == model_id), None)
    if not m:
        DL.update(error=f"未知模型 {model_id}")
        return
    if m["path"].exists():
        DL.update(running=False, done=1, total=1, error=None)
        return
    net = load_net_settings()
    routes = _routes_for(m["url"], net)
    _download_via_routes(routes, m["path"], net.get("http_proxy"), DL)

def start_download(model_id: str) -> None:
    if DL["running"]:
        return
    DL.update(running=True, done=0, total=1, id=model_id, error=None)
    def worker():
        try:
            download(model_id)
        except Exception as e:
            log.exception("模型下载失败")
            DL["error"] = f"{e}"[:300]
        finally:
            DL["running"] = False
    threading.Thread(target=worker, daemon=True).start()

def progress() -> dict:
    return {k: DL[k] for k in ("running", "done", "total", "id", "error")}
