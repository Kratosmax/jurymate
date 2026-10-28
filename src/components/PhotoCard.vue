<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  photo: import('../api').Photo
  scoreLabel?: string
  scoreColor?: string
  note?: string
  badge?: string
  /** 当前评分模式：决定分项标签是「审美」还是「画质」 */
  mode?: '' | 'aesthetic' | 'quality'
}>()
defineEmits<{ click: [] }>()

// 右上角显示「综合分」；下方一行给出分项分（都是百分制）
const scoreText = computed(() => {
  if (props.scoreLabel) return props.scoreLabel
  const v = props.photo.score
  return `★ ${v != null ? Math.round(v) : '—'}`
})
const color = computed(() =>
  props.scoreColor ?? scoreColorOf(props.photo.score))
const showMetrics = computed(() => !props.scoreLabel)
// 画质优先用的是 MUSIQ 画质分，它本身就含清晰度，不再单列「清晰」
const quality = computed(() => props.mode === 'quality')
const aesText = computed(() => {
  const v = props.photo.aesthetic
  return v != null ? Math.round(v) : '—'
})
const sharpText = computed(() => {
  const v = props.photo.sharp_score
  return v != null ? Math.round(v) : '—'
})
const metricsText = computed(() => quality.value
  ? `画质 ${aesText.value}`
  : `审美 ${aesText.value} ｜ 清晰 ${sharpText.value}`)

function scoreColorOf(s: number | null | undefined) {
  // 百分制下的三档配色。阈值按当前评分口径（审美分 38-57、综合分 20-80）定，
  // 换成新口径后应重新标定，见设置页权重说明。
  return s == null ? 'var(--dim)' : s >= 70 ? 'var(--good)' : s >= 45 ? 'var(--txt)' : 'var(--warn)'
}
</script>

<template>
  <div class="card" :class="{ winner: photo.status === 'winner' }" @click="$emit('click')">
    <img :src="photo.thumb_grid" loading="lazy" />
    <div class="score" :style="{ color }" :title="note">{{ scoreText }}</div>
    <div v-if="badge" class="aid" style="position: absolute; top: 8px; left: 8px; z-index: 2;
         background: rgba(226, 185, 59, .92); color: #1a1a1a; padding: 2px 9px;
         border-radius: 6px; font-size: 11px;">{{ badge }}</div>
    <div class="cap">
      <b>{{ photo.name }}</b>
      <span>
        {{ photo.n_faces ? photo.n_faces + '脸' : '无脸' }} · 锐{{ Math.round(photo.sharpness) }}
        <template v-if="photo.width"> · {{ photo.width }}×{{ photo.height }}</template>
      </span>
      <span v-if="showMetrics" class="mets">{{ metricsText }}</span>
    </div>
    <!-- 卡片底部的额外操作区（勾选框、单张按钮等）。
         调用方通过 #footer 传入；不用 slot 的话子内容会被直接丢弃。 -->
    <div v-if="$slots.footer" class="cap-extra" @click.stop>
      <slot name="footer" />
    </div>
  </div>
</template>
