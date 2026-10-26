<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, type Group, type State } from '../api'

const props = defineProps<{ state: State | null }>()
const emit = defineEmits<{ done: []; toast: [m: string] }>()

const queue = ref<Group[]>([])
const pos = ref(0)
const sel = ref(0)


const group = computed(() => queue.value[pos.value] ?? null)
const aiIdx = computed(() => {
  const g = group.value
  return g ? g.members.findIndex((m) => m.idx === g.winner) : -1
})

function rebuild() {
  // 刷新后要停在"当前这一组"，不能无脑回队首——否则「跳过」会被任何一次
  // 操作后的 refresh 打回第 0 组，跳过的组又冒出来。
  const next = (props.state?.groups ?? []).filter((g) => g.needs_pk)
  const curId = queue.value[pos.value]?.id
  queue.value = next
  const keep = curId == null ? -1 : next.findIndex((g) => g.id === curId)
  pos.value = keep >= 0
    ? keep
    : Math.min(Math.max(pos.value, 0), Math.max(next.length - 1, 0))
  selDraw()
}
watch(() => props.state, rebuild)

function selDraw() {
  sel.value = Math.min(Math.max(sel.value, 0), (group.value?.members.length ?? 1) - 1)
}
async function pick(i: number) {
  const g = group.value
  if (!g) return
  await api.pk(g.id, 'pick', g.members[i].idx)
  emit('done')
  pos.value++
}
async function act(a: 'all' | 'none') {
  const g = group.value
  if (!g) return
  await api.pk(g.id, a, g.members[sel.value]?.idx)
  emit('done')
  pos.value++
}
async function undo() {
  await api.undo()
  emit('done')
  pos.value = Math.max(0, pos.value - 1)
}
function skip() {
  pos.value++
}
function toggleZoom(e: MouseEvent) {
  const img = e.target as HTMLImageElement
  img.classList.toggle('zoomed')
}

onMounted(async () => {
  rebuild()
  window.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
async function onKey(e: KeyboardEvent) {
  // 仅本页可见时响应（v-show 挂载常驻）
  if (!(document.querySelector('#p-pk') as HTMLElement)?.offsetParent) return
  const g = group.value
  if (!g) return
  const n = g.members.length
  if (e.key === 'ArrowLeft') { sel.value = Math.max(0, sel.value - 1); selDraw() }
  else if (e.key === 'ArrowRight') { sel.value = Math.min(n - 1, sel.value + 1); selDraw() }
  else if (e.key === 'ArrowUp') await act('all')
  else if (e.key === 'ArrowDown') await act('none')
  else if (e.key === 'Enter') await pick(sel.value)
  else if (e.key === 's' || e.key === 'S') skip()
  else if (e.key === 'z' || e.key === 'Z') await undo()
}
</script>

<template>
  <div class="view" id="p-pk">
    <div v-if="!queue.length" class="hintbar">
      没有需要你拍板的组 —— AI 对每组的选择都很笃定。看看
      <a href="#" @click.prevent="$emit('done')">胜出页</a> 或通知里提到的复查区。
    </div>
    <template v-else>
      <div class="hintbar">
        第 <b>{{ pos + 1 }}/{{ queue.length }}</b> 组 · {{ group.members.length }} 张 ·
        前两名分差仅 <b>{{ group.gap }}</b> 分，AI 默认选了第
        <b>{{ aiIdx + 1 }}</b> 张（左上角蓝标）——不同意就换
      </div>
      <div id="pkStage">
        <div v-for="(m, i) in group.members" :key="m.idx" class="side" :class="{ selL: i === sel }">
          <span v-if="i === aiIdx" class="aid">AI 首选</span>
          <img :src="m.thumb_view" title="点击 1:1 放大" @click="toggleZoom" />
          <div class="cap">
            <b>{{ m.name }}</b>
            <span style="color: var(--dim)">
              锐度 {{ Math.round(m.sharpness) }}{{ m.n_faces ? ' · ' + m.n_faces + '脸' : '' }}
              {{ m.reject_reasons.length ? ' · ' + m.reject_reasons.join('/') : '' }}
            </span>
          </div>
        </div>
      </div>
      <div style="text-align: center; color: var(--dim); font-size: 12px; margin-top: 12px">
        <kbd>←</kbd><kbd>→</kbd> 选图 ｜ <kbd>Enter</kbd> 定胜者 ｜ <kbd>↑</kbd> 全要 ｜
        <kbd>↓</kbd> 全不要 ｜ <kbd>S</kbd> 跳过 ｜ <kbd>Z</kbd> 撤销 ｜ 点图片放大
      </div>
    </template>
  </div>
</template>
