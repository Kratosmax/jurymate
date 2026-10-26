<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
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

// localStorage 可能被写坏（进程在写入中被杀）或存了非数组值。
// 这行在 setup 顶层同步执行，一旦抛错整个组件不会渲染（表现为首页白屏、
// 侧栏仍在），所以必须兜住。
function loadRecent(): string[] {
  try {
    const raw = localStorage.getItem('jurymate_dirs')
    const v = raw ? JSON.parse(raw) : []
    return Array.isArray(v) ? v.filter((x) => typeof x === 'string') : []
  } catch {
    return []
  }
}
const recent = ref<string[]>(loadRecent())
function remember(d: string) {
  if (!d) return
  recent.value = [d, ...recent.value.filter((x) => x !== d)].slice(0, 6)
  try {
    localStorage.setItem('jurymate_dirs', JSON.stringify(recent.value))
  } catch { /* 隐私模式或配额满：不影响主流程 */ }
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
    if (p.error) {
      // 模型类失败要显眼且可操作：直接展开模型面板，让用户能立刻点下载
      errMsg.value = p.error
      errHint.value = p.error_hint ?? ''
      if (p.error_kind === 'model') {
        showModels.value = true
        checkSys()
      }
      return
    }
    errMsg.value = ''; errHint.value = ''
    emit('refresh')
    emit('toast', '整理完成，看看有哪些需要你拍板')
  }
}
onBeforeUnmount(() => clearInterval(timer))

// 模型就绪检查（首启动向导）
// sys 保持 null 表示"还没拿到/拿失败"，模板一律用 sys?.models 访问；
// 后端结构变化或旧 sidecar 时不能让整个首页崩掉。
const sys = ref<any>(null)
const models = computed<any[]>(() =>
  Array.isArray(sys.value?.models) ? sys.value.models : [])
const hasMissingModel = computed(() =>
  models.value.some((m: any) => m.installed === false))
const dlRunning = ref(false)
const dlId = ref('')
const dlDone = ref(0)
const dlTotal = ref(1)
const netMirrors = ref<string[]>([])
const netProxy = ref('')
const useMirror = ref(false)
const errMsg = ref('')
const errHint = ref('')
const showModels = ref(false)
const APP_VERSION = __APP_VERSION__
// sidecar 版本与界面版本不一致时提示：本地构建复用旧 exe 会导致数据形状错配
const verMismatch = computed(() => {
  const v = sys.value?.version
  return !!v && v !== 'unknown' && v !== APP_VERSION
})
const dlPct = computed(() => dlTotal.value ? Math.round(dlDone.value / dlTotal.value * 100) : 0)
async function checkSys() {
  try {
    sys.value = await api.systemDetect()
    netMirrors.value = [...(sys.value?.net?.github_mirrors ?? [])]
    netProxy.value = sys.value?.net?.http_proxy ?? ''
    useMirror.value = !!sys.value?.net?.use_mirror
  } catch {}
}
onMounted(checkSys)
async function saveNet() {
  const r = await api.netSettingsSave(
    useMirror.value,
    netMirrors.value.filter(s => s.trim()),
    netProxy.value.trim(),
  )
  if (r.ok) { emit('toast', '网络设置已保存'); checkSys() }
}
async function dlModel(id: string) {
  dlRunning.value = true; dlId.value = id; dlDone.value = 0; dlTotal.value = 1
  await api.modelsDownload(id)
  const timer = setInterval(async () => {
    const p = await api.modelsProgress()
    dlDone.value = p.done; dlTotal.value = p.total || 1
    if (!p.running) { clearInterval(timer); dlRunning.value = false
      if (p.error) emit('toast', '下载失败: ' + p.error)
      checkSys() }
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
      <div v-if="verMismatch" class="hintbar" style="text-align:left;border-color:var(--warn)">
        <b style="color:var(--warn)">后端与界面版本不一致</b>
        <div style="margin-top:6px;font-size:12px;color:var(--dim)">
          界面 v{{ APP_VERSION }} ／ 后端 v{{ sys.version }}。
          常见于本地构建复用了旧的 sidecar，请重新打包后端；正式安装包不受影响。
        </div>
      </div>
      <div v-if="errMsg" class="hintbar" style="text-align:left;border-color:var(--bad)">
        <b style="color:var(--bad)">无法继续：{{ errMsg }}</b>
        <div v-if="errHint" style="margin-top:6px;color:var(--dim);font-size:12px">{{ errHint }}</div>
        <div style="margin-top:8px;font-size:12px">
          下方「模型就绪检查」可直接下载；国内网络建议先开镜像站或填代理。
        </div>
      </div>
      <div v-if="sys && (showModels || hasMissingModel)" class="hintbar" style="text-align:left">
        <b>模型就绪检查</b>（{{ sys.backend_hint }}<template v-if="sys.gpu">｜{{ sys.gpu }}</template>）
        <div v-for="m in models" :key="m.id" style="display:flex;align-items:center;gap:10px;margin-top:6px">
          <span style="flex:1">{{ m.name }} · {{ m.size_mb }}MB</span>
          <span v-if="m.installed === true" style="color:var(--good)">✓ 已就绪</span>
          <template v-else>
            <button class="ghost" :disabled="dlRunning" @click="dlModel(m.id)">
              {{ dlRunning && dlId === m.id ? `下载中 ${dlPct}%` : '下载' }}
            </button>
          </template>
        </div>
        <div v-if="dlRunning" class="prog" style="margin:8px 0 0"><div :style="{width: dlPct+'%'}" /></div>
        <details style="margin-top:10px">
          <summary style="cursor:pointer;font-size:12px">网络设置（下载源 / 镜像站 / 代理）</summary>
          <div style="margin-top:8px;font-size:12px">
            <div style="color:var(--dim);margin-bottom:6px">
              默认从模型原仓库直连下载。国内网络不通时，可开启镜像站或填代理。
            </div>

            <label style="display:flex;align-items:center;gap:8px;cursor:pointer;margin-bottom:8px">
              <input type="checkbox" v-model="useMirror" style="width:auto;margin:0" />
              <span>使用 GitHub 镜像站（仅影响 GitHub 来源的模型）</span>
            </label>

            <template v-if="useMirror">
              <div style="color:var(--dim);margin:0 0 4px">镜像前缀（按序尝试，全部失败回落直连）：</div>
              <div v-for="(_, i) in netMirrors" :key="i" style="display:flex;gap:6px;margin-bottom:4px">
                <input v-model="netMirrors[i]" type="text" style="flex:1;padding:4px 8px;font-size:12px"
                       placeholder="https://ghfast.top" />
                <button class="ghost" style="padding:2px 8px" @click="netMirrors.splice(i, 1)">删</button>
              </div>
              <button class="ghost" style="padding:2px 10px" @click="netMirrors.push('')">+ 添加镜像</button>
            </template>

            <div style="color:var(--dim);margin:10px 0 4px">
              HTTP 代理（对所有下载生效，与镜像站互不冲突，可空）：
            </div>
            <input v-model="netProxy" type="text" style="width:280px;padding:4px 8px;font-size:12px"
                   placeholder="http://127.0.0.1:10808" />
            <div style="font-size:11px;color:var(--dim);margin-top:4px">
              填了代理会同时用于程序内下载与模型库自身的下载。
            </div>

            <div style="margin-top:8px">
              <button class="act" style="padding:5px 14px;font-size:12px" @click="saveNet">保存网络设置</button>
            </div>
          </div>
        </details>
        <div style="font-size:11px;color:var(--dim);margin-top:8px">
          insightface 人脸模型为非商业许可，不随程序分发、由你按需下载。
        </div>
      </div>
      <div v-if="state?.photos?.length" class="stat-row">
        <div class="stat" style="animation-delay: 0s"><b>{{ state.photos.length }}</b><span>共 {{ state.groups.length }} 组</span></div>
        <div class="stat" style="animation-delay: .05s"><b style="color: var(--good)">{{ state.photos.filter((p: any) => p.status === 'winner').length }}</b><span>暂定胜出</span></div>
        <div class="stat" style="animation-delay: .1s"><b style="color: var(--acc)">{{ state.groups.filter((g: any) => g.needs_pk).length }}</b><span>等你拍板</span></div>
        <div class="stat" style="animation-delay: .15s"><b style="color: var(--dim)">{{ state.photos.filter((p: any) => p.status === 'reject').length }}</b><span>落选待复查</span></div>
      </div>
      <div v-if="state?.photos?.length" style="margin-top: 24px">
        <button v-if="state.groups.some((g: any) => g.needs_pk)" class="act" @click="$emit('go', 'pk')">
          进入决策（{{ state.groups.filter((g: any) => g.needs_pk).length }} 组待拍板）
        </button>
        <template v-else>
          <button class="act" @click="$emit('go', 'winners')">查看胜出与评分</button>
          <button class="ghost" style="margin-left: 10px" @click="$emit('go', 'review')">复查落选</button>
        </template>
      </div>
    </div>
  </div>
</template>
