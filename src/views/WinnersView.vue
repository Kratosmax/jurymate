<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import { api, type Photo, type State } from '../api'

const props = defineProps<{ state: State | null }>()
const emit = defineEmits<{ changed: []; toast: [m: string] }>()

const winners = computed(() => (props.state?.photos ?? []).filter((p) => p.status === 'winner'))

// ---- 右键菜单 ----
// 只做两件事：移回复查、打开文件所在位置。
// WebView 自带的菜单（共享/打印/复制图像/复制图像链接/另存为/放大）
// 由 App.vue 的全局 contextmenu 拦截统一禁掉。
const menu = ref<{ x: number; y: number; photo: Photo } | null>(null)

function groupOf(path: string) {
  return (props.state?.groups ?? []).find((g) => g.members.some((m) => m.path === path))
}

function onContextMenu(e: MouseEvent, p: Photo) {
  e.preventDefault()
  e.stopPropagation()
  // 菜单宽约 190、高约 90，贴边时往回收，避免出屏
  const x = Math.min(e.clientX, window.innerWidth - 200)
  const y = Math.min(e.clientY, window.innerHeight - 100)
  menu.value = { x, y, photo: p }
}
function closeMenu() {
  menu.value = null
}

async function moveBackToReview() {
  const m = menu.value
  closeMenu()
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
  const m = menu.value
  closeMenu()
  if (!m) return
  try {
    const how = await invoke<string>('reveal_in_explorer', { path: m.photo.path })
    // reused = 复用了已打开的窗口，opened = 新开了一个
    emit('toast', how === 'reused' ? '已在已有窗口定位' : '已打开所在文件夹')
  } catch (e) {
    emit('toast', `打开失败：${e}`)
  }
}

onMounted(() => {
  window.addEventListener('click', closeMenu)
  window.addEventListener('scroll', closeMenu, true)
})
onBeforeUnmount(() => {
  window.removeEventListener('click', closeMenu)
  window.removeEventListener('scroll', closeMenu, true)
})

// ---- 归类 / 导出 ----
const busy = ref<'org' | 'csv' | ''>('')
const selectMode = ref(false)
const selected = ref<Set<string>>(new Set())
const lastCsv = ref('')

function toggleSel(p: string) {
  const s = new Set(selected.value)
  s.has(p) ? s.delete(p) : s.add(p)
  selected.value = s
}
function selectAll() { selected.value = new Set(winners.value.map((w) => w.path)) }
function selectNone() { selected.value = new Set() }

async function organizeFlow() {
  busy.value = 'org'
  const paths = selectMode.value && selected.value.size ? [...selected.value] : undefined
  try {
    const r = await api.organize(false, paths)
    if (r.ok) {
      const extra = r.skipped_same ? `，${r.skipped_same} 个已存在跳过` : ''
      const conflict = r.skipped_conflict ? `，${r.skipped_conflict} 组内容不同未覆盖` : ''
      emit('toast', r.copied
        ? `已复制 ${r.copied} 个文件到 winners${extra}${conflict}`
        : `无需复制：${r.skipped_same} 个文件已是最新${conflict}`)
      emit('changed')
    } else {
      emit('toast', r.error || '归类失败')
    }
  } catch (e) {
    emit('toast', `归类出错：${e}`)
  } finally {
    busy.value = ''
  }
}

async function exportCsv() {
  busy.value = 'csv'
  try {
    const r = await api.exportCsv()
    if (r.ok) {
      lastCsv.value = r.path || ''
      emit('toast', `已导出 ${r.rows} 条记录`)
      // 导出后直接帮用户定位到文件（原需求：快速打开路径）
      const name = (r.path || '').split(/[\\/]/).pop() || ''
      if (name) await invoke<string>('reveal_in_explorer', { path: r.path }).catch(() => '')
    } else {
      emit('toast', r.error || '导出失败')
    }
  } catch (e) {
    emit('toast', `导出出错：${e}`)
  } finally {
    busy.value = ''
  }
}

async function openWinnersFolder() {
  await api.openFolder('winners')
}
</script>

<template>
  <div class="view" id="p-winners">
    <div class="toolbar">
      <h2 class="pgtitle">
        胜出 {{ winners.length }} 张
        <span class="subtle" title="MUSIQ 无参考审美模型给出的 5 分制映射，仅作排序参考">★ 审美分</span>
      </h2>
      <button class="ghost" :disabled="!!busy" @click="exportCsv" title="导出胜出清单，并自动定位到文件">
        {{ busy === 'csv' ? '导出中…' : '导出 CSV' }}
      </button>
      <button class="ghost" @click="openWinnersFolder" title="打开 winners 文件夹">📁</button>
      <button class="ghost" :class="{ act: selectMode }"
              title="只归类勾选的照片"
              @click="selectMode = !selectMode; selectNone()">
        {{ selectMode ? '退出勾选' : '勾选' }}
      </button>
      <button class="act" :disabled="!!busy" @click="organizeFlow"
              title="把胜出照片复制一份到 winners 文件夹，原文件保留">
        {{ busy === 'org' ? '处理中…' : (selectMode && selected.size ? `归类 ${selected.size} 张` : '归类') }}
      </button>
    </div>

    <div v-if="selectMode" class="selbar">
      <span>已选 <b>{{ selected.size }}</b> 张</span>
      <button class="ghost sm" @click="selectAll">全选</button>
      <button class="ghost sm" @click="selectNone">全不选</button>
    </div>

    <div class="grid">
      <PhotoCard v-for="(p, i) in winners" :key="p.path" :photo="p"
                 :style="{ animationDelay: Math.min(i * 0.03, 0.5) + 's' }"
                 :badge="p.manual_winner ? '人工召回' : ''"
                 @click="null" @contextmenu="onContextMenu($event, p)">
        <template #footer>
          <div class="cardfoot">
            <input v-if="selectMode" type="checkbox"
                   :checked="selected.has(p.path)" @click.stop="toggleSel(p.path)" />
            <span v-if="(p as any).group_size > 1 || groupOf(p.path)"
                  class="subtle" :title="'同组共 ' + (groupOf(p.path)?.members.length ?? 1) + ' 张'">
              组内 {{ groupOf(p.path)?.members.length }} 选 1
            </span>
          </div>
        </template>
      </PhotoCard>
    </div>
    <div v-if="!winners.length" class="empty">还没有胜出片</div>

    <!-- 右键菜单 -->
    <div v-if="menu" class="ctxmenu" :style="{ left: menu.x + 'px', top: menu.y + 'px' }"
         @click.stop>
      <div class="ctxitem" @click="moveBackToReview">移回复查</div>
      <div class="ctxitem" @click="revealFile">打开文件所在位置</div>
    </div>
  </div>
</template>

<style scoped>
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; flex-wrap: nowrap; }
.toolbar .pgtitle { flex: 1; margin: 0; white-space: nowrap; }
.toolbar button { white-space: nowrap; flex: 0 0 auto; }
.subtle { color: var(--dim); font-size: 12px; font-weight: 400; }
.selbar { display: flex; gap: 10px; align-items: center; font-size: 12px; margin-bottom: 10px; color: var(--dim); }
.ghost.sm { padding: 2px 10px; font-size: 12px; }
.cardfoot { display: flex; align-items: center; gap: 8px; }
.ctxmenu {
  position: fixed; z-index: 200; min-width: 180px; padding: 4px;
  background: #23262e; border: 1px solid #3a3f4b; border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0,0,0,.45);
}
.ctxitem { padding: 8px 12px; border-radius: 6px; font-size: 13px; cursor: pointer; white-space: nowrap; }
.ctxitem:hover { background: var(--acc); color: #fff; }
</style>
