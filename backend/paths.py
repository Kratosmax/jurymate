"""应用数据目录：JuryMate 自有缓存/权重/配置的唯一根。

所有落盘路径都从这里派生，不在各模块里各拼一遍，避免换目录时漏改。

用户可以自定义根目录（设置界面或 JURYMATE_DATA_DIR 环境变量）。解析优先级：
1. 环境变量 JURYMATE_DATA_DIR —— 便携版/多实例/测试时覆盖，最高优先级
2. 配置文件里的 data_root —— 用户在设置里选过的目录
3. 默认 %LOCALAPPDATA%/JuryMate（其他平台 ~/.cache/JuryMate）

配置文件本身必须放在"能确定的位置"，否则用户改了根目录后找不到它。
所以配置固定落在默认位置（bootstrap 文件），只有实际数据跟着用户走。

导入本模块即把 TORCH_HOME 指向数据目录——pyiqa 的权重位置由
torch.hub.get_dir() 决定（= $TORCH_HOME/hub），而该值在 pyiqa 导入时固化，
所以必须在任何 torch/pyiqa 导入之前设好。

同样在导入时把 TEMP/TMP 指向数据目录下的 tmp——PyInstaller onefile 每次
启动都会把全部依赖解压到 %TEMP%/_MEI*（约 1.1GB），进程被强杀时删不掉，
累积起来会把系统盘吃满（实测 27 个共 27.45GB）。把 TEMP 重定向到数据目录
后，这些残留就跟其它缓存一起放在用户指定的位置，不再污染系统盘。
"""
import json
import os
import sys
from pathlib import Path

# 配置文件名：固定落在默认位置，作为"用户选了哪个根目录"的唯一来源
CONF_NAME = "paths.json"

# 照片目录下的会话状态目录名（state.json / thumbs / recall_stats 都在里面）。
# 用户删掉它 = 重置该目录的整理进度。
STATE_DIRNAME = ".jurymate"


def default_root() -> Path:
    """默认数据根：Windows 用 %LOCALAPPDATA%，其他平台用 ~/.cache。"""
    base = os.environ.get("LOCALAPPDATA") or (Path.home() / ".cache")
    return Path(base) / "JuryMate"


def _conf_path() -> Path:
    return default_root() / CONF_NAME


def read_conf() -> dict:
    try:
        return json.loads(_conf_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_conf(**kv) -> None:
    """合并写配置（只覆盖传入的键）。"""
    cur = read_conf()
    cur.update(kv)
    p = _conf_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")


def data_dir() -> Path:
    """当前生效的数据根。每次调用都重新解析，支持运行中切换。"""
    env = os.environ.get("JURYMATE_DATA_DIR", "").strip()
    if env:
        return Path(env)
    conf = read_conf().get("data_root", "").strip()
    if conf:
        return Path(conf)
    return default_root()


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


def tmp_dir() -> Path:
    """进程临时目录（PyInstaller 的 _MEI* 解压目标也在这里）。"""
    return ensure("tmp")


def thumbs_dir(session_state: Path) -> Path:
    """缩略图缓存：跟随会话状态目录（在照片目录下）。

    刻意不放到数据根：缩略图和照片一一对应，跟着照片目录走，
    用户搬移照片目录时缓存一起走，不会留下孤儿文件。
    """
    d = session_state / "thumbs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _redirect_temp() -> None:
    """把 TEMP/TMP 指到数据目录下的 tmp，让 _MEI* 落在用户指定的位置。

    只在被 PyInstaller 打包（frozen）时改：源码直跑时不该动开发者的环境。
    子进程会继承这两个变量，所以 sidecar 及其衍生的进程都会跟随。
    """
    if not getattr(sys, "frozen", False):
        return
    try:
        t = str(tmp_dir())
        os.environ["TEMP"] = t
        os.environ["TMP"] = t
        import tempfile
        tempfile.tempdir = t
    except Exception:
        pass   # 目录不可写等情况下退回系统默认，不能让后端起不来


# TORCH_HOME 必须在任何 torch/pyiqa 导入之前设好
os.environ["TORCH_HOME"] = str(torch_dir())
_redirect_temp()
