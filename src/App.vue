<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { api } from './api'
import { cardsIn, pageEnter } from './motion'
import { loadNetCfg, updateCandidates } from './netstore'
import { listen } from '@tauri-apps/api/event'
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

// 检查更新的结果放进常驻状态（定义见文件上方 updCheck）
async function checkUpdate(silent: boolean) {
  if (!silent) { updChecking.value = true; updCheck.value = null }
  try {
    if (!(window as any).__TAURI_INTERNALS__) return   // 浏览器模式无更新器
    const cfg = loadNetCfg()
    const endpoints = updateCandidates(cfg)
    const proxy = cfg.http_proxy.trim() || null
    const info = await invoke<{
      available: boolean; current_version: string
      version: string | null; notes: string | null
    }>('updater_check', { endpoints, proxy })
    if (info.available && info.version) {
      updateInfo.value = { version: info.version, body: info.notes ?? '' }
      updCheck.value = { kind: 'ok', text: `发现新版本 v${info.version}` }
    } else {
      updCheck.value = { kind: 'none', text: `已是最新版本（v${APP_VERSION}）` }
    }
  } catch (e) {
    const cfg = loadNetCfg()
    const lines = updateCandidates(cfg).length
    updCheck.value = {
      kind: 'err',
      text: '检查更新失败',
      detail: `${e}`.slice(0, 140) + `（已尝试 ${lines} 条线路${cfg.http_proxy ? '，含代理' : ''}）`,
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
  let un: (() => void) | undefined
  try {
    const cfg = loadNetCfg()
    const endpoints = updateCandidates(cfg)
    const proxy = cfg.http_proxy.trim() || null
    // 关键：安装器起来时应用还活着、sidecar 也还活着，会锁住
    // dist-backend/*.exe 导致安装卡在"文件被占用"。必须在下载安装前
    // 主动停掉后端并等文件真正释放（RunEvent::Exit 的清理来得太晚）。
    updStage.value = '准备安装'
    await invoke<string>('prepare_update').catch(() => 'skip')
    un = await listen<[number, number] | { got: number; total: number | null }>(
      'updater://progress',
      (ev) => {
        const p: any = ev.payload
        if (Array.isArray(p)) {
          updGot.value = p[0]
          if (p[1]) updTotal.value = p[1]
        } else {
          updGot.value = p.got
          if (p.total) updTotal.value = p.total
        }
        updStage.value = '下载中'
      },
    )
    updStage.value = '下载中'
    await invoke('updater_install', { endpoints, proxy })
    updStage.value = '重启中'
    await relaunch()
  } catch (e) {
    // 说清阶段与影响，再给动作；不把异常类型直接甩给用户
    const phase = updStage.value || '更新'
    updStage.value = ''
    toast(`${phase}失败，现有安装未被修改（可能需要管理员权限）。可重试，或到 Releases 手动下载。`)
    console.error('[jurymate] 更新失败:', e)
  } finally {
    un?.()
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

const pages: { id: Page; label: string }[] = [
  { id: 'home', label: '总览' },
  { id: 'pk', label: '决策' },
  { id: 'winners', label: '胜出' },
  { id: 'review', label: '复查' },
]

function toast(m: string, ms = 3000) {
  toastMsg.value = m
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => (toastMsg.value = ''), ms)
}
async function refresh() {
  S.value = await api.state()
}
function go(p: Page) {
  if (p === page.value) return
  page.value = p
  // 切页动画必须在"DOM 已更新、但还没绘制"这一瞬间启动（nextTick）。
  // 之前用 requestAnimationFrame，那是下一帧绘制之后才跑——用户会先看到
  // 新页面完整出现，再被动画拉回 opacity:0 重放一次，表现为"闪一下/抖一下"。
  nextTick(() => {
    const el = Array.from(
      document.querySelectorAll<HTMLElement>('.view-host .view'),
    ).find((e) => e.offsetParent !== null)
    if (!el) return
    pageEnter(el)
    // 卡片也要重放：CSS 只在挂载时跑一次，v-show 的页面会错过
    cardsIn(el)
  })
}

// 侧栏数字改成"这一页有多少东西"，比快捷键提示有用得多。
// 决策页显示待拍板组数，其余显示照片张数。
const navCount = computed<Record<string, number>>(() => {
  const s = S.value
  if (!s) return {} as Record<string, number>
  return {
    home: s.photos.length,
    pk: s.groups.filter((g) => g.needs_pk).length,
    winners: s.photos.filter((p) => p.status === 'winner').length,
    review: s.photos.filter((p) => p.status === 'reject').length,
  } as Record<string, number>
})

// 侧栏选中块：像 iOS 标签栏一样在项目之间滑动，而不是整块瞬间换位置
const navEl = ref<HTMLElement | null>(null)
const pill = ref({ top: 0, height: 0, show: false })
function syncPill() {
  const el = navEl.value?.querySelector('.nav-item.on') as HTMLElement | null
  if (!el) { pill.value.show = false; return }
  pill.value = { top: el.offsetTop, height: el.offsetHeight, show: true }
}
watch(page, () => nextTick(syncPill))
onMounted(() => {
  nextTick(syncPill)
  window.addEventListener('resize', syncPill)
})

onMounted(() => {
  refresh()
  checkUpdate(true)   // 启动静默检查
  // 禁掉 WebView2 的默认右键菜单：里面是"共享/打印/复制图像/复制图像链接/
  // 另存为/放大"这些浏览器功能，对本地选片工具没有意义。
  // 用 capture 阶段拦，保证在页面任何元素之前生效；
  // 胜出页自己的右键菜单在组件内先 preventDefault 并渲染自定义菜单。
  document.addEventListener('contextmenu', (e) => e.preventDefault(), true)
})
</script>

<template>
  <nav class="side" ref="navEl">
    <div v-if="pill.show" class="nav-pill" :style="{ top: pill.top + 'px', height: pill.height + 'px' }" />
    <div class="brand">⚖️ Jury<em>Mate</em></div>
    <div class="brand-sub">AI 陪审 · 你是法官</div>
    <button v-for="p in pages" :key="p.id" class="nav-item" :class="{ on: page === p.id }" @click="go(p.id)">
      {{ p.label }}
      <span v-if="navCount[p.id]" class="k">{{ navCount[p.id] }}</span>
    </button>
    <div class="side-foot">
      <div class="tagline">全本地推理 · 零 token</div>
      <div style="margin-top:8px">
        <button class="nav-item" :class="{ on: page === 'settings' }" @click="go('settings')">
          设置
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
      <SettingsView v-show="page === 'settings'" @toast="toast" @netsaved="() => {}"
                    @refresh="refresh" :updater="updaterApi" />
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
