<script setup lang="ts">
import { computed, ref } from 'vue'
import { api, type State } from '../api'

const props = defineProps<{ state: State | null }>()
const emit = defineEmits<{ changed: []; toast: [m: string] }>()

const winners = computed(() => (props.state?.photos ?? []).filter((p) => p.status === 'winner'))
const lightbox = ref('')

function scoreColor(s: number | null) {
  return s == null ? 'var(--dim)' : s >= 4 ? 'var(--good)' : s >= 3 ? 'var(--txt)' : 'var(--warn)'
}
function groupOf(path: string) {
  return props.state?.groups.find((g) => g.members.some((m) => m.path === path))
}
async function organizeFlow() {
  const dry = await api.organize(true)
  if (!dry.ok) return emit('toast', dry.error ?? '')
  if (!dry.moved) return emit('toast', '没有需要移动的文件（可能都已归类）')
  if (confirm(`将移动 ${dry.moved} 个文件：\n· ${dry.winners} 组 → winners/\n· ${dry.rejects} 组 → rejects/\n· 未决策 ${dry.pending_kept} 张不动`)) {
    const r = await api.organize(false)
    emit('toast', r.ok ? `已归类：winners ${r.winners} 组，rejects ${r.rejects} 组` : r.error ?? '')
    emit('changed')
  }
}
async function exportCsv() {
  const r = await api.exportCsv()
  emit('toast', r.ok ? `已导出 ${r.rows} 条到 ${r.path}` : r.error ?? '')
}
async function openFolder() {
  await api.openFolder()
}
</script>

<template>
  <div class="view" id="p-winners">
    <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 16px">
      <h2 class="pgtitle" style="flex: 1; margin: 0">
        胜出 {{ winners.length }} 张
        <span style="color: var(--dim); font-size: 12px; font-weight: 400">★ = 5 分制审美参考分（悬停看含义）</span>
      </h2>
      <button class="ghost" @click="exportCsv">导出 CSV 清单</button>
      <button class="ghost" @click="openFolder">📁 打开结果文件夹</button>
      <button class="danger" @click="organizeFlow">归类到 winners/ 与 rejects/</button>
    </div>
    <div class="grid">
      <div v-for="(p, i) in winners" :key="p.path" class="card winner"
           :style="{ animationDelay: Math.min(i * 0.03, 0.5) + 's' }" @click="lightbox = p.thumb_view">
        <img :src="p.thumb_grid" loading="lazy" />
        <div class="score" :style="{ color: scoreColor(p.aesthetic) }"
             title="MUSIQ 无参考审美模型的 5 分制映射（人类平均审美参考分，AI 拿它排序）">
          ★ {{ p.aesthetic ?? '—' }}
        </div>
        <div class="cap">
          <b>{{ p.name }}</b>
          <span>{{ (groupOf(p.path)?.members.length ?? 1) > 1 ? '组内 ' + groupOf(p.path)!.members.length + ' 选 1 · ' : '' }}{{ p.width }}×{{ p.height }}</span>
        </div>
      </div>
    </div>
    <div v-if="!winners.length" class="empty">还没有胜出片</div>
    <teleport to="body">
      <div v-if="lightbox" id="lightbox-lb" class="mask on" style="z-index: 70" @click="lightbox = ''">
        <img :src="lightbox" style="max-width: 97vw; max-height: 97vh; object-fit: contain" />
      </div>
    </teleport>
  </div>
</template>
