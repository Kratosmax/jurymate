<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from './api'
import { check, type Update } from '@tauri-apps/plugin-updater'
import { relaunch } from '@tauri-apps/plugin-process'
import { invoke } from '@tauri-apps/api/core'

const updateInfo = ref<{ version: string; body: string } | null>(null)
const updating = ref(false)
const APP_VERSION = __APP_VERSION__

// 更新下载进度：457MB 的包不给反馈，用户会以为卡死
const updStage = ref('')
const updGot = ref(0)
const updTotal = ref(0)
const updPct = computed(() =>
  updTotal.value ? Math.min(99, Math.round((updGot.value / updTotal.value) * 100)) : 0)

// 检查更新的结果放进常驻状态，而不是只发一条 2.6 秒的 toast。
// toast 会被随后的状态刷新冲掉，用户看到的就是"点了按钮没反应"——
// 归档、导出都因为同样的原因被误报成"功能坏了"，这里是第三处。
// 必须定义在 updaterApi 之前（它要引用）。
const updCheck = ref<{
  kind: 'ok' | 'none' | 'err'; text: string; detail?: string
} | null>(null)
const updChecking = ref(false)

// 设置页要用到这些状态与动作。用 reactive 包一层：ref 塞进普通对象传给
// 子组件不会自动解包，子组件读到的是 Ref 对象而不是值。
const updaterApi = reactive({
  APP_VERSION,
  get updateInfo() { return updateInfo.value },
  get updating() { return updating.value },
  get updStage() { return updStage.value },
  get updGot() { return updGot.value },
  get updTotal() { return updTotal.value },
  get updPct() { return updPct.value },
  get updCheck() { return updCheck.value },
  get updChecking() { return updChecking.value },
  checkUpdate: (silent: boolean) => checkUpdate(silent),
  doUpdate: () => doUpdate(),
})

// 更新器默认只认系统代理，不认应用内「网络设置」的代理——用户明明配了代理、
// 模型能下，检查更新却失败。这里把同一份设置显式传给更新器。
// 未配置时传 undefined，行为退回系统代理/直连，不改变原有默认。
let cachedProxy: string | null | undefined
async function updaterProxy(): Promise<string | undefined> {
  if (cachedProxy !== undefined) return cachedProxy ?? undefined
  try {
    const net = await api.netSettingsGet()
    // 后端出错时会返回 { ok:false, error }，此时不能把结果缓存成"无代理"——
    // 否则一次早于 initBase 的失败会让代理永久失效。
    if (!net || (net as any).ok === false || (net as any).error) {
      return undefined
    }
    cachedProxy = net.http_proxy?.trim() || null
  } catch {
    return undefined   // 同样不缓存，下次重试
  }
  return cachedProxy ?? undefined
}
/** 网络设置变更后清掉缓存，让下次检查更新用新代理。 */
function invalidateProxy() { cachedProxy = undefined }


// 检查更新的结果放进常驻状态（定义见文件上方 updCheck）
async function checkUpdate(silent: boolean) {
  if (!silent) { updChecking.value = true; updCheck.value = null }
  try {
    if (!(window as any).__TAURI_INTERNALS__) return   // 浏览器模式无更新器
    const proxy = await updaterProxy()
    const u: Update | null = await check({ proxy })
    if (u) {
      updateInfo.value = { version: u.version, body: u.body ?? '' }
      updCheck.value = { kind: 'ok', text: `发现新版本 v${u.version}` }
    } else {
      updCheck.value = { kind: 'none', text: `已是最新版本（v${APP_VERSION}）` }
    }
  } catch (e) {
    const via = await updaterProxy()
    updCheck.value = {
      kind: 'err',
      text: '检查更新失败',
      // 把"用没用代理"写出来：系统代理关着又没填代理时，直连 GitHub
      // 必然超时，这时候说"网络受限"比只说"失败"有用得多。
      detail: `${e}`.slice(0, 120) + (via ? `（已用代理 ${via}）` : '（未配置代理，走系统代理/直连）'),
    }
  } finally {
    if (!silent) updChecking.value = false
  }
}
async function doUpdate() {
  if (!updateInfo.value || updating.value) return
  updating.value = true
  updStage.value = '检查更新'
  updGot.value = 0; updTotal.value = 0
  try {
    const proxy = await updaterProxy()
    console.info('[jurymate] 更新器使用代理(下载):', proxy ?? '(系统代理/直连)')
    const u: Update | null = await check({ proxy })
    if (!u) return
    // 关键：安装器起来时应用还活着、sidecar 也还活着，会锁住
    // dist-backend/*.exe 导致安装卡在"文件被占用"。必须在下载安装前
    // 主动停掉后端并等文件真正释放（RunEvent::Exit 的清理来得太晚）。
    updStage.value = '准备安装'
    await invoke<string>('prepare_update').catch(() => 'skip')
    updStage.value = '下载中'
    await u.downloadAndInstall((ev) => {
      if (ev.event === 'Started') {
        updTotal.value = ev.data.contentLength ?? 0
        updStage.value = '下载中'
      } else if (ev.event === 'Progress') {
        updGot.value += ev.data.chunkLength
      } else if (ev.event === 'Finished') {
        updStage.value = '校验并安装'
        updGot.value = updTotal.value || updGot.value   // 校验通过才视为 100%
      }
    })
    updStage.value = '重启中'
    await relaunch()
  } catch (e) {
    // 说清阶段与影响，再给动作；不把异常类型直接甩给用户
    const phase = updStage.value || '更新'
    updStage.value = ''
    toast(`${phase}失败，现有安装未被修改（可能需要管理员权限）。可重试，或到 Releases 手动下载。`)
    console.error('[jurymate] 更新失败:', e)
  } finally {
    updating.value = false
  }
}
window.addEventListener('keydown', (e) => {
  // 输入框里打字不能触发检查更新（路径含 u 的目录很常见）
  const tag = (document.activeElement as HTMLElement)?.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA') return
  if (e.key === 'u' || e.key === 'U') checkUpdate(false)
})
import HomeView from './views/HomeView.vue'
import DecideView from './views/DecideView.vue'
import WinnersView from './views/WinnersView.vue'
import ReviewView from './views/ReviewView.vue'
import SettingsView from './views/SettingsView.vue'

type Page = 'home' | 'pk' | 'winners' | 'review' | 'settings'
const page = ref<Page>('home')
const S = ref<import('./api').State | null>(null)
const toastMsg = ref('')
let toastTimer: ReturnType<typeof setTimeout> | undefined

const pages: { id: Page; label: string; key: string }[] = [
  { id: 'home', label: '总览', key: '1' },
  { id: 'pk', label: '决策', key: '2' },
  { id: 'winners', label: '胜出', key: '3' },
  { id: 'review', label: '复查', key: '4' },
]

function toast(m: string) {
  toastMsg.value = m
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => (toastMsg.value = ''), 2600)
}
async function refresh() {
  S.value = await api.state()
}
function go(p: Page) {
  page.value = p
}
const pkBadge = computed(() => S.value?.groups.filter((g) => g.needs_pk).length ?? 0)

// 1-4 切页（PK 页内部的方向键等由子组件自行监听）
window.addEventListener('keydown', (e) => {
  const tag = (document.activeElement as HTMLElement)?.tagName
  if (tag === 'INPUT') return
  if (e.key >= '1' && e.key <= '4') go(pages[Number(e.key) - 1].id)
})

onMounted(() => {
  refresh()
  checkUpdate(true)   // 启动静默检查
})
</script>

<template>
  <nav class="side">
    <div class="brand">⚖️ Jury<em>Mate</em></div>
    <div class="brand-sub">AI 陪审 · 你是法官</div>
    <button v-for="p in pages" :key="p.id" class="nav-item" :class="{ on: page === p.id }" @click="go(p.id)">
      {{ p.label }}
      <span v-if="p.id === 'pk' && pkBadge" class="badge">{{ pkBadge }}</span>
      <span v-else class="k">{{ p.key }}</span>
    </button>
    <div class="side-foot">
      <div class="tagline">全本地推理 · 零 token</div>
      <div><kbd>?</kbd> 快捷键 · <kbd>1-4</kbd> 切页</div>
      <div style="margin-top:8px">
        <button class="nav-item" :class="{ on: page === 'settings' }" @click="go('settings')">
          设置
          <span class="k">5</span>
        </button>
      </div>
      <div style="margin-top:6px;font-size:10px;opacity:.6">v{{ APP_VERSION }}</div>
    </div>
  </nav>
  <div class="main">
    <div class="view-host">
      <HomeView v-show="page === 'home'" :state="S" @go="go" @toast="toast" @refresh="refresh" />
      <DecideView v-show="page === 'pk'" :state="S" @done="refresh" @toast="toast" />
      <WinnersView v-show="page === 'winners'" :state="S" @changed="refresh" @toast="toast" />
      <ReviewView v-show="page === 'review'" :state="S" @changed="refresh" @toast="toast" />
      <SettingsView v-show="page === 'settings'" @toast="toast" @netsaved="invalidateProxy"
                    :updater="updaterApi" />
    </div>
  </div>
  <transition name="toast">
    <div v-if="toastMsg" class="toast">{{ toastMsg }}</div>
  </transition>
</template>

<style>
.toast {
  position: fixed; bottom: 26px; left: 50%; transform: translateX(-50%);
  background: var(--panel2); border: 1px solid var(--line); padding: 10px 22px;
  border-radius: 10px; z-index: 60; max-width: 80vw; font-size: 13px;
  box-shadow: 0 8px 30px rgba(0, 0, 0, .4);
}
.toast-enter-active, .toast-leave-active { transition: all .25s var(--ease); }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translate(-50%, 12px); }
</style>
