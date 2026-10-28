/** 评分历史折线（自 v1 dashboard.trendSvg 移植）：每点标数值，同模型才连线语义由调用方保证。 */
import type { TrendPoint } from './api'

export function trendSvg(hist: TrendPoint[], accentVar = 'var(--accent)'): string {
  if (!hist.length) return ''
  const w = 560, h = 110, pad = 26
  const min = Math.min(...hist.map(x => x.score)) - 8
  const max = Math.max(...hist.map(x => x.score)) + 8
  const X = (i: number) => pad + i * (w - pad * 2) / Math.max(1, hist.length - 1)
  const Y = (s: number) => h - 24 - (s - min) / Math.max(1, max - min) * (h - 46)
  const pts = hist.map((x, i) => `${X(i).toFixed(1)},${Y(x.score).toFixed(1)}`).join(' ')
  const dots = hist.map((x, i) =>
    `<circle cx="${X(i)}" cy="${Y(x.score)}" r="3.4" fill="${accentVar}"><title>${x.time} · ${x.score} 分（${x.model}）</title></circle>` +
    `<text x="${X(i)}" y="${(Y(x.score) - 7).toFixed(1)}" text-anchor="middle" font-size="9" fill="rgba(148,163,184,.9)">${x.score}</text>`).join('')
  const labels = hist.map((x, i) =>
    (i % Math.ceil(hist.length / 5) === 0 || i === hist.length - 1)
      ? `<text x="${X(i)}" y="${h - 6}" text-anchor="middle" font-size="9" fill="rgba(148,163,184,.8)">${x.time.slice(5, 10)}</text>`
      : '').join('')
  const lastModel = hist[hist.length - 1].model
  const mixed = hist.some(x => x.model !== lastModel)
  return `<svg class="trend" viewBox="0 0 ${w} ${h}" style="width:100%">` +
    `<line x1="${pad}" y1="${h - 24}" x2="${w - pad}" y2="${h - 24}" stroke="rgba(148,163,184,.25)"/>` +
    `<polyline class="trend-line" points="${pts}" fill="none" stroke="${accentVar}" stroke-width="1.6"/>` +
    dots + labels + '</svg>' +
    `<div class="trendcap">近 ${hist.length} 条 · 每点标数值 · 全部为模型 <b>${lastModel}</b>${mixed ? '（含跨模型记录——仅同模型段连线）' : ''}</div>`
}
