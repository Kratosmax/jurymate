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

function groupOf(path: string) {
  return props.state?.groups.find((g) => g.members.some((m) => m.path === path))
}

async function organizeFlow() {
  busy.value = 'org'
  actionMsg.value = null
  try {
    const dry = await api.organize(true)
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
          + ' —— 再点一次「确认归类」执行',
      }
      return
    }

    const r = await api.organize(false)
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
      <button class="ghost" :disabled="!!busy" @click="exportCsv">
        {{ busy === 'csv' ? '导出中…' : '导出 CSV 清单' }}
      </button>
      <button class="ghost" @click="openFolder">📁 打开结果文件夹</button>
      <button class="danger" :disabled="!!busy" @click="organizeFlow">
        {{ busy === 'org' ? '处理中…' : (confirmMove ? '确认归类' : '归类到 winners/ 与 rejects/') }}
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
    </div>
    <div class="grid">
      <PhotoCard v-for="(p, i) in winners" :key="p.path" :photo="p"
                 :style="{ animationDelay: Math.min(i * 0.03, 0.5) + 's' }"
                 :badge="p.manual_winner ? '人工召回' : ''"
                 :note="'MUSIQ 无参考审美模型的 5 分制映射（人类平均审美参考分，AI 拿它排序）'"
                 @click="lightbox = p.thumb_view">
        <template #cap-extra>
          {{ (groupOf(p.path)?.members.length ?? 1) > 1 ? '组内 ' + groupOf(p.path)!.members.length + ' 选 1 · ' : '' }}
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
