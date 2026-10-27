<script setup lang="ts">
import { computed, ref } from 'vue'
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
function toggleSel(p: string) {
  const s = new Set(selected.value)
  if (s.has(p)) s.delete(p)
  else s.add(p)
  selected.value = s
}
function selectAll() {
  selected.value = new Set(winners.value.map((w) => w.path))
}
function selectNone() {
  selected.value = new Set()
}

// 单张人工指定胜负：用户要能直接说"这张要/不要"，而不是只能接受组内选择
async function setStatus(path: string, status: 'winner' | 'reject') {
  const r = await api.setStatus(path, status)
  if (r.ok) {
    if (status === 'reject') {
      // 照片会从胜出页消失，必须告诉用户去哪找回，
      // 否则看起来像"照片丢了"。
      actionMsg.value = {
        kind: 'warn',
        text: '已移到落选',
        detail: '这张已从胜出页移除。要恢复的话去「复查」页——那里列出所有落选片，点图即可召回。',
      }
    } else {
      actionMsg.value = { kind: 'ok', text: '已设为胜出', detail: path }
    }
    emit('changed')
  } else {
    actionMsg.value = { kind: 'err', text: '操作失败', detail: r.error }
  }
}

function groupOf(path: string) {
  return props.state?.groups.find((g) => g.members.some((m) => m.path === path))
}

async function organizeFlow() {
  busy.value = 'org'
  actionMsg.value = null
  const paths = selectMode.value && selected.value.size ? [...selected.value] : undefined
  try {
    const dry = await api.organize(true, paths)
    if (!dry.ok) {
      actionMsg.value = { kind: 'err', text: '归档失败', detail: dry.error }
      return
    }
    // dry-run 返回 moved 可能为 0：照片已经归位（幂等），这不是错误，
    // 要如实说"已全部在目标文件夹"，而不是让用户以为功能没反应。
    if (!dry.moved) {
      actionMsg.value = {
        kind: 'warn',
        text: '所有照片都已在目标文件夹里，无需移动',
        detail: `winners ${dry.winners} 组 · rejects ${dry.rejects} 组 · 冲突跳过 ${dry.skipped_conflict} 个`
          + (dry.pending_kept ? ` · 未决策 ${dry.pending_kept} 张保持不动` : ''),
      }
      return
    }

    // 不用 window.confirm：Tauri 的 webview 对它的支持不可靠，
    // 之前点了没弹框也没动作，等于按钮失效。改为页面内二次确认。
    if (!confirmMove.value) {
      confirmMove.value = true
      actionMsg.value = {
        kind: 'warn',
        text: `将移动 ${dry.moved} 个文件`,
        detail: `winners ${dry.winners} 组 · rejects ${dry.rejects} 组`
          + (dry.pending_kept ? ` · 未决策 ${dry.pending_kept} 张不动` : '')
          + (paths ? ` · 仅处理勾选的 ${paths.length} 张` : ' · 处理全部照片')
          + ' —— 再点一次「确认归类」执行',
      }
      return
    }

    const r = await api.organize(false, paths)
    confirmMove.value = false
    if (r.ok) {
      actionMsg.value = {
        kind: 'ok',
        text: `已归类 ${r.moved ?? 0} 个文件`,
        detail: `winners ${r.winners} 组 · rejects ${r.rejects} 组`
          + (r.skipped_conflict ? ` · 重名跳过 ${r.skipped_conflict} 个` : ''),
      }
      emit('changed')
    } else {
      actionMsg.value = { kind: 'err', text: '归档失败', detail: r.error }
    }
  } catch (e) {
    actionMsg.value = { kind: 'err', text: '归档出错', detail: String(e) }
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
      <button class="danger" :disabled="!!busy" @click="organizeFlow">
        {{ busy === 'org' ? '处理中…' : (confirmMove ? '确认归类' : (selectMode && selected.size ? `归类选中的 ${selected.size} 张` : '归类全部照片')) }}
      </button>
      <button v-if="confirmMove" class="ghost" @click="confirmMove = false; actionMsg = null">取消</button>
    </div>

    <!-- 归类是什么，必须写清楚：这是**真的移动磁盘文件**，不可逆 -->
    <div class="hintbar" style="font-size:12px;line-height:1.7">
      <b>「归类」会真的移动文件</b>：把胜出的照片移进
      <code>{{ props.state?.folder }}\winners\</code>，落选的移进 <code>rejects\</code>。
      连拍的 JPG 与同名 .xmp 会跟着一起走。不开「勾选部分归类」时处理全部照片；
      开了就只处理你勾选的那些。移动后原位置就没有了，请确认后再执行。
    </div>

    <div v-if="selectMode" class="hintbar" style="display:flex;gap:10px;align-items:center;font-size:12px">
      <span>勾选要归类的照片（已选 <b>{{ selected.size }}</b> 张）</span>
      <button class="ghost" style="padding:2px 10px" @click="selectAll">全选</button>
      <button class="ghost" style="padding:2px 10px" @click="selectNone">全不选</button>
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
                 :style="{ animationDelay: Math.min(i * 0.03, 0.5) + 's' }"
                 :badge="p.manual_winner ? '人工召回' : ''"
                 :note="'MUSIQ 无参考审美模型的 5 分制映射（人类平均审美参考分，AI 拿它排序）'"
                 @click="lightbox = p.thumb_view">
        <template #cap-extra>
          <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:6px">
            <input v-if="selectMode" type="checkbox" style="width:auto;margin:0"
                   :checked="selected.has(p.path)" @click.stop="toggleSel(p.path)" />
            <!-- 单张人工指定：AI 的判断可以推翻，不用受组逻辑限制 -->
            <button class="ghost" style="padding:1px 8px;font-size:11px"
                    @click.stop="setStatus(p.path, 'reject')">移到落选</button>
          </div>
          <div v-if="(groupOf(p.path)?.members.length ?? 1) > 1" style="font-size:11px;color:var(--dim)">
            组内 {{ groupOf(p.path)!.members.length }} 选 1
          </div>
        </template>
      </PhotoCard>
    </div>
    <div v-if="!winners.length" class="empty">还没有胜出片</div>
    <teleport to="body">
      <div v-if="lightbox" id="lightbox-lb" class="mask on" style="z-index: 70" @click="lightbox = ''">
        <img :src="lightbox" style="max-width: 97vw; max-height: 97vh; object-fit: contain" />
      </div>
    </teleport>
  </div>
</template>
