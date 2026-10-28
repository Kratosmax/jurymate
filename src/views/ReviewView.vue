<script setup lang="ts">
import { computed, ref } from 'vue'
import { api, type State } from '../api'
import PhotoCard from '../components/PhotoCard.vue'

const props = defineProps<{ state: State | null }>()
const emit = defineEmits<{ changed: []; toast: [m: string] }>()

const rejects = computed(() =>
  (props.state?.photos ?? []).filter((p) => p.status === 'reject').sort((a, b) => (b.score ?? 0) - (a.score ?? 0)),
)
const byReason = computed(() => {
  const m: Record<string, typeof rejects.value> = {}
  for (const p of rejects.value) {
    for (const t of p.reject_reasons.length ? p.reject_reasons : ['无原因']) {
      ;(m[t] ??= []).push(p)
    }
  }
  return m
})
// 用 path 而不是下标记录"当前选中"：卡片是按原因分组渲染的，
// DOM 顺序和 rejects 的排序并不一致，用下标记会错位。
const activePath = ref('')

function moveActive(d: number) {
  const list = rejects.value
  if (!list.length) return
  const cur = list.findIndex((p) => p.path === activePath.value)
  const next = Math.max(0, Math.min(list.length - 1, (cur < 0 ? (d > 0 ? -1 : 0) : cur) + d))
  activePath.value = list[next].path
  const el = document.querySelector(`#revWrap .card[data-path="${CSS.escape(list[next].path)}"]`) as HTMLElement | undefined
  el?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
}

async function recall(path: string) {
  const r = await api.recall(path)
  if (r.ok) {
    emit('changed')
    emit('toast', `已召回，审美分 ${r.aesthetic ?? '—'}`)
  } else emit('toast', (r.error ?? '') + (r.hint ? `｜${r.hint}` : ''))
}
function onKey(e: KeyboardEvent) {
  if (!(document.querySelector('#p-review') as HTMLElement)?.offsetParent) return
  if (e.key === 'r' || e.key === 'R') {
    const t = activePath.value || rejects.value[0]?.path
    if (t) recall(t)
    e.preventDefault()
  }
  if (e.key === 'ArrowRight' || e.key === 'ArrowDown') { e.preventDefault(); moveActive(1) }
  if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') { e.preventDefault(); moveActive(-1) }
}
window.addEventListener('keydown', onKey)
</script>

<template>
  <div class="view" id="p-review">
    <div class="hintbar">
      落选片按 AI 给出的原因分组，组内按综合分降序——先看高分片，AI 最拿不准的都在最前面。
      点图或按 <kbd>R</kbd> 召回（补评分、定为胜出）。
    </div>
    <div id="revWrap">
      <div v-for="(ps, t) in byReason" :key="t" class="sec" style="margin-bottom: 22px">
        <h3 style="font-size: 14px; color: var(--dim); font-weight: 500; margin-bottom: 8px">{{ t }}（{{ ps.length }}）</h3>
        <div class="grid">
          <PhotoCard v-for="p in ps" :key="p.path" :photo="p"
                     :score-label="String(Math.round(p.score ?? 0))"
                     :mode="props.state?.score_mode === 'quality' ? 'quality' : 'aesthetic'"
                     :note="'综合分（扣掉淘汰原因惩罚后的分数）'"
                     :style="{ cursor: 'pointer' }"
                     @click="recall(p.path)" />
        </div>
      </div>
      <div v-if="!rejects.length" class="empty">没有落选片</div>
    </div>
  </div>
</template>
