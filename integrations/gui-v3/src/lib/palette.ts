/** 外观与墙纸（iOS「显示与亮度」+「墙纸」两项设置）：
 * html[data-appearance] = light | dark（auto 时跟随系统 prefers-color-scheme）；
 * html[data-wallpaper] = 墙纸 id（纯 CSS 渐变光斑，离线可用）。
 * 存储键 eco_v23_* 与 v1 / v2 / v2.1 / v2.2 互不串味。 */

export type Appearance = 'auto' | 'light' | 'dark'
export type WallpaperId = 'aurora' | 'dawn' | 'ocean' | 'dusk' | 'graphite'

export type WallpaperDef = { id: WallpaperId; name: string; preview: string }

export const WALLPAPERS: WallpaperDef[] = [
  { id: 'aurora', name: '极光', preview: 'linear-gradient(135deg,#5ac8fa,#5856d6 55%,#af52de)' },
  { id: 'dawn', name: '晨曦', preview: 'linear-gradient(135deg,#ffcc80,#ff8a9a 50%,#a78bfa)' },
  { id: 'ocean', name: '深海', preview: 'linear-gradient(135deg,#34c7c0,#0a84ff 55%,#1e2a78)' },
  { id: 'dusk', name: '暮光', preview: 'linear-gradient(135deg,#ff9f0a,#ff375f 50%,#5e2a84)' },
  { id: 'graphite', name: '石墨', preview: 'linear-gradient(135deg,#8e8e93,#3a3a3c 60%,#1c1c1e)' },
]

const K_APP = 'eco_v23_appearance'
const K_WP = 'eco_v23_wallpaper'

const read = (k: string): string => {
  try { return localStorage.getItem(k) ?? '' } catch { return '' }
}
const write = (k: string, v: string): void => {
  try { localStorage.setItem(k, v) } catch { /* 隐私模式 → 仅当前会话生效 */ }
}

const mq = typeof window !== 'undefined' ? window.matchMedia('(prefers-color-scheme: dark)') : null

export function getAppearance(): Appearance {
  const v = read(K_APP)
  return (v === 'light' || v === 'dark' || v === 'auto') ? v : 'dark'
}
export function getWallpaper(): WallpaperId {
  const v = read(K_WP)
  return (WALLPAPERS.some(w => w.id === v) ? v : 'aurora') as WallpaperId
}

/** 实际生效的明暗（auto 解析后） */
export function resolvedAppearance(a: Appearance = getAppearance()): 'light' | 'dark' {
  if (a === 'auto') return mq?.matches ? 'dark' : 'light'
  return a
}

export function applyAppearance(a: Appearance): void {
  write(K_APP, a)
  document.documentElement.dataset.appearance = resolvedAppearance(a)
}
export function applyWallpaper(id: WallpaperId): void {
  write(K_WP, id)
  document.documentElement.dataset.wallpaper = id
}

export function initTheme(): void {
  applyAppearance(getAppearance())
  applyWallpaper(getWallpaper())
  mq?.addEventListener('change', () => { if (getAppearance() === 'auto') applyAppearance('auto') })
}
