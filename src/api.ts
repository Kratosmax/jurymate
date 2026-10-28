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
  sharp_score?: number | null
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
  /** 当前评分模式：'aesthetic' | 'quality' | ''（未选） */
  score_mode?: string
  /** 缓存里现有分数是哪个模式算出来的 */
  scored_mode?: string
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
 * 首次安装后实测可到 60~90 秒（Rust 侧 spawn_backend 自己等 90 秒）。
 * 原来这里只等 60 秒、而且把"失败"缓存在模块变量里永不重试——
 * 用户看到的就是「读取路径失败，可稍后刷新本页」，只能按 F5 才能恢复。
 *
 * 现在：等 180 秒；失败不缓存（下次调用重新试）；每秒钟回调一次已等秒数，
 * 让界面能显示"正在启动，已等 32 秒"而不是一个死掉的报错。
 */
let backendReady: Promise<boolean> | null = null
export async function waitBackend(
  onTick?: (seconds: number) => void,
  timeoutMs = 180000,
): Promise<boolean> {
  if (backendReady) return backendReady
  backendReady = (async () => {
    await initBase()
    if (!BASE) return false
    const t0 = Date.now()
    const deadline = t0 + timeoutMs
    while (Date.now() < deadline) {
      try {
        const r = await fetch(BASE + '/api/state', { method: 'GET' })
        if (r.ok) return true
      } catch { /* 还没起来，继续等 */ }
      onTick?.(Math.round((Date.now() - t0) / 1000))
      await new Promise(res => setTimeout(res, 1000))
    }
    return false
  })()
  const ok = await backendReady
  // 失败不能缓存成永久状态：否则后续任何一次调用都立刻返回 false，
  // 页面再怎么刷新都没用，只有 F5 重新加载整个模块才能恢复。
  if (!ok) backendReady = null
  return ok
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

/** 把 groups[].members 的缩略图也补成绝对地址。
 *
 * 决策页用的是 group.members（后端 /api/state 单独拼的一份精简照片），
 * 原来只对 s.photos 调了 absT，members 里还是 "/thumbs/..." 相对路径，
 * 在 Tauri 的 file:// 下解析不到，于是决策页整页都是破图。
 */
function absGroups(groups: Group[]): Group[] {
  return groups.map((g) => ({ ...g, members: g.members.map((m) => ({ ...absT(m), idx: m.idx })) }))
}

export const api = {
  async state(): Promise<State> {
    await initBase()
    const s = await req<State>('/api/state')
    return { ...s, photos: s.photos.map(absT), groups: absGroups(s.groups ?? []) }
  },
  start: (folder: string, mode: 'full' | 'incremental' | 'reset' = 'full') =>
    req<{ ok: boolean; error?: string }>('/api/start', {
      folder,
      incremental: mode === 'incremental',
      reset: mode === 'reset',
    }),
  progress: () => req<{ running: boolean; done: number; total: number; stage: string; error: string | null; error_kind?: string | null; error_hint?: string }>('/api/progress'),
  pk: (group_id: number, action: 'pick' | 'all' | 'none', pick?: number) =>
    req<{ ok: boolean; error?: string }>('/api/pk', { group_id, action, pick }),
  recall: (path: string) => req<{ ok: boolean; aesthetic: number | null; error?: string; hint?: string }>('/api/recall', { path }),
  // 人工指定单张胜负：绕过组逻辑，用户可以直接说"这张要/不要"
  setStatus: (path: string, status: 'winner' | 'reject') =>
    req<{ ok: boolean; status?: string; error?: string }>('/api/set_status', { path, status }),
  undo: () => req<{ ok: boolean; error?: string }>('/api/undo', {}),
  // 归类 = 把 winner 复制到 winners/（原文件不动，落选不落盘）
  // 重复点不会堆副本：按内容 SHA-256 判重
  organize: (dry: boolean, paths?: string[]) =>
    req<{
      ok: boolean; copied?: number; winners?: number; rejects_skipped?: number
      pending_kept?: number; skipped_same?: number; skipped_conflict?: number
      conflicts?: string[]; error?: string
    }>('/api/organize', { dry, paths }),
  exportCsv: () => req<{ ok: boolean; path?: string; rows?: number; error?: string }>('/api/export_csv', {}),
  openFolder: (sub = '', select = '') =>
    req<{ ok: boolean; selected?: string; error?: string }>('/api/open_folder', { sub, select }),
  systemDetect: () => req<any>('/api/system/detect'),
  modelsDownload: (id: string) => req<{ ok: boolean; error?: string | null }>('/api/models/download', { id }),
  // 按需删除单个模型；删的是当前模式的模型时后端会把 score_mode 置空
  modelsRemove: (id: string) =>
    req<{ ok: boolean; error?: string; score_mode?: string }>('/api/models/remove', { id }),
  modelsProgress: () => req<{ running: boolean; done: number; total: number; id: string; error: string | null }>('/api/models/progress'),
  netSettingsGet: () => req<{
    use_mirror: boolean; github_mirrors: string[]; http_proxy: string | null
    order?: 'mirror_first' | 'direct_first'
  }>('/api/net/settings'),
  netSettingsSave: (use_mirror: boolean, github_mirrors: string[], http_proxy: string,
                    order: 'mirror_first' | 'direct_first' = 'mirror_first') =>
    req<{ ok: boolean }>('/api/net/settings', { use_mirror, github_mirrors, http_proxy, order }),
  settingsGet: () => req<{
    ok: boolean; scan_workers: number; cpu_count: number; auto_workers: number
    score_mode?: string; score_weight?: number; scored_mode?: string
  }>('/api/settings'),
  settingsSave: (body: { scan_workers?: number; score_mode?: string; score_weight?: number }) =>
    req<{ ok: boolean; error?: string }>('/api/settings', body),
  // 重算综合分：只改权重时纯重算；换模式时后端会用新模型重新评分
  rescore: () =>
    req<{ ok: boolean; rescored?: boolean; error?: string; need_mode?: boolean }>(
      '/api/rescore', {}),
  pathsGet: () => req<{
    data_root: string; default_root: string; is_default: boolean; env_override: boolean
    subdirs: { key: string; label: string; path: string; size: number }[]
  }>('/api/paths'),
  pathsSave: (data_root: string) =>
    req<{ ok: boolean; data_root?: string; started?: boolean; error?: string }>(
      '/api/paths', { data_root }),
  pathsProgress: () => req<{
    running: boolean; done: number; total: number; current: string
    migrated: { key: string; label: string }[]
    skipped: { key: string; label: string; reason: string }[]
    error: string | null; target: string; old: string
  }>('/api/paths/progress'),
}
