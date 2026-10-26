<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from './api'
import { check, type Update } from '@tauri-apps/plugin-updater'
import { relaunch } from '@tauri-apps/plugin-process'

const updateInfo = ref<{ version: string; body: string } | null>(null)
const updating = ref(false)
const APP_VERSION = __APP_VERSION__

// 更新下载进度：457MB 的包不给反馈，用户会以为卡死
const updStage = ref('')
const updGot = ref(0)
const updTotal = ref(0)
const updPct = computed(() =>
  updTotal.value ? Math.min(99, Math.round((updGot.value / updTotal.value) * 100)) : 0)

async function checkUpdate(silent: boolean) {
  try {
    if (!(window as any).__TAURI_INTERNALS__) return   // 浏览器模式无更新器
    const u: Update | null = await check()
    if (u) updateInfo.value = { version: u.version, body: u.body ?? '' }
    else if (!silent) toast(`已是最新版本（v${APP_VERSION}）`)
  } catch {
    if (!silent) toast('检查更新失败（清单未发布或网络不可达）')
  }
}
async function doUpdate() {
  if (!updateInfo.value || updating.value) return
  updating.value = true
  updStage.value = '检查更新'
  updGot.value = 0; updTotal.value = 0
  try {
    const u: Update | null = await check()
    if (!u) return
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

type Page = 'home' | 'pk' | 'winners' | 'review'
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
      <div style="margin-top:6px">
        <button class="ghost" style="padding:3px 10px;font-size:11px" :disabled="updating"
                @click="checkUpdate(false)">检查更新</button>
        <button v-if="updateInfo" class="act" style="padding:3px 10px;font-size:11px"
                :disabled="updating" @click="doUpdate">
          {{ updating ? (updStage || '更新中…') : `升级到 v${updateInfo.version}` }}
        </button>
      </div>
      <div style="margin-top:6px;font-size:10px;opacity:.6">v{{ APP_VERSION }}</div>
    </div>
    <div v-if="updating && updStage" class="hintbar" style="margin:10px 16px 0">
      {{ updStage }}
      <template v-if="updTotal">
        {{ updPct }}%（{{ (updGot / 1048576).toFixed(0) }} / {{ (updTotal / 1048576).toFixed(0) }} MB）
      </template>
      <div class="prog" style="margin-top:6px"><div :style="{ width: updPct + '%' }" /></div>
    </div>
    <div v-if="updateInfo && !updating" class="hintbar" style="margin:14px 16px 0">
      可更新到 v{{ updateInfo.version }}{{ updateInfo.body ? '：' + updateInfo.body.slice(0, 120) : '' }}
    </div>
  </nav>
  <div class="main">
    <div class="view-host">
      <HomeView v-show="page === 'home'" :state="S" @go="go" @toast="toast" @refresh="refresh" />
      <DecideView v-show="page === 'pk'" :state="S" @done="refresh" @toast="toast" />
      <WinnersView v-show="page === 'winners'" :state="S" @changed="refresh" @toast="toast" />
      <ReviewView v-show="page === 'review'" :state="S" @changed="refresh" @toast="toast" />
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
