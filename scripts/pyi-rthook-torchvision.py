# PyInstaller runtime hook：修正 torchvision 的 C++ 扩展加载。
#
# 背景：
#   torchvision.extension._load_library() 用 _get_extension_path(name) 拼出
#   "_C_stable.pyd" 的**文件路径**，再交给 torch.ops.load_library()。
#   它只看 torchvision 包目录（os.path.dirname(torchvision.__file__)）。
#   onefile 模式下包被解压到 _MEIPASS/torchvision，正常情况路径是对的；
#   但如果 PyInstaller 把 .pyd 归到别处（或没带），运行时就报
#     RuntimeError: operator torchvision::nms does not exist
#   而源码环境一切正常——验收时极易漏掉。
#
# 做法：在 import torchvision 之前，把 _MEIPASS 下所有 *_C_stable.pyd
# 补到 torchvision 包目录旁边（若尚不存在），并显式 load_library 一次。

import os
import shutil
import sys

if getattr(sys, "frozen", False):
    base = getattr(sys, "_MEIPASS", None)
    if base:
        try:
            import torchvision

            tv_dir = os.path.dirname(torchvision.__file__)
            target = os.path.join(tv_dir, "_C_stable.pyd")

            if not os.path.exists(target):
                # 在 _MEIPASS 里找，找到就复制到 torchvision 包目录
                for root, _dirs, files in os.walk(base):
                    if "_C_stable.pyd" in files:
                        src = os.path.join(root, "_C_stable.pyd")
                        try:
                            shutil.copy2(src, target)
                        except Exception:
                            pass
                        break

            if os.path.exists(target):
                try:
                    import torch

                    torch.ops.load_library(target)
                except Exception:
                    # 已经加载过会抛重复注册，属正常
                    pass
        except Exception:
            # 这里绝不能让异常冒出来：torchvision 缺失时闭眼/审美本就会
            # 走 ModelUnavailable 分支，不该变成启动崩溃。
            pass
