# -*- coding: utf-8 -*-
"""JuryMate 自包含端到端测试：不依赖任何会话，可重复执行。

步骤：起服务 → start 扫描样图目录 → 轮询 → 校验 state → PK/recall/undo 冒烟 → 落盘结论。
结论追加写 selftest.log（PASS/FAIL 逐项 + state 摘要）。

样图目录：环境变量 JURYMATE_TEST_DIR，默认 <工作区>/temp/jurymate-test
（需含 9 张 ARW；DSC01928/01929 是背影人像回归样本）。
解释器：环境变量 JURYMATE_PY，其次本仓库 .venv，最后当前解释器。
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG = ROOT / "selftest.log"
TMP = os.environ.get("JURYMATE_TEST_DIR") or str(
    ROOT.parent / "temp" / "jurymate-test")
PORT = int(os.environ.get("JURYMATE_TEST_PORT") or 5091)  # 避开 5090 主服务
BASE = f"http://127.0.0.1:{PORT}"
# 解释器：环境变量优先（可指向任意装好依赖的 venv），其次本仓库 .venv
PY = os.environ.get("JURYMATE_PY") or str(ROOT / ".venv" / "Scripts" / "python.exe")
if not Path(PY).exists():
    PY = sys.executable

results = []

def check(name, ok, detail=""):
    results.append((name, bool(ok), str(detail)))
    print(("[PASS] " if ok else "[FAIL] ") + name + (f"  | {detail}" if detail else ""), flush=True)

def api(path, body=None, method=None):
    req = urllib.request.Request(BASE + path, method=method or ("POST" if body is not None else "GET"),
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"ok": False, "_http": e.code, "error": e.read().decode("utf-8", "replace")}

def main():
    # 0) 依赖自检（幂等修复提示）
    try:
        import onnxruntime, cv2, insightface, pyiqa, pkg_resources  # noqa
        check("依赖导入", True, f"providers={onnxruntime.get_available_providers()}")
    except Exception as e:
        check("依赖导入", False, f"{e} —— 按 CODEX_PROGRESS.md 的验证矩阵修依赖后重跑")

    # 1) 清理旧状态，起服务
    state_json = Path(TMP) / ".jurymate" / "state.json"
    if state_json.exists():
        state_json.unlink()
    log_lines = []
    proc = subprocess.Popen([PY, str(ROOT / "backend" / "app.py"), "--port", str(PORT), "--no-browser"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    up = False
    for _ in range(40):
        time.sleep(1)
        try:
            urllib.request.urlopen(BASE + "/", timeout=3)
            up = True
            break
        except Exception:
            pass
    check("服务启动", up)
    if not up:
        try:
            proc.terminate()
        except Exception:
            pass
        return

    try:
        # 2) 扫描
        r = api("/api/start", {"folder": TMP})
        check("start 接受任务", r.get("ok") is True, str(r))
        last = {}
        t0 = time.time()
        while time.time() - t0 < 900:  # 15 分钟上限
            time.sleep(2)
            last = api("/api/progress")
            if not last.get("running"):
                break
        check("管线完成无错误", not last.get("error"), f"stage={last.get('stage')} done={last.get('done')}/{last.get('total')}")

        # 3) state 校验
        st = api("/api/state")
        photos, groups = st.get("photos", []), st.get("groups", [])
        n_arw = len([f for f in Path(os.environ["JURYMATE_TEST_DIR"]).iterdir() if f.is_file() and f.suffix.lower() in (".arw", ".cr2", ".nef", ".dng")])
        check(f"照片数 = {n_arw}", len(photos) == n_arw, f"实际 {len(photos)}")
        check("分组非空", len(groups) >= 1, f"{len(groups)} 组")

        # 用户痛点回归：背影人像不许被判"失焦"
        back = [p for p in photos if "01928" in p["name"] or "01929" in p["name"]]
        if back:
            bad = [p["name"] for p in back if "失焦" in p["reject_reasons"]]
            check("背影人像未被误判失焦", not bad, f"{[p['name'] for p in back]} reasons={[p['reject_reasons'] for p in back]}")
            same_group = back[0]["group_id"] == back[1]["group_id"] if len(back) == 2 else False
            check("连拍两张同组", same_group, f"group_ids={[p['group_id'] for p in back]}")

        # 人脸/审美信号正常
        n_face = sum(1 for p in photos if p["n_faces"])
        n_aes = sum(1 for p in photos if p.get("aesthetic") is not None)
        check("审美分产出", n_aes > 0, f"{n_aes}/{len(photos)} 张有分")

        # 4) 决策流冒烟
        pk_groups = [g for g in groups if g["needs_pk"]]
        if pk_groups:
            g = pk_groups[0]
            r = api("/api/pk", {"group_id": g["id"], "action": "pick", "pick": g["members"][0]["idx"]})
            check("PK 选择", r.get("ok") is True)
            st = api("/api/state")
            g2 = next(x for x in st["groups"] if x["id"] == g["id"])
            check("PK 后不再需要人工", g2["needs_pk"] is False)
        r = api("/api/undo", {})
        check("撤销", r.get("ok") is True, r.get("error", ""))
        rej = [p for p in st["photos"] if p["status"] == "reject"]
        if rej:
            r = api("/api/recall", {"path": rej[0]["path"]})
            check("复查召回", r.get("ok") is True, f"补评分={r.get('aesthetic')}")
        st = api("/api/state")
        win = [p for p in st["photos"] if p["status"] == "winner"]
        check("胜出图非空且有分", bool(win) and all(p.get("aesthetic") is not None for p in win),
              f"{len(win)} 张胜出, 分={[p.get('aesthetic') for p in win]}")

        # 回归：全要 = 整组胜出（V1.0 bug：非胜者变 pending 被 organize 送 rejects）
        multi = next((g for g in st["groups"] if len(g["members"]) >= 2), None)
        if multi:
            r = api("/api/pk", {"group_id": multi["id"], "action": "all"})
            st = api("/api/state")
            g2 = next(x for x in st["groups"] if x["id"] == multi["id"])
            all_win = all(st["photos"][m["idx"]]["status"] == "winner" for m in g2["members"])
            check("全要 = 整组胜出", r.get("ok") is True and all_win,
                  f"members status={[st['photos'][m['idx']]['status'] for m in g2['members']]}")
            # 回归：召回图不被后续 PK 的 refresh 打回 pending（V1.0 bug）
            st = api("/api/state")
            rej2 = [p for p in st["photos"] if p["status"] == "reject"]
            if rej2:
                r = api("/api/recall", {"path": rej2[0]["path"]})
                other = next((g for g in st["groups"] if g["id"] != multi["id"]
                              and len(g["members"]) >= 2), None)
                if other:
                    api("/api/pk", {"group_id": other["id"], "action": "pick",
                                    "pick": other["members"][0]["idx"]})
                st = api("/api/state")
                still = next(p for p in st["photos"] if p["path"] == rej2[0]["path"])
                check("召回后仍为胜出", still["status"] == "winner", still["status"])

        # organize dry 计划（不移动真文件）：pending 不进计划、无重名冲突
        st = api("/api/state")
        n_pending = sum(1 for p in st["photos"] if p["status"] == "pending")
        r = api("/api/organize", {"dry": True})
        check("organize 计划保留 pending", r.get("ok") is True
              and r.get("pending_kept", 0) == n_pending, str({k: r.get(k) for k in ("winners", "rejects", "pending_kept", "skipped_conflict")}))

        summary = {
            "photos": len(st["photos"]),
            "groups": len(st["groups"]),
            "pk_needed": sum(1 for g in st["groups"] if g["needs_pk"]),
            "winners": len(win),
            "rejects": len(st["photos"]) - len(win),
        }
    finally:
        try:
            proc.terminate()
        except Exception:
            pass

    ok_all = all(ok for _, ok, _ in results)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"\n===== selftest @ {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        for name, ok, detail in results:
            f.write(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  | {detail}" if detail else "") + "\n")
        if "summary" in dir():
            f.write("SUMMARY: " + json.dumps(summary, ensure_ascii=False) + "\n")
        f.write(f"RESULT: {'ALL PASS' if ok_all else 'HAS FAILURES'}\n")
    print("RESULT:", "ALL PASS" if ok_all else "HAS FAILURES", flush=True)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"\n===== selftest CRASH @ {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n{traceback.format_exc()}\n")
        print("CRASH:", e, flush=True)
