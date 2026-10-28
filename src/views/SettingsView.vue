<script setup lang="ts">
// 独立设置页：把「检查更新」「存储位置」「网络设置」从首页拆出来。
// 之前它们挤在首页的折叠区里，页面被内容撑长、滚动与关闭都不顺手，
// 而且用户很难找到入口。
import { computed, onMounted, ref } from 'vue'
import { open as openDialog } from '@tauri-apps/plugin-dialog'
import { api, waitBackend } from '../api'

const emit = defineEmits<{ toast: [m: string]; netsaved: [] }>()
const props = defineProps<{ updater: any }>()

// ---------- 存储位置 ----------
const pathsInfo = ref<{
  data_root: string; default_root: string; is_default: boolean; env_override: boolean
  subdirs: { key: string; label: string; path: string; size: number }[]
} | null>(null)
const pathsBusy = ref(false)
const pathErr = ref('')

function fmt(b: number): string {
  if (!b) return '0'
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(0)} KB`
  if (b < 1024 ** 3) return `${(b / 1048576).toFixed(0)} MB`
  return `${(b / 1073741824).toFixed(2)} GB`
}

async function loadPaths() {
  // 首次统计目录占用要递归扫 tmp（常驻几 GB），实测 5 秒左右；
  // 后端有 30 秒缓存，之后就是毫秒级。这里把等待说明写准，
  // 否则用户会以为是"后端没启动"。
  let lastErr = ''
  for (let i = 0; i < 60; i++) {
    try {
      const p = await api.pathsGet()
      if (p && (p as any).data_root) { pathsInfo.value = p; pathErr.value = ''; return }
      const e = (p as any)?.error
      lastErr = e ? String(e).slice(0, 120) : '返回缺少 data_root'
    } catch (e) { lastErr = String(e).slice(0, 120) }
    if (i === 0) pathErr.value = '正在统计各目录占用（首次需数秒）…'
    await new Promise(r => setTimeout(r, 1000))
  }
  pathErr.value = lastErr
}

/** 调系统目录选择器，而不是让用户手打路径 */
async function pickDir() {
  try {
    const sel = await openDialog({
      directory: true,
      multiple: false,
      title: '选择 JuryMate 数据目录',
      defaultPath: pathsInfo.value?.data_root || undefined,
    })
    if (typeof sel === 'string' && sel) await applyPath(sel)
  } catch (e) {
    emit('toast', `打开目录选择器失败：${e}`)
  }
}

// 迁移进度：后端立刻返回并后台搬，前端轮询 /api/paths/progress 画进度条。
// 同步搬几百 MB 时请求一直挂着、界面没有任何反馈，用户不知道是否结束。
const migrate = ref<{
  running: boolean; done: number; total: number; current: string
  migrated: { label: string }[]; skipped: { label: string; reason: string }[]
  error: string | null
} | null>(null)
const migPct = computed(() =>
  migrate.value?.total ? Math.min(100, Math.round(migrate.value.done / migrate.value.total * 100)) : 0)

async function applyPath(target: string) {
  pathsBusy.value = true
  migrate.value = null
  try {
    const r = await api.pathsSave(target)
    if (!r.ok) { emit('toast', r.error || '切换失败'); return }
    // 后端已启动后台迁移，开始轮询
    await pollMigrate()
  } catch (e) {
    emit('toast', `切换失败：${e}`)
  } finally {
    pathsBusy.value = false
  }
}

async function pollMigrate() {
  for (let i = 0; i < 600; i++) {          // 最多等 10 分钟
    try {
      const p = await api.pathsProgress()
      migrate.value = p
      if (!p.running) {
        if (p.error) emit('toast', `迁移失败：${p.error}`)
        else {
          const n = p.migrated?.length ?? 0
          const s = p.skipped?.length ?? 0
          emit('toast', s
            ? `迁移完成（${n} 项成功，${s} 项跳过）`
            : `迁移完成（${n} 项）`)
        }
        await loadPaths()
        return
      }
    } catch { /* 后端重启中，继续等 */ }
    await new Promise(r => setTimeout(r, 500))
  }
  emit('toast', '迁移仍在进行，可稍后回来看结果')
}

async function resetPath() {
  pathsBusy.value = true
  try {
    const r = await api.pathsSave('')
    if (r.ok) { emit('toast', '已恢复默认位置'); await loadPaths() }
    else emit('toast', r.error || '恢复失败')
  } finally { pathsBusy.value = false }
}

// ---------- 网络设置 ----------
// 一套配置同时服务模型下载与更新检查，不再拆成两处各管一段。
const netMirrors = ref<string[]>([])
const netProxy = ref('')
const useMirror = ref(false)
const netOrder = ref<'mirror_first' | 'direct_first'>('mirror_first')
const netSavedMsg = ref('')

// ---- 性能设置 ----
const perf = ref({ scan_workers: 0, cpu_count: 8, auto_workers: 4 })
async function loadPerf() {
  try {
    const p = await api.settingsGet()
    if (p.ok) perf.value = { scan_workers: p.scan_workers, cpu_count: p.cpu_count, auto_workers: p.auto_workers }
  } catch {}
}
async function savePerf() {
  const r = await api.settingsSave(perf.value.scan_workers || 0)
  if (r.ok) {
    emit('toast', '已保存，下次扫描生效')
    await loadPerf()
  } else {
    emit('toast', r.error || '保存失败')
  }
}
async function loadNet() {
  try {
    const n = await api.netSettingsGet()
    netMirrors.value = [...(n.github_mirrors ?? [])]
    netProxy.value = n.http_proxy ?? ''
    useMirror.value = !!n.use_mirror
    netOrder.value = (n.order === 'direct_first') ? 'direct_first' : 'mirror_first'
  } catch {}
}
async function saveNet() {
  const r = await api.netSettingsSave(
    useMirror.value, netMirrors.value.filter(s => s.trim()),
    netProxy.value.trim(), netOrder.value)
  if (r.ok) {
    emit('netsaved')          // 让更新器丢掉代理缓存，下次检查用新值
    await loadNet()
    netSavedMsg.value = '已保存，检查更新与模型下载都会用这套设置'
    setTimeout(() => (netSavedMsg.value = ''), 4000)
  }
}

// ---- 模型就绪检查 ----
// 从首页移过来：模型属于「程序配置」，放在设置里更合理，
// 而且必须能看到每个模型的下载按钮。
const sys = ref<any>(null)
const models = computed<any[]>(() => Array.isArray(sys.value?.models) ? sys.value.models : [])
const dlRunning = ref(false)
const dlId = ref('')
const dlDone = ref(0)
const dlTotal = ref(1)
const dlPct = computed(() => dlTotal.value ? Math.round(dlDone.value / dlTotal.value * 100) : 0)

async function checkSys() {
  try { sys.value = await api.systemDetect() } catch {}
}
async function dlModel(id: string) {
  dlRunning.value = true; dlId.value = id; dlDone.value = 0; dlTotal.value = 1
  await api.modelsDownload(id)
  const timer = setInterval(async () => {
    const p = await api.modelsProgress()
    dlDone.value = p.done; dlTotal.value = p.total || 1
    if (!p.running) {
      clearInterval(timer); dlRunning.value = false
      emit('toast', p.error ? `下载失败：${p.error}` : '下载完成')
      checkSys()
    }
  }, 500)
}

onMounted(async () => {

  // 先等后端把 1.1GB 运行时解压完再取数据，否则整页都是空值
  pathErr.value = '正在等待后端启动（首次启动约需 20 秒）…'
  const ok = await waitBackend().catch(() => false)
  if (!ok) { pathErr.value = '后端未在预期时间内就绪，可稍后刷新本页'; return }
  pathErr.value = ''
  await Promise.all([loadPaths(), loadNet(), loadPerf()]); checkSys()
})
</script>

<template>
  <div class="view">
    <h1>设置</h1>
    <p class="dim" style="margin:-6px 0 20px">
      所有改动即时生效或按说明重启生效；位置与网络配置都保存在本机。
    </p>

    <!-- 检查更新 -->
    <section class="card">
      <h2>检查更新</h2>
      <div class="row">
        <div>
          <div>当前版本 <b>v{{ props.updater?.APP_VERSION }}</b></div>
          <div v-if="props.updater?.updateInfo" class="dim" style="margin-top:4px">
            可更新到 <b>v{{ props.updater.updateInfo.version }}</b>
          </div>
        </div>
        <div style="display:flex;gap:8px">
          <button class="ghost" :disabled="props.updater?.updating || props.updater?.updChecking"
                  @click="props.updater?.checkUpdate(false)">
            {{ props.updater?.updChecking ? '检查中…' : '检查更新' }}
          </button>
          <button v-if="props.updater?.updateInfo" class="act"
                  :disabled="props.updater?.updating" @click="props.updater?.doUpdate()">
            {{ props.updater?.updating ? (props.updater?.updStage || '更新中…') : `升级到 v${props.updater.updateInfo.version}` }}
          </button>
        </div>
      </div>

      <!-- 检查结果常驻显示：只发 toast 的话会被随后的刷新冲掉，
           用户看到的就是"点了没反应" -->
      <div v-if="props.updater?.updCheck" class="hintbar" style="margin-top:10px"
           :style="{ borderColor: props.updater.updCheck.kind === 'ok' ? 'var(--acc)'
                        : props.updater.updCheck.kind === 'err' ? 'var(--bad)' : 'var(--line)' }">
        <div>{{ props.updater.updCheck.text }}</div>
        <div v-if="props.updater.updCheck.detail" class="dim"
             style="margin-top:4px;font-size:12px;word-break:break-all">
          {{ props.updater.updCheck.detail }}
        </div>
      </div>

      <div v-if="props.updater?.updating && props.updater?.updStage" class="hintbar" style="margin-top:10px">
        {{ props.updater.updStage }}
        <template v-if="props.updater.updTotal">
          · {{ props.updater.updPct }}%
          （{{ (props.updater.updGot / 1048576).toFixed(0) }} / {{ (props.updater.updTotal / 1048576).toFixed(0) }} MB）
        </template>
        <div class="prog" style="margin-top:6px">
          <div :style="{ width: props.updater.updPct + '%' }" />
        </div>
      </div>

      <div v-if="props.updater?.updateInfo && !props.updater?.updating" class="dim"
           style="margin-top:10px;font-size:12px;white-space:pre-wrap;max-height:180px;overflow:auto">
        {{ props.updater.updateInfo.body }}
      </div>
      <div class="dim" style="margin-top:10px;font-size:12px"
           title="网络设置里的代理同时作用于检查更新与模型下载；不填则走系统代理">
        ⓘ 代理与模型下载共用「网络设置」里的那一套。
      </div>
    </section>

    <!-- 存储位置 -->
    <section class="card">
      <h2>存储位置</h2>

      <div v-if="pathErr" class="hintbar" style="border-color:var(--bad);margin-bottom:10px">读取路径失败：{{ pathErr }}</div>
      <div v-if="pathsInfo?.env_override" class="hintbar" style="border-color:var(--warn);margin-bottom:10px">
        当前由环境变量 <code>JURYMATE_DATA_DIR</code> 指定，界面里的设置不会生效。
      </div>

      <!-- 迁移进度：几百 MB 跨盘搬运要十几秒，必须给出可判断"是否结束"的反馈 -->
      <div v-if="migrate && (migrate.running || migrate.total)" class="hintbar"
           :style="{ marginBottom: '12px',
                     borderColor: migrate.error ? 'var(--bad)'
                                  : migrate.running ? 'var(--acc)' : 'var(--good)' }">
        <div style="display:flex;justify-content:space-between;gap:12px">
          <b>{{ migrate.error ? '迁移失败' : (migrate.running ? (migrate.current || '正在迁移…') : '迁移完成') }}</b>
          <span v-if="migrate.total" class="dim" style="font-size:12px">
            {{ fmt(migrate.done) }} / {{ fmt(migrate.total) }}（{{ migPct }}%）
          </span>
        </div>
        <div class="prog" style="margin-top:8px">
          <div :style="{ width: (migrate.running ? migPct : 100) + '%',
                         background: migrate.error ? 'var(--bad)' : '' }" />
        </div>
        <div v-if="migrate.error" style="margin-top:6px;font-size:12px;color:var(--bad)">
          {{ migrate.error }}
        </div>
        <div v-if="!migrate.running" style="margin-top:8px;font-size:12px;color:var(--dim)">
          <div v-for="m in migrate.migrated" :key="'m' + m.label">✓ {{ m.label }}已迁移</div>
          <div v-for="s in migrate.skipped" :key="'s' + s.label" style="color:var(--warn)">
            ✗ {{ s.label }}未迁移 —— {{ s.reason }}
          </div>
          <div>临时解压目录不迁移（每次启动重建的运行时）</div>
          <div v-if="migrate.skipped.length" style="margin-top:6px;color:var(--warn)">
            有项目未迁移。若原因是文件被占用，关闭并重开 JuryMate 后再切一次目录即可；
            也可手动把上面列出的旧文件复制到新目录。
          </div>
        </div>
      </div>

      <div class="row">
        <div style="flex:1;min-width:0">
          <div class="dim" style="font-size:12px">当前目录</div>
          <div style="word-break:break-all;font-size:13px">
            {{ pathsInfo?.data_root || '—' }}
            <span v-if="pathsInfo?.is_default" class="dim">（默认）</span>
          </div>
        </div>
        <div style="display:flex;gap:8px;flex-shrink:0">
          <button class="act" :disabled="pathsBusy" @click="pickDir">
            {{ pathsBusy ? '处理中…' : '选择目录…' }}
          </button>
          <button v-if="!pathsInfo?.is_default" class="ghost" :disabled="pathsBusy"
                  @click="resetPath">恢复默认</button>
        </div>
      </div>

      <table v-if="pathsInfo?.subdirs?.length" style="width:100%;margin-top:14px;font-size:13px">
        <thead>
          <tr class="dim" style="text-align:left">
            <th style="padding:4px 0;font-weight:normal">内容</th>
            <th style="padding:4px 0;text-align:right;font-weight:normal">占用</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="d in pathsInfo.subdirs" :key="d.key">
            <td style="padding:3px 0">{{ d.label }}</td>
            <td style="padding:3px 0;text-align:right">{{ fmt(d.size) }}</td>
          </tr>
        </tbody>
      </table>

      <div class="dim" style="margin-top:12px;font-size:12px"
           title="临时解压目录不迁移；日志若被占用会改为复制">
        ⓘ 切换时自动迁移已有数据。
      </div>
    </section>

    <!-- 网络设置 -->
    <section class="card">
      <h2>网络设置</h2>

      <label style="display:flex;align-items:center;gap:8px;cursor:pointer;margin-bottom:8px;font-size:13px">
        <input type="checkbox" v-model="useMirror" style="width:auto;margin:0" />
        <span>使用 GitHub 镜像站（仅影响 GitHub 来源的模型与更新）</span>
      </label>

      <template v-if="useMirror">
        <div class="dim" style="margin:0 0 4px;font-size:12px">镜像前缀（按下面的顺序尝试）：</div>
        <div v-for="(_, i) in netMirrors" :key="i" style="display:flex;gap:6px;margin-bottom:4px">
          <input v-model="netMirrors[i]" type="text" style="flex:1;padding:4px 8px;font-size:12px"
                 placeholder="https://ghfast.top" />
          <button class="ghost" style="padding:2px 8px" @click="netMirrors.splice(i, 1)">删</button>
        </div>
        <button class="ghost" style="padding:2px 10px" @click="netMirrors.push('')">+ 添加镜像</button>

        <div class="dim" style="margin:14px 0 4px;font-size:12px">尝试顺序：</div>
        <div style="display:flex;gap:16px;font-size:12px">
          <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
            <input type="radio" value="mirror_first" v-model="netOrder" style="width:auto;margin:0" />
            <span>先镜像，失败回直连<span class="dim">（通常更快）</span></span>
          </label>
          <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
            <input type="radio" value="direct_first" v-model="netOrder" style="width:auto;margin:0" />
            <span>先直连，失败回镜像<span class="dim">（文件更可信）</span></span>
          </label>
        </div>
      </template>

      <div class="dim" style="margin:14px 0 4px;font-size:12px">
        HTTP 代理（对更新检查与模型下载同时生效，可空）：
      </div>
      <input v-model="netProxy" type="text" style="width:280px;padding:4px 8px;font-size:12px"
             placeholder="http://127.0.0.1:10808" />

      <div style="margin-top:12px;display:flex;align-items:center;gap:10px">
        <button class="act" style="padding:5px 14px;font-size:12px" @click="saveNet">保存网络设置</button>
        <span v-if="netSavedMsg" class="dim" style="font-size:12px;color:var(--good)">{{ netSavedMsg }}</span>
      </div>
    </section>

    <!-- 模型 -->
    <section class="card">
      <h2>模型</h2>
      <div class="dim" style="font-size:12px;margin-bottom:8px"
           title="insightface 人脸模型为非商业许可，不随程序分发，按需下载">
        ⓘ 缺哪个就点哪个的「下载」。
      </div>
      <div v-if="!sys" class="dim" style="font-size:12px">正在读取模型状态…</div>
      <div v-for="m in models" :key="m.id" style="display:flex;align-items:center;gap:10px;margin-top:6px">
        <span style="flex:1;font-size:13px">{{ m.name }} · {{ m.size_mb }}MB</span>
        <span v-if="m.installed === true" style="color:var(--good);font-size:12px">✓ 已就绪</span>
        <button v-else class="ghost" :disabled="dlRunning" style="padding:3px 14px;font-size:12px"
                @click="dlModel(m.id)">
          {{ dlRunning && dlId === m.id ? `下载中 ${dlPct}%` : '下载' }}
        </button>
      </div>
      <div v-if="dlRunning" class="prog" style="margin:8px 0 0"><div :style="{width: dlPct+'%'}" /></div>
      <div v-if="sys?.backend_hint" class="dim" style="font-size:11px;margin-top:8px">
        {{ sys.backend_hint }}<template v-if="sys.gpu">｜{{ sys.gpu }}</template>
      </div>
    </section>

    <!-- 性能 -->
    <section class="card">
      <h2>性能</h2>
      <div class="row">
        <div>
          <div style="font-size:13px">
            扫描并发线程数
            <span class="dim" style="font-size:12px">（本机 {{ perf.cpu_count }} 核）</span>
          </div>
          <div class="dim" style="font-size:12px;margin-top:3px"
               title="解码是性能瓶颈；线程越多越快，但超过磁盘和内存带宽后会回落">
            影响 RAW 解码速度；留 0 由程序自动选择。
          </div>
        </div>
        <div style="display:flex;align-items:center;gap:8px;flex:0 0 auto">
          <input type="number" v-model.number="perf.scan_workers" min="0"
                 :max="perf.cpu_count" style="width:88px;padding:6px 8px;font-size:13px" />
          <span class="dim" style="font-size:12px;white-space:nowrap">
            {{ perf.scan_workers === 0 ? `自动（${perf.auto_workers}）` : `1–${perf.cpu_count}` }}
          </span>
          <button class="act" style="padding:5px 14px;font-size:12px" @click="savePerf">保存</button>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
h1 { font-size: 22px; margin: 0 0 8px; }
h2 { font-size: 15px; margin: 0 0 10px; }
.card {
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 16px 18px;
  margin-bottom: 16px;
  background: var(--panel);
}
.row { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.dim { color: var(--dim); }
</style>
