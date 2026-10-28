<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import { api, type State } from '../api'
import PhotoCard from '../components/PhotoCard.vue'

const props = defineProps<{ state: State | null }>()
const emit = defineEmits<{ changed: []; toast: [m: string] }>()

const winners = computed(() => (props.state?.photos ?? []).filter((p) => p.status === 'winner'))
const lightbox = ref('')

// 归档/导出的结果放在页面里常驻显示。
// 原来只发一条 2.6 秒的 toast——它会被紧接着的 emit('changed') 重新渲染冲掉，
// 用户看到的就是"点了没反应"，尽管操作其实成功了。
const actionMsg = ref<{ kind: 'ok' | 'warn' | 'err'; text: string; detail?: string } | null>(null)
const busy = ref<'org' | 'csv' | ''>('')
const confirmMove = ref<boolean>(false)

// 归类范围：默认全部，也可以只处理勾选的照片
const selected = ref<Set<string>>(new Set())
const selectMode = ref(false)
const lastCsv = ref('')
function selectNone() {
  selected.value = new Set()
}
function toggleSel(p: string) {
  const s = new Set(selected.value)
  if (s.has(p)) s.delete(p)
  else s.add(p)
  selected.value = s
}

async function organizeFlow() {
  busy.value = 'org'
  actionMsg.value = null
  const paths = selectMode.value && selected.value.size ? [...selected.value] : undefined
  try {
    const dry = await api.organize(true, paths)
    if (!dry.ok) {
      actionMsg.value = { kind: 'err', text: '归类失败', detail: dry.error }
      return
    }
    const parts = [
      `winners ${dry.winners} 组`,
    ]
    if (dry.rejects_skipped) parts.push(`落选 ${dry.rejects_skipped} 张不复制（在「复查」页管理）`)
    if (dry.skipped_same) parts.push(`${dry.skipped_same} 个文件已存在且内容一致，跳过`)
    if (dry.skipped_conflict) parts.push(`${dry.skipped_conflict} 组同名但内容不同，未覆盖`)
    if (dry.pending_kept) parts.push(`未决策 ${dry.pending_kept} 张不动`)

    // 没有需要复制的东西：如实说明为什么，而不是让用户以为按钮没反应
    if (!dry.copied) {
      actionMsg.value = {
        kind: dry.skipped_conflict ? 'warn' : 'ok',
        text: dry.skipped_conflict
          ? '没有复制：目标里已有同名但内容不同的文件'
          : '无需复制：这些照片都已在目标文件夹里，且内容一致',
        detail: parts.join(' · ')
          + (dry.conflicts?.length ? ` · 例：${dry.conflicts.slice(0, 3).join('、')}` : ''),
      }
      return
    }

    if (!confirmMove.value) {
      confirmMove.value = true
      actionMsg.value = {
        kind: 'warn',
        text: `将复制 ${dry.copied} 个文件`,
        detail: parts.join(' · ')
          + (paths ? ` · 仅处理勾选的 ${paths.length} 张` : ' · 处理全部照片')
          + ' —— 原文件保留在原位，再点一次「确认归类」执行',
      }
      return
    }

    const r = await api.organize(false, paths)
    confirmMove.value = false
    if (r.ok) {
      const d = [`winners ${r.winners} 组`]
      if (r.rejects_skipped) d.push(`落选 ${r.rejects_skipped} 张不复制`)
      if (r.skipped_same) d.push(`已存在跳过 ${r.skipped_same} 个`)
      if (r.skipped_conflict) d.push(`内容不同未覆盖 ${r.skipped_conflict} 组`)
      actionMsg.value = {
        kind: r.skipped_conflict ? 'warn' : 'ok',
        text: `已复制 ${r.copied ?? 0} 个文件`,
        detail: d.join(' · ') + ' —— 原文件仍在原目录',
      }
      emit('changed')
    } else {
      actionMsg.value = { kind: 'err', text: '归类失败', detail: r.error }
    }
  } catch (e) {
    actionMsg.value = { kind: 'err', text: '归类出错', detail: String(e) }
  } finally {
    busy.value = ''
  }
}

async function exportCsv() {
  busy.value = 'csv'
  actionMsg.value = null
  try {
    const r = await api.exportCsv()
    if (r.ok) {
      lastCsv.value = r.path || ''
      actionMsg.value = {
        kind: 'ok',
        text: `已导出 ${r.rows} 条记录`,
        detail: r.path,
      }
    } else {
      actionMsg.value = { kind: 'err', text: '导出失败', detail: r.error }
    }
  } catch (e) {
    actionMsg.value = { kind: 'err', text: '导出出错', detail: String(e) }
  } finally {
    busy.value = ''
  }
}

async function openFolder() {
  await api.openFolder('winners')
}
// 导出后直接定位到 CSV：省得用户自己去资源管理器里翻
async function openCsv(path: string) {
  const name = path.split(/[\\/]/).pop() || ''
  await api.openFolder('', name)
}

// ---- 右键菜单 ----
// 只保留两项：移回复查、打开文件所在位置。
// WebView 自带的共享/打印/复制图像/另存为等在 App.vue 里全局禁掉。
const ctx = ref<{ x: number; y: number; photo: any } | null>(null)

function onContextMenu(e: MouseEvent, p: any) {
  e.preventDefault()
  e.stopPropagation()
  ctx.value = {
    x: Math.min(e.clientX, window.innerWidth - 210),
    y: Math.min(e.clientY, window.innerHeight - 100),
    photo: p,
  }
}
function closeCtx() { ctx.value = null }

async function moveBackToReview() {
  const m = ctx.value
  closeCtx()
  if (!m) return
  const r = await api.setStatus(m.photo.path, 'reject')
  if (r.ok) {
    emit('toast', `${m.photo.name} 已移回复查`)
    emit('changed')
  } else {
    emit('toast', r.error || '操作失败')
  }
}

async function revealFile() {
  const m = ctx.value
  closeCtx()
  if (!m) return
  try {
    const how = await invoke<string>('reveal_in_explorer', { path: m.photo.path })
    emit('toast', how === 'reused' ? '已在已有窗口定位' : '已打开所在文件夹')
  } catch (e) {
    emit('toast', `打开失败：${e}`)
  }
}

// ---- 左右键切换当前照片 ----
const activeIdx = ref(-1)
function moveActive(d: number) {
  const n = winners.value.length
  if (!n) return
  const start = activeIdx.value < 0 ? 0 : activeIdx.value
  activeIdx.value = Math.max(0, Math.min(n - 1, start + d))
  const el = document.querySelectorAll('#p-winners .card')[activeIdx.value] as HTMLElement | undefined
  el?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  // 大图（lightbox）开着时，大图也要跟着切，而不是只动小图框选
  if (lightbox.value) lightbox.value = winners.value[activeIdx.value]?.thumb_view ?? lightbox.value
}
function onKey(e: KeyboardEvent) {
  if (!(document.querySelector('#p-winners') as HTMLElement)?.offsetParent) return
  if (e.key === 'ArrowLeft') { e.preventDefault(); moveActive(-1) }
  else if (e.key === 'ArrowRight') { e.preventDefault(); moveActive(1) }
}

onMounted(() => {

  window.addEventListener('keydown', onKey)
  window.addEventListener('click', closeCtx)
  window.addEventListener('scroll', closeCtx, true)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  window.removeEventListener('click', closeCtx)
  window.removeEventListener('scroll', closeCtx, true)
})
</script>

<template>
  <div class="view" id="p-winners">
    <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 16px">
      <h2 class="pgtitle" style="flex: 1; margin: 0">
        胜出 {{ winners.length }} 张
        <span style="color: var(--dim); font-size: 12px; font-weight: 400">★ = 5 分制审美参考分（悬停看含义）</span>
      </h2>
      <button class="ghost" :disabled="!!busy" @click="exportCsv">
        {{ busy === 'csv' ? '导出中…' : '导出 CSV 清单' }}
      </button>
      <button class="ghost" @click="openFolder">📁 打开结果文件夹</button>
      <button class="ghost" :class="{ act: selectMode }" @click="selectMode = !selectMode; selectNone()">
        {{ selectMode ? '退出勾选' : '勾选部分归类' }}
      </button>
      <button class="act" :disabled="!!busy" @click="organizeFlow">
        {{ busy === 'org' ? '处理中…' : (confirmMove ? '确认归类' : (selectMode && selected.size ? `归类选中的 ${selected.size} 张` : '归类全部照片')) }}
      </button>
      <button v-if="confirmMove" class="ghost" @click="confirmMove = false; actionMsg = null">取消</button>
    </div>



    <!-- 结果常驻显示：toast 会被随后的状态刷新冲掉，用户看不到 -->
    <div v-if="actionMsg" class="hintbar" :style="{
           borderColor: actionMsg.kind === 'ok' ? 'var(--good)'
                      : actionMsg.kind === 'warn' ? 'var(--warn)' : 'var(--bad)' }">
      <div :style="{ color: actionMsg.kind === 'ok' ? 'var(--good)'
                          : actionMsg.kind === 'warn' ? 'var(--warn)' : 'var(--bad)' }">
        <b>{{ actionMsg.text }}</b>
      </div>
      <div v-if="actionMsg.detail" style="margin-top:4px;font-size:12px;color:var(--dim);word-break:break-all">
        {{ actionMsg.detail }}
      </div>
      <!-- 导出后直接定位到 CSV，不用自己去资源管理器翻 -->
      <div v-if="lastCsv" style="margin-top:8px">
        <button class="ghost" style="padding:3px 12px;font-size:12px" @click="openCsv(lastCsv)">
          📂 打开 CSV 所在位置
        </button>
      </div>
    </div>
    <div class="grid">
      <PhotoCard v-for="(p, i) in winners" :key="p.path" :photo="p"
                 :class="{ cardsel: i === activeIdx }"
                 :style="{ animationDelay: Math.min(i * 0.03, 0.5) + 's' }"
                 :badge="p.manual_winner ? '手动' : ''"
                 :note="'MUSIQ 无参考审美模型的 5 分制映射（人类平均审美参考分，AI 拿它排序）'"
                 @click="lightbox = p.thumb_view" @contextmenu="onContextMenu($event, p)">
        <template #footer>
          <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:6px">
            <input v-if="selectMode" type="checkbox" style="width:auto;margin:0"
                   :checked="selected.has(p.path)" @click.stop="toggleSel(p.path)" />
          </div>
        </template>
      </PhotoCard>
    </div>
    <div v-if="!winners.length" class="empty">还没有胜出片</div>
    <!-- 右键菜单：移回复查 / 打开文件所在位置 -->
    <div v-if="ctx" class="ctxmenu" :style="{ left: ctx.x + 'px', top: ctx.y + 'px' }" @click.stop>
      <div class="ctxitem" @click="moveBackToReview">移回复查</div>
      <div class="ctxitem" @click="revealFile">打开文件所在位置</div>
    </div>
    <teleport to="body">
      <div v-if="lightbox" id="lightbox-lb" class="mask on" style="z-index: 70" @click="lightbox = ''">
        <img :src="lightbox" style="max-width: 97vw; max-height: 97vh; object-fit: contain" />
      </div>
    </teleport>
  </div>
</template>

<style scoped>
.ctxmenu {
  position: fixed; z-index: 200; min-width: 176px; padding: 4px;
  background: #23262e; border: 1px solid #3a3f4b; border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0,0,0,.45);
}
.ctxitem { padding: 8px 12px; border-radius: 6px; font-size: 13px; cursor: pointer; white-space: nowrap; }
.ctxitem:hover { background: var(--acc); color: #fff; }
</style>
