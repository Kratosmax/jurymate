/**
 * 统一的入场动效（Web Animations API）。
 *
 * 为什么不用 CSS 动画：所有页面都用 v-show 常驻挂载，CSS 的 `animation`
 * 只在元素创建时跑一次——用户第一次切到那一页时动画早就放完了，
 * 表现为"有的页面滑入、有的页面瞬间出现"。
 * 这里改成切页时手动播一次，所有页面走同一套曲线与节奏。
 */

const easeEmphasized = 'cubic-bezier(.16, 1, .3, 1)'

function reduced(): boolean {
  try {
    return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
  } catch {
    return false
  }
}

/** 整页入场：轻微上浮 + 淡入 */
export function pageEnter(el: HTMLElement | null | undefined): void {
  if (!el || reduced()) return
  el.animate(
    [
      { opacity: 0, transform: 'translateY(8px)' },
      { opacity: 1, transform: 'none' },
    ],
    { duration: 260, easing: easeEmphasized, fill: 'backwards' },
  )
}

/**
 * 卡片依次入场（stagger）。
 *
 * 只对前 32 张做错峰：再多的卡片用户也看不到，全排一遍只会拖慢切页。
 * `fill: 'backwards'` 保证延迟期间是"透明待入"而不是先闪一下再动。
 */
export function cardsIn(root: HTMLElement | null | undefined): void {
  if (!root || reduced()) return
  const cards = root.querySelectorAll<HTMLElement>('.card')
  const max = 32
  cards.forEach((card, i) => {
    if (i >= max) return
    card.animate(
      [
        { opacity: 0, transform: 'translateY(10px) scale(.98)' },
        { opacity: 1, transform: 'none' },
      ],
      {
        duration: 320,
        delay: Math.min(i * 26, 420),
        easing: easeEmphasized,
        fill: 'backwards',
      },
    )
  })
}
