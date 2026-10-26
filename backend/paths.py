"""应用数据目录：JuryMate 自有缓存/权重/配置的唯一根。

所有落盘路径都从这里派生，不在各模块里各拼一遍，避免换目录时漏改。
Windows 落 %LOCALAPPDATA%/JuryMate（与日志同根），其他平台回落 ~/.cache/JuryMate。

导入本模块即把 TORCH_HOME 指向数据目录——pyiqa 的权重位置由
torch.hub.get_dir() 决定（= $TORCH_HOME/hub），而该值在 pyiqa 导入时固化，
所以必须在任何 torch/pyiqa 导入之前设好。pipeline 顶层 import faces，
faces import paths，因此正常调用链上这个时机是安全的。
"""
import os
from pathlib import Path


def data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or (Path.home() / ".cache")
    return Path(base) / "JuryMate"


def ensure(sub: str = "") -> Path:
    """取（并创建）数据目录下的子目录。"""
    d = data_dir() / sub if sub else data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir() -> Path:
    return ensure("logs")


def torch_dir() -> Path:
    """torch 缓存根（TORCH_HOME）。pyiqa 权重落在 <此>/hub/pyiqa/。"""
    return ensure("torch")


# 照片目录下的会话状态目录名（state.json / thumbs / recall_stats 都在里面）。
# 用户删掉它 = 重置该目录的整理进度。
STATE_DIRNAME = ".jurymate"


# 在 pyiqa/torch 被导入前把缓存根钉到 JuryMate 目录。
# 强制覆盖（不用 setdefault）：后端是独立子进程，不影响其他工具；
# 若沿用外部 TORCH_HOME，权重会散落到 JuryMate 目录之外。
os.environ["TORCH_HOME"] = str(torch_dir())
