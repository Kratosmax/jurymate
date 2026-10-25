<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  photo: import('../api').Photo
  scoreLabel?: string
  scoreColor?: string
  note?: string
}>()
defineEmits<{ click: [] }>()

const scoreText = computed(() =>
  props.scoreLabel ?? `★ ${props.photo.aesthetic ?? '—'}`)
const color = computed(() =>
  props.scoreColor ?? scoreColorOf(props.photo.aesthetic))

function scoreColorOf(s: number | null) {
  return s == null ? 'var(--dim)' : s >= 4 ? 'var(--good)' : s >= 3 ? 'var(--txt)' : 'var(--warn)'
}
</script>

<template>
  <div class="card" :class="{ winner: photo.status === 'winner' }" @click="$emit('click')">
    <img :src="photo.thumb_grid" loading="lazy" />
    <div class="score" :style="{ color }" :title="note">{{ scoreText }}</div>
    <div class="cap">
      <b>{{ photo.name }}</b>
      <span>
        {{ photo.n_faces ? photo.n_faces + '脸' : '无脸' }} · 锐{{ Math.round(photo.sharpness) }}
        <template v-if="photo.width"> · {{ photo.width }}×{{ photo.height }}</template>
      </span>
    </div>
  </div>
</template>
