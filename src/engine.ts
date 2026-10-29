import { invoke } from '@tauri-apps/api/core'
import { engineLines, loadNetCfg } from './netstore'

export interface EngineStatus {
  installed: boolean
  version: string | null
  source: 'engine' | 'bundled' | 'dev' | 'none'
  path: string | null
}

export interface EngineInfo {
  available: boolean
  version: string | null
  notes: string | null
  current_version: string | null
}

export function isTauri(): boolean {
  return !!(window as any).__TAURI_INTERNALS__
}

export async function getEngineStatus(): Promise<EngineStatus> {
  if (!isTauri()) {
    return { installed: true, version: __APP_VERSION__, source: 'dev', path: null }
  }
  return invoke<EngineStatus>('engine_status')
}

function lines() {
  const cfg = loadNetCfg()
  const ls = engineLines(cfg)
  return {
    endpoints: ls.map((l) => l.url),
    prefixes: ls.map((l) => l.prefix),
    proxy: cfg.http_proxy.trim() || null,
  }
}

export async function checkEngine(): Promise<EngineInfo> {
  if (!isTauri()) throw new Error('浏览器模式不支持引擎更新')
  return invoke<EngineInfo>('engine_check', lines())
}

export async function installEngine(): Promise<string> {
  if (!isTauri()) throw new Error('浏览器模式不支持引擎安装')
  return invoke<string>('engine_install', lines())
}

export async function startEngine(): Promise<void> {
  if (!isTauri()) return
  await invoke('engine_start')
}
