<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from './api'
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

onMounted(refresh)
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
