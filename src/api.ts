export interface Photo {
  path: string
  name: string
  is_raw: boolean
  width: number
  height: number
  thumb_view: string
  thumb_grid: string
  sharpness: number
  overexp_ratio: number
  underexp_ratio: number
  faces: { bbox: number[]; ear_l: number | null; ear_r: number | null; closed?: boolean; half?: boolean }[]
  n_faces: number
  face_sharpness?: number
  aesthetic: number | null
  score: number
  reject_reasons: string[]
  status: 'winner' | 'reject' | 'pending'
  group_id: number
  mtime_str: string
  manual_winner?: boolean
}

export interface GroupMember extends Photo { idx: number }
export interface Group {
  id: number
  members: GroupMember[]
  winner: number
  gap: number
  needs_pk: boolean
}
export interface State {
  folder: string | null
  photos: Photo[]
  groups: Group[]
  settings: Record<string, number>
}

// Tauri webview 的 origin 不是后端：需要绝对 baseUrl；浏览器/vite 模式走相对路径。
// 全站唯一 fetch 出口：BASE 解析 + HTTP 错误 → { ok:false, error } 统一形状，
// 调用方只认 ok 字段（视图层不再散布 try/catch）。
let BASE = ''
let baseReady: Promise<void> | null = null
async function initBase() {
  if (!baseReady) {
    baseReady = (async () => {
      const t = (window as any).__TAURI_INTERNALS__ ?? (window as any).__TAURI__
      if (t) {
        try {
          const { invoke } = await import('@tauri-apps/api/core')
          BASE = `http://127.0.0.1:${await invoke<number>('backend_port')}`
        } catch {
          BASE = 'http://127.0.0.1:5090'   // invoke 失败兜底（约定端口）
        }
      }
    })().catch(() => {})
  }
  await baseReady
}

/**
 * 等后端真正可服务。
 *
 * 后端是 PyInstaller onefile，冷启动要把约 1.1GB 运行时解压出来，
 * 实测可到 20 秒以上。此前页面在端口起来之前就发请求，全部失败，
 * 界面显示成空值（例如「当前目录 —」），用户以为功能坏了。
 */
let backendReady: Promise<boolean> | null = null
async function waitBackend(timeoutMs = 60000): Promise<boolean> {
  if (!backendReady) {
    backendReady = (async () => {
      await initBase()
      if (!BASE) return false
      const deadline = Date.now() + timeoutMs
      while (Date.now() < deadline) {
        try {
          const r = await fetch(BASE + '/api/state', { method: 'GET' })
          if (r.ok) return true
        } catch { /* 还没起来，继续等 */ }
        await new Promise(res => setTimeout(res, 800))
      }
      return false
    })()
  }
  return backendReady
}

async function req<T = any>(path: string, body?: unknown, method?: string): Promise<T> {
  await initBase()
  let r: Response
  try {
    r = await fetch(BASE + path, {
      method: method ?? (body !== undefined ? 'POST' : 'GET'),
      headers: { 'Content-Type': 'application/json' },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  } catch (e) {
    // 网络层失败（sidecar 未起/被墙）也归一为业务错误形状
    return { ok: false, error: `无法连接后端服务（${e}）` } as any
  }  if (!r.ok && !(r.headers.get('content-type') ?? '').includes('json')) {
    return { ok: false, error: `HTTP ${r.status}` } as any
  }
  return r.json()
}

function absT(p: Photo): Photo {
  return BASE && !p.thumb_view.startsWith('http')
    ? { ...p, thumb_view: BASE + p.thumb_view, thumb_grid: BASE + p.thumb_grid }
    : p
}

export { waitBackend }

export const api = {
  async state(): Promise<State> {
    await initBase()
    const s = await req<State>('/api/state')
    return { ...s, photos: s.photos.map(absT) }
  },
  start: (folder: string, incremental: boolean) =>
    req<{ ok: boolean; error?: string }>('/api/start', { folder, incremental }),
  progress: () => req<{ running: boolean; done: number; total: number; stage: string; error: string | null; error_kind?: string | null; error_hint?: string }>('/api/progress'),
  pk: (group_id: number, action: 'pick' | 'all' | 'none', pick?: number) =>
    req<{ ok: boolean; error?: string }>('/api/pk', { group_id, action, pick }),
  recall: (path: string) => req<{ ok: boolean; aesthetic: number | null; error?: string; hint?: string }>('/api/recall', { path }),
  undo: () => req<{ ok: boolean; error?: string }>('/api/undo', {}),
  organize: (dry: boolean) => req<{ ok: boolean; moved?: number; winners?: number; rejects?: number; pending_kept?: number; skipped_conflict?: number; error?: string }>('/api/organize', { dry }),
  exportCsv: () => req<{ ok: boolean; path?: string; rows?: number; error?: string }>('/api/export_csv', {}),
  openFolder: (sub = 'winners') => req<{ ok: boolean; error?: string }>('/api/open_folder', { sub }),
  systemDetect: () => req<any>('/api/system/detect'),
  modelsDownload: (id: string) => req<{ ok: boolean; error?: string | null }>('/api/models/download', { id }),
  modelsProgress: () => req<{ running: boolean; done: number; total: number; id: string; error: string | null }>('/api/models/progress'),
  netSettingsGet: () => req<{ use_mirror: boolean; github_mirrors: string[]; http_proxy: string | null }>('/api/net/settings'),
  netSettingsSave: (use_mirror: boolean, github_mirrors: string[], http_proxy: string) =>
    req<{ ok: boolean }>('/api/net/settings', { use_mirror, github_mirrors, http_proxy }),
  pathsGet: () => req<{
    data_root: string; default_root: string; is_default: boolean; env_override: boolean
    subdirs: { key: string; label: string; path: string; size: number }[]
  }>('/api/paths'),
  pathsSave: (data_root: string) =>
    req<{ ok: boolean; data_root?: string; error?: string }>('/api/paths', { data_root }),
}
