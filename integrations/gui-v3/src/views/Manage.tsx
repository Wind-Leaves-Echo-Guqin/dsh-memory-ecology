import { useEffect, useState } from 'react'
import {
  fetchCirclesConfig, fetchCoreMeta, runAction, type CircleConfig, type CoreMeta,
} from '../lib/api_v3_2_0'
import { useAsync } from '../hooks/useAsync'
import { copyText, ui, useUi, type ExportRow } from '../lib/ui'

/** G4 · 圈管理 —— 编辑 `circles.json`（路由前缀 / 预算 / 只读开关）。
 *
 * **写操作不直接改文件**：提交后转发给核心 CLI（`circles-write`），由核心
 * 校验 + 备份 + 原子写，并立刻重建目录视图。所以 dry 阶段只做本地 schema 校验
 * （不写盘），run 阶段才真正落盘。
 *
 * 这条纪律的理由：GUI 要是自己能写 circles.json，就等于有了第二个写者，
 * 校验/备份/一致性全部要重新实现一遍，且必然与核心漂移。
 */

const SAMPLE = `{
  "global_budget_chars": 600,
  "circles": [
    {
      "id": "my-project",
      "display_name": "我的项目",
      "match": { "cwd_prefix": ["D:/work/my-project"] },
      "budget_chars": 700,
      "catalog_limit": 20,
      "readonly": false,
      "sources": []
    }
  ]
}`

export default function ManageView() {
  const s = useUi()
  const { data: cfg, err } = useAsync<CircleConfig>(() => fetchCirclesConfig(), [s.refreshTick])
  const { data: meta } = useAsync<CoreMeta>(() => fetchCoreMeta(), [s.refreshTick])
  const [text, setText] = useState('')
  const [hint, setHint] = useState<{ ok: boolean; msg: string } | null>(null)
  const [busy, setBusy] = useState(false)

  // 首次拿到配置后填进编辑区；后续刷新不覆盖用户正在编辑的内容
  useEffect(() => {
    if (!cfg || text) return
    setText(JSON.stringify({
      global_budget_chars: cfg.global_budget_chars ?? 600,
      circles: cfg.circles ?? [],
    }, null, 2))
  }, [cfg, text])

  useEffect(() => {
    if (!cfg) return
    const rows: ExportRow[] = [
      { k: '配置文件', v: cfg.path ?? '—' },
      { k: '存在', v: cfg.exists ? '是' : '否（当前按目录名自动划分）' },
      { k: '改于', v: (cfg.mtime ?? '—').replace('T', ' ') },
    ]
    ui.setExportRows(rows)
  }, [cfg])
  useEffect(() => () => ui.setExportRows([]), [])

  const submit = async () => {
    let parsed: unknown
    try {
      parsed = JSON.parse(text)
    } catch (e) {
      setHint({ ok: false, msg: 'JSON 非法：' + (e instanceof Error ? e.message : String(e)) })
      return
    }
    setBusy(true)
    try {
      // 先 dry（本地 schema 校验，不写盘）
      const dry = await runAction('circle_write', 'dry', { json: JSON.stringify(parsed) })
      if (!dry.ok) {
        setHint({ ok: false, msg: dry.error || dry.output || '校验未通过' })
        return
      }
      // 校验过了 → 弹确认闸门（strong），确认后 run
      ui.openDrawer({
        title: '写入圈配置',
        sub: '改动会影响：哪些启动目录归到哪个圈、每圈目录预算。下一次会话装配时生效。',
        node: (
          <div className="eco-kv">
            <div><span>预演结果</span><b className="ok">校验通过（未写入）</b></div>
            <div><span>执行方式</span><b className="mono">python -m memory_ecology circles-write</b></div>
            <div><span>回滚</span><b>核心写前自动备份 circles.json.bak-改圈前-*</b></div>
            <div><span>风险</span><b>改路由会让某些记忆换圈可见；改只读会拦住该圈投递</b></div>
            <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
              <button className="btn strong" disabled={busy}
                onClick={async () => {
                  setBusy(true)
                  try {
                    const r = await runAction('circle_write', 'run', { json: JSON.stringify(parsed) })
                    setHint({ ok: r.ok, msg: r.ok ? '已写入并重建目录视图' : (r.error || r.output || '失败') })
                    ui.toast(r.ok ? '圈配置已应用' : '写入失败', r.ok)
                    if (r.ok) { ui.closeDrawer(); ui.bumpRefresh() }
                  } finally { setBusy(false) }
                }}>确认写入</button>
            </div>
          </div>
        ),
      })
    } finally {
      setBusy(false)
    }
  }

  if (err) return <div className="empty view-enter">圈配置读取失败：{err}</div>

  return (
    <div className="dash view-enter">
      <div className="vh">
        圈管理
        <small>写走核心 CLI（校验 + 备份 + 原子写 + 立刻重建目录视图）· GUI 不直接改文件</small>
      </div>

      {cfg?.exists === false ? (
        <div className="eco-alarm" style={{ background: 'rgba(255,214,10,.12)', borderColor: 'rgba(255,214,10,.4)', color: '#ffd60a' }}>
          <b>还没有 circles.json</b> —— 现在所有圈都按「启动目录末级名」自动划分
          （注意：自动划分**不是**路由配置，未声明的目录只会看到全局记忆）。
          存一份配置后就会按前缀路由。
        </div>
      ) : null}

      <section className="panel">
        <div className="row" style={{ marginBottom: 10 }}>
          <span className="chip">配置 <b className="mono ellipsis" style={{ maxWidth: 420 }}>{cfg?.path ?? '—'}</b></span>
          {cfg?.mtime ? <span className="chip">改于 <b>{cfg.mtime.replace('T', ' ')}</b></span> : null}
          <span className="chip">核心 <b>{meta?.core_ok ? '可达' : '不可达'}</b></span>
        </div>
        <textarea
          className="eco-json"
          spellCheck={false}
          value={text}
          placeholder={SAMPLE}
          onChange={e => { setText(e.target.value); setHint(null) }}
        />
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn" disabled={busy} onClick={() => void submit()}>
            {busy ? '处理中…' : '校验并写入'}
          </button>
          <button className="ghost-btn" onClick={() => { setText(''); ui.bumpRefresh() }}>
            还原为磁盘内容
          </button>
          <button className="ghost-btn" onClick={() => void copyText(text, '配置已复制')}>
            复制
          </button>
          {hint ? (
            <span className={'tag ' + (hint.ok ? 'ok' : 'bad')} style={{ maxWidth: 520 }}>
              {hint.msg.slice(0, 160)}
            </span>
          ) : null}
        </div>
      </section>

      <section className="panel eco-note">
        <b>字段说明</b>
        <div className="faint">
          <span className="mono">id</span> 圈名（小写，字母数字 ._- ，≤40）·
          <span className="mono"> display_name</span> 显示名 ·
          <span className="mono"> match.cwd_prefix[]</span> 匹配的启动目录前缀（<b>最长前缀胜</b>，按路径段边界）·
          <span className="mono"> budget_chars</span> 该圈目录字符预算（60..20000）·
          <span className="mono"> catalog_limit</span> 该圈目录条数上限 ·
          <span className="mono"> readonly</span> 只读圈（拒绝投递）·
          <span className="mono"> sources[]</span> 该圈可挂载哪些记忆包
          <br /><br />
          <b>路由规则</b>：cwd 前缀最长匹配 → 最近祖先圈 → 都不中则 global。
          Windows 下忽略大小写（否则同一个项目会因为 shell 起法不同裂成两个圈）。
          <b>没声明就不猜</b> —— 按目录名判圈会让无关项目看到别的圈的机密（已修的漏洞）。
        </div>
      </section>
    </div>
  )
}
