<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
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

// 模型就绪检查（首启动向导）
const sys = ref<any>(null)
const dlRunning = ref(false)
const dlId = ref('')
const dlDone = ref(0)
const dlTotal = ref(1)
const proxy = ref('')
async function checkSys() {
  try { sys.value = await (await fetch('/api/system/detect')).json() } catch {}
}
onMounted(checkSys)
async function dlModel(id: string) {
  dlRunning.value = true; dlId.value = id; dlDone.value = 0; dlTotal.value = 1
  await fetch('/api/models/download', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, proxy: proxy.value }) })
  const timer = setInterval(async () => {
    const p = await (await fetch('/api/models/progress')).json()
    dlDone.value = p.done; dlTotal.value = p.total
    if (!p.running) { clearInterval(timer); dlRunning.value = false; checkSys() }
  }, 500)
}
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
      <div v-if="sys && sys.models.some((m: any) => m.installed === false)" class="hintbar" style="text-align:left">
        <b>模型就绪检查</b>（检测到 {{ sys.backend_hint }}）
        <div v-for="m in (sys.models as any[])" :key="m.id" style="display:flex;align-items:center;gap:10px;margin-top:6px">
          <span style="flex:1">{{ m.name }} · {{ m.size_mb }}MB</span>
          <span v-if="m.installed === true" style="color:var(--good)">✓ 已就绪</span>
          <span v-else-if="m.installed === null" style="color:var(--dim)">首次评分自动下载</span>
          <template v-else>
            <button class="ghost" :disabled="dlRunning" @click="dlModel(m.id)">
              {{ dlRunning && dlId === m.id ? `下载中 ${Math.round(dlDone/dlTotal*100)}%` : '下载' }}
            </button>
          </template>
        </div>
        <div v-if="dlRunning" class="prog" style="margin:8px 0 0"><div :style="{width: (dlDone/dlTotal*100)+'%'}" /></div>
        <div style="font-size:11px;color:var(--dim);margin-top:6px">
          insightface 模型为非商业许可，不随程序分发、由你按需下载；可设置下载代理
          <input v-model="proxy" placeholder="http://127.0.0.1:10808（可选）" style="width:220px;padding:4px 8px;font-size:12px" />
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
