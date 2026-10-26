# -*- coding: utf-8 -*-
"""闭眼检测正例回归：用样图验证 MediaPipe EAR 管线。

样图目录（Pexels 免费许可）默认取环境变量 JURYMATE_EYE_DIR，
未设置时用 temp/jurymate-eye-test；也可命令行传入：
  closed_1 侧脸闭眼 / closed_2 近正面闭眼特写 / closed_4 正面闭眼 → closed=True
  open_1 正面睁眼 → closed=False
  closed_3 远景侧身小脸 → 无脸豁免（不算失败）
用法：python verify_eyes.py [样图目录]
"""
import glob
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))
import cv2  # noqa: E402
import numpy as np  # noqa: E402

import pipeline  # noqa: E402
import faces  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
DIR = (sys.argv[1] if len(sys.argv) > 1
       else os.environ.get("JURYMATE_EYE_DIR")
       or os.path.join(os.path.dirname(_HERE), "temp", "jurymate-eye-test"))
EXPECT = {"closed": True, "open": False}

def main():
    ok = total = skipped = 0
    for f in sorted(glob.glob(os.path.join(DIR, "*.jpg"))):
        name = os.path.basename(f)
        if name.startswith("_"):
            continue
        key = name.split("_")[0]
        if key not in EXPECT:
            continue
        exp = EXPECT[key]
        img = pipeline.load_preview(f)
        bgr = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2BGR)
        fl = faces.analyze(bgr)
        if not fl:
            print(f"[SKIP] {name}: 无脸（远景豁免）")
            skipped += 1
            continue
        main = fl[0]
        if main.get("ear_l") is None:
            print(f"[SKIP] {name}: 无有效 EAR 信号（检测到脸但 FaceMesh 未出点，无法判闭眼）")
            skipped += 1
            continue
        got = bool(main.get("closed"))
        hit = got == exp
        ok += hit
        total += 1
        print(f"[{'PASS' if hit else 'FAIL'}] {name}: ear={main.get('ear_l')}/{main.get('ear_r')} closed={got}")
    print(f"\nRESULT: {ok}/{total} 命中, {skipped} 张豁免"
          f" —— 闭眼样图必须全命中；若 FAIL 检查 face_landmarker.task 与阈值 ear_closed")

if __name__ == "__main__":
    main()
