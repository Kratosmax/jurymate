"""硬件检测与推理后端（EP）选择。

EP 优先级不写死，按实际显卡厂商决定首选后端：NVIDIA 优先 CUDA，
AMD/Intel 优先 DirectML，Apple 优先 CoreML。然后再在 onnxruntime
**实际可用**的 EP 里筛——装了哪个包才有什么 EP，AMD 机器没装
onnxruntime-directml 时就只剩 CPU，这时报"推荐 DML"没有意义。

首选 EP 不可用时按次选降级（例如 N 卡但 CUDA 包没装好，仍可走 DML）。
"""
import logging
import subprocess
import sys

log = logging.getLogger("jurymate.hardware")

# 显卡名里的厂商特征词（小写匹配）
_VENDOR_TOKENS = {
    "nvidia": ("nvidia", "geforce", "rtx", "gtx", "quadro", "tesla"),
    "amd": ("amd", "radeon"),
    "intel": ("intel", "arc", "iris", "uhd graphics", "hd graphics"),
    "apple": ("apple",),
}

# 各厂商的首选 → 次选 EP 顺序；CPU 统一兜底，不在此表内
_PREFERENCE = {
    "nvidia": ["CUDAExecutionProvider", "DmlExecutionProvider", "CoreMLExecutionProvider"],
    "amd": ["DmlExecutionProvider", "CUDAExecutionProvider", "CoreMLExecutionProvider"],
    "intel": ["DmlExecutionProvider", "CoreMLExecutionProvider", "CUDAExecutionProvider"],
    "apple": ["CoreMLExecutionProvider", "DmlExecutionProvider", "CUDAExecutionProvider"],
    "": ["CUDAExecutionProvider", "DmlExecutionProvider", "CoreMLExecutionProvider"],
}

_GPU_CACHE: list[str] | None = None


def gpu_names() -> list[str]:
    """真实显卡名列表（过滤虚拟显示器；结果缓存，探测失败返回空）。"""
    global _GPU_CACHE
    if _GPU_CACHE is not None:
        return _GPU_CACHE
    names: list[str] = []
    if sys.platform == "win32":
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_VideoController).Name"],
                capture_output=True, text=True, timeout=15,
                # sidecar 是 GUI 子系统（--windowed），没有自己的控制台。
                # 不加这个标志，每次探测显卡都会弹一个空白 PowerShell 窗口，
                # 0.2 秒后消失——用户看到的就是"控制台一闪而过"。
                creationflags=subprocess.CREATE_NO_WINDOW)
            raw = [l.strip() for l in out.stdout.splitlines() if l.strip()]
            names = [n for n in raw if "virtual" not in n.lower()]
        except Exception as e:
            log.warning("hardware: 显卡探测失败 %s", e)
    _GPU_CACHE = names
    return names


def gpu_vendor() -> str:
    """判断显卡厂商：nvidia / amd / intel / apple；识别不出返回空串。"""
    if sys.platform == "darwin":
        return "apple"
    for name in gpu_names():
        low = name.lower()
        for vendor, tokens in _VENDOR_TOKENS.items():
            if any(t in low for t in tokens):
                return vendor
    return ""


def available_providers() -> list[str]:
    """onnxruntime 实际可用的 EP；未安装时返回空。"""
    try:
        import onnxruntime as ort
        return list(ort.get_available_providers())
    except Exception as e:
        log.warning("hardware: onnxruntime 不可用 %s", e)
        return []


def ranked_providers() -> list[str]:
    """按显卡厂商排好的 EP 列表，CPU 永远兜底。

    只返回实际可用的 EP —— 把没装的 EP 传给 onnxruntime 会直接报错。
    """
    avail = available_providers()
    vendor = gpu_vendor()
    pref = _PREFERENCE.get(vendor, _PREFERENCE[""])
    ranked = [p for p in pref if p in avail]
    if "CPUExecutionProvider" in avail:
        ranked.append("CPUExecutionProvider")
    return ranked


def describe_backend() -> dict:
    """给界面用的后端描述：实际会用哪个 EP + 一句人话提示。"""
    avail = available_providers()
    vendor = gpu_vendor()
    ranked = ranked_providers()
    first = ranked[0] if ranked else ""
    if first == "CUDAExecutionProvider":
        backend, hint = "cuda", "N 卡：CUDA EP（推荐）"
    elif first == "DmlExecutionProvider":
        backend, hint = "dml", "AMD/Intel 显卡：DirectML EP（推荐）"
    elif first == "CoreMLExecutionProvider":
        backend, hint = "coreml", "Apple：CoreML EP"
    else:
        backend, hint = "cpu", "纯 CPU 推理"
    # 首选 EP 没装时补一句，避免"提示推荐 X 但实际跑 CPU"的困惑
    if vendor and first == "CPUExecutionProvider":
        want = _PREFERENCE[vendor][0]
        pkg = {"CUDAExecutionProvider": "onnxruntime-gpu",
               "DmlExecutionProvider": "onnxruntime-directml",
               "CoreMLExecutionProvider": "onnxruntime"}.get(want, "onnxruntime")
        hint += f"（未检测到 {pkg}，装了可加速）"
    return {"backend": backend, "backend_hint": hint, "vendor": vendor,
            "gpu": " / ".join(gpu_names()), "providers": avail,
            "ranked": ranked}
