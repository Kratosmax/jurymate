/**
 * 网络设置的本地存储（localStorage）。
 *
 * 为什么要本地一份：网络设置同时服务两件事——
 *   1) 后端模型下载器（需要后端进程活着）
 *   2) 前端更新检查（后端没起来时也要能用）
 * 之前整套配置只存在后端，应用刚启动、sidecar 还在解压 1.1GB 的那 60~90 秒里，
 * 用户在设置页改镜像根本存不进去，只能干等。
 *
 * 现在：localStorage 是前端的第一事实源，改完立刻生效；后端起来后再同步一份。
 */

export interface NetCfg {
  use_mirror: boolean
  github_mirrors: string[]
  http_proxy: string
  order: 'mirror_first' | 'direct_first'
}

const KEY = 'jurymate.net'

export const DEFAULT_NET: NetCfg = {
  use_mirror: false,
  github_mirrors: ['https://ghfast.top', 'https://gh-proxy.com'],
  http_proxy: '',
  order: 'mirror_first',
}

export function loadNetCfg(): NetCfg {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return { ...DEFAULT_NET }
    const v = JSON.parse(raw)
    return {
      use_mirror: v.use_mirror === true,
      github_mirrors: (() => {
        const arr = Array.isArray(v.github_mirrors)
          ? v.github_mirrors.filter((m: unknown) => typeof m === 'string' && m.trim())
          : []
        // 空列表 + 已启用镜像 = 用内置默认线路，否则用户开了开关却一条线路都没有
        return arr.length ? arr : [...DEFAULT_NET.github_mirrors]
      })(),
      // 空列表 + 已启用镜像 = 用内置默认线路，否则用户开了开关却一条线路都没有
      http_proxy: typeof v.http_proxy === 'string' ? v.http_proxy : '',
      order: v.order === 'direct_first' ? 'direct_first' : 'mirror_first',
    }
  } catch {
    return { ...DEFAULT_NET }
  }
}

export function saveNetCfg(cfg: NetCfg): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(cfg))
  } catch { /* 隐私模式/配额满：不影响本次运行 */ }
}

/** 规范化：去空格、去尾斜杠、去重（不区分大小写） */
export function normalizeMirrors(list: string[]): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const raw of list) {
    const m = raw.trim().replace(/\/+$/, '')
    if (!m) continue
    const k = m.toLowerCase()
    if (seen.has(k)) continue
    seen.add(k)
    out.push(m)
  }
  return out
}

/**
 * 生成更新清单的候选 URL（镜像优先 / 直连优先）。
 *
 * 规则与后端模型下载器一致：只对 GitHub 域加前缀，前缀 = base + "/" + 原始绝对 URL。
 * 最后一定带一条直连兜底，除非用户把直连也关掉（这里不做，保持可更新优先）。
 */
export function updateCandidates(cfg: NetCfg): string[] {
  const direct = 'https://github.com/Kratosmax/jurymate/releases/latest/download/latest.json'
  const out: string[] = []
  if (cfg.use_mirror) {
    const mirrors = normalizeMirrors(cfg.github_mirrors)
    const mk = (m: string) => `${m}/${direct}`
    if (cfg.order === 'mirror_first') out.push(...mirrors.map(mk))
    out.push(direct)
    if (cfg.order === 'direct_first') out.push(...mirrors.map(mk))
  } else {
    out.push(direct)
  }
  return [...new Set(out)]
}
