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

async function req<T = any>(path: string, body?: unknown, method?: string): Promise<T> {
  const r = await fetch(path, {
    method: method ?? (body !== undefined ? 'POST' : 'GET'),
    headers: { 'Content-Type': 'application/json' },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  return r.json()
}

export const api = {
  state: () => req<State>('/api/state'),
  start: (folder: string, incremental: boolean) =>
    req<{ ok: boolean; error?: string }>('/api/start', { folder, incremental }),
  progress: () => req<{ running: boolean; done: number; total: number; stage: string; error: string | null }>('/api/progress'),
  pk: (group_id: number, action: 'pick' | 'all' | 'none', pick?: number) =>
    req<{ ok: boolean; error?: string }>('/api/pk', { group_id, action, pick }),
  recall: (path: string) => req<{ ok: boolean; aesthetic: number | null; error?: string }>('/api/recall', { path }),
  undo: () => req<{ ok: boolean; error?: string }>('/api/undo', {}),
  organize: (dry: boolean) => req<{ ok: boolean; moved?: number; winners?: number; rejects?: number; pending_kept?: number; skipped_conflict?: number; error?: string }>('/api/organize', { dry }),
  exportCsv: () => req<{ ok: boolean; path?: string; rows?: number; error?: string }>('/api/export_csv', {}),
  openFolder: (sub = 'winners') => req<{ ok: boolean; error?: string }>('/api/open_folder', { sub }),
}
