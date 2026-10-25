<script setup lang="ts">
import { computed, ref } from 'vue'
import { api, type State } from '../api'

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
const hi = ref(-1)

async function recall(path: string) {
  const r = await api.recall(path)
  if (r.ok) {
    emit('changed')
    emit('toast', `已召回，审美分 ${r.aesthetic ?? '—'}`)
  } else emit('toast', r.error ?? '')
}
function onKey(e: KeyboardEvent) {
  if (!(document.querySelector('#p-review') as HTMLElement)?.offsetParent) return
  const cards = document.querySelectorAll('#revWrap .card')
  if (e.key === 'r' || e.key === 'R') {
    const c = cards[hi.value >= 0 ? hi.value : 0] as HTMLElement | undefined
    if (c?.dataset.path) recall(c.dataset.path)
  }
  if (e.key === 'ArrowDown' && hi.value < cards.length - 1) {
    hi.value++
    cards[hi.value].scrollIntoView({ block: 'nearest' })
    e.preventDefault()
  }
  if (e.key === 'ArrowUp' && hi.value > 0) {
    hi.value--
    cards[hi.value].scrollIntoView({ block: 'nearest' })
    e.preventDefault()
  }
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
          <div v-for="p in ps" :key="p.path" class="card" :data-path="p.path"
               title="点击召回（AI 判错了？捞回来）" @click="recall(p.path)">
            <img :src="p.thumb_grid" loading="lazy" />
            <div class="score" title="综合分（审美分 - 原因惩罚）">{{ p.score ?? '—' }}</div>
            <div class="cap"><b>{{ p.name }}</b><span>{{ p.n_faces ? p.n_faces + '脸' : '无脸' }} · 锐{{ Math.round(p.sharpness) }}</span></div>
          </div>
        </div>
      </div>
      <div v-if="!rejects.length" class="empty">没有落选片</div>
    </div>
  </div>
</template>
