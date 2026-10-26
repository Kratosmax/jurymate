"""分组：时间连拍 burst（union-find）+ 感知哈希兜底合并。

不采用 DINOv2 语义分组（2026-10-25 用户实测：不同场景会被归为一组）。
- burst：同机身同镜头、相邻拍摄间隔 < burst_gap 秒 → 连拍组。
- phash：不相连的 burst/孤图，感知哈希汉明距离 <= phash_dist → 合并（手持连拍同场景）。
- 单张图（无组）直接走个人决策，不需要 PK。
"""
import logging
from datetime import datetime
from pathlib import Path

import imagehash

log = logging.getLogger("jurymate")

def _mtime(path: str) -> float:
    return Path(path).stat().st_mtime

def group(infos: list[dict], settings: dict | None = None) -> list[list[int]]:
    """infos: pipeline 的 photo dict 列表（含 path/mtime/phash）。返回组索引列表。"""
    st = settings or {}
    gap = float(st.get("burst_gap", 1.2))
    pd = int(st.get("phash_dist", 8))

    n = len(infos)
    parent = list(range(n))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def merge(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # 1) 时间 burst：按拍摄时间排序后相邻合并
    order = sorted(range(n), key=lambda i: infos[i].get("mtime") or 0)
    for a, b in zip(order, order[1:]):
        ta, tb = infos[a].get("mtime"), infos[b].get("mtime")
        if ta and tb and abs(tb - ta) <= gap:
            merge(a, b)

    # 2) phash 兜底：不同 burst 的图视觉几乎相同（手持连拍/延时）也合并
    #    phash 在 state 里是十六进制字符串，必须转回 ImageHash（字符串相减
    #    抛 TypeError 曾被静默吞掉导致连拍永不合并——实测教训）
    import imagehash as _ih
    hashes = []
    for i in range(n):
        h = infos[i].get("phash")
        if not h:
            continue
        try:
            hashes.append((i, h if isinstance(h, imagehash.ImageHash) else _ih.hex_to_hash(h)))
        except (ValueError, AttributeError):
            pass
    for j in range(len(hashes)):
        ia, ha = hashes[j]
        for k in range(j + 1, len(hashes)):
            ib, hb = hashes[k]
            if find(ia) == find(ib):
                continue
            if ha - hb <= pd:
                merge(ia, ib)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    out = sorted(groups.values(), key=lambda g: min(g))
    log.info("group: %d 张 → %d 组", n, len(out))
    return out
