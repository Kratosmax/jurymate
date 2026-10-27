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
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

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
TASK = {"running": False, "done": 0, "total": 0, "stage": "", "error": None,
        "error_kind": None, "error_hint": ""}
_LOCK = threading.Lock()          # 启动任务互斥
_SNAPSHOT_LOCK = threading.Lock() # STATE 列表引用快照（API 读 vs worker 原地写）

def snapshot_state() -> tuple[list, list]:
    """原子取出 photos/groups 的浅拷贝（读侧）。"""
    with _SNAPSHOT_LOCK:
        return list(STATE["photos"]), list(STATE["groups"])

DEFAULT_SETTINGS = {
    "burst_gap": 3.0, "phash_dist": 10, "pk_gap": 0.35,
    "min_score": 2.0, "ear_closed": 0.18, "ear_open": 0.25,
}

def state_file() -> Path | None:
    if not STATE["folder"]:
        return None
    d = Path(STATE["folder"]) / paths.STATE_DIRNAME
    d.mkdir(exist_ok=True)
    return d / "state.json"

STATE_VERSION = 2   # 评分映射/状态机变更时 +1；旧版本 state 直接弃用重扫

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
    missing = [m for m in models_mod.MODELS if not models_mod._installed(m)]
    if missing:
        # 数量多时只列前两个，避免提示条过长
        names = "、".join(m["name"] for m in missing[:2])
        if len(missing) > 2:
            names += f" 等 {len(missing)} 个"
        raise errors.ModelUnavailable(
            names,
            detail="尚未下载",
            hint="请在首页「模型就绪检查」逐个点下载；国内网络可在网络设置里开启镜像站或填代理",
        )

@app.route("/api/start", methods=["POST"])
def start():
    body = request.json or {}
    folder = body.get("folder", "").strip()
    incremental = bool(body.get("incremental"))
    if not folder or not Path(folder).is_dir():
        return jsonify({"ok": False, "error": "目录不存在"}), 400
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
            # 增量：同目录已有人工决策，只补新图。
            # 磁盘 state 是唯一事实源——用户删掉它 = 明确要重置，内存旧态
            # （含 manual_winner）必须作废、退化为全量重扫。
            # （实测教训：删磁盘缓存后点增量补扫，内存旧态的"人工召回"永远残留）
            if incremental and STATE["photos"] \
                    and Path(STATE["folder"]).resolve() == Path(folder).resolve():
                with _SNAPSHOT_LOCK:
                    STATE.update(photos=[], groups=[])
                    load_state(folder)
                    do_incremental = bool(STATE["photos"])   # 磁盘无 state → 退化为全量
                if do_incremental:
                    # 增量要推理新图，必须先确认模型齐备
                    TASK.update(stage="检查模型…")
                    preflight_models()
                    # 管线跑几分钟，不能持锁——否则 /api/state 全程阻塞、界面假死。
                    # 它原地扩展 photos/groups，读侧靠 _SNAPSHOT_LOCK 的浅拷贝兜住。
                    n_new = pipeline.add_new_photos(
                        folder, STATE["photos"], STATE["groups"],
                        Path(folder) / paths.STATE_DIRNAME, STATE["settings"], progress)
                    with _SNAPSHOT_LOCK:
                        TASK.update(done=len(STATE["photos"]), total=len(STATE["photos"]),
                                    stage=f"增量完成：新增 {n_new} 张")
                        save_state()
                    return
            # 从磁盘恢复进度（跨会话续做）——纯读 state，不推理，不需要模型；
            # 召回补评分时才用 musiq，那条路径自己会报 ModelUnavailable
            if load_state(folder):
                refresh_statuses()
                save_state()
                return
            # 全量扫描：需要全套模型，缺了立刻失败而不是产出"无人脸"的空结果
            TASK.update(stage="检查模型…")
            preflight_models()
            with _SNAPSHOT_LOCK:
                STATE.update(photos=[], groups=[])
            # 同上：扫描期间不持锁，让前端能持续读进度与已有状态
            result = pipeline.run(folder, Path(folder) / paths.STATE_DIRNAME,
                                  STATE["settings"], progress)
            with _SNAPSHOT_LOCK:
                STATE.update(photos=result["photos"], groups=result["groups"])
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
                    "settings": dict(STATE["settings"])})

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
    ph["score"] = pipeline.composite_score(ph, ph.get("aesthetic"), [])
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

@app.route("/api/organize", methods=["POST"])
def organize():
    """把 winner 移入 winners/、reject 移入 rejects/。

    数据安全规则：
    - pending（未决策）一律不动；
    - RAW+JPG 双拍与同 stem .xmp sidecar 整体随迁（stem 大小写不敏感，
      任意 RAW 扩展名——V1.0 只认大写 .ARW 是 bug）；
    - 目标重名不覆盖（跳过并计数）；
    - dry=true 只返回计划不移动（测试用）。
    """
    folder = Path(STATE["folder"])
    if not STATE["photos"]:
        return jsonify({"ok": False, "error": "还没有扫描结果"}), 400
    dry = bool((request.json or {}).get("dry"))
    plan = {"winners": 0, "rejects": 0, "pending_kept": 0, "skipped_conflict": 0}
    moves = []  # (src, dst)
    for ph in STATE["photos"]:
        if ph["status"] == "pending":
            plan["pending_kept"] += 1
            continue
        dest = "winners" if ph["status"] == "winner" else "rejects"
        src = Path(ph["path"])
        if not src.exists():
            continue
        if src.parent.name in ("winners", "rejects"):
            continue  # 已归位（幂等）
        ddir = folder / dest
        stem_u = src.stem.upper()
        group_files = {src}
        for f in folder.iterdir():
            if not f.is_file() or f == src:
                continue
            if (f.name.upper() == stem_u + ".XMP"
                    or (f.stem.upper() == stem_u and f.suffix.lower() in (".jpg", ".jpeg"))):
                group_files.add(f)
        for c in ph.get("companions", []):
            cp = Path(c)
            if cp.exists() and cp.parent == folder:
                group_files.add(cp)
        ddir.mkdir(exist_ok=True)
        ok = True
        for f in group_files:
            if (ddir / f.name).exists():
                plan["skipped_conflict"] += 1
                ok = False
        if ok:
            plan[dest] += 1
            moves.extend((f, ddir / f.name) for f in group_files)
    if not dry:
        for f, t in moves:
            shutil.move(str(f), str(t))
        by_name = {Path(p["path"]).name: p for p in STATE["photos"]}
        for f, t in moves:
            ph = by_name.get(f.name)
            if ph is None or Path(ph["path"]) != f:
                continue
            ph["path"] = str(t)
            ddir = t.parent
            ph["companions"] = [str(ddir / Path(c).name)
                                if Path(c).parent == folder and (ddir / Path(c).name).exists()
                                else c
                                for c in ph.get("companions", [])]
        save_state()
    plan["moved"] = len(moves)
    return jsonify({"ok": True, **plan})

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

@app.route("/api/paths", methods=["GET", "POST"])
def paths_api():
    """查询 / 切换数据根目录。

    GET 返回当前生效值与每个子目录的实际位置、占用大小；
    POST 校验目标可写后写配置。切换只影响之后产生的文件——已经下载的
    模型留在旧位置，因为搬 440MB 权重既慢又可能中断，不如让用户自己决定
    （界面上给出提示和旧路径）。
    """
    if request.method == "POST":
        body = request.json or {}
        raw = (body.get("data_root") or "").strip()
        if not raw:
            paths.write_conf(data_root="")
            log.info("paths: 恢复默认 %s", paths.default_root())
            return jsonify({"ok": True, "data_root": str(paths.data_dir())})

        target = Path(raw).expanduser()
        if not target.is_absolute():
            return jsonify({"ok": False, "error": "必须填绝对路径"}), 400
        # 校验可写：建目录 + 试写一个探测文件
        try:
            target.mkdir(parents=True, exist_ok=True)
            probe = target / ".jurymate-write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except Exception as e:
            return jsonify({"ok": False, "error": f"目录不可写：{e}"}), 400
        # 拒绝把数据目录放进程序安装目录：卸载时会连数据一起删掉
        paths.write_conf(data_root=str(target))
        log.info("paths: 数据根切到 %s", target)
        return jsonify({"ok": True, "data_root": str(target)})

    def size_of(p: Path) -> int:
        try:
            return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        except Exception:
            return 0

    root = paths.data_dir()
    subs = []
    for key, label in (("logs", "日志"), ("torch", "审美模型缓存"),
                       ("insightface", "人脸模型"), ("tmp", "临时解压")):
        p = root / key
        subs.append({"key": key, "label": label, "path": str(p),
                     "size": size_of(p) if p.exists() else 0})
    return jsonify({
        "data_root": str(root),
        "default_root": str(paths.default_root()),
        "is_default": root == paths.default_root(),
        "env_override": bool(os.environ.get("JURYMATE_DATA_DIR", "").strip()),
        "subdirs": subs,
    })

@app.route("/api/net/settings", methods=["GET", "POST"])
def net_settings():
    if request.method == "POST":
        body = request.json or {}
        use_mirror = bool(body.get("use_mirror"))
        mirrors = body.get("github_mirrors", [])
        proxy = body.get("http_proxy", "")
        models_mod.save_net_settings(use_mirror, mirrors, proxy)
        # 立刻生效：库自身的自动下载读环境变量
        models_mod._apply_proxy_env((proxy or "").strip() or None)
        log.info("net: use_mirror=%s mirrors=%d proxy=%s",
                 use_mirror, len([m for m in mirrors if str(m).strip()]),
                 "set" if (proxy or "").strip() else "none")
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
    """在资源管理器中打开结果文件夹（本地单用户工具的合理便利）。"""
    if not STATE["folder"]:
        return jsonify({"ok": False, "error": "还没有扫描结果"}), 400
    sub = (request.json or {}).get("sub") or ""
    d = Path(STATE["folder"])
    if sub in ("winners", "rejects") and (d / sub).exists():
        d = d / sub
    if not d.exists():
        return jsonify({"ok": False, "error": "目录不存在"}), 404
    import subprocess
    subprocess.Popen(["explorer", str(d)])
    return jsonify({"ok": True})

@app.route("/thumbs/<path:p>")
def thumbs(p):
    folder = Path(STATE["folder"]) if STATE["folder"] else Path(".")
    return send_from_directory(folder / paths.STATE_DIRNAME / "thumbs", p)

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5090)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
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
