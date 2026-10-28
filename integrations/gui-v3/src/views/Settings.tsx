import { useEffect, useState, type ReactNode } from 'react'
import { Ico } from '../lib/icons'
import { useEco } from '../lib/eco'
import { applyAppearance, applyWallpaper, getAppearance, getWallpaper, WALLPAPERS, type Appearance, type WallpaperId } from '../lib/palette'
import { copyDiagnostic, V03_URL } from '../lib/sys'
import { ui, useUi, type AnimLevel } from '../lib/ui'
import { APP, AppGlyph, Switch } from '../components/kit'

/** 设置 App：iPadOS 双栏（左侧分组列表 + 右侧详情），收纳 v2.2 状态栏/顶栏里的全部偏好项。 */

type Sec = 'wallpaper' | 'display' | 'motion' | 'notify' | 'lock' | 'about' | 'help' | 'versions'

const SECS: Array<{ id: Sec; name: string; ico: string; color: string; group: number }> = [
  { id: 'wallpaper', name: '墙纸', ico: 'photo', color: '#32ade6', group: 0 },
  { id: 'display', name: '显示与亮度', ico: 'sun', color: '#0a84ff', group: 0 },
  { id: 'motion', name: '动效', ico: 'sparkle', color: '#5e5ce6', group: 0 },
  { id: 'notify', name: '通知', ico: 'bell', color: '#ff3b30', group: 1 },
  { id: 'lock', name: '锁定屏幕', ico: 'lock', color: '#636366', group: 1 },
  { id: 'about', name: '通用 · 关于本机', ico: 'gear', color: '#8e8e93', group: 2 },
  { id: 'help', name: '帮助与快捷键', ico: 'help', color: '#30d158', group: 2 },
  { id: 'versions', name: '其他版本', ico: 'compass', color: '#ff9f0a', group: 2 },
]

function Group({ title, foot, children }: { title?: string; foot?: ReactNode; children: ReactNode }) {
  return (
    <section className="ios-group">
      {title && <h4>{title}</h4>}
      <div className="ios-list">{children}</div>
      {foot && <p className="ios-foot">{foot}</p>}
    </section>
  )
}

function Row({ label, value, children, onClick, chevron, mono }: { label: ReactNode; value?: ReactNode; children?: ReactNode; onClick?: () => void; chevron?: boolean; mono?: boolean }) {
  return (
    <div className={'ios-row' + (onClick ? ' link' : '')} onClick={onClick}>
      <span>{label}</span>
      {value != null && <span className={'ios-val' + (mono ? ' mono' : '')}>{value}</span>}
      {children}
      {chevron && <Ico name="chev" size={13} className="ios-chev" />}
    </div>
  )
}

const ANIM: Array<{ v: AnimLevel; name: string; desc: string }> = [
  { v: 'low', name: '轻度', desc: '仅淡入淡出，关闭启动缩放与光斑漂移' },
  { v: 'medium', name: '中度', desc: '过渡 + 度量 + 图表' },
  { v: 'full', name: '满载', desc: '全部动效（推荐）' },
]

export default function Settings() {
  const s = useUi()
  const { meta, bell } = useEco()
  const [sec, setSec] = useState<Sec>('display')
  const [appearance, setAppearance] = useState<Appearance>(getAppearance)
  const [wp, setWp] = useState<WallpaperId>(getWallpaper)
  const [lockBoot, setLockBoot] = useState(ui.lockOnBoot)

  useEffect(() => {
    if (!meta) return
    ui.setExportRows([
      { k: '生态版本', v: meta.version }, { k: '评分模型', v: meta.score_model }, { k: '数据快照', v: meta.snapshot },
      { k: '数据根', v: meta.root }, { k: '单写入方今日运行', v: meta.writer_today_ran ? '是' : '否' },
      { k: '检索 CLI', v: meta.search_cli_ready ? '就绪' : '不可用' },
    ])
  }, [meta])
  useEffect(() => () => ui.setExportRows([]), [])

  const setApp = (a: Appearance) => { applyAppearance(a); setAppearance(a) }
  const cur = SECS.find(x => x.id === sec)!

  return (
    <div className="settings view-enter">
      <aside className="set-side">
        <div className="vh">设置</div>
        <div className="ios-list set-profile" onClick={() => setSec('about')}>
          <AppGlyph app={APP.dashboard} size={58} />
          <div><b>记忆生态 · 观测舱</b><span>v3.2.0 液态玻璃 · 只读观测 · 生态 {meta?.version ?? '—'}</span></div>
          <Ico name="chev" size={13} className="ios-chev" />
        </div>
        {[0, 1, 2].map(g => (
          <div key={g} className="ios-list set-nav">
            {SECS.filter(x => x.group === g).map(x => (
              <button key={x.id} className={'ios-row link' + (sec === x.id ? ' sel' : '')} onClick={() => setSec(x.id)}>
                <span className="set-ico" style={{ background: x.color }}><Ico name={x.ico} size={15} /></span>
                <span className="set-name">{x.name}</span>
                {x.id === 'notify' && bell.length > 0 && <i className="set-badge">{bell.length}</i>}
                <Ico name="chev" size={13} className="ios-chev" />
              </button>
            ))}
          </div>
        ))}
      </aside>

      <div className="set-detail" key={sec}>
        <div className="set-detail-title">{cur.name}</div>

        {sec === 'wallpaper' && (
          <Group title="选取墙纸" foot="墙纸同时作用于主屏幕、锁定屏幕与应用背后的液态玻璃折射。">
            <div className="wp-grid">
              {WALLPAPERS.map(w => (
                <button key={w.id} className={'wp-card' + (wp === w.id ? ' on' : '')} onClick={() => { applyWallpaper(w.id); setWp(w.id) }}>
                  <span className="wp-thumb" style={{ background: w.preview }}>
                    <span className="wp-thumb-time">9:41</span>
                    {wp === w.id && <span className="wp-check"><Ico name="check" size={14} /></span>}
                  </span>
                  <span>{w.name}</span>
                </button>
              ))}
            </div>
          </Group>
        )}

        {sec === 'display' && (
          <>
            <Group title="外观">
              <div className="appearance-pick">
                {(['light', 'dark'] as const).map(a => (
                  <button key={a} className={'ap-card' + (appearance === a ? ' on' : '')} onClick={() => setApp(a)}>
                    <span className={'ap-thumb ' + a}><i /><i /><i /></span>
                    <span>{a === 'light' ? '浅色' : '深色'}</span>
                    <span className={'ap-radio' + (appearance === a ? ' on' : '')}><Ico name="check" size={11} /></span>
                  </button>
                ))}
              </div>
              <Row label="自动（跟随系统）"><Switch on={appearance === 'auto'} onChange={v => setApp(v ? 'auto' : (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'))} /></Row>
            </Group>
            <Group title="文字大小" foot="作用于全部应用的正文字号（标题按比例缩放）。">
              <div className="ios-row font-row">
                <span className="fs-a small">A</span>
                <input type="range" min={0} max={3} step={1} value={s.fontStep} onChange={e => ui.setFontStep(Number(e.target.value))} className="ios-range" />
                <span className="fs-a big">A</span>
              </div>
            </Group>
          </>
        )}

        {sec === 'motion' && (
          <Group title="动画强度" foot="3D 视图（技能星图 / 血缘谱系 / 时间线星系）的帧率可在灵动岛展开后查看。">
            {ANIM.map(a => (
              <Row key={a.v} label={<span className="two-line"><b>{a.name}</b><small>{a.desc}</small></span>} onClick={() => ui.setAnimLevel(a.v)}>
                {s.animLevel === a.v && <Ico name="check" size={16} className="ios-checkmark" />}
              </Row>
            ))}
          </Group>
        )}

        {sec === 'notify' && (
          <>
            <Group title="告警来源" foot="五源聚合，90 秒轮询：cron 近 7 天失败 / L1 记忆越线 / 待处理候选 / 体检报告 P0 / 技能悬空引用。">
              {bell.length === 0 && <Row label="暂无告警" value="全部正常" />}
              {bell.map(b => <Row key={b.key} label={<span className="two-line"><b>{b.sev === 'bad' ? '严重' : '提醒'}</b><small>{b.text}</small></span>} chevron onClick={() => ui.navigate(b.goto)} />)}
            </Group>
            <Group>
              <Row label="打开通知中心" chevron onClick={() => ui.setOverlay('nc')} />
            </Group>
          </>
        )}

        {sec === 'lock' && (
          <Group foot="锁屏显示时间、体检 / 水位仪表与最近通知；单击、上滑或按任意键解锁。">
            <Row label="启动时显示锁定屏幕"><Switch on={lockBoot} onChange={v => { ui.setLockOnBoot(v); setLockBoot(v) }} /></Row>
            <Row label="立即锁定" chevron onClick={() => ui.lock()} />
          </Group>
        )}

        {sec === 'about' && (
          <>
            <Group title="关于本机">
              <Row label="名称" value="记忆生态 · 观测舱 v3.2.0" />
              <Row label="观测舱版本" value="v3.2.0（对齐核心 3.2.0）" />
              <Row label="生态版本" value={meta?.version ?? '—'} />
              <Row label="评分模型" value={meta?.score_model ?? '—'} />
              <Row label="数据快照" value={meta?.snapshot?.replace('T', ' ') ?? '—'} />
              <Row label="数据根" value={meta?.root ?? '—'} mono />
              <Row label="单写入方（Hermes cron）" value={meta?.writer_today_ran ? '今日已运行' : '今日未运行'} />
              <Row label="检索 CLI" value={meta?.search_cli_ready ? '就绪' : '不可用'} />
              {Object.entries(meta?.locks ?? {}).map(([k, l]) => (
                <Row key={k} label={'锁文件 ' + k} value={l.age_hours != null && l.age_hours < 2 ? '活动中' : '陈旧残留'} />
              ))}
            </Group>
            <Group foot="诊断包含版本、数据根、水位与告警数，可粘贴给维护者。">
              <Row label="拷贝诊断信息" chevron onClick={() => void copyDiagnostic(s.bellCount)} />
            </Group>
          </>
        )}

        {sec === 'help' && (
          <Group>
            <Row label="新功能介绍" chevron onClick={() => ui.setGuideStep(0)} />
            <Row label="快捷键" chevron onClick={() => ui.setOverlay('kbd')} />
            <Row label="控制中心" value="点状态栏右侧" />
            <Row label="通知中心" value="点状态栏左侧时间" />
            <Row label="多任务" value="双击 Home 条 / Ctrl+↑" />
          </Group>
        )}

        {sec === 'versions' && (
          <Group foot="四套前端共用同一个观测舱服务（8788）的只读接口；写通道只在 v0.3。">
            <Row label={<span className="two-line"><b>v0.3 观测舱</b><small>原生 JS · 写操作 + 确认闸门</small></span>} chevron onClick={() => window.open(V03_URL, '_blank')} />
            <Row label={<span className="two-line"><b>gui-v2 预览</b><small>React + three · /v2/</small></span>} chevron onClick={() => window.open('/v2/', '_blank')} />
            <Row label={<span className="two-line"><b>v2.2 Sentri</b><small>设计系统版 · /v2.2/</small></span>} chevron onClick={() => window.open('/v2.2/', '_blank')} />
          </Group>
        )}
      </div>
    </div>
  )
}
