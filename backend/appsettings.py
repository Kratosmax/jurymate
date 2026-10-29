"""应用级设置（跨照片目录共用）。

为什么要单独存：评分模式、权重、扫描线程数描述的是「这台机器上这个软件
怎么工作」，不属于某个照片目录。以前它们只写在
`<照片目录>/.jurymate/state.json` 里，而程序启动时 `STATE["settings"]` 是空的，
设置页读到的就是默认值——用户每次重开都发现"上次选的评分模式没了"。

这里存一份全局副本，启动时优先采用；切换目录时也以它为准覆盖目录里的旧值。
"""
import json
import logging
import os

import paths

log = logging.getLogger("jurymate")

_NAME = "app_settings.json"
KEYS = ("score_mode", "score_weight", "scan_workers")


def _file():
    return paths.data_dir() / _NAME


def load() -> dict:
    """读全局设置；文件不存在或损坏都返回 {}（用调用方的默认值兜底）。"""
    f = _file()
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("app_settings 读取失败，使用默认值：%s", e)
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: data[k] for k in KEYS if k in data}


def save(values: dict) -> None:
    """合并写入。只接受白名单键，避免把临时状态写进配置文件。"""
    cur = load()
    cur.update({k: values[k] for k in KEYS if k in values})
    f = _file()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, f)          # 同目录原子替换，写一半断电不会留下坏文件
    except Exception as e:
        log.warning("app_settings 保存失败：%s", e)
