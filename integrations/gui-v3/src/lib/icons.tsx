/** 内联 SVG 图标集（自 v1 viz/icons.js 移植：16 网格 · stroke 1.5 · currentColor）。
 * 不用 unicode 符号的原因同 v1：各平台字重/基线不一致，SVG 全可控且零请求。 */

const P: Record<string, string> = {
  dashboard: '<path d="M2.2 11.2a6 6 0 0 1 11.6 0"/><path d="M8 11.2 11.2 7.2"/><circle cx="8" cy="11.4" r="1.1"/>',
  memories: '<path d="M8 1.8 14.4 5 8 8.2 1.6 5z"/><path d="m2.2 8 5.8 2.9L13.8 8"/><path d="m2.2 11 5.8 2.9L13.8 11"/>',
  experiences: '<path d="M8 1.6 9.4 6.6 14.4 8 9.4 9.4 8 14.4 6.6 9.4 1.6 8 6.6 6.6z"/>',
  starmap: '<circle cx="3.2" cy="11.6" r="1.7"/><circle cx="8.4" cy="4.6" r="1.9"/><circle cx="13" cy="10.4" r="1.6"/><path d="m4.5 10.4 2.9-4.2M9.7 6.1l2.5 2.9M4.9 11.9l6.6-.9"/>',
  lineage: '<circle cx="4" cy="3.4" r="1.8"/><circle cx="12" cy="3.4" r="1.8"/><circle cx="8" cy="12.6" r="1.8"/><path d="M4 5.2v2.2c0 1.1.9 2 2 2h4c1.1 0 2-.9 2-2V5.2M8 9.4v1.4"/>',
  candidates: '<path d="M8 1.9c2.5 0 4.5 3.5 4.5 6.3A4.5 4.5 0 0 1 8 14.1 4.5 4.5 0 0 1 3.5 8.2C3.5 5.4 5.5 1.9 8 1.9Z"/><path d="m6.4 8.4 1.5-1.1 1.3 1.3 1.6-1.3"/>',
  timeline: '<path d="M2 3.6h12M2 8h12M2 12.4h12"/><circle cx="5.4" cy="3.6" r="1.5" fill="currentColor" stroke="none"/><circle cx="10.2" cy="8" r="1.5" fill="currentColor" stroke="none"/><circle cx="7" cy="12.4" r="1.5" fill="currentColor" stroke="none"/>',
  health: '<path d="M8 13.4S2.2 9.9 2.2 6.3A2.9 2.9 0 0 1 8 4.7a2.9 2.9 0 0 1 5.8 1.6c0 3.6-5.8 7.1-5.8 7.1Z"/><path d="m5.9 7.5 1.5 1.5 2.8-2.8"/>',
  actions: '<circle cx="8" cy="8" r="2.6"/><path d="M8 1.8v2M8 12.2v2M1.8 8h2M12.2 8h2M3.6 3.6l1.4 1.4M11 11l1.4 1.4M12.4 3.6 11 5M5 11l-1.4 1.4"/>',
  bell: '<path d="M8 2.2a3.6 3.6 0 0 0-3.6 3.6c0 3.2-1.5 4.3-1.5 4.3h10.2s-1.5-1.1-1.5-4.3A3.6 3.6 0 0 0 8 2.2Z"/><path d="M6.5 12.2a1.6 1.6 0 0 0 3 0"/>',
  firstaid: '<rect x="1.8" y="4.6" width="12.4" height="8.6" rx="1.6"/><path d="M6 4.6V3.2h4v1.4"/><path d="M8 6.7v4.4M5.8 8.9h4.4"/>',
  refresh: '<path d="M13.2 8A5.2 5.2 0 1 1 11.5 4"/><path d="M13.4 1.8v3.4H10"/>',
  search: '<circle cx="7" cy="7" r="4.4"/><path d="m10.3 10.3 4 4"/>',
  close: '<path d="m3.4 3.4 9.2 9.2M12.6 3.4l-9.2 9.2"/>',
  cruise: '<path d="M2.4 6V2.4H6M10 2.4h3.6V6M13.6 10v3.6H10M6 13.6H2.4V10"/>',
  diag: '<rect x="3.4" y="2.6" width="9.2" height="10.8" rx="1.6"/><path d="M6.2 2.6V1.5h3.6v1.1M5.8 6.4h4.4M5.8 9.1h4.4"/>',
  help: '<circle cx="8" cy="8" r="6.2"/><path d="M6.2 6.2a1.9 1.9 0 1 1 2.5 1.9c-.5.2-.7.5-.7 1v.4M8 11.7v.1"/>',
  external: '<path d="M6.6 3.2H3.6c-.8 0-1.4.6-1.4 1.4v7.8c0 .8.6 1.4 1.4 1.4h7.8c.8 0 1.4-.6 1.4-1.4v-3"/><path d="M9.4 2.4h4.2v4.2M13.4 2.6 7.8 8.2"/>',
  warn: '<path d="M8 2.3 14.3 13.7H1.7z"/><path d="M8 6.3v3.5M8 11.6v.1"/>',
  gear: '<circle cx="8" cy="8" r="2.2"/><path d="M8 1.6v1.6M8 12.8v1.6M1.6 8h1.6M12.8 8h1.6M3.5 3.5l1.1 1.1M11.4 11.4l1.1 1.1M12.5 3.5l-1.1 1.1M4.6 11.4l-1.1 1.1"/><circle cx="8" cy="8" r="4.6"/>',
  ledger: '<rect x="3" y="1.8" width="10" height="12.4" rx="1.8"/><path d="M5.6 5h4.8M5.6 8h4.8M5.6 11h2.8"/>',
  back: '<path d="M10.4 2.6 5 8l5.4 5.4"/>',
  share: '<path d="M8 1.8v8.4M5.2 4.6 8 1.8l2.8 2.8"/><path d="M5.4 7H4.2c-.8 0-1.4.6-1.4 1.4v4.4c0 .8.6 1.4 1.4 1.4h7.6c.8 0 1.4-.6 1.4-1.4V8.4c0-.8-.6-1.4-1.4-1.4h-1.2"/>',
  more: '<circle cx="3.6" cy="8" r="1" fill="currentColor"/><circle cx="8" cy="8" r="1" fill="currentColor"/><circle cx="12.4" cy="8" r="1" fill="currentColor"/>',
  moon: '<path d="M13.4 9.6A5.8 5.8 0 0 1 6.4 2.6a5.8 5.8 0 1 0 7 7Z"/>',
  sun: '<circle cx="8" cy="8" r="2.8"/><path d="M8 1.4v1.4M8 13.2v1.4M1.4 8h1.4M13.2 8h1.4M3.3 3.3l1 1M11.7 11.7l1 1M12.7 3.3l-1 1M4.3 11.7l-1 1"/>',
  grid: '<rect x="2" y="2" width="5" height="5" rx="1.3"/><rect x="9" y="2" width="5" height="5" rx="1.3"/><rect x="2" y="9" width="5" height="5" rx="1.3"/><rect x="9" y="9" width="5" height="5" rx="1.3"/>',
  lock: '<rect x="3" y="7" width="10" height="7.2" rx="1.8"/><path d="M5.2 7V5a2.8 2.8 0 0 1 5.6 0v2"/>',
  play: '<path d="M4.6 2.6v10.8L13 8z"/>',
  standby: '<rect x="1.6" y="3.4" width="12.8" height="9.2" rx="2"/><path d="M8 5.6V8l1.8 1.2"/>',
  text: '<path d="M2.4 13 5.6 4l3.2 9M3.5 10h4.2M10 13l1.9-5.4 1.9 5.4M10.6 11.6h2.6"/>',
  kbd: '<rect x="1.4" y="3.8" width="13.2" height="8.4" rx="1.6"/><path d="M4 6.4h.1M6.7 6.4h.1M9.3 6.4h.1M12 6.4h.1M4 9.4h8"/>',
  photo: '<rect x="1.8" y="2.8" width="12.4" height="10.4" rx="2"/><circle cx="5.6" cy="6.4" r="1.2"/><path d="m2.4 12 3.6-3.4 2.4 2.2 2.2-2 3.2 3"/>',
  info: '<circle cx="8" cy="8" r="6.2"/><path d="M8 7.2v4M8 4.8v.1"/>',
  chev: '<path d="m6 3.4 4.6 4.6L6 12.6"/>',
  check: '<path d="m3.2 8.4 3 3 6.6-6.8"/>',
  compass: '<circle cx="8" cy="8" r="6.2"/><path d="m10.6 5.4-1.6 3.6-3.6 1.6 1.6-3.6z"/>',
  copy: '<rect x="5" y="5" width="8.6" height="8.6" rx="1.6"/><path d="M11 5V3.8c0-.9-.7-1.6-1.6-1.6H4c-.9 0-1.6.7-1.6 1.6v5.4c0 .9.7 1.6 1.6 1.6H5"/>',
  wave: '<path d="M1.6 8h2l1.6-4.4 2.4 8.8L10 5.6l1.4 2.4h3"/>',
  // ── v3.2.0（四门 / 圈 / 注入 / 记忆包）──
  gates: '<rect x="1.8" y="2.4" width="5.2" height="11.2" rx="1.4"/><rect x="9" y="2.4" width="5.2" height="6.4" rx="1.4"/><path d="M9 11.6h5.2"/>',
  circles: '<circle cx="8" cy="8" r="5.6"/><circle cx="8" cy="8" r="2.2"/><path d="M8 2.4v2M8 11.6v2M2.4 8h2M11.6 8h2"/>',
  inject: '<path d="M8 1.8v7.2"/><path d="m5.2 6.4 2.8 2.8 2.8-2.8"/><path d="M2.4 11.4v1.2c0 .8.6 1.4 1.4 1.4h8.4c.8 0 1.4-.6 1.4-1.4v-1.2"/>',
  packs: '<path d="M2.2 5.2 8 2.2l5.8 3v5.6L8 13.8 2.2 10.8z"/><path d="M2.2 5.2 8 8.2l5.8-3M8 8.2v5.6"/>',
  cost: '<circle cx="8" cy="8" r="6.2"/><path d="M8 3.4v9.2M10.4 5.8c-.5-.7-1.4-1.1-2.4-1.1-1.4 0-2.4.7-2.4 1.8 0 1 .9 1.6 2.4 1.9 1.6.3 2.5.9 2.5 1.9 0 1.1-1.1 1.8-2.5 1.8-1.1 0-2-.4-2.5-1.2"/>',
  sparkle: '<path d="M6.4 2.4 7.5 6l3.6 1.1-3.6 1.1-1.1 3.6-1.1-3.6L1.7 7.1 5.3 6z"/><path d="M12.2 9.4l.6 1.8 1.8.6-1.8.6-.6 1.8-.6-1.8-1.8-.6 1.8-.6z"/>',
}

export type IcoName = keyof typeof P | string

export function Ico({ name, size = 16, className = '' }: { name: IcoName; size?: number; className?: string }) {
  const p = P[name]
  if (!p) return null
  return (
    <svg className={'ico ' + className} width={size} height={size} viewBox="0 0 16 16" fill="none"
      stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true" focusable="false" dangerouslySetInnerHTML={{ __html: p }} />
  )
}

/** 空态插画（自 v1 Icons.empty 移植）。 */
const ILLUS: Record<string, string> = {
  memories: '<path d="M60 16 100 34 60 52 20 34Z"/><path d="m20 48 40 18 40-18" opacity=".75"/><path d="m20 62 40 18 40-18" opacity=".5"/>',
  search: '<circle cx="52" cy="38" r="20"/><path d="m67 53 18 18"/><path d="M45 33a7 7 0 0 1 12 4.4c0 3-4.4 3.4-4.4 6.2" opacity=".7"/><circle cx="52" cy="48" r="1.4" fill="currentColor" stroke="none" opacity=".7"/>',
  candidates: '<path d="M60 14c12 0 22 17 22 30a22 22 0 0 1-44 0c0-13 10-30 22-30Z"/><path d="m51 44 7-5 6 6 8-7" opacity=".7"/><path d="M18 74h84" stroke-dasharray="3 6" opacity=".7"/>',
  timeline: '<path d="M16 68h88"/><path d="M16 48h88" stroke-dasharray="3 6" opacity=".6"/><circle cx="34" cy="48" r="5"/><circle cx="60" cy="68" r="5"/><circle cx="86" cy="68" r="5" opacity=".6"/>',
  actions: '<rect x="26" y="16" width="68" height="56" rx="6"/><path d="M40 34h40M40 46h40M40 58h22" stroke-dasharray="3 5" opacity=".7"/>',
  generic: '<rect x="24" y="24" width="72" height="48" rx="6" stroke-dasharray="5 5"/><path d="M48 48h24" opacity=".6"/>',
}

export function EmptyArt({ kind = 'generic', title, hint }: { kind?: string; title?: string; hint?: string }) {
  const art = ILLUS[kind] ?? ILLUS.generic
  return (
    <div className="empty-art">
      <svg className="empty-illus" viewBox="0 0 120 86" fill="none" stroke="currentColor" strokeWidth="1.4"
        strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" dangerouslySetInnerHTML={{ __html: art }} />
      {title && <div className="empty-title">{title}</div>}
      {hint && <div className="empty-hint">{hint}</div>}
    </div>
  )
}
