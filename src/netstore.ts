/**
 * 网络设置的本地存储（localStorage）。
 *
 * 网络设置同时服务两件事：后端模型下载器（要后端活着）与前端更新检查。
 * 应用刚启动、sidecar 还在解压的那 60~90 秒里，配置必须已经可读可改，
 * 所以 localStorage 是前端的第一事实源，后端起来后再同步一份。
 */

export interface NetCfg {
  use_mirror: boolean
  github_mirrors: string[]
  http_proxy: string
  order: 'mirror_first' | 'direct_first'
}

export interface UpdateLine {
  /** 清单地址（镜像前缀已拼好） */
  url: string
  /** 线路前缀；直连为空串 */
  prefix: string
}

const KEY = 'jurymate.net'
const DIRECT = 'https://github.com/Kratosmax/jurymate/releases/latest/download/latest.json'

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
    const list = Array.isArray(v.github_mirrors)
      ? v.github_mirrors.filter((m: unknown) => typeof m === 'string' && m.trim())
      : []
    return {
      use_mirror: v.use_mirror === true,
      // 空列表 + 已启用镜像 = 用内置默认线路，否则用户开了开关却一条线路都没有
      github_mirrors: list.length ? list : [...DEFAULT_NET.github_mirrors],
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

/** 更新线路：镜像优先/直连优先，直连永远兜底 */
export function updateLines(cfg: NetCfg): UpdateLine[] {
  const out: UpdateLine[] = []
  if (cfg.use_mirror) {
    const mirrors = normalizeMirrors(cfg.github_mirrors)
    const mk = (m: string): UpdateLine => ({ url: `${m}/${DIRECT}`, prefix: m })
    if (cfg.order === 'mirror_first') out.push(...mirrors.map(mk))
    out.push({ url: DIRECT, prefix: '' })
    if (cfg.order === 'direct_first') out.push(...mirrors.map(mk))
  } else {
    out.push({ url: DIRECT, prefix: '' })
  }
  const seen = new Set<string>()
  return out.filter((l) => (seen.has(l.url) ? false : (seen.add(l.url), true)))
}

/** 只要 URL 列表（界面显示用） */
export function updateCandidates(cfg: NetCfg): string[] {
  return updateLines(cfg).map((l) => l.url)
}
