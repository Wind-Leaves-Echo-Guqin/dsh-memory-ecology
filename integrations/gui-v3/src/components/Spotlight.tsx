import { useEffect, useMemo, useRef, useState } from 'react'
import { fetchSearch, invalidate, type SearchHit, type SearchResult } from '../lib/api'
import { Ico } from '../lib/icons'
import { hl } from '../lib/md'
import { applyAppearance, applyWallpaper, WALLPAPERS } from '../lib/palette'
import { copyDiagnostic, V03_URL } from '../lib/sys'
import { copyText, exportView, ui, useUi, type ViewId } from '../lib/ui'
import { APP, APPS, AppGlyph, Glass, Segmented } from './kit'

/** Spotlight = v2.2 顶栏三模式检索 + Ctrl+K 命令面板合一：
 * 输入即筛「应用 / 操作」，Enter 走只读 CLI 检索（经验 / 报错 / 生态，粘贴报错自动切报错模式）。 */

type Mode = 'note' | 'error' | 'eco'
const MODE_HINT: Record<Mode, string> = {
  note: '经验检索：搜经验笔记本的关键词',
  error: '报错检索：粘贴报错原文，自动定位根因经验',
  eco: '生态检索：搜技能库与工作流',
}
const TRACEBACK_RE = /traceback|exception|error:|错误|失败|raise\s+\w+/i
const HIST = 'eco_v23_search_hist'

type HistEntry = { q: string; kind: Mode }
const loadHist = (): HistEntry[] => { try { return JSON.parse(localStorage.getItem(HIST) ?? '[]') as HistEntry[] } catch { return [] } }
const pushHist = (q: string, kind: Mode) => {
  const list = loadHist().filter(x => x.q !== q)
  list.unshift({ q, kind })
  try { localStorage.setItem(HIST, JSON.stringify(list.slice(0, 20))) } catch { /* */ }
}

type Cmd = { label: string; ico: string; kw: string; run: () => void }

function useCommands(view: ViewId, bell: number): Cmd[] {
  return useMemo(() => {
    const list: Cmd[] = [
      { label: '刷新快照', ico: 'refresh', kw: 'refresh 刷新 重新读取', run: () => { invalidate(); ui.bumpRefresh(); ui.toast('已重新读取快照', true) } },
      { label: '通知中心（告警）', ico: 'bell', kw: '告警 铃铛 通知 bell', run: () => ui.setOverlay('nc') },
      { label: '控制中心', ico: 'grid', kw: '控制 cc control', run: () => ui.setOverlay('cc') },
      { label: '多任务', ico: 'grid', kw: '多任务 切换 switcher 最近', run: () => ui.setOverlay('switcher') },
      { label: '导出当前应用 · JSON', ico: 'share', kw: '导出 export json', run: () => exportView('json', view) },
      { label: '导出当前应用 · Markdown', ico: 'copy', kw: '导出 export md markdown', run: () => exportView('md', view) },
      { label: '导出当前应用 · 纯文本', ico: 'text', kw: '导出 export text 文本', run: () => exportView('text', view) },
      { label: '巡航 · 轮播应用', ico: 'play', kw: '巡航 cruise 轮播', run: () => ui.setCruise('rotate') },
      { label: '待机 · StandBy 大屏', ico: 'standby', kw: '待机 standby 大屏 巡航', run: () => ui.setCruise('big') },
      { label: '深色外观', ico: 'moon', kw: '深色 暗色 dark 外观', run: () => applyAppearance('dark') },
      { label: '浅色外观', ico: 'sun', kw: '浅色 亮色 light 外观', run: () => applyAppearance('light') },
      { label: '跟随系统外观', ico: 'sun', kw: '自动 auto 外观 系统', run: () => applyAppearance('auto') },
      { label: '动效 · 轻度', ico: 'sparkle', kw: '动画 动效 轻 low', run: () => ui.setAnimLevel('low') },
      { label: '动效 · 满载', ico: 'sparkle', kw: '动画 动效 满 full', run: () => ui.setAnimLevel('full') },
      { label: '拷贝诊断信息', ico: 'diag', kw: '诊断 diag', run: () => void copyDiagnostic(bell) },
      { label: '新功能介绍', ico: 'help', kw: '引导 guide 帮助 介绍', run: () => ui.setGuideStep(0) },
      { label: '快捷键', ico: 'kbd', kw: '快捷键 键盘 kbd shortcut', run: () => ui.setOverlay('kbd') },
      { label: '锁定屏幕', ico: 'lock', kw: '锁定 锁屏 lock', run: () => ui.lock() },
      { label: '急救箱（v0.3）', ico: 'firstaid', kw: '急救 修复 firstaid', run: () => window.open(V03_URL + '#/firstaid', '_blank') },
      { label: '在 v0.3 观测舱打开（写操作）', ico: 'external', kw: 'v0.3 经典 写 classic', run: () => window.open(V03_URL, '_blank') },
    ]
    for (const w of WALLPAPERS) list.push({ label: '墙纸 · ' + w.name, ico: 'photo', kw: '墙纸 壁纸 wallpaper ' + w.name, run: () => applyWallpaper(w.id) })
    return list
  }, [view, bell])
}

function HitRow({ h, terms, onJump }: { h: SearchHit; terms: string[]; onJump: (id: string) => void }) {
  const id = h.id ?? ''
  const clickable = id.startsWith('exp-')
  return (
    <div className={'spot-hit' + (clickable ? ' click' : '')} onClick={clickable ? () => onJump(id) : undefined}>
      <div className="sr-line">
        {h.type && <span className="tag">{h.type}</span>}
        {h.status && <span className="tag">{h.status}</span>}
        <span className="sr-t" dangerouslySetInnerHTML={{ __html: hl(h.trigger || h.name || h.id, terms) }} />
        {h.indeg != null && <span className="faint">被引{h.indeg}</span>}
        {clickable && <Ico name="chev" size={13} className="spot-chev" />}
      </div>
      {h.evidence && <div className="sr-d mono" dangerouslySetInnerHTML={{ __html: hl(h.evidence.slice(0, 140), terms) }} />}
      {h.desc && <div className="sr-d" dangerouslySetInnerHTML={{ __html: hl(h.desc.slice(0, 140), terms) }} />}
      {h.sections && <div className="sr-d faint" dangerouslySetInnerHTML={{ __html: hl(h.sections.slice(0, 120), terms) }} />}
    </div>
  )
}

export function Spotlight() {
  const s = useUi()
  const [q, setQ] = useState('')
  const [mode, setMode] = useState<Mode>('note')
  const [res, setRes] = useState<null | { type: 'loading' | 'result' | 'error'; result?: SearchResult; terms?: string[]; msg?: string }>(null)
  const [, force] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const histIdx = useRef(-1)
  const cmds = useCommands(s.view, s.bellCount)
  const open = s.overlay === 'spot'

  useEffect(() => {
    if (open) { setQ(''); setRes(null); histIdx.current = -1; window.setTimeout(() => inputRef.current?.focus(), 40) }
  }, [open])

  if (!open) return null
  const query = q.trim().toLowerCase()
  const apps = query ? APPS.filter(a => (a.name + a.hint + a.id).toLowerCase().includes(query)) : []
  const actions = query ? cmds.filter(c => (c.label + c.kw).toLowerCase().includes(query)).slice(0, 6) : []
  const suggestions: ViewId[] = [...s.recents, ...APPS.map(a => a.id)].filter((v, i, arr) => arr.indexOf(v) === i).slice(0, 8)
  const hist = loadHist()

  const doSearch = async (qArg?: string, modeArg?: Mode) => {
    const text = (qArg ?? q).trim()
    const m = modeArg ?? mode
    if (!text) return
    setQ(text)
    pushHist(text, m)
    setRes({ type: 'loading' })
    const terms = [text, ...text.split(/[\s,;，；、()（）[\]{}<>"'`]+/)]
    try {
      setRes({ type: 'result', result: await fetchSearch(m, text), terms })
    } catch (e) {
      setRes({ type: 'error', msg: e instanceof Error ? e.message : String(e) })
    }
  }
  const close = () => ui.closeOverlays()
  const openApp = (id: ViewId) => { close(); ui.navigate(id) }

  return (
    <div className="center-mask spot-mask" onClick={close}>
      <div className="spot" onClick={e => e.stopPropagation()}>
        <Glass className="spot-field">
          <Ico name="search" size={20} />
          <input ref={inputRef} value={q} placeholder="搜索应用、操作，或回车检索经验 / 报错 / 生态" autoComplete="off" spellCheck={false}
            onChange={e => {
              const v = e.target.value
              setQ(v); setRes(null)
              if (mode !== 'error' && TRACEBACK_RE.test(v)) { setMode('error'); ui.toast('检测到报错文本，已切到报错模式', true) }
            }}
            onKeyDown={e => {
              if (e.key === 'Enter') void doSearch()
              else if ((e.key === 'ArrowUp' || e.key === 'ArrowDown') && hist.length) {
                e.preventDefault()
                const dir = e.key === 'ArrowUp' ? 1 : -1
                histIdx.current = Math.max(-1, Math.min(hist.length - 1, histIdx.current + dir))
                setQ(histIdx.current < 0 ? '' : hist[histIdx.current].q)
              }
            }} />
          {q && <button className="spot-clear" onClick={() => { setQ(''); setRes(null); inputRef.current?.focus() }} title="清除"><Ico name="close" size={11} /></button>}
        </Glass>
        <div className="spot-mode">
          <Segmented value={mode} onChange={m => { setMode(m); ui.toast(MODE_HINT[m], true); if (q.trim()) void doSearch(undefined, m) }}
            options={[{ v: 'note', label: '经验', title: MODE_HINT.note }, { v: 'error', label: '报错', title: MODE_HINT.error }, { v: 'eco', label: '生态', title: MODE_HINT.eco }]} />
        </div>

        <Glass className="spot-panel">
          {!query && !res && (
            <>
              <div className="spot-sec">Siri 建议</div>
              <div className="spot-apps">
                {suggestions.map(id => (
                  <button key={id} className="spot-app" onClick={() => openApp(id)} title={APP[id].hint}>
                    <AppGlyph app={APP[id]} size={54} /><span>{APP[id].name}</span>
                  </button>
                ))}
              </div>
              <div className="spot-sec">最近检索 {hist.length > 0 && <a onClick={() => { try { localStorage.removeItem(HIST) } catch { /* */ } force(x => x + 1) }}>清除</a>}</div>
              {hist.length === 0 && <div className="spot-empty">暂无检索历史——输入关键词后按回车</div>}
              {hist.slice(0, 5).map((h, i) => (
                <button key={i} className="spot-row" onClick={() => { setMode(h.kind); void doSearch(h.q, h.kind) }}>
                  <Ico name="search" size={14} /><span className="ellipsis">{h.q}</span><span className="faint">{{ note: '经验', error: '报错', eco: '生态' }[h.kind]}</span>
                </button>
              ))}
            </>
          )}

          {query && !res && (
            <>
              {apps.length > 0 && <div className="spot-sec">应用</div>}
              {apps.map(a => (
                <button key={a.id} className="spot-row big" onClick={() => openApp(a.id)}>
                  <AppGlyph app={a} size={34} /><span><b>{a.name}</b><small>{a.hint}</small></span>
                </button>
              ))}
              {actions.length > 0 && <div className="spot-sec">操作</div>}
              {actions.map(c => (
                <button key={c.label} className="spot-row" onClick={() => { close(); c.run() }}>
                  <span className="spot-act-ico"><Ico name={c.ico} size={14} /></span><span>{c.label}</span>
                </button>
              ))}
              <div className="spot-sec">检索</div>
              <button className="spot-row" onClick={() => void doSearch()}>
                <span className="spot-act-ico blue"><Ico name="search" size={14} /></span>
                <span>在{{ note: '经验笔记本', error: '报错根因', eco: '技能与工作流' }[mode]}中检索「{q.trim()}」</span>
                <kbd className="kbd-key">回车</kbd>
              </button>
            </>
          )}

          {res?.type === 'loading' && <div className="spot-empty">检索中…（只读 CLI 子进程）</div>}
          {res?.type === 'error' && <div className="alarm-inline">检索失败（fail-open，不影响其他功能）：{res.msg}</div>}
          {res?.type === 'result' && res.result && (
            <>
              <div className="spot-sec">
                {res.result.hits?.length ? `${res.result.hits.length} 条结果` : '结果'}
                {res.result.exc && res.result.exc.length > 0 && <span className="faint"> · 异常类：{res.result.exc.join(', ')}</span>}
                <a onClick={() => void copyText(JSON.stringify(res.result, null, 2), '检索结果已拷贝')}>拷贝结果</a>
              </div>
              {res.result.hits?.length
                ? res.result.hits.map((h, i) => <HitRow key={i} h={h} terms={res.terms ?? []} onJump={id => { close(); ui.navigate('experiences', { kind: 'exp', id }) }} />)
                : res.result.raw
                  ? <pre className="mono sr-raw">{res.result.raw.slice(0, 2000)}</pre>
                  : <div className="spot-empty">没有命中——试试更短的关键词，或切换检索模式。</div>}
            </>
          )}
        </Glass>
      </div>
    </div>
  )
}
