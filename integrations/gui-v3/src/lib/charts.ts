/** 时间线图表（自 v1 viz/charts.js 移植泳道图与按日直方图；演化回放 v2.1 暂缺——见 README）。
 * 零依赖手写 SVG 字符串；交互（悬停 tooltip / 点击跳转）由视图层事件委托实现。 */
import type { LogRow } from './api'

export const GATE_NAME: Record<string, string> = {
  gate: '门① 写入整合', quota: '门② 巩固/配额', distill: '门③ 画像蒸馏', review: '门④ 复核',
}
export const GATE_COL: Record<string, string> = {
  gate: '#38bdf8', quota: '#f59e0b', distill: '#a78bfa', review: '#34d399',
}

/** ① 泳道图：每门一行，事件为色块；g[data-ev] 供事件委托取行号。 */
export function swimlaneSvg(rows: LogRow[]): string {
  if (!rows.length) return ''
  const gates = ['gate', 'quota', 'distill', 'review']
  const min = rows[rows.length - 1].ts
  const max = rows[0].ts
  let t0 = new Date(min).getTime() || 0
  let t1 = new Date(max).getTime() || t0 + 1
  if (t1 - t0 < 60000) t1 = t0 + 60000
  const W = 1000, laneH = 46, padL = 96, padR = 16, top = 18
  const H = top + gates.length * laneH + 26
  const X = (ts: string) => padL + (new Date(ts).getTime() - t0) / (t1 - t0) * (W - padL - padR)
  let svg = `<svg class="swim" viewBox="0 0 ${W} ${H}" style="width:100%">`
  const day = new Date(t0); day.setHours(0, 0, 0, 0)
  while (day.getTime() <= t1) {
    const gx = X(day.toISOString())
    if (gx >= padL) svg += `<line x1="${gx}" y1="${top}" x2="${gx}" y2="${H - 22}" stroke="rgba(148,163,184,.18)" stroke-dasharray="2 4"/>` +
      `<text x="${gx + 3}" y="${H - 8}" font-size="9" fill="rgba(148,163,184,.7)">${day.toISOString().slice(5, 10)}</text>`
    day.setDate(day.getDate() + 1)
  }
  gates.forEach((g, i) => {
    const y = top + i * laneH + laneH / 2
    svg += `<text x="8" y="${y + 3}" font-size="10.5" fill="currentColor">${GATE_NAME[g]}</text>` +
      `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="rgba(148,163,184,.18)"/>`
  })
  rows.forEach((r, idx) => {
    const gi = gates.indexOf(r.table)
    if (gi < 0) return
    const y = top + gi * laneH + laneH / 2
    const x = X(r.ts)
    const delay = Math.min(idx * 14, 620)
    svg += `<g class="swim-ev" data-ev="${idx}" style="cursor:pointer;animation-delay:${delay}ms">` +
      `<rect x="${x - 2.2}" y="${y - 7}" width="4.4" height="14" rx="2" fill="${GATE_COL[r.table]}"/>` +
      `<rect x="${x - 7}" y="${y - 10}" width="14" height="20" fill="transparent"/></g>`
  })
  svg += '</svg>'
  return svg
}

/** ② 按日直方图：每天各门动作数堆叠柱（title 悬停明细）。 */
export function histogramSvg(rows: LogRow[]): string {
  if (!rows.length) return ''
  const days: Record<string, Record<string, number>> = {}
  const order: string[] = []
  for (const r of rows) {
    const d = r.ts.slice(0, 10)
    if (!days[d]) { days[d] = { gate: 0, quota: 0, distill: 0, review: 0 }; order.push(d) }
    if (days[d][r.table] != null) days[d][r.table]++
  }
  order.sort()
  const W = 1000, H = 150, padL = 40, padB = 22, padT = 12
  let maxN = 1
  for (const d of order) {
    const s = days[d].gate + days[d].quota + days[d].distill + days[d].review
    if (s > maxN) maxN = s
  }
  const bw = Math.min(34, (W - padL - 10) / order.length - 4)
  let svg = `<svg viewBox="0 0 ${W} ${H}" style="width:100%">`
  svg += `<line x1="${padL}" y1="${H - padB}" x2="${W - 8}" y2="${H - padB}" stroke="rgba(148,163,184,.25)"/>`
  order.forEach((d, i) => {
    const x = padL + i * ((W - padL - 10) / order.length) + 2
    let y = H - padB
    let total = 0
    for (const g of ['gate', 'quota', 'distill', 'review']) {
      const n = days[d][g]
      if (!n) continue
      const h = n / maxN * (H - padB - padT)
      y -= h
      svg += `<rect x="${x}" y="${y}" width="${bw}" height="${h}" fill="${GATE_COL[g]}" opacity=".82"><title>${d} ${GATE_NAME[g]}：${n}</title></rect>`
      total += n
    }
    if (order.length <= 14 || i % Math.ceil(order.length / 14) === 0)
      svg += `<text x="${x + bw / 2}" y="${H - 8}" font-size="9" text-anchor="middle" fill="rgba(148,163,184,.8)">${d.slice(5)}</text>` +
        `<text x="${x + bw / 2}" y="${y - 3}" font-size="9" text-anchor="middle" fill="rgba(148,163,184,.6)">${total}</text>`
  })
  svg += '</svg>'
  return svg
}
