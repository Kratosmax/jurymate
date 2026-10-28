<script setup lang="ts">
// 独立设置页：把「检查更新」「存储位置」「网络设置」从首页拆出来。
// 之前它们挤在首页的折叠区里，页面被内容撑长、滚动与关闭都不顺手，
// 而且用户很难找到入口。
import { computed, onMounted, ref } from 'vue'
import { open as openDialog } from '@tauri-apps/plugin-dialog'
import { api, waitBackend } from '../api'
import { loadNetCfg, normalizeMirrors, saveNetCfg } from '../netstore'

const emit = defineEmits<{ toast: [m: string]; netsaved: []; refresh: [] }>()
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
// 别写假默认值：以前这里写死 cpu_count:8 / auto_workers:4，后端请求一失败
// 界面就显示"本机 8 核 / 自动(4)"，用户以为自己机器被识别错了。
// 现在 0 表示"还没读到"，界面显示「—」。
const perf = ref({ scan_workers: 0, cpu_count: 0, auto_workers: 0 })
async function loadPerf() {
  try {
    const p = await api.settingsGet()
    if (p.ok) perf.value = { scan_workers: p.scan_workers, cpu_count: p.cpu_count, auto_workers: p.auto_workers }
  } catch {}
}
async function savePerf() {
  const r = await api.settingsSave({ scan_workers: perf.value.scan_workers || 0 })
  if (r.ok) {
    emit('toast', '已保存，下次扫描生效')
    await loadPerf()
  } else {
    emit('toast', r.error || '保存失败')
  }
}
async function loadNet() {
  // 先读本地：后端还在解压时也能看到、也能改
  const local = loadNetCfg()
  netMirrors.value = [...local.github_mirrors]
  netProxy.value = local.http_proxy
  useMirror.value = local.use_mirror
  netOrder.value = local.order
  // 本地没存过（首次使用）才从后端取一次作为初始值
  if (localStorage.getItem('jurymate.net')) return
  try {
    const n = await api.netSettingsGet()
    netMirrors.value = [...(n.github_mirrors ?? [])]
    netProxy.value = n.http_proxy ?? ''
    useMirror.value = !!n.use_mirror
    netOrder.value = (n.order === 'direct_first') ? 'direct_first' : 'mirror_first'
    saveNetCfg({
      use_mirror: useMirror.value,
      github_mirrors: normalizeMirrors(netMirrors.value),
      http_proxy: netProxy.value.trim(),
      order: netOrder.value,
    })
  } catch { /* 后端没起来就用默认值 */ }
}
async function saveNet() {
  const cfg = {
    use_mirror: useMirror.value,
    github_mirrors: normalizeMirrors(netMirrors.value),
    http_proxy: netProxy.value.trim(),
    order: netOrder.value,
  }
  // 本地先落盘并立刻生效（更新检查马上能用），后端只是同步一份给模型下载器
  saveNetCfg(cfg)
  emit('netsaved')
  netSavedMsg.value = '已保存到本机，立即生效'
  setTimeout(() => (netSavedMsg.value = ''), 4000)
  try {
    await api.netSettingsSave(cfg.use_mirror, cfg.github_mirrors, cfg.http_proxy, cfg.order)
    await loadPaths()
  } catch { /* 后端未就绪：本地已生效，下次启动会再同步 */ }
}

// ---- 模型就绪检查 ----
// 从首页移过来：模型属于「程序配置」，放在设置里更合理，
// 而且必须能看到每个模型的下载按钮。
// scoreMode 默认空 = 还没选。没选之前不加载、不下载任何评分模型。
const scoreMode = ref<'' | 'aesthetic' | 'quality'>('')
const weight = ref(50)

// ---- 滑杆：手指跟随 + 松手吸附 ----
// 值仍然是 step=5（0/5/10…100），但拇指在拖动过程中"贴着手指走"（连续），
// 松手时才用弹簧曲线滑到最近的 5 的倍数。这样既保住步进，又没有跳变感。
const visual = ref(50)
const dragging = ref(false)

function ratioAt(e: PointerEvent | MouseEvent): number {
  const el = e.currentTarget as HTMLElement
  const r = el.getBoundingClientRect()
  return Math.max(0, Math.min(100, ((e.clientX - r.left) / r.width) * 100))
}
function onSliderDown(e: PointerEvent) {
  dragging.value = true
  ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
  const r = ratioAt(e)
  visual.value = r
  weight.value = Math.round(r / 5) * 5
}
function onSliderMove(e: PointerEvent) {
  if (!dragging.value) return
  const r = ratioAt(e)
  visual.value = r
  weight.value = Math.round(r / 5) * 5
}
function onSliderUp() {
  if (!dragging.value) return
  dragging.value = false
  visual.value = weight.value   // 交给 CSS 的弹簧过渡去落位
  saveWeight()
}
/** 键盘操作：方向键改值后直接落位并保存 */
function snapAndSave() {
  visual.value = weight.value
  saveWeight()
}
const sliderHue = computed(() => Math.round(200 + visual.value * 0.8))
const sys = ref<any>(null)
const models = computed<any[]>(() => Array.isArray(sys.value?.models) ? sys.value.models : [])
const depModels = computed<any[]>(() =>
  models.value.filter((m: any) => m.id !== 'nima' && m.id !== 'musiq'))
const scoreModels = computed<any[]>(() =>
  models.value.filter((m: any) => m.id === 'nima' || m.id === 'musiq'))
function isInstalled(id: string) {
  return models.value.find((m: any) => m.id === id)?.installed === true
}
function modelName(id: string) {
  return id === 'nima' ? 'NIMA（审美优先）' : 'MUSIQ（画质优先）'
}
function modelMb(id: string) {
  return id === 'nima' ? 208 : 104
}

// 下载状态带「是哪张卡片发起的」：不加这个，两张卡片底部会同时冒进度条
const dlRunning = ref(false)
const dlId = ref('')
const dlCard = ref('')        // 'score' | 'dep'
const dlDone = ref(0)
const dlTotal = ref(1)
const dlPct = computed(() => dlTotal.value ? Math.round(dlDone.value / dlTotal.value * 100) : 0)

// 选模式要先确认下载（模型几百 MB，不能点一下就偷偷跑）
const pendingMode = ref<'' | 'aesthetic' | 'quality'>('')
// 改完设置问一句「要不要重算分数」，避免用户以为设置没生效
const rescoreAsk = ref(false)
const rescoreBusy = ref(false)
const rescoreStage = ref('')

async function checkSys() {
  try { sys.value = await api.systemDetect() } catch {}
  try {
    const s = await api.settingsGet()
    scoreMode.value = s.score_mode === 'quality' ? 'quality'
      : s.score_mode === 'aesthetic' ? 'aesthetic' : ''
    weight.value = Number(s.score_weight ?? 50)
    visual.value = weight.value       // 初次载入直接落位，不要从 50 扫过去
  } catch {}
}

function modeId(m: 'aesthetic' | 'quality') { return m === 'aesthetic' ? 'nima' : 'musiq' }

async function chooseMode(m: 'aesthetic' | 'quality') {
  if (scoreMode.value === m) return
  const id = modeId(m)
  if (!isInstalled(id)) { pendingMode.value = m; return }   // 弹确认条
  await applyMode(m)
}

async function applyMode(m: 'aesthetic' | 'quality') {
  const r = await api.settingsSave({ score_mode: m })
  if (!r.ok) { emit('toast', r.error || '保存失败'); await checkSys(); return }
  scoreMode.value = m
  pendingMode.value = ''
  emit('toast', m === 'aesthetic' ? '已启用审美优先（NIMA）' : '已启用画质优先（MUSIQ）')
  await checkSys()
  emit('refresh')          // 让其他页的分项标签跟着换（审美 ↔ 画质）
  rescoreAsk.value = true
}

async function confirmDownloadMode() {
  const m = pendingMode.value
  if (!m) return
  pendingMode.value = ''
  const ok = await dlModel(modeId(m), 'score')
  if (ok) await applyMode(m)
}

async function saveWeight() {
  const r = await api.settingsSave({ score_weight: weight.value })
  if (!r.ok) { emit('toast', r.error || '保存失败'); return }
  rescoreAsk.value = true
}

async function doRescore() {
  rescoreAsk.value = false
  rescoreBusy.value = true
  rescoreStage.value = ''
  const r = await api.rescore()
  if (!r.ok) {
    rescoreBusy.value = false
    emit('toast', r.error || '重算失败')
    return
  }
  if (!r.rescored) {           // 只是权重重算，毫秒级，已经算完了
    rescoreBusy.value = false
    emit('toast', '分数已按新权重重新计算')
    emit('refresh')
    return
  }
  // 换了模型：后台重新评分，轮询进度
  const timer = setInterval(async () => {
    const p = await api.progress()
    rescoreStage.value = p.stage || ''
    if (!p.running) {
      clearInterval(timer)
      rescoreBusy.value = false
      emit('toast', p.error ? `重新评分失败：${p.error}` : '已用新模型重新评分')
      emit('refresh')
    }
  }, 700)
}

/** 下载单个模型。返回是否成功（失败/取消返回 false）。 */
async function dlModel(id: string, card: 'score' | 'dep' = 'dep'): Promise<boolean> {
  dlRunning.value = true; dlId.value = id; dlCard.value = card
  dlDone.value = 0; dlTotal.value = 1
  const r = await api.modelsDownload(id)
  if (!r.ok) {
    dlRunning.value = false
    emit('toast', r.error || '下载未能启动')
    return false
  }
  return await new Promise<boolean>((resolve) => {
    const timer = setInterval(async () => {
      const p = await api.modelsProgress()
      dlDone.value = p.done; dlTotal.value = p.total || 1
      if (!p.running) {
        clearInterval(timer)
        dlRunning.value = false
        dlCard.value = ''
        emit('toast', p.error ? `下载失败：${p.error}` : '下载完成')
        await checkSys()
        resolve(!p.error && isInstalled(id))
      }
    }, 500)
  })
}

// 删除单个模型：删完回到「未下载」，用户想重下再点下载
const removing = ref('')
async function removeModel(id: string) {
  if (removing.value) return
  removing.value = id
  const r = await api.modelsRemove(id)
  removing.value = ''
  if (!r.ok) { emit('toast', r.error || '删除失败'); return }
  await checkSys()
  emit('refresh')
  emit('toast', `已删除 ${modelName(id)} 模型`)
}

const bootFailed = ref(false)
const bootSecs = ref(0)
const backendUp = ref(false)
async function boot() {
  // 网络设置先读本地，不等后端——否则 sidecar 解压的那一分钟里
  // 用户在设置页改镜像根本存不进去。
  loadNet()
  bootFailed.value = false
  bootSecs.value = 0
  pathErr.value = ''
  const ok = await waitBackend((s) => { bootSecs.value = s }).catch(() => false)
  if (!ok) {
    bootFailed.value = true
    pathErr.value = '后端未在预期时间内就绪'
    return
  }
  backendUp.value = true
  await Promise.all([loadPaths(), loadPerf()]); checkSys()
  // 本地网络配置同步给后端（后端刚起来时它自己那份可能还是默认值）
  try {
    const c = loadNetCfg()
    await api.netSettingsSave(c.use_mirror, c.github_mirrors, c.http_proxy, c.order)
  } catch { /* 失败不影响本地生效 */ }
}

onMounted(boot)
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

      <div v-if="!backendUp && !bootFailed" class="hintbar" style="margin-bottom:10px">
        <span class="spin" /> 正在等待后端启动<span style="display:inline-block;width:52px;text-align:right;opacity:.7">{{ bootSecs }}s</span>
        <span class="dim" style="font-size:12px">（首次安装要解压约 1.1GB 运行时；网络设置现在就可以改，已保存到本机）</span>
      </div>
      <div v-if="pathErr" class="hintbar" :style="{ borderColor: 'var(--bad)', marginBottom: '10px' }">
        {{ pathErr }}
        <div v-if="bootFailed" style="margin-top:8px">
          <button class="ghost" style="padding:3px 12px;font-size:12px" @click="boot">重试</button>
        </div>
      </div>
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

    <!-- 评分模型 -->
    <section class="card">
      <h2>评分模型</h2>
      <div class="dim" style="font-size:12px;margin-bottom:10px">
        二选一。选中后才会下载对应模型，两个都下也没关系，随时可以单独删掉。
      </div>
      <div class="modes">
        <button class="modecard" :class="{ on: scoreMode === 'aesthetic' }"
                :disabled="!!pendingMode" @click="chooseMode('aesthetic')">
          <b>审美优先</b>
          <span>构图 · 光影 · 氛围</span>
          <em>快 · NIMA 208MB</em>
        </button>
        <button class="modecard" :class="{ on: scoreMode === 'quality' }"
                :disabled="!!pendingMode" @click="chooseMode('quality')">
          <b>画质优先</b>
          <span>清晰度 · 噪点 · 压缩伪影</span>
          <em>慢 · MUSIQ 104MB</em>
        </button>
      </div>

      <!-- 选模式前的下载确认：模型几百 MB，不能点一下就偷偷跑 -->
      <div v-if="pendingMode" class="hintbar" style="margin-top:12px">
        <div>
          选择「{{ pendingMode === 'aesthetic' ? '审美优先' : '画质优先' }}」需要下载
          <b>{{ modelName(modeId(pendingMode)) }}</b> 模型，约 <b>{{ modelMb(modeId(pendingMode)) }} MB</b>，是否继续？
        </div>
        <div style="display:flex;gap:8px;margin-top:10px">
          <button class="act" style="padding:4px 14px;font-size:12px" @click="confirmDownloadMode">
            下载并启用
          </button>
          <button class="ghost" style="padding:4px 14px;font-size:12px" @click="pendingMode = ''">
            取消
          </button>
        </div>
      </div>

      <!-- 每个评分模型一行：已就绪可删，未就绪可下 -->
      <div v-for="m in scoreModels" :key="m.id" class="modelrow" style="margin-top:8px">
        <span style="flex:1;font-size:13px">{{ m.name }}</span>
        <template v-if="m.installed === true">
          <span style="color:var(--good);font-size:12px">✓ 已就绪</span>
          <button class="ghost" :disabled="!!removing || dlRunning" style="padding:3px 12px;font-size:12px"
                  @click="removeModel(m.id)">
            {{ removing === m.id ? '删除中…' : '删除' }}
          </button>
        </template>
        <button v-else class="ghost" :disabled="dlRunning" style="padding:3px 14px;font-size:12px"
                @click="dlModel(m.id, 'score')">
          {{ dlRunning && dlId === m.id && dlCard === 'score' ? `下载中 ${dlPct}%` : `下载 ${m.size_mb}MB` }}
        </button>
      </div>
      <div v-if="dlRunning && dlCard === 'score'" class="prog" style="margin:10px 0 0">
        <div :style="{ width: dlPct + '%' }" />
      </div>
      <div v-if="!scoreMode" class="hintbar" style="margin-top:12px;border-color:var(--warn, #d8a13a)">
        还没有选择评分模式。选好之后才能开始整理。
      </div>
    </section>

    <!-- 改完设置问一句要不要重算，否则用户会以为设置没生效。
         放在评分模型卡片正下方——放页面底部时用户根本看不到。 -->
    <section v-if="rescoreAsk" class="card" style="border-color:var(--acc)">
      <div style="font-size:13px">
        评分设置已更改，是否按新设置重新计算全部照片的分数？
      </div>
      <div class="dim" style="font-size:12px;margin-top:4px">
        只是重算分数与分组结果，不会重新解压照片。
      </div>
      <div style="display:flex;gap:8px;margin-top:12px">
        <button class="act" style="padding:4px 14px;font-size:12px" @click="doRescore">
          重新计算
        </button>
        <button class="ghost" style="padding:4px 14px;font-size:12px" @click="rescoreAsk = false">
          稍后再说
        </button>
      </div>
    </section>

    <!-- 依赖模型 -->
    <section class="card">
      <h2>依赖模型</h2>
      <div class="dim" style="font-size:12px;margin-bottom:8px">
        人脸检测与闭眼判断要用，和评分模式无关。
      </div>
      <div v-for="m in depModels" :key="m.id" class="modelrow" style="margin-top:6px">
        <span style="flex:1;font-size:13px">{{ m.name }} · {{ m.size_mb }}MB</span>
        <template v-if="m.installed === true">
          <span style="color:var(--good);font-size:12px">✓ 已就绪</span>
          <button class="ghost" :disabled="!!removing || dlRunning" style="padding:3px 12px;font-size:12px"
                  @click="removeModel(m.id)">
            {{ removing === m.id ? '删除中…' : '删除' }}
          </button>
        </template>
        <button v-else class="ghost" :disabled="dlRunning" style="padding:3px 14px;font-size:12px"
                @click="dlModel(m.id, 'dep')">
          {{ dlRunning && dlId === m.id && dlCard === 'dep' ? `下载中 ${dlPct}%` : '下载' }}
        </button>
      </div>
      <div v-if="dlRunning && dlCard === 'dep'" class="prog" style="margin:10px 0 0">
        <div :style="{ width: dlPct + '%' }" />
      </div>
    </section>

    <!-- 评分权重：只在审美优先下有意义（画质分本身已含清晰度） -->
    <section v-if="scoreMode !== 'quality'" class="card">
      <h2>评分权重</h2>
      <div class="dim" style="font-size:12px;margin-bottom:12px"
           title="往左更看重清晰度，往右更看重审美。综合分 = 两者按滑块比例加权。">
        ⓘ 拖动滑块调整「审美」与「清晰度」在综合分里的占比。
      </div>
      <div class="slider-wrap">
        <!-- 权重常驻显示：用户拖完必须一眼看到现在是什么比例 -->
        <div class="wreadout">
          审美 <b>{{ weight }}%</b><span class="sep">／</span>清晰 <b>{{ 100 - weight }}%</b>
        </div>
        <!-- 自绘轨道 + 拇指：原生 input 只有 step=5 的语义与键盘可达性，
             视觉位置由 visual（弹簧插值）驱动，所以拖动是连续的、落点是 5 的倍数 -->
        <div class="wstage" :class="{ on: dragging }" :style="{ '--hue': sliderHue }"
             @pointerdown="onSliderDown" @pointermove="onSliderMove"
             @pointerup="onSliderUp" @pointercancel="onSliderUp">
          <div class="wtrack"><div class="wfill" :style="{ width: visual + '%' }" /></div>
          <div class="wknob" :style="{ left: visual + '%' }" />
          <input class="wnative" type="range" min="0" max="100" step="5"
                 v-model.number="weight" aria-label="审美与清晰的权重"
                 @change="snapAndSave" />
        </div>
        <div class="slider-labels">
          <span>更看重清晰度</span>
          <span class="mid">按住左右拖动</span>
          <span>更看重审美</span>
        </div>
      </div>
      <div v-if="rescoreBusy" class="dim" style="margin-top:10px;font-size:12px">
        {{ rescoreStage || '正在重新计算…' }}
      </div>
    </section>

    <!-- 性能 -->
    <section class="card">
      <h2>性能</h2>
      <div class="row">
        <div>
          <div style="font-size:13px">
            扫描并发线程数
            <span class="dim" style="font-size:12px">（本机 {{ perf.cpu_count || '—' }} 核）</span>
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
