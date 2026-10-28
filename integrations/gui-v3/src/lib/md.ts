/** 极简 Markdown 渲染（自 v1 core.md 原样移植）：先转义再内联，
 * 支持 标题/列表/表格/引用/hr/行内代码/加粗/链接。输出 HTML 字符串。 */

const esc = (s: unknown): string =>
  String(s ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c] ?? c))

const inline = (s: string): string =>
  esc(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '$1')

export function md(src: unknown): string {
  const lines = String(src ?? '').replace(/\r/g, '').split('\n')
  let out = ''
  let i = 0
  let para: string[] = []
  const flush = () => {
    if (para.length) { out += `<p>${inline(para.join(' '))}</p>`; para = [] }
  }
  while (i < lines.length) {
    const L = lines[i]
    if (/^\|/.test(L)) {
      flush()
      const rows: string[] = []
      while (i < lines.length && /^\|/.test(lines[i])) rows.push(lines[i++])
      if (rows.length >= 2 && /^[\s|:-]+$/.test(rows[1])) rows.splice(1, 1)
      let tbl = '<table>'
      rows.forEach((r, k) => {
        const cells = r.replace(/^\||\|$/g, '').split('|').map(c => inline(c.trim()))
        tbl += '<tr>' + cells.map(c => (k === 0 ? `<th>${c}</th>` : `<td>${c}</td>`)).join('') + '</tr>'
      })
      out += tbl + '</table>'
      continue
    }
    const hm = L.match(/^(#{1,4})\s+(.*)/)
    if (hm) { flush(); out += `<h${hm[1].length}>${inline(hm[2])}</h${hm[1].length}>`; ++i; continue }
    if (/^(-{3,}|\*{3,})\s*$/.test(L)) { flush(); out += '<hr>'; ++i; continue }
    if (/^[-*]\s+/.test(L)) {
      flush()
      const items: string[] = []
      while (i < lines.length && /^[-*]\s+/.test(lines[i]))
        items.push('<li>' + inline(lines[i++].replace(/^[-*]\s+/, '')) + '</li>')
      out += `<ul>${items.join('')}</ul>`
      continue
    }
    if (/^>/.test(L)) { flush(); out += `<p class="md-dim">${inline(L.replace(/^>\s?/, ''))}</p>`; ++i; continue }
    if (!L.trim()) { flush(); ++i; continue }
    para.push(L); ++i
  }
  flush()
  return out
}

/** 报告渲染 + P0 行告警样式（自 v1 health.mdP0 移植——DOM 后处理含 P0 的行）。 */
export function mdP0(src: unknown): string {
  const div = document.createElement('div')
  div.innerHTML = md(src)
  div.querySelectorAll('td, li').forEach(el => {
    const t = el.textContent ?? ''
    if (/\bP0\b/.test(t) && t.trim().length > 6) {
      el.classList.add('p0-row')
      if (el.tagName === 'TD' && t.trim().startsWith('P0')) el.classList.add('p0-cell')
    }
  })
  div.querySelectorAll('p').forEach(el => {
    const t = (el.textContent ?? '').trim()
    if (/^.{0,4}P0[::）)]?/.test(t) && t.length > 8) el.classList.add('p0-row')
  })
  return div.innerHTML
}

/** 检索命中高亮（自 v1 core.hl 移植：先转义再包 mark，顺序不能反）。 */
export function hl(text: unknown, terms: string[]): string {
  let out = esc(text)
  const list = (terms ?? []).filter(t => t && String(t).trim().length >= 2)
    .map(t => String(t).trim()).sort((a, b) => b.length - a.length).slice(0, 8)
  for (const t of list) {
    const re = new RegExp('(' + t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gi')
    out = out.replace(re, '<mark class="hl">$1</mark>')
  }
  return out
}
