"""模型管理：硬件检测 → 推荐后端；模型清单/安装状态；双通道下载器。

下载网络策略（全部由用户决定，不写死）：
- **镜像站开关**：`use_mirror` 默认 **False**，即默认从模型原仓库直连下载。
  用户显式开启后，才对 GitHub 域 URL 依次尝试 `github_mirrors` 里的前缀，
  全部失败仍回落直连。
- **HTTP 代理**：`http_proxy` 独立生效，与镜像站开关无关。填了就通过代理
  访问源站（含 Google storage 的 FaceLandmarker / HuggingFace 的 MUSIQ）。
- 两者都不配 = 纯直连。
网络设置持久化到 <数据目录>/net_settings.json（见 paths.py）。

三条模型均从各自原仓库下载（不经过任何第三方选片项目）：
- face_landmarker.task：Google MediaPipe 官方 storage
- buffalo_l：insightface 官方 GitHub release
- musiq 权重：pyiqa 作者的 HuggingFace 仓库

获取方式（都已被管线隐式依赖）：
- face_landmarker.task：闭眼检测（MediaPipe Tasks）——本模块下载
- buffalo_l：insightface 人脸检测/识别——本模块下载；未下载时 insightface
  首次调用也会自行下载到同一目录（两条路径落到同一处）
- musiq 权重：pyiqa 美学评分——本模块下载；未下载时 pyiqa 首次评分自行下载
发布红线：buffalo_l 权重为 insightface 非商业许可，**不随安装包分发**，
由用户按需下载（本模块即该下载入口）。
"""
import json
import logging
import os
import threading
import urllib.request
from pathlib import Path

import hardware
import paths

log = logging.getLogger("jurymate.models")

DL = {"running": False, "done": 0, "total": 1, "id": "", "error": None}

def _net_settings_path() -> Path:
    """每次重新解析：用户可能在运行中切换了数据根目录。

    不能在模块顶层算成常量——那样切换目录后网络设置仍写回旧位置。
    """
    return paths.data_dir() / "net_settings.json"

# 常见 GitHub 加速镜像（仅作可选项呈现；默认不启用，见 use_mirror）
DEFAULT_GITHUB_MIRRORS = [
    "https://ghfast.top",
    "https://gh-proxy.com",
]

def load_net_settings() -> dict:
    """读网络设置。

    统一的网络层：代理、镜像站、优先级是一套配置，模型下载与更新检查共用，
    不再各管一段。字段：

      http_proxy      HTTP/SOCKS 代理，如 http://127.0.0.1:10808
      github_mirrors  GitHub 加速前缀，按数组顺序尝试
      use_mirror      是否启用镜像
      order           尝试顺序：'mirror_first' 或 'direct_first'

    兼容旧配置：只有 github_mirrors 而没有 use_mirror 时，视为用户曾开启
    镜像（沿用旧行为，避免升级后突然走直连而卡住）。
    """
    try:
        s = json.loads(_net_settings_path().read_text(encoding="utf-8"))
    except Exception:
        s = {}
    raw_mirrors = s.get("github_mirrors")
    if not isinstance(raw_mirrors, list):
        raw_mirrors = DEFAULT_GITHUB_MIRRORS
    mirrors = [str(m).rstrip("/") for m in raw_mirrors if str(m).strip()]
    use_mirror = s.get("use_mirror")
    if not isinstance(use_mirror, bool):
        use_mirror = False
    proxy = (s.get("http_proxy") or "").strip() or None
    order = s.get("order")
    if order not in ("mirror_first", "direct_first"):
        order = "mirror_first"
    return {"use_mirror": use_mirror, "github_mirrors": mirrors,
            "http_proxy": proxy, "order": order}


def save_net_settings(use_mirror: bool, mirrors: list[str], http_proxy: str,
                      order: str = "mirror_first") -> None:
    if order not in ("mirror_first", "direct_first"):
        order = "mirror_first"
    p = _net_settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(
        {"use_mirror": bool(use_mirror),
         "github_mirrors": [m.rstrip("/") for m in mirrors if str(m).strip()],
         "http_proxy": http_proxy or "",
         "order": order}, ensure_ascii=False, indent=1), encoding="utf-8")


def _routes_for(url: str, net: dict) -> list[tuple[str, str]]:
    """为 URL 生成按序尝试的下载路由列表 [(url, 通道名)]。

    仅当用户开启镜像站、且 URL 属 GitHub 域时才插入镜像前缀；
    其余一律直连（代理由 opener 统一处理，不影响路由本身）。

    order 决定"镜像优先"还是"直连优先"：国内直连 GitHub 常常等到超时
    才回落，反过来（镜像优先、失败回直连）通常更快；但从镜像下载可能
    拿到过期或不完整的文件，所以两种顺序都要留给用户选。
    """
    is_github = "github.com" in url or "raw.githubusercontent.com" in url
    mirrored = []
    if net.get("use_mirror") and is_github:
        for m in net.get("github_mirrors", []):
            mirrored.append((f"{m}/{url}", f"镜像 {m}"))
    direct = (url, "直连")
    if net.get("order") == "direct_first":
        return [direct] + mirrored
    return mirrored + [direct]


def _opener(http_proxy: str | None):
    if http_proxy:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": http_proxy, "https": http_proxy}))
    return urllib.request.build_opener()

def _apply_proxy_env(http_proxy: str | None) -> None:
    """把代理写进进程环境变量，供**库自身的下载器**使用。

    本模块的下载器走 _opener，但 insightface 用 requests、torch.hub 用
    urlopen，它们只认环境变量。不设这一步，用户填的代理对"库自动下载"
    完全无效——那正是最需要代理的场景。"""
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        if http_proxy:
            os.environ[k] = http_proxy
        else:
            os.environ.pop(k, None)

MODELS = [
    {
        "id": "face_landmarker",
        "name": "FaceLandmarker（闭眼检测）",
        "size_mb": 3.7,
        "path": paths.data_dir() / "face_landmarker.task",
        "url": ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
                "face_landmarker/float16/1/face_landmarker.task"),
        "unpack": "file",
    },
    {
        "id": "buffalo_l",
        "name": "人脸检测 / 识别（insightface）",
        "size_mb": 330,
        # 与 faces._model_root() 同一处：<数据目录>/insightface/models/buffalo_l
        "path": paths.data_dir() / "insightface" / "models" / "buffalo_l",
        "url": "https://github.com/deepinsight/insightface/releases/download/models/buffalo_l.zip",
        # 产物是 zip：必须解压成目录，insightface 的 storage.download 也是这么做的
        "unpack": "zip",
    },
    {
        "id": "musiq",
        "name": "MUSIQ 审美评分权重",
        "size_mb": 104,
        # pyiqa 按 torch.hub.get_dir()/pyiqa/<url 文件名> 查找；paths.py 已把
        # TORCH_HOME 钉到数据目录，故这里是 <数据目录>/torch/hub/pyiqa/<文件>
        "path": paths.torch_dir() / "hub" / "pyiqa"
                / "musiq_koniq_ckpt-e95806b9.pth",
        "url": ("https://hf-mirror.com/chaofengc/IQA-PyTorch-Weights/resolve/main/"
                "musiq_koniq_ckpt-e95806b9.pth"),
        "unpack": "file",
    },
]

def detect() -> dict:
    """硬件与推理后端检测 + 模型安装状态。

    EP 的选择交给 hardware 模块按显卡厂商决定，这里只做转述，
    避免出现"界面提示推荐 X、实际跑 Y"的分裂。
    """
    hw = hardware.describe_backend()
    models = []
    for m in MODELS:
        models.append({"id": m["id"], "name": m["name"], "size_mb": m["size_mb"],
                       "installed": _installed(m)})
    net = load_net_settings()
    return {"providers": hw["providers"], "gpu": hw["gpu"],
            "backend": hw["backend"], "backend_hint": hw["backend_hint"],
            "vendor": hw["vendor"], "ranked": hw["ranked"],
            "models": models, "net": net}

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

def _installed(m: dict) -> bool:
    """安装判据：文件非空 / 目录非空。目录型模型（buffalo_l）不能只判 exists，
    否则一个下载失败的半截 zip 文件也会被当成"已就绪"。"""
    p = Path(m["path"])
    if not p.exists():
        return False
    if p.is_file():
        return p.stat().st_size > 0
    return any(p.iterdir())

def _unpack_zip(zip_path: Path, dest: Path) -> None:
    """解压到 dest 目录，按 basename 拍平。

    insightface 的 buffalo_l.zip 是平铺的 5 个 onnx；按 basename 落盘同时
    兼容带顶层目录的打包方式，也顺手挡掉 zip 内的路径穿越。"""
    import zipfile
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = Path(info.filename).name
            if not name:
                continue
            with zf.open(info) as src, (dest / name).open("wb") as out:
                while True:
                    chunk = src.read(1 << 16)
                    if not chunk:
                        break
                    out.write(chunk)
    zip_path.unlink(missing_ok=True)

def download(model_id: str) -> None:
    m = next((m for m in MODELS if m["id"] == model_id), None)
    if not m:
        DL.update(error=f"未知模型 {model_id}")
        return
    dest = Path(m["path"])
    if _installed(m):
        DL.update(running=False, done=1, total=1, error=None)
        return
    net = load_net_settings()
    _apply_proxy_env(net.get("http_proxy"))
    routes = _routes_for(m["url"], net)
    if m.get("unpack") == "zip":
        # zip 型（buffalo_l）：先下到同名 .zip，解压成目录后再清理
        zip_path = dest.parent / (dest.name + ".zip")
        _download_via_routes(routes, zip_path, net.get("http_proxy"), DL)
        _unpack_zip(zip_path, dest)
    else:
        _download_via_routes(routes, dest, net.get("http_proxy"), DL)

def start_download(model_id: str) -> bool:
    """返回是否真的启动了下载（False = 已有下载在进行）。"""
    if DL["running"]:
        return False
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
    return True

def progress() -> dict:
    return {k: DL[k] for k in ("running", "done", "total", "id", "error")}
