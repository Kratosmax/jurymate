<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import { api, type State } from '../api'

const props = defineProps<{ state: State | null }>()
const emit = defineEmits<{ go: [p: 'pk' | 'winners' | 'review']; toast: [m: string]; refresh: [] }>()

const folder = ref('')
const scanning = ref(false)
const done = ref(0)
const total = ref(0)
const stage = ref('')
let timer: ReturnType<typeof setInterval> | undefined
const t0 = ref(0)

const recent = ref<string[]>(JSON.parse(localStorage.getItem('jurymate_dirs') ?? '[]'))
function remember(d: string) {
  if (!d) return
  recent.value = [d, ...recent.value.filter((x) => x !== d)].slice(0, 6)
  localStorage.setItem('jurymate_dirs', JSON.stringify(recent.value))
}

async function start(incremental = false) {
  const f = folder.value.trim()
  if (!f) return
  remember(f)
  const r = await api.start(f, incremental)
  if (!r.ok) return
  scanning.value = true
  t0.value = Date.now()
  timer = setInterval(poll, 700)
}
async function poll() {
  const p = await api.progress()
  done.value = p.done; total.value = p.total; stage.value = p.stage
  if (!p.running) {
  clearInterval(timer)
  scanning.value = false
  if (p.error) { emit('toast', '失败: ' + p.error); return }
  emit("refresh")
  emit("toast", "整理完成，看看有哪些需要你拍板")
  }
}
onBeforeUnmount(() => clearInterval(timer))

const frac = () => (total.value ? done.value / total.value : 0)
const etaText = () => {
  const f = frac()
  if (f >= 0.999) return '，正在收尾…'
  if (f > 0.02) return `，预计剩余 ${Math.max(1, Math.round(((Date.now() - t0.value) / f) * (1 - f) / 1000))} 秒`
  return '，准备中…'
}
</script>

<template>
  <div class="view" id="p-home">
    <div style="max-width: 660px; margin: 30px auto; text-align: center">
      <h2 class="pgtitle">把照片目录交给我</h2>
      <p class="pgsub">
        AI 完成技术初筛、连拍归组、默认选片与胜出图评分（全本地，零 token）<br />
        你只复核「分差小」的组，落选片随时复查召回
      </p>
      <div style="display: flex; gap: 10px; justify-content: center">
        <input v-model="folder" type="text" style="width: 440px"
               placeholder="照片文件夹绝对路径，如 C:\Users\you\Pictures\xx" list="dirs" />
        <datalist id="dirs"><option v-for="d in recent" :key="d" :value="d" /></datalist>
        <button class="act" :disabled="scanning" @click="start(false)">开始整理</button>
        <button v-if="state?.photos?.length" class="ghost" :disabled="scanning"
                title="目录里有新图时只补新图，已有人工决策保留" @click="start(true)">增量补扫</button>
      </div>
      <div style="margin-top: 10px; font-size: 12px; color: var(--dim)">
        最近：<template v-for="(d, i) in recent.slice(0, 3)" :key="d"><a href="#"
                style="color: var(--acc)" @click.prevent="folder = d; start(false)">{{ d }}</a><span v-if="i < Math.min(recent.length, 3) - 1"> · </span></template>
      </div>
      <div v-if="scanning" style="text-align: left">
        <div class="prog"><div :style="{ width: frac() * 100 + '%' }" /></div>
        <div style="text-align: center; color: var(--dim); font-size: 13px">
          {{ stage }} {{ done }}/{{ total }}{{ etaText() }}
        </div>
      </div>
      <div v-if="state?.photos?.length" class="stat-row">
        <div class="stat" style="animation-delay: 0s"><b>{{ state.photos.length }}</b><span>共 {{ state.groups.length }} 组</span></div>
        <div class="stat" style="animation-delay: .05s"><b style="color: var(--good)">{{ state.photos.filter(p => p.status === 'winner').length }}</b><span>暂定胜出</span></div>
        <div class="stat" style="animation-delay: .1s"><b style="color: var(--acc)">{{ state.groups.filter(g => g.needs_pk).length }}</b><span>等你拍板</span></div>
        <div class="stat" style="animation-delay: .15s"><b style="color: var(--dim)">{{ state.photos.filter(p => p.status === 'reject').length }}</b><span>落选待复查</span></div>
      </div>
      <div v-if="state?.photos?.length" style="margin-top: 24px">
        <button v-if="state.groups.some(g => g.needs_pk)" class="act" @click="$emit('go', 'pk')">
          进入决策（{{ state.groups.filter(g => g.needs_pk).length }} 组待拍板）
        </button>
        <template v-else>
          <button class="act" @click="$emit('go', 'winners')">查看胜出与评分</button>
          <button class="ghost" style="margin-left: 10px" @click="$emit('go', 'review')">复查落选</button>
        </template>
      </div>
    </div>
  </div>
</template>
