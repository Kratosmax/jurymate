"""cullmate 服务：Flask API + 状态持久化。

状态保存在照片目录的 .cullmate/state.json（pianke 同款单文件夹单会话策略），
随时关闭、下次启动同目录自动恢复。
"""
import json
import logging
import shutil
import threading
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

import pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("cullmate")

# 前端静态目录仅源码模式存在（浏览器访问）；PyInstaller 打包模式下
# 前端由 Tauri webview 提供，Flask 只承担 API
_FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
app = Flask(__name__, static_folder=str(_FRONTEND) if _FRONTEND.exists() else None,
            static_url_path="")

# CORS：Tauri webview 的 origin（http://tauri.localhost）与 sidecar（127.0.0.1）
# 跨域——没有这个头，打包版前端的所有 API 响应都会被 webview 拦截（实测数据全空）。
# 本地单机工具，全开是安全默认。
@app.after_request
def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp

STATE = {"photos": [], "groups": [], "folder": None, "settings": {}, "undo_stack": []}
TASK = {"running": False, "done": 0, "total": 0, "stage": "", "error": None}
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
    d = Path(STATE["folder"]) / ".cullmate"
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
    f = Path(folder) / ".cullmate" / "state.json"
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
        TASK.update(running=True, done=0, total=0, stage="", error=None)
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
                    with _SNAPSHOT_LOCK:
                        n_new = pipeline.add_new_photos(
                            folder, STATE["photos"], STATE["groups"],
                            Path(folder) / ".cullmate", STATE["settings"], progress)
                    TASK.update(done=len(STATE["photos"]), total=len(STATE["photos"]),
                                stage=f"增量完成：新增 {n_new} 张")
                    save_state()
                    return
            # 从磁盘恢复进度（跨会话续做）
            if load_state(folder):
                refresh_statuses()
                save_state()
                return
            # 全量扫描
            with _SNAPSHOT_LOCK:
                STATE.update(photos=[], groups=[])
            result = pipeline.run(folder, Path(folder) / ".cullmate",
                                  STATE["settings"], progress)
            with _SNAPSHOT_LOCK:
                STATE.update(photos=result["photos"], groups=result["groups"])
            refresh_statuses()
            save_state()
        except Exception as e:
            log.exception("管线失败")
            TASK["error"] = str(e)
        finally:
            TASK["running"] = False
    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True, "restored": False})

@app.route("/api/progress")
def progress_api():
    return jsonify({k: TASK[k] for k in ("running", "done", "total", "stage", "error")})

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
    with _SNAPSHOT_LOCK:
        ph = next((p for p in STATE["photos"] if p["path"] == path), None)
        if not ph:
            return jsonify({"ok": False, "error": "照片不存在"}), 404
        return _recall_apply(ph, path)

def _recall_apply(ph: dict, path: str):
    push_undo()
    old_reasons = list(ph["reject_reasons"])
    ph["reject_reasons"] = []
    ph["manual_winner"] = True   # 人工召回 = 持久胜出，后续 refresh 不得覆盖
    if ph.get("aesthetic") is None:
        ph["aesthetic"] = pipeline.aesthetic.score(pipeline.load_preview(path))
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

import models as models_mod

@app.route("/api/system/detect")
def system_detect():
    return jsonify(models_mod.detect())

@app.route("/api/models/download", methods=["POST"])
def models_download():
    body = request.json or {}
    r = models_mod.start_download(body.get("id", ""))
    return jsonify({"ok": bool(r), "error": None if r else "下载进行中"})

@app.route("/api/net/settings", methods=["GET", "POST"])
def net_settings():
    if request.method == "POST":
        body = request.json or {}
        models_mod.save_net_settings(body.get("github_mirrors", []),
                                     body.get("http_proxy", ""))
        return jsonify({"ok": True})
    return jsonify(models_mod.load_net_settings())

@app.route("/api/models/progress")
def models_progress():
    return jsonify(models_mod.progress())

@app.route("/api/export_csv", methods=["POST"])
def export_csv():
    """胜出清单 CSV（utf-8-sig，Excel 直开；衔接调色流程的选片输入）。"""
    if not STATE["photos"]:
        return jsonify({"ok": False, "error": "还没有扫描结果"}), 400
    folder = Path(STATE["folder"])
    rows = []
    for g in STATE["groups"]:
        w = STATE["photos"][g["winner"]] if 0 <= g["winner"] < len(STATE["photos"]) else None
        if not w:
            continue
        rows.append({
            "文件名": w["name"], "路径": w["path"], "审美分": w.get("aesthetic"),
            "综合分": w.get("score"), "组内张数": len(g["members"]),
            "是否人工确认": bool(w.get("manual_winner")), "落选原因": "/".join(w["reject_reasons"]),
            "宽度": w["width"], "高度": w["height"], "拍摄时间": w["mtime_str"],
        })
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
    return send_from_directory(folder / ".cullmate" / "thumbs", p)

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
