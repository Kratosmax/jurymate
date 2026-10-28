<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, type Group, type State } from '../api'

const props = defineProps<{ state: State | null }>()
const emit = defineEmits<{ done: []; toast: [m: string] }>()

const queue = ref<Group[]>([])
const pos = ref(0)
const sel = ref(0)

// 两种审片模式：
//   pk     —— 同组两张并排对比（原有机制）
//   single —— 单张大图，左右切换，适合先快速过一遍再决定
// 模式持久化：用户偏好单图就一直是单图，不该每次打开都退回默认
const MODE_KEY = 'jurymate.pkMode'
const saved = (typeof localStorage !== 'undefined' && localStorage.getItem(MODE_KEY)) as 'pk' | 'single' | null
const mode = ref<'pk' | 'single'>(saved === 'single' ? 'single' : 'pk')

// 单图模式下已在本组内挑走的照片（继续审剩余时不再显示）
const takenIds = ref<Set<number>>(new Set())

const group = computed(() => queue.value[pos.value] ?? null)
const aiIdx = computed(() => {
  const g = group.value
  return g ? g.members.findIndex((m) => m.idx === g.winner) : -1
})

/** 当前还在候选池里的成员（单图模式会随挑选递减） */
const pool = computed(() => {
  const g = group.value
  if (!g) return []
  return g.members.filter((m) => !takenIds.value.has(m.idx))
})

/** 单图模式当前显示的那张 */
const cur = computed(() => pool.value[Math.min(sel.value, pool.value.length - 1)] ?? null)

function rebuild() {
  const next = (props.state?.groups ?? []).filter((g) => g.needs_pk)
  const curId = queue.value[pos.value]?.id
  queue.value = next
  const keep = curId == null ? -1 : next.findIndex((g) => g.id === curId)
  if (!next.length) {
    pos.value = 0
  } else {
    pos.value = keep >= 0
      ? keep
      : Math.min(Math.max(pos.value, 0), next.length - 1)
  }
  selDraw()
}
watch(() => props.state, () => {
  // 组换了就清空本组已挑记录
  const curId = queue.value[pos.value]?.id
  rebuild()
  if (queue.value[pos.value]?.id !== curId) takenIds.value = new Set()
})

function followSel() {
  // 两种模式都要跟随：
  // - PK：选中项可能在横向溢出区外
  // - 单图：底部缩略图条同样会溢出，选的图可能在可视区之外
  const sel2 = mode.value === 'single'
    ? document.querySelectorAll('.thumbs .thumb')[sel.value]
    : document.querySelectorAll('#pkStage .side')[sel.value]
  ;(sel2 as HTMLElement | undefined)?.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' })
}

function selDraw() {
  const n = pool.value.length
  sel.value = n ? Math.min(Math.max(sel.value, 0), n - 1) : 0
}

async function pick(i: number) {
  const g = group.value
  if (!g) return
  await api.pk(g.id, 'pick', g.members[i].idx)
  takenIds.value = new Set()
  emit('done')
  pos.value++
}

async function setOne(idx: number, status: 'winner' | 'reject') {
  const g = group.value
  if (!g) return
  const m = g.members.find((x) => x.idx === idx)
  if (!m) return
  await api.setStatus(m.path, status)
}

/** 单图模式：Enter —— 把当前这张定为胜出，从候选池移除 */
async function pickCurrent() {
  const c = cur.value
  if (!c) return
  await setOne(c.idx, 'winner')
  const next = new Set(takenIds.value)
  next.add(c.idx)
  takenIds.value = next
  selDraw()
  if (pool.value.length === 0) {
    takenIds.value = new Set()
    emit('done')
    pos.value++
  } else if (pool.value.length === 1) {
    // 只剩一张：已经选了一张胜出，这张没被选中，按规则进复查
    await setOne(pool.value[0].idx, 'reject')
    takenIds.value = new Set()
    emit('done')
    pos.value++
  } else {
    emit('toast', '已定为胜出，继续审剩下的；↓ 结束本组')
  }
}

/** 单图模式：↓ —— 结束本组，剩余全部进复查 */
async function endGroupRejectRest() {
  for (const m of pool.value) await setOne(m.idx, 'reject')
  takenIds.value = new Set()
  emit('done')
  pos.value++
}

/** 单图模式：↑ —— 剩余全部胜出 */
async function allWinRest() {
  for (const m of pool.value) await setOne(m.idx, 'winner')
  takenIds.value = new Set()
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
  takenIds.value = new Set()
  pos.value++
}
function toggleZoom(e: MouseEvent) {
  const img = e.target as HTMLImageElement
  img.classList.toggle('zoomed')
}
function switchMode(m: 'pk' | 'single') {
  mode.value = m
  try { localStorage.setItem(MODE_KEY, m) } catch {}
  sel.value = 0
  takenIds.value = new Set()
}

onMounted(async () => {
  rebuild()
  window.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
async function onKey(e: KeyboardEvent) {
  if (!(document.querySelector('#p-pk') as HTMLElement)?.offsetParent) return
  const g = group.value
  if (!g) return
  if (e.key === 'ArrowLeft') {
    sel.value = Math.max(0, sel.value - 1); selDraw(); followSel()
  } else if (e.key === 'ArrowRight') {
    sel.value = Math.min(pool.value.length - 1, sel.value + 1); selDraw(); followSel()
  } else if (e.key === 'ArrowUp') {
    if (mode.value === 'single') await allWinRest(); else await act('all')
  } else if (e.key === 'ArrowDown') {
    if (mode.value === 'single') await endGroupRejectRest(); else await act('none')
  } else if (e.key === 'Enter') {
    if (mode.value === 'single') await pickCurrent(); else await pick(sel.value)
  } else if (e.key === 's' || e.key === 'S') skip()
  else if (e.key === 'z' || e.key === 'Z') await undo()
}
</script>

<template>
  <div class="view" id="p-pk">
    <div v-if="!group" class="hintbar">
      <template v-if="!queue.length">
        没有需要你拍板的组 —— AI 对每组的选择都很笃定。看看
        <a href="#" @click.prevent="$emit('done')">胜出页</a> 或「复查」区。
      </template>
      <template v-else>
        当前组已处理完，正在刷新…（若长时间停在这里，点
        <a href="#" @click.prevent="rebuild">这里</a> 重新载入）
      </template>
    </div>
    <template v-else>
      <div class="pkhead">
        <div class="pkmeta">
          第 <b>{{ pos + 1 }}/{{ queue.length }}</b> 组 · {{ group.members.length }} 张
          <span v-if="group.gap < 99">· 前两名分差 <b>{{ Math.round(group.gap) }}</b> 分</span>
        </div>
        <!-- 模式切换：右上角两个图标 -->
        <div class="modesw">
          <button class="modebtn" :class="{ on: mode === 'pk' }" title="并排对比两张"
                  @click="switchMode('pk')">⊞</button>
          <button class="modebtn" :class="{ on: mode === 'single' }" title="单张大图，左右切换"
                  @click="switchMode('single')">▣</button>
        </div>
      </div>

      <!-- 并排 PK -->
      <div v-if="mode === 'pk'" id="pkStage">
        <div v-for="(m, i) in group.members" :key="m.idx" class="side" :class="{ selL: i === sel }">
          <span v-if="i === aiIdx" class="aid" title="AI 默认选择">AI 首选</span>
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

      <!-- 单张大图 -->
      <div v-else class="singlewrap">
        <div class="singlestage" v-if="cur">
          <img :src="cur.thumb_view" title="点击 1:1 放大" @click="toggleZoom" />
          <div class="singlecap">
            <b>{{ cur.name }}</b>
            <span>
              锐度 {{ Math.round(cur.sharpness) }}<template v-if="cur.n_faces"> · {{ cur.n_faces }}脸</template>
              <template v-if="cur.reject_reasons.length"> · {{ cur.reject_reasons.join('/') }}</template>
            </span>
            <span class="idx">{{ sel + 1 }} / {{ pool.length }}</span>
          </div>
        </div>
        <div v-else class="empty">本组已全部处理</div>
        <div class="thumbs">
          <div v-for="(m, i) in pool" :key="m.idx" class="thumb" :class="{ on: i === sel }"
               @click="sel = i">
            <img :src="m.thumb_grid" />
          </div>
        </div>
      </div>

      <div class="keys">
        <template v-if="mode === 'pk'">
          <kbd>←</kbd><kbd>→</kbd> 选图 ｜ <kbd>Enter</kbd> 定胜者 ｜
          <kbd>↑</kbd> 全要 ｜ <kbd>↓</kbd> 全不要 ｜ <kbd>S</kbd> 跳过 ｜ <kbd>Z</kbd> 撤销
        </template>
        <template v-else>
          <kbd>←</kbd><kbd>→</kbd> 切图 ｜ <kbd>Enter</kbd> 定为胜出 ｜
          <kbd>↑</kbd> 剩余全部胜出 ｜ <kbd>↓</kbd> 结束本组（剩余进复查） ｜ <kbd>S</kbd> 跳过 ｜ <kbd>Z</kbd> 撤销
        </template>
      </div>
    </template>
  </div>
</template>

<style scoped>
.pkhead {
  display: flex; align-items: center; gap: 10px; margin-bottom: 12px;
}
.pkmeta { flex: 1; color: var(--dim); font-size: 13px; }
.modesw { display: flex; gap: 6px; }
.modebtn {
  width: 38px; height: 34px; border-radius: 8px; cursor: pointer;
  background: transparent; color: var(--dim);
  border: 1px solid var(--line); font-size: 16px; line-height: 1;
}
.modebtn.on { color: #fff; background: var(--acc); border-color: var(--acc); }
.modebtn:hover { border-color: var(--acc); }

.singlewrap { display: flex; flex-direction: column; align-items: center; gap: 12px; }
.singlestage {
  position: relative; width: 100%; display: flex; justify-content: center;
}
.singlestage img {
  max-width: min(100%, 1100px); max-height: 62vh; object-fit: contain;
  border-radius: 10px; background: #000;
}
.singlecap {
  position: absolute; left: 0; right: 0; bottom: 0;
  display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap;
  padding: 10px 16px; border-radius: 0 0 10px 10px;
  background: linear-gradient(transparent, rgba(0,0,0,.78));
  font-size: 13px;
}
.singlecap b { font-size: 15px; }
.singlecap span { color: #cfcfcf; }
.singlecap .idx { margin-left: auto; color: #fff; }

.thumbs { display: flex; gap: 8px; overflow-x: auto; max-width: 100%; padding-bottom: 4px; }
.thumb {
  flex: 0 0 auto; width: 86px; height: 60px; border-radius: 6px; overflow: hidden;
  border: 2px solid transparent; opacity: .55; cursor: pointer;
}
.thumb.on { border-color: var(--acc); opacity: 1; }
.thumb img { width: 100%; height: 100%; object-fit: cover; }

.keys { text-align: center; color: var(--dim); font-size: 12px; margin-top: 14px; }
</style>
