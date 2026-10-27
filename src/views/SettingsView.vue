<script setup lang="ts">
// 独立设置页：把「检查更新」「存储位置」「网络设置」从首页拆出来。
// 之前它们挤在首页的折叠区里，页面被内容撑长、滚动与关闭都不顺手，
// 而且用户很难找到入口。
import { onMounted, ref } from 'vue'
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
  // 后端是 PyInstaller onefile，首次启动要把约 1.1GB 运行时解压出来，
  // 实测冷启动可达 20 秒以上。这里必须等够，否则用户看到的就是一个「—」。
  let lastErr = ''
  for (let i = 0; i < 60; i++) {
    try {
      const p = await api.pathsGet()
      if (p && (p as any).data_root) { pathsInfo.value = p; pathErr.value = ''; return }
      const e = (p as any)?.error
      lastErr = e ? String(e).slice(0, 120) : '返回缺少 data_root'
    } catch (e) { lastErr = String(e).slice(0, 120) }
    if (i === 0) pathErr.value = '正在等待后端启动…'
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

async function applyPath(target: string) {
  pathsBusy.value = true
  try {
    const r = await api.pathsSave(target)
    if (!r.ok) { emit('toast', r.error || '切换失败'); return }
    const mig = (r as any).migrated ?? []
    const skip = (r as any).skipped ?? []
    let msg = `已切换到 ${(r as any).data_root}`
    if (mig.length) msg += `，已迁移 ${mig.map((m: any) => m.label).join('、')}`
    if (skip.length) msg += `；${skip.map((s: any) => `${s.label}未迁移（${s.reason}）`).join('；')}`
    emit('toast', msg)
    await loadPaths()
  } catch (e) {
    emit('toast', `切换失败：${e}`)
  } finally {
    pathsBusy.value = false
  }
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
const netMirrors = ref<string[]>([])
const netProxy = ref('')
const useMirror = ref(false)
async function loadNet() {
  try {
    const n = await api.netSettingsGet()
    netMirrors.value = [...(n.github_mirrors ?? [])]
    netProxy.value = n.http_proxy ?? ''
    useMirror.value = !!n.use_mirror
  } catch {}
}
async function saveNet() {
  const r = await api.netSettingsSave(
    useMirror.value, netMirrors.value.filter(s => s.trim()), netProxy.value.trim())
  if (r.ok) { emit('toast', '网络设置已保存'); emit('netsaved'); await loadNet() }
}

onMounted(async () => {
  // 先等后端把 1.1GB 运行时解压完再取数据，否则整页都是空值
  pathErr.value = '正在等待后端启动（首次启动约需 20 秒）…'
  const ok = await waitBackend().catch(() => false)
  if (!ok) { pathErr.value = '后端未在预期时间内就绪，可稍后刷新本页'; return }
  pathErr.value = ''
  await Promise.all([loadPaths(), loadNet()])
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
          <button class="ghost" :disabled="props.updater?.updating"
                  @click="props.updater?.checkUpdate(false)">检查更新</button>
          <button v-if="props.updater?.updateInfo" class="act"
                  :disabled="props.updater?.updating" @click="props.updater?.doUpdate()">
            {{ props.updater?.updating ? (props.updater?.updStage || '更新中…') : `升级到 v${props.updater.updateInfo.version}` }}
          </button>
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
      <div class="dim" style="margin-top:10px;font-size:12px">
        更新器访问 GitHub 用的是系统代理；「网络设置」里的代理只作用于模型下载与检查更新本身。
      </div>
    </section>

    <!-- 存储位置 -->
    <section class="card">
      <h2>存储位置</h2>
      <div class="dim" style="font-size:12px;margin-bottom:10px">
        模型、缓存、日志、临时解压都放在这个目录下。系统盘空间紧张时换到别的盘，
        换目录时会自动把已有数据一起迁过去。
      </div>

      <div v-if="pathErr" class="hintbar" style="border-color:var(--bad);margin-bottom:10px">读取路径失败：{{ pathErr }}</div>
      <div v-if="pathsInfo?.env_override" class="hintbar" style="border-color:var(--warn);margin-bottom:10px">
        当前由环境变量 <code>JURYMATE_DATA_DIR</code> 指定，界面里的设置不会生效。
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

      <div class="dim" style="margin-top:12px;font-size:12px">
        切换会迁移模型与缓存（数百 MB，可能需要几十秒）。临时解压目录不迁移，
        里面是每次启动重建的运行时。日志若正被占用则改为复制，旧文件需手动删除。
      </div>
    </section>

    <!-- 网络设置 -->
    <section class="card">
      <h2>网络设置</h2>
      <div class="dim" style="font-size:12px;margin-bottom:10px">
        默认从模型原仓库直连下载。国内网络不通时，可开启镜像站或填代理。
      </div>

      <label style="display:flex;align-items:center;gap:8px;cursor:pointer;margin-bottom:8px;font-size:13px">
        <input type="checkbox" v-model="useMirror" style="width:auto;margin:0" />
        <span>使用 GitHub 镜像站（仅影响 GitHub 来源的模型）</span>
      </label>

      <template v-if="useMirror">
        <div class="dim" style="margin:0 0 4px;font-size:12px">镜像前缀（按序尝试，全部失败回落直连）：</div>
        <div v-for="(_, i) in netMirrors" :key="i" style="display:flex;gap:6px;margin-bottom:4px">
          <input v-model="netMirrors[i]" type="text" style="flex:1;padding:4px 8px;font-size:12px"
                 placeholder="https://ghfast.top" />
          <button class="ghost" style="padding:2px 8px" @click="netMirrors.splice(i, 1)">删</button>
        </div>
        <button class="ghost" style="padding:2px 10px" @click="netMirrors.push('')">+ 添加镜像</button>
      </template>

      <div class="dim" style="margin:12px 0 4px;font-size:12px">
        HTTP 代理（对所有下载生效，与镜像站互不冲突，可空）：
      </div>
      <input v-model="netProxy" type="text" style="width:280px;padding:4px 8px;font-size:12px"
             placeholder="http://127.0.0.1:10808" />

      <div style="margin-top:12px">
        <button class="act" style="padding:5px 14px;font-size:12px" @click="saveNet">保存网络设置</button>
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
