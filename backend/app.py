"""JuryMate 服务：Flask API + 状态持久化。

状态保存在照片目录的 .jurymate/state.json（单文件夹单会话策略），
随时关闭、下次启动同目录自动恢复。
"""
import json
import logging
import logging.handlers
import os
import shutil
import sys
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory


# --windowed 打包时 sys.stdout / sys.stderr 都是 None。
# 我们自己的日志用 _stderr_is_tty 兜住了，但**第三方库**会直接
# print() 或 sys.stdout.write()——例如 pyiqa 加载权重时往 stdout 写
# 下载进度条，于是抛：
#   AttributeError: 'NoneType' object has no attribute 'write'
# 这个异常还会被包装成 ModelUnavailable，看起来像"模型缺失"，
# 实际模型就在本地，排查方向完全被带偏（0.1.9 真机验收时踩到）。
# 必须在导入任何第三方库之前换掉，所以这段放在文件最前面。
class _NullIO:
    def write(self, *a, **k):
        return 0
    def flush(self):
        pass
    def isatty(self):
        return False
    def fileno(self):
        raise OSError("no fileno")
    def readline(self, *a, **k):
        return ""
    def close(self):
        pass
    @property
    def encoding(self):
        return "utf-8"
    @property
    def errors(self):
        return "replace"


if sys.stdout is None:
    sys.stdout = _NullIO()
if sys.stderr is None:
    sys.stderr = _NullIO()


import aesthetic
import appsettings
import errors
import paths
import pipeline

# 日志：始终落 <数据目录>/logs/backend.log（滚动 5×2MB）。
# 正式版是 --windowed 打包（无控制台），所以绝不能只靠 stdout：
# 用户遇到问题时唯一能拿到线索的就是这个文件。
_DATA_DIR = paths.logs_dir()
_fh = logging.handlers.RotatingFileHandler(_DATA_DIR / "backend.log",
                                           maxBytes=2 * 1024 * 1024, backupCount=5,
                                           encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
_handlers: list[logging.Handler] = [_fh]

# 需要看详细日志时再挂控制台输出。
# 注意 sys.stderr 在 --windowed 打包下是 None，必须先判空再访问属性。
def _stderr_is_tty() -> bool:
    try:
        return bool(sys.stderr) and sys.stderr.isatty()
    except Exception:
        return False


if os.environ.get("JURYMATE_DEBUG") or _stderr_is_tty():
    try:
        _ch = logging.StreamHandler()
        _ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        _handlers.append(_ch)
    except Exception:
        pass

# 注意：basicConfig 只在 root 没有 handler 时才生效，所以级别必须在这里一次定好。
# 之前写成「先 basicConfig(INFO) 再 basicConfig(DEBUG)」，第二次是空操作，
# 导致 JURYMATE_DEBUG 根本不起作用。
logging.basicConfig(
    level=logging.DEBUG if os.environ.get("JURYMATE_DEBUG") else logging.INFO,
    handlers=_handlers,
    format="%(asctime)s %(levelname)s %(message)s",
    force=True,
)
log = logging.getLogger("jurymate")

# 版本唯一来源是 src-tauri/tauri.conf.json。三条获取途径按可靠性排序：
# 1. 环境变量 JURYMATE_VERSION —— Rust 壳拉起 sidecar 时注入（打包版走这条）
# 2. 上游 tauri.conf.json —— 源码直跑 backend/app.py 时可用
# 3. unknown —— 都没有时如实标注，用于前端提示版本不一致
def _read_version() -> str:
    import json as _json
    env_ver = os.environ.get("JURYMATE_VERSION", "").strip()
    if env_ver:
        return env_ver
    try:
        conf = Path(__file__).resolve().parent.parent / "src-tauri" / "tauri.conf.json"
        return str(_json.loads(conf.read_text(encoding="utf-8"))["version"])
    except Exception:
        return "unknown"

APP_VERSION = _read_version()

# 启动即把用户配置的代理写进环境变量：insightface(requests)/torch.hub(urlopen)
# 的自动下载只认环境变量，晚设一步就会在用户填了代理的情况下仍走直连。
import models as models_mod  # noqa: E402
models_mod._apply_proxy_env(models_mod.load_net_settings().get("http_proxy"))

# 前端静态目录仅源码模式存在（浏览器访问）；PyInstaller 打包模式下
# 前端由 Tauri webview 提供，Flask 只承担 API。
# 前端产物在 dist/（vite build 输出），不是 frontend/。
_FRONTEND = Path(__file__).resolve().parent.parent / "dist"
if not (_FRONTEND / "index.html").exists():
    _FRONTEND = None
app = Flask(__name__, static_folder=str(_FRONTEND) if _FRONTEND else None,
            static_url_path="")

# CORS：Tauri webview 的 origin（http://tauri.localhost）与 sidecar（127.0.0.1）
# 跨域——没有这个头，打包版前端的所有 API 响应都会被 webview 拦截（实测数据全空）。
# 本地单机工具，全开是安全默认。
# OPTIONS 用 before_request 短路：Flask 自动 OPTIONS 响应曾被观察到缺失
# CORS 头（打包版 preflight 失败 → POST 永远不发），短路处理根除该路径。
@app.before_request
def _cors_preflight():
    if request.method == "OPTIONS":
        resp = app.make_default_options_response()
        return _cors_headers(resp)

@app.after_request
def _cors_headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Max-Age"] = "86400"
    return resp

STATE = {"photos": [], "groups": [], "folder": None, "settings": {}, "undo_stack": []}

# 迁移任务的实时状态（前端轮询 /api/paths/progress）。
# 必须定义在使用它的函数之前——Python 在调用时才解析全局名，
# 但放在文件末尾会让可读性变差且容易被后续编辑打乱顺序。
MIGRATE = {"running": False, "done": 0, "total": 0, "current": "",
           "migrated": [], "skipped": [], "error": None,
           "target": "", "old": ""}
TASK = {"running": False, "done": 0, "total": 0, "stage": "", "error": None,
        "error_kind": None, "error_hint": ""}
_LOCK = threading.Lock()          # 启动任务互斥
_SNAPSHOT_LOCK = threading.Lock() # STATE 列表引用快照（API 读 vs worker 原地写）

def snapshot_state() -> tuple[list, list]:
    """原子取出 photos/groups 的浅拷贝（读侧）。"""
    with _SNAPSHOT_LOCK:
        return list(STATE["photos"]), list(STATE["groups"])

DEFAULT_SETTINGS = {
    "burst_gap": 3.0, "phash_dist": 10, "pk_gap": 7.0,
    # 100 分制：40 分以下判"低分"
    "min_score": 40.0, "ear_closed": 0.18, "ear_open": 0.25,
    # 评分模式：默认空 = 还没选。没选之前不加载、不下载任何评分模型，
    # 由设置页让用户显式二选一，选完才开始下载对应权重。
    #   aesthetic = NIMA（快，评构图/光影/氛围）
    #   quality   = MUSIQ（慢，评画质/噪点/压缩）
    "score_mode": "",
    # 综合分里"审美"的权重（0-100，步进 5），剩余给清晰度。
    # 默认 50 即 100:100（1:1）。界面不展示这个比例，只在计算时生效。
    "score_weight": 50,
    # 解码线程数。0 = 由程序自动决定（min(8, CPU 核数)）。
    # 实测瓶颈是 RAW 解码（人脸 279ms/张、审美 348ms/张，其余是解码），
    # 解码是 I/O+CPU 混合且 rawpy 不持 GIL，加线程确实能加快。
    "scan_workers": 0,
}

# 启动即载入应用级设置：这些偏好跨照片目录共用，不能等 /api/start 才恢复。
# 以前它们只存在 <照片目录>/.jurymate/state.json 里，进程一起来
# STATE["settings"] 是空的，设置页读到的就是默认值——用户看到的就是
# "上次选的评分模式没保留"。
STATE["settings"] = {**DEFAULT_SETTINGS, **appsettings.load()}

def state_file() -> Path | None:
    if not STATE["folder"]:
        return None
    d = Path(STATE["folder"]) / paths.STATE_DIRNAME
    d.mkdir(exist_ok=True)
    return d / "state.json"

STATE_VERSION = 3   # 评分映射/状态机变更时 +1；旧版本 state 直接弃用重扫
                    # v3：100 分制改为「模型各自尺子」（NIMA×10 / MUSIQ 原值），
                    #     旧分数口径不同，一律作废重扫。

def save_state():
    f = state_file()
    if f:
        slim = {k: v for k, v in STATE.items() if k != "undo_stack"}
        slim["version"] = STATE_VERSION
        f.write_text(json.dumps(slim, ensure_ascii=False), encoding="utf-8")

def load_state(folder: str) -> bool:
    f = Path(folder) / paths.STATE_DIRNAME / "state.json"
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("version") != STATE_VERSION:
                log.warning("state 版本 %s ≠ %d，弃用重扫", data.get("version"), STATE_VERSION)
                return False
            STATE.update(data)
            STATE["undo_stack"] = []
            # 目录里的 settings 是历史副本，可能过期；应用级设置永远优先。
            # 不这样覆盖的话，切回旧目录会把用户刚改的评分模式/权重又顶回去。
            STATE["settings"] = {**DEFAULT_SETTINGS,
                                 **data.get("settings", {}),
                                 **appsettings.load()}
            log.info("恢复进度：%d 张", len(STATE["photos"]))
            return True
        except Exception as e:
            log.warning("状态文件损坏，重新扫描：%s", e)
    return False

def push_undo():
    STATE["undo_stack"].append(json.dumps(
        {"photos": [{k: ph.get(k) for k in ("path", "status", "reject_reasons",
                                            "manual_winner")}
                    for ph in STATE["photos"]],
         "groups": [{k: g.get(k) for k in ("id", "winner", "needs_pk")}
                    for g in STATE["groups"]]}, ensure_ascii=False))
    if len(STATE["undo_stack"]) > 30:
        STATE["undo_stack"].pop(0)

def refresh_statuses():
    """重算 status。人工意志（manual_winner：召回/"全要"）优先于组逻辑，
    否则一次 PK 就会把召回的图打回 pending（V1.0 实测 bug）。"""
    for ph in STATE["photos"]:
        if ph["reject_reasons"]:
            ph["status"] = "reject"
            ph["manual_winner"] = False
        elif ph.get("manual_winner"):
            ph["status"] = "winner"
        else:
            ph["status"] = None  # 交给组逻辑
    for g in STATE["groups"]:
        for i in g["members"]:
            ph = STATE["photos"][i]
            if ph["status"] is None:
                ph["status"] = "winner" if i == g["winner"] else (
                    "pending" if not ph["reject_reasons"] else "reject")

@app.route("/")
def index():
    if not app.static_folder:
        return jsonify({"ok": True, "mode": "sidecar-api-only",
                        "hint": "前端由 Tauri 提供；浏览器模式请用源码运行"})
    return send_from_directory(app.static_folder, "index.html")

def preflight_models():
    """扫描前的模型可用性检查：缺失就抛 ModelUnavailable，别等跑到一半才炸。

    恢复进度/增量补扫本身不推理，但后续的召回补评分、复查都会用到模型；
    更重要的是——如果模型缺了却让用户以为"整理完成"，结果里所有人脸信号
    都是空的，比直接报错难排查得多。
    """
    mode = STATE["settings"].get("score_mode") or ""
    if not mode:
        raise errors.ModelUnavailable(
            "评分模型（未选择）", "还没有选择评分模式",
            "请在「设置 → 评分模型」里选择「审美优先」或「画质优先」")
    need_score_id = "nima" if mode == "aesthetic" else "musiq"
    # 只要求"当前评分模式"对应的那一个评分模型，不能因为另一个没下载就拦下扫描
    required = [m for m in models_mod.models()
                if m["id"] in ("face_landmarker", "buffalo_l", need_score_id)]
    missing = [m for m in required if not models_mod._installed(m)]
    if missing:
        names = "、".join(m["name"] for m in missing[:2])
        if len(missing) > 2:
            names += f" 等 {len(missing)} 个"
        raise errors.ModelUnavailable(
            names,
            detail="尚未下载",
            hint="请在「设置 → 模型」里点下载；国内网络可在网络设置里开启镜像站或填代理",
        )

def peek_state_settings(folder: str) -> dict:
    """只从磁盘 state.json 里读 settings，不加载照片。

    为什么要它：进程刚启动时 STATE["settings"] 是空的，而用户上次已经选过
    评分模式（存在 state.json 里）。不先读一眼，模式守卫就会把第一次
    「开始整理」拦下来，误报「请先选择评分模式」。
    """
    f = Path(folder) / paths.STATE_DIRNAME / "state.json"
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        if data.get("version") != STATE_VERSION:
            return {}
        s = data.get("settings")
        return s if isinstance(s, dict) else {}
    except Exception:
        return {}


@app.route("/api/start", methods=["POST"])
def start():
    body = request.json or {}
    folder = body.get("folder", "").strip()
    incremental = bool(body.get("incremental"))
    # reset=True：无视磁盘缓存，丢掉全部人工决策与状态，从零重建。
    # 用户第一次打开某个目录却看到"人工召回"标记时就是靠这个清掉——
    # .jurymate/state.json 是跨会话的，之前点过召回就会一直留着。
    reset = bool(body.get("reset"))
    if not folder or not Path(folder).is_dir():
        return jsonify({"ok": False, "error": "目录不存在"}), 400
    # 刚启动时 STATE["settings"] 是空的，但用户上次可能已经选过评分模式。
    # 先看磁盘上的 state.json，避免把第一次「开始整理」误判成"没选模式"。
    if not (STATE["settings"].get("score_mode") or ""):
        peeked = peek_state_settings(folder)
        if peeked.get("score_mode"):
            STATE["settings"] = {**DEFAULT_SETTINGS, **peeked}
    # 没选评分模式就不该开跑：既算不出分，也没有对应的模型可加载。
    # 用 need_mode 让前端直接跳到设置页，而不是弹一句用户看不懂的报错。
    if not (STATE["settings"].get("score_mode") or ""):
        return jsonify({"ok": False, "error": "请先选择评分模式", "need_mode": True}), 400
    with _LOCK:
        if TASK["running"]:
            return jsonify({"ok": False, "error": "任务进行中"}), 409
        TASK.update(running=True, done=0, total=0, stage="", error=None,
                    error_kind=None, error_hint="")
    STATE.update(folder=folder,
                 settings={**DEFAULT_SETTINGS, **STATE.get("settings", {})})

    def worker():
        try:
            def progress(done, total, name):
                TASK.update(done=done, total=total, stage=name)
            if reset:
                # 强制重新整理：清内存态 + 删磁盘 state + 删缩略图缓存。
                # 三者必须一起清：只清内存会让 load_state 又把旧的读回来；
                # 不删缩略图则仍会沿用旧图的预览/尺寸。
                with _SNAPSHOT_LOCK:
                    STATE.update(photos=[], groups=[], undo_stack=[])
                sd = Path(folder) / paths.STATE_DIRNAME
                try:
                    if sd.exists():
                        shutil.rmtree(sd)
                        log.info("reset: 已清除状态目录 %s", sd)
                except Exception as e:
                    log.warning("reset: 清理 %s 失败（继续全量重扫）：%s", sd, e)
                TASK.update(stage="已清除缓存，从零重新整理…")
            # 「开始整理」= 读缓存 + 自动补扫新文件。
            # 旧行为是 load_state 成功就直接 return，导致用户往目录里加了新照片
            # 再点开始整理，界面还是旧结果——必须补扫。
            # 磁盘 state 是唯一事实源：用户删了它 = 明确要重置，退化为全量重扫。
            if not reset:
                if STATE["photos"] and STATE["folder"] \
                        and Path(STATE["folder"]).resolve() != Path(folder).resolve():
                    with _SNAPSHOT_LOCK:
                        STATE.update(photos=[], groups=[])
                if not STATE["photos"]:
                    load_state(folder)
                if STATE["photos"]:
                    with _SNAPSHOT_LOCK:
                        refresh_statuses()
                    mode_now = STATE["settings"].get("score_mode") or ""
                    # 评分模式换过但还没重算：旧分数是另一个模型给的，
                    # 直接增量补扫会让新旧口径混在一张表里，所以先把缓存里的
                    # 全部分数用当前模型刷一遍（复用 view.jpg，不重新解码 RAW）。
                    if STATE.get("scored_mode") != mode_now:
                        aesthetic.set_mode(mode_now)
                        preflight_models()
                        TASK.update(stage="评分模式已变更，重新评分…")
                        pipeline.score_candidates(
                            STATE["photos"], Path(folder) / paths.STATE_DIRNAME,
                            STATE["settings"], progress, report_all=len(STATE["photos"]))
                        with _SNAPSHOT_LOCK:
                            pipeline.rescore(STATE["photos"], STATE["groups"],
                                             STATE["settings"])
                            STATE["scored_mode"] = mode_now
                    TASK.update(stage="检查新照片…")
                    try:
                        n_new = pipeline.add_new_photos(
                            folder, STATE["photos"], STATE["groups"],
                            Path(folder) / paths.STATE_DIRNAME, STATE["settings"], progress)
                    except errors.ModelUnavailable:
                        raise
                    with _SNAPSHOT_LOCK:
                        TASK.update(done=len(STATE["photos"]), total=len(STATE["photos"]),
                                    stage=(f"完成：新增 {n_new} 张" if n_new else "已是最新，无需补扫"))
                        save_state()
                    return
            # 全量扫描：需要全套模型，缺了立刻失败而不是产出"无人脸"的空结果
            TASK.update(stage="检查模型…")
            aesthetic.set_mode(STATE["settings"].get("score_mode") or "")
            preflight_models()
            with _SNAPSHOT_LOCK:
                STATE.update(photos=[], groups=[])
            # 同上：扫描期间不持锁，让前端能持续读进度与已有状态
            result = pipeline.run(folder, Path(folder) / paths.STATE_DIRNAME,
                                  STATE["settings"], progress)
            with _SNAPSHOT_LOCK:
                STATE.update(photos=result["photos"], groups=result["groups"])
                STATE["scored_mode"] = STATE["settings"].get("score_mode") or ""
                refresh_statuses()
                save_state()
        except errors.ModelUnavailable as e:
            # 模型类失败：给用户可操作的信息，而不是一句 traceback 尾巴
            log.error("管线中止（模型不可用）：%s", e)
            TASK.update(error=str(e), error_kind="model", error_hint=e.hint)
        except Exception as e:
            log.exception("管线失败")
            TASK.update(error=str(e), error_kind="other", error_hint="")
        finally:
            TASK["running"] = False
    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True, "restored": False})

@app.route("/api/progress")
def progress_api():
    return jsonify({k: TASK[k] for k in ("running", "done", "total", "stage",
                                             "error", "error_kind", "error_hint")})


@app.route("/api/rescore", methods=["POST"])
def rescore_api():
    """按当前设置重算综合分与组内排序。

    - 只改了权重：纯重算，毫秒级，不碰模型。
    - 换了评分模式：分数本身要换模型重算（复用已解压的 view.jpg，不重新解码
      RAW、不重新检脸），NIMA 约 6 秒、MUSIQ 约 55 秒。
    """
    with _LOCK:
        if TASK["running"]:
            return jsonify({"ok": False, "error": "任务进行中"}), 409
    if not STATE["photos"]:
        return jsonify({"ok": False, "error": "还没有可重算的结果"}), 400
    mode = STATE["settings"].get("score_mode") or ""
    if not mode:
        return jsonify({"ok": False, "error": "请先选择评分模式", "need_mode": True}), 400

    need_model = STATE.get("scored_mode") != mode
    if not need_model:
        with _SNAPSHOT_LOCK:
            pipeline.rescore(STATE["photos"], STATE["groups"], STATE["settings"])
            refresh_statuses()
            save_state()
        log.info("rescore: 仅重算综合分（权重变化）")
        return jsonify({"ok": True, "rescored": False})

    folder = STATE["folder"]
    with _LOCK:
        TASK.update(running=True, done=0, total=0, stage="", error=None,
                    error_kind=None, error_hint="")

    def worker():
        try:
            def progress(done, total, name):
                TASK.update(done=done, total=total, stage=name)
            aesthetic.set_mode(mode)
            preflight_models()
            photos = STATE["photos"]
            pipeline.score_candidates(photos, Path(folder) / paths.STATE_DIRNAME,
                                      STATE["settings"], progress,
                                      report_all=len(photos))
            with _SNAPSHOT_LOCK:
                pipeline.rescore(photos, STATE["groups"], STATE["settings"])
                STATE["scored_mode"] = mode
                refresh_statuses()
                save_state()
            log.info("rescore: 已用 %s 重新评分 %d 张", mode, len(photos))
        except errors.ModelUnavailable as e:
            log.error("重新评分中止（模型不可用）：%s", e)
            TASK.update(error=str(e), error_kind="model", error_hint=e.hint)
        except Exception as e:
            log.exception("重新评分失败")
            TASK.update(error=str(e), error_kind="other", error_hint="")
        finally:
            TASK["running"] = False

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True, "rescored": True})

# 启动就绪状态：前端用它决定是否允许操作。
# sidecar 是 PyInstaller onefile，启动要解压约 1.1GB，实测十几秒；
# 这期间前端点「开始整理」是连不上端口的，用户看到的就是"点了没反应"。
PRELOAD = {"model_ready": False, "model_error": ""}


@app.route("/api/ready", methods=["GET"])
def api_ready():
    # backend_ready 与 model_ready 分开：没选评分模式时模型永远不会就绪，
    # 但后端本身是好的，前端不该一直转圈。
    return jsonify({"ok": True, "backend_ready": True,
                    "model_ready": PRELOAD["model_ready"],
                    "model_error": PRELOAD["model_error"],
                    "score_mode": STATE["settings"].get("score_mode") or ""})


@app.route("/api/state")
def state_api():
    # 快照读：worker 线程可能正在原地扩展 photos/groups（增量扫描），
    # 直接迭代同一 list 会 RuntimeError: list changed size during iteration
    with _SNAPSHOT_LOCK:
        photos_src = list(STATE["photos"])
        groups_src = list(STATE["groups"])
    photos = [{k: v for k, v in ph.items() if k != "phash"} for ph in photos_src]
    groups = []
    for g in groups_src:
        members = [dict(photos_src[i], idx=i) for i in g["members"]
                   if 0 <= i < len(photos_src)]
        groups.append({k: g.get(k) for k in ("id", "winner", "gap", "needs_pk")}
                      | {"members": members})
    return jsonify({"folder": STATE["folder"], "photos": photos, "groups": groups,
                    "settings": dict(STATE["settings"]),
                    # 前端据此决定分项标签写「审美」还是「画质」
                    "score_mode": STATE["settings"].get("score_mode") or "",
                    "scored_mode": STATE.get("scored_mode") or ""})

@app.route("/api/pk", methods=["POST"])
def pk():
    """body: {group_id, action: "pick"|"all"|"none", pick: member_idx}"""
    body = request.json or {}
    gid = body.get("group_id")
    with _SNAPSHOT_LOCK:
        g = next((g for g in STATE["groups"] if g["id"] == gid), None)
        if not g:
            return jsonify({"ok": False, "error": "组不存在"}), 404
        return _pk_apply(g, body)

def _pk_apply(g: dict, body: dict):
    action = body.get("action")
    push_undo()
    mem = g["members"]
    if action == "all":
        # 全要：整组都是胜出（人工意志），不该被后续 refresh 打回 pending
        for i in mem:
            STATE["photos"][i]["reject_reasons"] = []
            STATE["photos"][i]["manual_winner"] = True
    elif action == "none":
        for i in mem:
            STATE["photos"][i]["reject_reasons"] = ["全组淘汰"]
            STATE["photos"][i]["status"] = "reject"
        g["winner"] = -1
        g["needs_pk"] = False
        save_state()
        return jsonify({"ok": True})
    else:
        pick = body.get("pick")
        if pick not in mem:
            return jsonify({"ok": False, "error": "无效选择"}), 400
        for i in mem:
            STATE["photos"][i]["reject_reasons"] = [] if i == pick else ["组内落选"]
            STATE["photos"][i]["manual_winner"] = False
    refresh_statuses()
    g["needs_pk"] = False
    save_state()
    return jsonify({"ok": True})

@app.route("/api/recall", methods=["POST"])
def recall():
    """复查召回：清原因、补审美分、定为 winner（人工确认）。

    每次召回把原因落进 recall_stats.json——这是阈值自校准的数据源：
    某类原因被召回得越多，说明该阈值越不可信。"""
    path = (request.json or {}).get("path")
    # 评分（可能触发模型加载，首次约 20 秒）必须在锁外做：持锁推理会把
    # /api/state 等所有读请求一起卡住，界面表现为假死。
    with _SNAPSHOT_LOCK:
        ph = next((p for p in STATE["photos"] if p["path"] == path), None)
        if not ph:
            return jsonify({"ok": False, "error": "照片不存在"}), 404
        need_score = ph.get("aesthetic") is None
    if need_score:
        try:
            ph_aes = pipeline.aesthetic.score(pipeline.load_preview(path))
        except errors.ModelUnavailable as e:
            return jsonify({"ok": False, "error": str(e),
                            "model": e.model, "hint": e.hint}), 503
    else:
        ph_aes = None
    with _SNAPSHOT_LOCK:
        ph = next((p for p in STATE["photos"] if p["path"] == path), None)
        if not ph:
            return jsonify({"ok": False, "error": "照片不存在"}), 404
        return _recall_apply(ph, path, ph_aes)

def _recall_apply(ph: dict, path: str, ph_aes: float | None = None):
    push_undo()
    old_reasons = list(ph["reject_reasons"])
    ph["reject_reasons"] = []
    ph["manual_winner"] = True   # 人工召回 = 持久胜出，后续 refresh 不得覆盖
    if ph_aes is not None:
        ph["aesthetic"] = ph_aes
    ph["score"] = pipeline.composite_score(ph, ph.get("aesthetic"), [],
                                           STATE.get("settings") or {})
    ph["status"] = "winner"
    try:
        sf = state_file()
        if sf:
            stats = {"recalls": []}
            sp = sf.parent / "recall_stats.json"
            if sp.exists():
                stats = json.loads(sp.read_text(encoding="utf-8"))
            stats["recalls"].append({"name": ph["name"], "reasons": old_reasons,
                                     "aesthetic": ph.get("aesthetic")})
            sp.write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        log.warning("recall 统计落盘失败（不影响召回）: %s", e)
    save_state()
    return jsonify({"ok": True, "aesthetic": ph.get("aesthetic")})


@app.route("/api/set_status", methods=["POST"])
def set_status():
    """人工指定单张照片的胜负，绕过组逻辑。

    用户需要能直接说"这张我要 / 这张不要"，而不是只能接受 AI 的组内选择。
    body: {path, status: "winner"|"reject"}

    与 pk 接口的区别：pk 作用于"组"（选组内胜者），这里作用于"单张"，
    且不受 group.winner 影响——refresh_statuses 里 manual_winner 优先于
    组逻辑，所以这样设的胜出不会被后续重算覆盖。
    """
    body = request.json or {}
    path = body.get("path")
    want = body.get("status")
    if want not in ("winner", "reject"):
        return jsonify({"ok": False, "error": "status 只能是 winner 或 reject"}), 400
    with _SNAPSHOT_LOCK:
        ph = next((p for p in STATE["photos"] if p["path"] == path), None)
        if not ph:
            return jsonify({"ok": False, "error": "照片不存在"}), 404
        # 快照用于撤销：人工改状态是最容易点错的操作
        STATE["undo_stack"].append(json.dumps(
            {"photos": [{"path": ph["path"], "status": ph["status"],
                         "reject_reasons": ph["reject_reasons"],
                         "manual_winner": ph.get("manual_winner", False)}],
             "groups": []}, ensure_ascii=False))
        if want == "winner":
            # 人工定胜：清掉机器给的落选理由，否则 refresh 会立刻把它判回 reject
            ph["reject_reasons"] = []
            ph["manual_winner"] = True
            ph["status"] = "winner"
        else:
            # 必须带上 reject_reasons，否则 refresh_statuses 看到空原因会把它
            # 当成"待定"重新按组逻辑算，落选状态就丢了（实测点完又变回胜出）。
            ph["manual_winner"] = False
            if "人工落选" not in ph["reject_reasons"]:
                ph["reject_reasons"] = list(ph["reject_reasons"]) + ["人工落选"]
            ph["status"] = "reject"
        refresh_statuses()
        # refresh 会把 manual_winner 的图改回 winner，这里再确认一次
        ph["status"] = want
        if want == "winner":
            ph["manual_winner"] = True
        # 单图模式是逐张处理的：如果这一组已经没有任何"待定"成员，
        # 就把 needs_pk 关掉，否则它会一直留在决策队列里出不去。
        gi = next((i for i, p in enumerate(STATE["photos"]) if p is ph), None)
        if gi is not None:
            for g in STATE["groups"]:
                if gi in g["members"]:
                    if all(STATE["photos"][i].get("status") in ("winner", "reject")
                           for i in g["members"] if 0 <= i < len(STATE["photos"])):
                        g["needs_pk"] = False
                    break
    save_state()
    return jsonify({"ok": True, "path": path, "status": want})


@app.route("/api/undo", methods=["POST"])
def undo():
    with _SNAPSHOT_LOCK:
        return _undo_apply()

def _undo_apply():
    if not STATE["undo_stack"]:
        return jsonify({"ok": False, "error": "没有可撤销的操作"}), 400
    snap = json.loads(STATE["undo_stack"].pop())
    by_path = {p["path"]: p for p in STATE["photos"]}
    for old in snap["photos"]:
        cur = by_path.get(old["path"])
        if cur:
            cur["status"] = old["status"]
            cur["reject_reasons"] = old["reject_reasons"]
            cur["manual_winner"] = bool(old.get("manual_winner"))
    for old in snap["groups"]:
        cur = next((g for g in STATE["groups"] if g["id"] == old["id"]), None)
        if cur:
            cur["winner"] = old["winner"]
            cur["needs_pk"] = old["needs_pk"]
    save_state()
    return jsonify({"ok": True})

def _same_content(a: Path, b: Path) -> bool:
    """内容一致判定：先比大小（快），大小相同再比 SHA-256。"""
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
        import hashlib

        def _h(p: Path) -> str:
            h = hashlib.sha256()
            with p.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    h.update(chunk)
            return h.hexdigest()

        return _h(a) == _h(b)
    except OSError as e:
        log.warning("organize: 比对失败，按不同处理 %s: %s", a, e)
        return False


def _organize_core(folder: Path, status_by_path: dict, dry: bool,
                   only_set=None) -> dict:
    """归类核心：把照片按胜负**复制**到 winners/ 与 rejects/。

    抽成纯函数是为了能脱离 Flask/全局 STATE 直接测判重逻辑
    （见 temp/test_organize_copy.py）。status_by_path: {绝对路径: "winner"|"reject"}。
    """
    plan = {"winners": 0, "pending_kept": 0, "rejects_skipped": 0,
            "skipped_same": 0, "skipped_conflict": 0}
    jobs = []
    conflicts = []

    # 目录索引一次，避免 O(n²)（1000 张时会在循环里扫 1000 遍整个目录）
    by_stem: dict[str, list[Path]] = {}
    try:
        for f in folder.iterdir():
            if f.is_file():
                by_stem.setdefault(f.stem.upper(), []).append(f)
    except OSError as e:
        log.warning("organize: 列目录失败，按无 sidecar 处理：%s", e)

    for src_str, st in status_by_path.items():
        if only_set is not None and src_str not in only_set:
            continue
        if st == "pending":
            plan["pending_kept"] += 1
            continue
        # 只复制胜出照片。落选的不落盘——「复查」页就是落选的归宿，
        # 再复制一份到 rejects/ 只是白占空间（用户明确要求）。
        if st != "winner":
            plan["rejects_skipped"] = plan.get("rejects_skipped", 0) + 1
            continue
        dest = "winners"
        src = Path(src_str)
        if not src.exists():
            continue
        if src.parent.name in ("winners", "rejects"):
            continue
        ddir = folder / dest
        stem_u = src.stem.upper()
        group_files = {src}
        for f in by_stem.get(stem_u, ()):
            if f == src:
                continue
            if (f.name.upper() == stem_u + ".XMP"
                    or f.suffix.lower() in (".jpg", ".jpeg")):
                group_files.add(f)

        # 整组判定：任一文件"同名但内容不同"则整组不复制，
        # 避免 RAW 是新的、JPG 是旧的这种半新半旧状态
        conflict = False
        for f in group_files:
            t = ddir / f.name
            if t.exists() and not _same_content(f, t):
                conflict = True
                break
        if conflict:
            plan["skipped_conflict"] += 1
            conflicts.append(src.name)
            continue

        todo = [(f, ddir / f.name) for f in group_files if not (ddir / f.name).exists()]
        already = len(group_files) - len(todo)
        if already:
            plan["skipped_same"] += already
        if not todo:
            continue
        plan["winners"] += 1
        jobs.extend(todo)

    if not dry:
        for _, t in jobs:
            t.parent.mkdir(parents=True, exist_ok=True)
        for f, t in jobs:
            shutil.copy2(str(f), str(t))   # copy2 保留 mtime
    plan["copied"] = len(jobs)
    plan["conflicts"] = conflicts[:20]
    return plan


@app.route("/api/organize", methods=["POST"])
def organize():
    """把 winner **复制**到 winners/、reject **复制**到 rejects/。

    为什么是复制而不是移动：移动会把照片从原目录搬走，用户下次扫描根目录
    就找不到它们了；而且移走是不可逆的，选错了很难恢复。复制则原文件
    始终在原位，代价只是多占一份空间。

    重复检测（用户明确要求"检查是否已经复制过、文件是否一致"）：
    - 目标已有同名文件时，比对**内容 SHA-256**：
      · 内容相同 → 计为"已存在，跳过"，不算冲突、不重复写盘
      · 内容不同 → 计为"同名但内容不同"，**不覆盖**，记进冲突列表
    - 这样反复点归类是幂等的，不会堆出一堆副本。

    其他数据安全规则：
    - pending（未决策）一律不动；
    - RAW+JPG 双拍与同 stem .xmp sidecar 整组一起复制（stem 大小写不敏感，
      任意 RAW 扩展名——V1.0 只认大写 .ARW 是 bug）；
    - dry=true 只返回计划不写盘。

    可选 paths：只处理这些照片（用户在界面上勾选的子集）。
    不传则处理全部。
    """
    folder = Path(STATE["folder"])
    if not STATE["photos"]:
        return jsonify({"ok": False, "error": "还没有扫描结果"}), 400
    body = request.json or {}
    dry = bool(body.get("dry"))
    only = body.get("paths")
    only_set = set(only) if isinstance(only, list) and only else None

    status_by_path = {p["path"]: p["status"] for p in STATE["photos"]}
    plan = _organize_core(folder, status_by_path, dry, only_set)
    if not dry and plan["copied"]:
        save_state()
    return jsonify({"ok": True, **plan})

@app.route("/api/settings", methods=["GET", "POST"])
def settings_api():
    """扫描参数设置（目前只有并发线程数）。

    scan_workers: 0 = 自动（min(8, CPU 核数)）；1..CPU 核数 = 指定线程数。
    """
    if request.method == "POST":
        body = request.json or {}
        cpu = os.cpu_count() or 4
        changed = {}
        if "scan_workers" in body:
            try:
                n = int(body["scan_workers"])
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "scan_workers 必须是整数"}), 400
            if n != 0 and not (1 <= n <= cpu):
                return jsonify({"ok": False,
                                "error": f"线程数需在 1..{cpu} 之间，或 0 表示自动"}), 400
            changed["scan_workers"] = n
        if "score_mode" in body:
            m = str(body["score_mode"])
            # 空字符串 = 取消选择（例如把当前模式的模型删了）
            if m and m not in ("aesthetic", "quality"):
                return jsonify({"ok": False, "error": "未知评分模式"}), 400
            changed["score_mode"] = m
        if "score_weight" in body:
            try:
                w = int(body["score_weight"])
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "score_weight 必须是整数"}), 400
            if not (0 <= w <= 100) or w % 5:
                return jsonify({"ok": False, "error": "权重需为 0..100 且是 5 的倍数"}), 400
            changed["score_weight"] = w
        if not changed:
            return jsonify({"ok": False, "error": "没有要保存的设置"}), 400
        with _SNAPSHOT_LOCK:
            STATE["settings"].update(changed)
            if "score_mode" in changed:
                aesthetic.set_mode(changed["score_mode"])
        # 写两份：目录内的 state.json（随项目走） + 应用级 app_settings.json（跨目录保留）
        save_state()
        appsettings.save(changed)
        return jsonify({"ok": True, **changed})
    cpu = os.cpu_count() or 4
    return jsonify({
        "ok": True,
        "scan_workers": int(STATE["settings"].get("scan_workers") or 0),
        "cpu_count": cpu,
        "auto_workers": min(8, max(2, cpu)),
        "score_mode": STATE["settings"].get("score_mode") or "",
        "score_weight": int(STATE["settings"].get("score_weight", 50)),
        # 缓存里现有分数是哪个模式算的：前端据此判断改设置后要不要重算
        "scored_mode": STATE.get("scored_mode") or "",
    })


@app.route("/api/system/detect")
def system_detect():
    """硬件/后端/模型状态 + sidecar 版本。

    前端会比对 version 与自身版本：本地构建若复用了旧 sidecar exe，
    两边结构可能不一致，宁可明确提示，不要让界面静默错乱。"""
    data = models_mod.detect()
    data["version"] = APP_VERSION
    return jsonify(data)

@app.route("/api/models/download", methods=["POST"])
def models_download():
    body = request.json or {}
    r = models_mod.start_download(body.get("id", ""))
    return jsonify({"ok": bool(r), "error": None if r else "下载进行中"})


@app.route("/api/models/remove", methods=["POST"])
def models_remove():
    """按需删除单个模型。删掉的若是当前评分模式在用的那个，模式自动置空。"""
    body = request.json or {}
    mid = str(body.get("id") or "")
    ok, err = models_mod.remove(mid)
    if not ok:
        return jsonify({"ok": False, "error": err}), 400
    cur = STATE["settings"].get("score_mode") or ""
    if cur and aesthetic.MODES.get(cur, {}).get("id") == mid:
        STATE["settings"]["score_mode"] = ""
        aesthetic.set_mode("")
        save_state()
        appsettings.save({"score_mode": ""})
        log.info("models: 当前评分模式 %s 的模型被删除，模式已置空", cur)
    return jsonify({"ok": True, "score_mode": STATE["settings"].get("score_mode") or ""})

@app.route("/api/paths", methods=["GET", "POST"])
def paths_api():
    """查询 / 切换数据根目录。

    GET  返回当前生效值、每个子目录的实际位置与占用。
    POST 校验目标可写后**自动把旧目录的数据迁移过去**，再写配置。

    为什么要自动迁移：只改配置不搬数据的话，用户会看到"占用了 4GB"但
    新目录是空的，旧目录还占着地方且界面上不再提到它——等于把文件藏起来。
    模型 440MB 重新下载对用户是实打实的代价，不该让他自己搬。
    """
    if request.method == "POST":
        body = request.json or {}
        raw = (body.get("data_root") or "").strip()
        old_root = paths.data_dir()

        if not raw:
            new_root = paths.default_root()
        else:
            new_root = Path(raw).expanduser()
            if not new_root.is_absolute():
                return jsonify({"ok": False, "error": "必须选绝对路径"}), 400

        try:
            if new_root.resolve() == old_root.resolve():
                return jsonify({"ok": True, "data_root": str(new_root),
                                "migrated": [], "skipped": []})
        except Exception:
            pass

        # 校验可写：建目录 + 试写探测文件
        try:
            new_root.mkdir(parents=True, exist_ok=True)
            probe = new_root / ".jurymate-write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except Exception as e:
            return jsonify({"ok": False, "error": f"目录不可写：{e}"}), 400

        # 迁移旧的子目录。tmp 不搬：里面是 PyInstaller 的临时解压产物，
        # 正被当前进程使用，搬它要拖几 GB 且毫无价值。
        #
        # 为什么放到后台线程：模型加起来几百 MB，shutil.move 跨盘时是"复制+删除"，
        # 实测十几秒。同步做的话 HTTP 请求一直挂着，前端既没有进度也不知道
        # 到底结束了没有（用户抱怨过）。这里立刻返回，由进度接口轮询。
        MIGRATE.update(running=True, done=0, total=0, current="",
                       migrated=[], skipped=[], error=None,
                       target=str(new_root), old=str(old_root))

        def migrate_worker():
            try:
                # 迁移清单 = 子目录 + 数据根下的散落文件。
                # 踩过的坑：只搬子目录时 face_landmarker.task（单个 3.7MB 文件，
                # 直接放在数据根下）被漏掉，切换后模型检查立刻报"尚未下载"。
                items = [(k, lb) for k, lb in _DATA_SUBDIRS
                         if k != "tmp" and (old_root / k).exists()]

                # 数据根顶层的散落文件（模型、配置等），逐个搬
                loose = []
                for f in old_root.iterdir():
                    if f.is_file() and f.name not in _NO_MIGRATE_FILES:
                        loose.append(f)

                # 进度基准：子目录字节数 + 散落文件大小
                sizes = {}
                for k, _ in items:
                    try:
                        sizes[k] = _dir_size(old_root / k) or 1
                    except Exception:
                        sizes[k] = 1
                for f in loose:
                    try:
                        sizes[f.name] = f.stat().st_size or 1
                    except Exception:
                        sizes[f.name] = 1
                total_bytes = sum(sizes.values()) or 1
                MIGRATE.update(total=total_bytes)

                done_bytes = 0
                for k, lb in items:
                    MIGRATE.update(current=f"正在迁移 {lb}…")
                    try:
                        how = _migrate_path(old_root / k, new_root / k)
                        MIGRATE["migrated"].append(
                            {"key": k, "label": lb, "how": how})
                    except Exception as e:
                        MIGRATE["skipped"].append(
                            {"key": k, "label": lb, "reason": str(e)[:120]})
                    done_bytes += sizes.get(k, 1)
                    MIGRATE.update(done=done_bytes)

                for f in loose:
                    MIGRATE.update(current=f"正在迁移 {f.name}…")
                    try:
                        how = _migrate_path(f, new_root / f.name)
                        MIGRATE["migrated"].append(
                            {"key": f.name, "label": f.name, "how": how})
                    except Exception as e:
                        MIGRATE["skipped"].append(
                            {"key": f.name, "label": f.name, "reason": str(e)[:120]})
                    done_bytes += sizes.get(f.name, 1)
                    MIGRATE.update(done=done_bytes)

                # 全部搬完才落配置：中途失败时旧目录还是权威位置，
                # 不能出现"配置指向新目录、但数据还在旧目录"的割裂状态。
                if old_root != new_root:
                    paths.write_conf(data_root="" if not raw else str(new_root))

                # 让已加载的模型实例失效。
                # faces._FACES / aesthetic._predictor 都是全局单例，内部绑定
                # 加载时那个模型目录；不清掉就会出现"目录切到 D 盘、推理仍在
                # 读 C 盘"的鬼状态——这类问题从界面上完全看不出来。
                try:
                    import faces as _faces_mod
                    import aesthetic as _aes_mod
                    _faces_mod.reset_cache()
                    _aes_mod.reset_cache()
                    log.info("paths: 已清除模型缓存，下次推理按新目录加载")
                except Exception as e:
                    log.warning("paths: 清除模型缓存失败（重启后仍会生效）：%s", e)

                # 迁移可能是在后台完成的，用户重新打开设置页时
                # 不能还看到缓存里的旧占用数字。
                _SIZE_CACHE.clear()

                MIGRATE.update(current="迁移完成", done=total_bytes)
                log.info("paths: %s -> %s (migrated=%s skipped=%s)",
                         old_root, new_root,
                         [m["key"] for m in MIGRATE["migrated"]],
                         [s["key"] for s in MIGRATE["skipped"]])
            except Exception as e:
                MIGRATE.update(error=f"{e}")
                log.exception("paths: 迁移失败")
            finally:
                MIGRATE.update(running=False)

        threading.Thread(target=migrate_worker, daemon=True).start()
        return jsonify({"ok": True, "data_root": str(new_root), "started": True})

    # GET：返回当前生效值、子目录位置与占用
    return jsonify(_paths_payload())


@app.route("/api/paths/progress")
def paths_progress():
    """迁移进度：前端轮询它来画进度条。"""
    m = dict(MIGRATE)
    m["migrated"] = list(MIGRATE["migrated"])
    m["skipped"] = list(MIGRATE["skipped"])
    return jsonify(m)



# 数据根下的子目录：(键, 中文名)。tmp 是进程临时区，不参与迁移。
_DATA_SUBDIRS = (
    ("logs", "日志"),
    ("torch", "审美模型缓存"),
    ("insightface", "人脸模型"),
    ("tmp", "临时解压"),
)

# 这些文件不迁移：
# - paths.json 必须留在默认位置，否则改完目录就再也找不到配置文件
# - 写探测文件是临时的，正常不会残留
_NO_MIGRATE_FILES = {"paths.json", ".jurymate-write-test"}


def _dir_size(p: Path) -> int:
    try:
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    except Exception:
        return 0


def _migrate_path(src: Path, dst: Path) -> str:
    """把 src 搬到 dst，返回 'moved' / 'copied'；不可搬时抛异常。

    为什么要区分 move 与 copy：Windows 上被进程打开的文件（正在写的日志、
    当前进程加载的模型）不允许 rename/delete，而 shutil.move 会整个失败。
    此时退化为"复制"——数据到了新位置，程序能继续用；旧文件留给用户删。
    直接报错跳过则会留下"新目录缺模型"的坏状态。
    """
    if src.is_file():
        if dst.exists():
            raise FileExistsError("目标已存在同名文件")
        try:
            shutil.move(str(src), str(dst))
            return "moved"
        except (PermissionError, OSError):
            shutil.copy2(str(src), str(dst))
            return "copied"

    if dst.exists() and any(dst.iterdir()):
        raise FileExistsError("目标已有同名非空目录")
    try:
        shutil.move(str(src), str(dst))
        return "moved"
    except (PermissionError, OSError):
        # 目录里有被占用的文件：整体复制，再尝试删源（删不掉就留着）
        shutil.copytree(str(src), str(dst), dirs_exist_ok=True)
        try:
            shutil.rmtree(str(src))
        except Exception:
            pass
        return "copied"


def _paths_payload() -> dict:
    root = paths.data_dir()
    subs = [{"key": k, "label": lb, "path": str(root / k),
             "size": _dir_size_cached(root / k)}
            for k, lb in _DATA_SUBDIRS]
    return {
        "data_root": str(root),
        "default_root": str(paths.default_root()),
        "is_default": root == paths.default_root(),
        "env_override": bool(os.environ.get("JURYMATE_DATA_DIR", "").strip()),
        "subdirs": subs,
    }


# 占用统计缓存：{(路径, 指纹): (字节数, 时间戳)}
# 为什么要缓存：tmp 常驻几 GB，递归统计一次要几百毫秒到数秒，
# 设置页每次打开都算会让接口超时（实测过 /api/paths 超时）。
_SIZE_CACHE: dict[str, tuple[int, float]] = {}
_SIZE_TTL = 30.0      # 秒；30 秒内的重复查询直接复用


def _dir_size_cached(p: Path) -> int:
    if not p.exists():
        return 0
    key = str(p)
    now = time.time()
    hit = _SIZE_CACHE.get(key)
    if hit and now - hit[1] < _SIZE_TTL:
        return hit[0]
    size = _dir_size(p)
    _SIZE_CACHE[key] = (size, now)
    return size


@app.route("/api/net/settings", methods=["GET", "POST"])
def net_settings():
    if request.method == "POST":
        body = request.json or {}
        use_mirror = bool(body.get("use_mirror"))
        mirrors = body.get("github_mirrors", [])
        proxy = body.get("http_proxy", "")
        order = body.get("order", "mirror_first")
        models_mod.save_net_settings(use_mirror, mirrors, proxy, order)
        # 立刻生效：库自身的自动下载读环境变量
        models_mod._apply_proxy_env((proxy or "").strip() or None)
        log.info("net: use_mirror=%s mirrors=%d proxy=%s order=%s",
                 use_mirror, len([m for m in mirrors if str(m).strip()]),
                 "set" if (proxy or "").strip() else "none", order)
        return jsonify({"ok": True})
    return jsonify(models_mod.load_net_settings())

@app.route("/api/models/progress")
def models_progress():
    return jsonify(models_mod.progress())

@app.route("/api/export_csv", methods=["POST"])
def export_csv():
    """胜出清单 CSV（utf-8-sig，Excel 直开；衔接调色流程的选片输入）。

    口径必须是 photos 里 status=="winner" 的每一张，不能按组取 g["winner"]：
    "全要"只给成员打 manual_winner 不改组胜者，人工召回也只改照片状态，
    按组出行会把这两类胜出片整批漏掉（与胜出页显示数量对不上）。
    """
    if not STATE["photos"]:
        return jsonify({"ok": False, "error": "还没有扫描结果"}), 400
    folder = Path(STATE["folder"])
    # 照片 → 所属组（按 members 全局索引反查）
    group_of: dict[str, dict] = {}
    for g in STATE["groups"]:
        for i in g["members"]:
            if 0 <= i < len(STATE["photos"]):
                group_of[STATE["photos"][i]["path"]] = g
    rows = []
    for ph in STATE["photos"]:
        if ph.get("status") != "winner":
            continue
        g = group_of.get(ph["path"])
        rows.append({
            "文件名": ph["name"], "路径": ph["path"], "审美分": ph.get("aesthetic"),
            "综合分": ph.get("score"),
            "组内张数": len(g["members"]) if g else 1,
            "是否人工确认": bool(ph.get("manual_winner")),
            "落选原因": "/".join(ph.get("reject_reasons") or []),
            "宽度": ph["width"], "高度": ph["height"], "拍摄时间": ph["mtime_str"],
        })
    rows.sort(key=lambda r: r["文件名"])
    out = folder / "winners_report.csv"
    import csv as _csv
    with out.open("w", newline="", encoding="utf-8-sig") as fh:
        wr = _csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["文件名"])
        wr.writeheader()
        wr.writerows(rows)
    return jsonify({"ok": True, "path": str(out), "rows": len(rows)})

@app.route("/api/open_folder", methods=["POST"])
def open_folder():
    """在资源管理器中打开结果文件夹（本地单用户工具的合理便利）。

    可选 sub: "winners"/"rejects" 只打开该子目录。
    可选 select: 一个文件名（如 winners_report.csv），打开所在目录并**选中该文件**。
                 导出后要用它——否则用户还得自己在资源管理器里找。
    """
    if not STATE["folder"]:
        return jsonify({"ok": False, "error": "还没有扫描结果"}), 400
    body = request.json or {}
    sub = body.get("sub") or ""
    sel = (body.get("select") or "").strip()
    d = Path(STATE["folder"])
    if sub in ("winners", "rejects") and (d / sub).exists():
        d = d / sub
    if not d.exists():
        return jsonify({"ok": False, "error": "目录不存在"}), 404
    import subprocess
    if sel:
        target = d / sel
        if target.exists():
            # explorer 的 /select, 语法要求逗号紧跟，且路径带引号
            subprocess.Popen(["explorer", f"/select,{target}"])
            return jsonify({"ok": True, "selected": str(target)})
    subprocess.Popen(["explorer", str(d)])
    return jsonify({"ok": True})

@app.route("/thumbs/<path:p>")
def thumbs(p):
    folder = Path(STATE["folder"]) if STATE["folder"] else Path(".")
    return send_from_directory(folder / paths.STATE_DIRNAME / "thumbs", p)


def _preload_aesthetic() -> None:
    """后台预加载当前评分模式的模型。

    MUSIQ 首次创建要读 104MB 权重 + 初始化 torch，实测 20 秒以上。原来是在
    扫描推进到评分阶段才惰性加载，用户看到的就是进度条卡在"加载审美模型"
    很久不动。后端一启动就预热，扫描跑到评分时模型已在内存里。

    没选模式时**什么都不做**：默认不选是产品决定，不能偷偷替用户加载/下载。
    """
    try:
        mode = STATE["settings"].get("score_mode") or ""
        if not mode:
            log.info("未选择评分模式，跳过模型预加载")
            return
        # app.py 顶部并没有 import aesthetic（只通过 pipeline 间接用到），
        # 这里必须自己导入，否则报 name 'aesthetic' is not defined。
        import aesthetic as _aesthetic
        _aesthetic.set_mode(mode)
        _aesthetic._get()
        PRELOAD["model_ready"] = True
        log.info("审美模型预加载完成")
    except Exception as e:
        PRELOAD["model_error"] = str(e)
        log.info("审美模型预加载跳过（稍后按需加载）：%s", e)


threading.Thread(target=_preload_aesthetic, daemon=True).start()

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5090)
    ap.add_argument("--no-browser", action="store_true")
    # --doctor：不启服务，只验证打包是否完整，退出码非 0 即失败。
    # 为什么要它：函数内延迟导入的模块（mediapipe.tasks、torchvision._C）
    # PyInstaller 静态分析看不到，源码环境跑一万遍也发现不了漏收；
    # 而读 PyInstaller 的 warn-*.txt 做文本匹配又必然被一堆无害的可选缺失
    # （fcntl/wandb/albumentations…）干扰。真正可靠的做法是让产物自己
    # 试着 import 一次，失败就报错——CI 里跑这一句就够。
    ap.add_argument("--doctor", action="store_true")
    # 显式指定 doctor 结果落盘位置。CI 里数据目录是默认位置（%LOCALAPPDATA%），
    # 传一个固定路径省得去猜、也便于构建脚本直接读。
    ap.add_argument("--doctor-out", default="")
    args = ap.parse_args()

    if args.doctor:
        import importlib

        # 结果必须**落文件**：正式 sidecar 是 --windowed 打包，没有控制台，
        # print() 无处可去（文件最前面已把 stdout 换成黑洞）。
        # 只靠 print 的话 CI 日志里什么都看不到，"断言通过"就变成了
        # "什么都没检查也可能通过"——实测踩到过。
        lines: list[str] = []

        def say(msg: str) -> None:
            lines.append(msg)
            print(msg)

        checks = [
            ("mediapipe", "闭眼检测"),
            ("mediapipe.tasks", "闭眼检测"),
            ("mediapipe.tasks.python", "闭眼检测"),
            ("mediapipe.tasks.python.vision", "闭眼检测"),
            ("insightface", "人脸检测"),
            ("insightface.app", "人脸检测"),
            ("cv2", "图像读写"),
            ("numpy", "数值"),
            ("torch", "审美评分"),
            ("torchvision", "审美评分"),
            ("pyiqa", "审美评分"),
            ("rawpy", "RAW 解码"),
            ("PIL", "图像"),
            ("flask", "服务"),
        ]
        failed = []
        for mod, purpose in checks:
            try:
                importlib.import_module(mod)
                say(f"[ OK ] {mod:32s} {purpose}")
            except Exception as e:
                failed.append((mod, purpose, e))
                say(f"[FAIL] {mod:32s} {purpose}  -> {e}")
        # torchvision 的 C++ 扩展是单独 load_library 的，import 成功不代表
        # nms 算子可用（0.1.9 就是这样漏过检查的），必须实调一次。
        try:
            import torch
            import torchvision  # noqa: F401
            _ = torch.ops.torchvision.nms(
                torch.tensor([[0., 0., 10., 10.]]),
                torch.tensor([0.9]), 0.5)
            say(f"[ OK ] {'torchvision::nms':32s} 检测框后处理")
        except Exception as e:
            failed.append(("torchvision::nms", "检测框后处理", e))
            say(f"[FAIL] {'torchvision::nms':32s} 检测框后处理  -> {e}")

        if failed:
            say(f"\nDOCTOR FAILED: {len(failed)} 项不可用")
            code = 1
        else:
            say("\nDOCTOR OK: 全部可用")
            code = 0

        # 写文件：CI 与本地构建都读它来举证（也便于用户排查）
        try:
            if args.doctor_out:
                out = __import__("pathlib").Path(args.doctor_out)
            else:
                import paths as _p
                out = _p.data_dir() / "logs" / "doctor.log"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"doctor 结果已写入 {out}")
        except Exception as e:
            print(f"doctor 结果写文件失败（不影响判定）：{e}")

        sys.exit(code)
        sys.exit(0)

    # 端口被占（双开/僵尸进程）自动顺延——实测浏览器打开旧端口会白屏排查半天
    port = args.port
    for _ in range(10):
        try:
            if not args.no_browser:
                threading.Timer(1.2, lambda p=port: __import__("webbrowser").open(
                    f"http://127.0.0.1:{p}")).start()
            app.run(host="127.0.0.1", port=port, threaded=True)
            break
        except OSError as e:
            if "10048" in str(e) or "address already in use" in str(e).lower():
                log.warning("端口 %d 被占用，改用 %d", port, port + 1)
                port += 1
                continue
            raise
