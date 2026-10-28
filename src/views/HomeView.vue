<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api, waitBackend, type State } from '../api'

const props = defineProps<{ state: State | null }>()
const emit = defineEmits<{ go: [p: 'pk' | 'winners' | 'review' | 'settings']; toast: [m: string]; refresh: [] }>()

const folder = ref('')
const scanning = ref(false)
const done = ref(0)
const total = ref(0)
const stage = ref('')
let timer: ReturnType<typeof setInterval> | undefined
const t0 = ref(0)
// 「强制重新整理」的二次确认（不用 window.confirm：Tauri webview 支持不可靠）
const confirmReset = ref<boolean>(false)

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

// mode: 'full' 首次/全量 | 'incremental' 只补新图 | 'reset' 清缓存从零重建
const shake = ref(false)
const needMode = ref(false)

function shakeFolder() {
  shake.value = false
  // 强制重排，让同一个动画能连续触发两次
  requestAnimationFrame(() => { shake.value = true })
  setTimeout(() => (shake.value = false), 700)
  emit('toast', '请先选择目标目录')
}

async function start(mode: 'full' | 'incremental' | 'reset' = 'full') {
  const f = folder.value.trim()
  if (!f) { shakeFolder(); return }
  if (mode === 'reset' && !confirmReset.value) {
    confirmReset.value = true
    return
  }
  confirmReset.value = false
  remember(f)
  const r = await api.start(f, mode)
  if (!r.ok) {
    // 没选评分模式：直接带去设置页，而不是弹一句看不懂的报错
    if ((r as any).need_mode) { needMode.value = true; return }
    if (r.error) emit('toast', r.error)
    return
  }
  needMode.value = false
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
      errKind.value = p.error_kind ?? ''
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
const dlRunning = ref(false)
const dlId = ref('')
const dlDone = ref(0)
const dlTotal = ref(1)
const errMsg = ref('')
// 后端是否已起来：sidecar 解压约 1.1GB，启动十几秒内点按钮是没反应的
const backendReady = ref(false)
  const errKind = ref('')
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
  try { sys.value = await api.systemDetect() } catch {}
}
onMounted(async () => {
  checkSys()
  // 轮询 /api/ready，直到 sidecar 端口可用
  for (let i = 0; i < 120; i++) {
    try {
      await waitBackend(2000)
      backendReady.value = true
      return
    } catch {
      await new Promise((r) => setTimeout(r, 500))
    }
  }
  backendReady.value = true   // 超时也放行，让用户看到真实报错
})

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
        <input v-model="folder" type="text" :class="{ shake }" style="width: 440px"
               placeholder="照片文件夹绝对路径，如 C:\Users\you\Pictures\xx" list="dirs" />
        <datalist id="dirs"><option v-for="d in recent" :key="d" :value="d" /></datalist>
        <button class="act" :disabled="scanning || !backendReady" @click="start('full')"
                title="读取已有结果，并自动补扫目录里新增的照片">开始整理</button>
        <button v-if="state?.photos?.length" class="ghost" :disabled="scanning"
                :class="{ danger: confirmReset }"
                title="清空该目录的缓存与全部人工标记，从零重新扫描"
                @click="start('reset')">
          {{ confirmReset ? '确认重新整理' : '重新整理' }}
        </button>
        <button v-if="confirmReset" class="ghost" @click="confirmReset = false">取消</button>
      </div>
      <div v-if="needMode" class="hintbar" style="text-align:left;border-color:var(--acc)">
        <b>还没有选择评分模式</b>
        <div style="margin-top:6px;font-size:12px;color:var(--dim)">
          请先到设置里选择「审美优先」或「画质优先」，选完才能开始整理。
        </div>
        <button class="act" style="margin-top:10px;padding:4px 14px;font-size:12px"
                @click="needMode = false; emit('go', 'settings')">
          去设置选择
        </button>
      </div>
      <div v-if="confirmReset" class="hintbar" style="text-align:left;border-color:var(--warn)">
        将删除该目录的扫描结果、缩略图缓存与全部<b>人工标记</b>，然后从零重新扫描。
        已复制到 winners/ 的照片不会被动。
      </div>
      <div v-if="!backendReady" style="margin-top: 10px; font-size: 12px; color: var(--dim)">
        <span class="spin" /> 正在启动，请稍候（首次启动要解压运行时，约十几秒）…
      </div>
      <div style="margin-top: 10px; font-size: 12px; color: var(--dim)">
        最近：<template v-for="(d, i) in recent.slice(0, 3)" :key="d"><a href="#"
                style="color: var(--acc)" @click.prevent="folder = d; start('full')">{{ d }}</a><span v-if="i < Math.min(recent.length, 3) - 1"> · </span></template>
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
        <!-- 只有真的是模型缺失才提下载；其它错误（比如代码异常）说这句话是误导 -->
        <div v-if="errKind === 'model'" style="margin-top:8px;font-size:12px">
          <span class="dim">到「设置 → 模型」里点对应模型的「下载」。
          国内网络建议先在设置里开镜像站或填代理。</span>
        </div>
        <div v-else style="margin-top:8px;font-size:12px;color:var(--dim)">
          这是程序内部错误，不是模型问题。日志在数据目录 logs/backend.log，可反馈给开发者。
        </div>
      </div>
      <div v-if="false" class="hintbar" style="text-align:left">
        <b v-if="sys">模型就绪检查</b><template v-if="sys?.backend_hint">（{{ sys.backend_hint }}<template v-if="sys.gpu">｜{{ sys.gpu }}</template>）</template>
        <div v-if="!sys" style="margin-top:6px;color:var(--dim);font-size:12px">
          后端状态读取中…（不影响下面的存储位置设置）
        </div>
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
        

        
        <div style="font-size:11px;color:var(--dim);margin-top:8px">
          insightface 人脸模型为非商业许可，不随程序分发、由你按需下载。
        </div>
      </div>      <div v-if="state?.photos?.length" class="stat-row">
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
