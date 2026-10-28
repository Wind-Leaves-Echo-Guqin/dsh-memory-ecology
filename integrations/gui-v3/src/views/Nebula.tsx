import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js'
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js'
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js'
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js'
import { fetchSkillMap, fateColor, hash01, type Skill, type SkillMap } from '../lib/skillmap'

/** 技能星系图（球状星云载体 · 用户指定方向）：一个 skill = 一个小旋星系，
 * 血缘连通分量聚成星团/星系群。粒子数 ∝ 被引数与体量，色按 fate；
 * 悬停浮标签、点选高亮血缘连线＋信息卡。数据来自观测舱既有 API（只读）。 */

const VERT = /* glsl */ `
attribute float aSize;
attribute float aPhase;
uniform float uTime;
uniform float uPixelRatio;
varying vec3 vColor;
varying float vTwinkle;
void main() {
  vColor = color;
  vTwinkle = 0.75 + 0.25 * sin(uTime * 0.8 + aPhase);
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  if (-mv.z < 1.0) gl_PointSize = 0.0;  // 近相机防护（审查 m1：重写时曾回退，恢复）
  else gl_PointSize = min(aSize * vTwinkle * uPixelRatio * (900.0 / -mv.z), 140.0);
  // 用户反馈「缩放到一定程度渲染 bug」：无上限时飞进球团内部点素铺满全屏
  gl_Position = projectionMatrix * mv;
}
`

const FRAG = /* glsl */ `
varying vec3 vColor;
varying float vTwinkle;
void main() {
  vec2 uv = gl_PointCoord - 0.5;
  float d = length(uv);
  float glow = smoothstep(0.38, 0.0, d);   // 视觉评审：halo 收紧防"雾球"
  glow *= glow;
  float core = smoothstep(0.10, 0.0, d) * 0.9;
  float a = (glow * 0.8 + core) * vTwinkle;
  if (a < 0.004) discard;
  gl_FragColor = vec4(vColor * (0.38 + core * 0.8), a);
}
`

/** 球状星系团布局（用户校准：不是星云，是球状星系团）——King 剖面式采样：
 * 致密核心 + 外围渐稀；真实技能的家族质心按剖面取样，族员在质心附近小半径散布。 */
const CLUSTER_R = 430
const kingSample = (u: number) => CLUSTER_R * Math.pow(Math.max(0, Math.min(1, u)), 1.9)

function layoutCentroids(map: SkillMap): Map<string, [number, number, number]> {
  const pos = new Map<string, [number, number, number]>()
  const comps = map.components
  comps.forEach((comp, i) => {
      // 家族质心：King 剖面取样（剖面分位 u≥0.28 → 半径≈0.09R，核心区只容 1-2 个族防堆叠；
      // 球状星系团核心是"星可分辨"不是"糊成一块"；最大族恒居最内核，延续旧版构图意图）
      const u = 0.28 + 0.64 * (i / Math.max(1, comps.length)) + (hash01(comp[0] ?? 'c') - 0.5) * 0.1
    const dir = randUnit(hash01(comp.join('|')))
    const cr = kingSample(u)
    const cx = dir.x * cr
    const cy = dir.y * cr * 0.85
    const cz = dir.z * cr
    const spread = Math.min(30, 5 + comp.length * 1.5)
    for (const n of comp) {
      pos.set(n, [
        cx + (hash01(n) - 0.5) * spread * 2,
        cy + (hash01(n + 'y') - 0.5) * spread,
        cz + (hash01(n + 'z') - 0.5) * spread * 2,
      ])
    }
  })
  return pos
}

function randUnit(seed: number): THREE.Vector3 {
  const z = (seed % 1) * 2 - 1
  const t = (seed * 7.31) % (Math.PI * 2)
  const r = Math.sqrt(Math.max(0, 1 - z * z))
  return new THREE.Vector3(r * Math.cos(t), z, r * Math.sin(t))
}

type GalaxyBuffers = {
  pos: Float32Array
  baseCol: Float32Array
  size: Float32Array
  phase: Float32Array
  owner: string[]
  count: number
}

function buildGalaxies(map: SkillMap, centroids: Map<string, [number, number, number]>): GalaxyBuffers {
  const pos: number[] = []
  const col: number[] = []
  const size: number[] = []
  const phase: number[] = []
  const owner: string[] = []
  for (const s of map.skills) {
    const c = centroids.get(s.name) ?? [0, 0, 0]
    const theme = fateColor(s.fate)
    const R = Math.min(15, 5.5 + s.indeg * 1.15 + s.chars / 800)
    const count = 120 + Math.min(300, s.indeg * 45 + Math.floor(s.chars / 260))
    const e1 = hash01(s.name + 'ax') * Math.PI
    const e2 = hash01(s.name + 'ay') * Math.PI
    const rot = new THREE.Quaternion().setFromEuler(new THREE.Euler(e1, e2, e1 * 0.5))
    const v = new THREE.Vector3()
    for (let k = 0; k < count; k++) {
      const arm = k % 2
      const r = 1.2 + Math.pow(Math.random(), 0.55) * R
      const a = (arm * Math.PI) + r * 0.5 + (Math.random() - 0.5) * 0.5
      v.set(Math.cos(a) * r, (Math.random() - 0.5) * (1.4 + R * 0.12), Math.sin(a) * r)
        .applyQuaternion(rot)
      pos.push(c[0] + v.x, c[1] + v.y, c[2] + v.z)
      const coreT = 1 - r / (R + 1.2)
      // 视觉评审：降亮度防加法混色洗白（饱和度保住 fate 色相），尺寸收紧保颗粒感
      const bright = 0.38 + coreT * 0.55 + Math.min(0.4, s.indeg * 0.1)
      col.push(theme[0] * bright, theme[1] * bright, theme[2] * bright)
      size.push(0.8 + Math.random() * 1.1 + coreT * 0.9)
      phase.push(Math.random() * Math.PI * 2)
      owner.push(s.name)
    }
  }
  return {
    pos: new Float32Array(pos),
    baseCol: new Float32Array(col),
    size: new Float32Array(size),
    phase: new Float32Array(phase),
    owner,
    count: owner.length,
  }
}

function lineageSet(map: SkillMap, name: string): Set<string> {
  const s = new Set<string>([name])
  for (const e of map.edges) {
    if (e.child === name || e.parent === name) {
      s.add(e.child)
      s.add(e.parent)
    }
  }
  return s
}

export default function Nebula() {
  const hostRef = useRef<HTMLDivElement>(null)
  const [map, setMap] = useState<SkillMap | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [hoverName, setHoverName] = useState<string | null>(null)
  const [selName, setSelName] = useState<string | null>(null)
  const [realOnly, setRealOnly] = useState(false)
  const tooltipRef = useRef<HTMLDivElement>(null)
  const placeholderRef = useRef<THREE.Points | null>(null)

  useEffect(() => {
    if (placeholderRef.current) placeholderRef.current.visible = !realOnly
  }, [realOnly, map])
  const mapRef = useRef<SkillMap | null>(null)
  const selRef = useRef<string | null>(null)

  useEffect(() => {
    fetchSkillMap()
      .then((m) => {
        mapRef.current = m
        setMap(m)
      })
      .catch((e) => setErr(String(e)))
  }, [])

  useEffect(() => {
    if (!map) return
    const host = hostRef.current!
    const renderer = new THREE.WebGLRenderer({ antialias: true })
    // 用户反馈「过亮」根治：ACES 色调映射压缩高光（加法混合的 15000 星叠加会过曝）
    renderer.toneMapping = THREE.ACESFilmicToneMapping
    renderer.toneMappingExposure = 0.62
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75))
    renderer.setSize(host.clientWidth, host.clientHeight)
    host.appendChild(renderer.domElement)

    const scene = new THREE.Scene()
    scene.background = new THREE.Color(0x030509)
    const camera = new THREE.PerspectiveCamera(58, host.clientWidth / host.clientHeight, 1, 6000)
    let dist = 830, tdist = 830  // 视觉评审：×0.85 让星团更满幅；tdist=滚轮目标，dist 每帧 lerp 追
    camera.position.set(0, 260, dist)

    // —— 数据驱动的技能星系 ——
    const centroids = layoutCentroids(map)
    const buf = buildGalaxies(map, centroids)
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.BufferAttribute(buf.pos, 3))
    const colAttr = new THREE.BufferAttribute(buf.baseCol.slice(), 3)
    geo.setAttribute('color', colAttr)
    geo.setAttribute('aSize', new THREE.BufferAttribute(buf.size, 1))
    geo.setAttribute('aPhase', new THREE.BufferAttribute(buf.phase, 1))
    const mat = new THREE.ShaderMaterial({
      vertexShader: VERT,
      fragmentShader: FRAG,
      uniforms: {
        uTime: { value: 0 },
        uPixelRatio: { value: Math.min(window.devicePixelRatio, 1.75) },
      },
      vertexColors: true,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    })
    const group = new THREE.Group()
    group.add(new THREE.Points(geo, mat))
    scene.add(group)

    // —— 占位星（留给未来技能）：King 剖面同分布，老星族暖金 + 10% 蓝离散星。
    // 视觉评审：占位星从软 glow shader 改为小圆点星（颗粒感，杜绝"雾球"）——
    // 圆点贴图由程序化 canvas 生成，零外部资源。 ——
    const phN = 15000  // 用户反馈：星体数量较少→球状星团密度提升（15000×20B=300KB，GPU 点渲染近乎免费）
    const pp = new Float32Array(phN * 3)
    const pc = new Float32Array(phN * 3)
    const cnv = document.createElement('canvas')
    cnv.width = 32
    cnv.height = 32
    const ctx = cnv.getContext('2d')!
    const grad = ctx.createRadialGradient(16, 16, 0, 16, 16, 16)
    grad.addColorStop(0, 'rgba(255,255,255,1)')
    grad.addColorStop(0.35, 'rgba(255,255,255,0.55)')
    grad.addColorStop(1, 'rgba(255,255,255,0)')
    ctx.fillStyle = grad
    ctx.fillRect(0, 0, 32, 32)
    const dotTex = new THREE.CanvasTexture(cnv)
    for (let i = 0; i < phN; i++) {
      const r = kingSample(Math.random())
      const t = Math.random() * Math.PI * 2
      const ph = Math.acos(Math.random() * 2 - 1)
      pp[i * 3] = r * Math.sin(ph) * Math.cos(t)
      pp[i * 3 + 1] = r * Math.cos(ph) * 0.85
      pp[i * 3 + 2] = r * Math.sin(ph) * Math.sin(t)
      const straggler = Math.random() < 0.1
      const b = 0.6 + Math.random() * 0.4
      if (straggler) {
        pc[i * 3] = 0.62 * b
        pc[i * 3 + 1] = 0.78 * b
        pc[i * 3 + 2] = 1.0 * b
      } else {
        // 老星族暖金（评审建议 #e8b877 为主；二轮加饱和度让暖色在加法混合下仍可辨）
        pc[i * 3] = 1.0 * b
        pc[i * 3 + 1] = 0.66 * b
        pc[i * 3 + 2] = 0.35 * b
      }
    }
    const phGeo = new THREE.BufferGeometry()
    phGeo.setAttribute('position', new THREE.BufferAttribute(pp, 3))
    phGeo.setAttribute('color', new THREE.BufferAttribute(pc, 3))
    const phMat = new THREE.PointsMaterial({
      size: 2.6,
      map: dotTex,
      vertexColors: true,
      transparent: true,
      opacity: 0.72,  // 用户反馈「过亮」：15000 颗加法混合回压
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      sizeAttenuation: true,
    })
    const phPoints = new THREE.Points(phGeo, phMat)
    phPoints.visible = !realOnly
    placeholderRef.current = phPoints
    group.add(phPoints)

    // 背景星尘（静态，非数据）
    {
      const N = 900
      const dp = new Float32Array(N * 3)
      const dc = new Float32Array(N * 3)
      for (let i = 0; i < N; i++) {
        const r = 500 + Math.random() * 900
        const t = Math.random() * Math.PI * 2
        const ph = Math.acos(Math.random() * 2 - 1)
        dp[i * 3] = r * Math.sin(ph) * Math.cos(t)
        dp[i * 3 + 1] = r * Math.cos(ph) * 0.6
        dp[i * 3 + 2] = r * Math.sin(ph) * Math.sin(t)
        const b = 0.25 + Math.random() * 0.4
        dc[i * 3] = 0.5 * b
        dc[i * 3 + 1] = 0.6 * b
        dc[i * 3 + 2] = 0.9 * b
      }
      const dg = new THREE.BufferGeometry()
      dg.setAttribute('position', new THREE.BufferAttribute(dp, 3))
      dg.setAttribute('color', new THREE.BufferAttribute(dc, 3))
      const dm = new THREE.PointsMaterial({ size: 2.2, vertexColors: true, transparent: true, opacity: 0.8, depthWrite: false })
      scene.add(new THREE.Points(dg, dm))
    }

    // —— 拾取锚点（不可见质心点云）——
    const order = map.skills.map((s) => s.name)
    const pickGeo = new THREE.BufferGeometry()
    const pickPos = new Float32Array(order.length * 3)
    order.forEach((n, i) => {
      const c = centroids.get(n)!
      pickPos[i * 3] = c[0]
      pickPos[i * 3 + 1] = c[1]
      pickPos[i * 3 + 2] = c[2]
    })
    pickGeo.setAttribute('position', new THREE.BufferAttribute(pickPos, 3))
    const pickMat = new THREE.PointsMaterial({
      size: 14,
      sizeAttenuation: true,
      transparent: true,
      opacity: 0,
      depthWrite: false,  // 审查 m2：隐形点不得在主粒子上凿深度孔
      colorWrite: false,
    })
    const pickPoints = new THREE.Points(pickGeo, pickMat)
    group.add(pickPoints)

    // —— 血缘/引用连线（选中时重建；血缘青色、引用紫罗兰）——
    let linksArr: THREE.LineSegments[] = []
    const clearLinks = () => {
      for (const l of linksArr) {
        group.remove(l)
        l.geometry.dispose()
        ;(l.material as THREE.Material).dispose()
      }
      linksArr = []
    }
    const applySelection = (sel: string | null) => {
      const lit = sel ? lineageSet(map, sel) : null
      const arr = colAttr.array as Float32Array
      for (let i = 0; i < buf.count; i++) {
        const f = !lit || lit.has(buf.owner[i]) ? 1.55 : 0.3
        arr[i * 3] = buf.baseCol[i * 3] * f
        arr[i * 3 + 1] = buf.baseCol[i * 3 + 1] * f
        arr[i * 3 + 2] = buf.baseCol[i * 3 + 2] * f
      }
      colAttr.needsUpdate = true
      clearLinks()
      if (!sel) return
      const c0 = centroids.get(sel)!
      const ptsL: number[] = []
      const ptsR: number[] = []
      for (const e of map.edges) {
        let other: string | null = null
        if (e.child === sel) other = e.parent
        else if (e.parent === sel) other = e.child
        if (!other) continue
        const c1 = centroids.get(other)
        if (!c1) continue
        ;(e.kind === 'related' ? ptsR : ptsL).push(c0[0], c0[1], c0[2], c1[0], c1[1], c1[2])
      }
      const mk = (pts: number[], color: number, opacity: number) => {
        if (!pts.length) return
        const lg = new THREE.BufferGeometry()
        lg.setAttribute('position', new THREE.BufferAttribute(new Float32Array(pts), 3))
        const ls = new THREE.LineSegments(
          lg,
          new THREE.LineBasicMaterial({ color, transparent: true, opacity, blending: THREE.AdditiveBlending }),
        )
        group.add(ls)
        linksArr.push(ls)
      }
      mk(ptsL, 0x7dd3fc, 0.55)
      mk(ptsR, 0x818cf8, 0.3)
    }
    applySelection(null)

    // —— 交互 ——
    const raycaster = new THREE.Raycaster()
    raycaster.params.Points = { threshold: 7 }  // 审查 m4：过大在星团内乱命中
    const mouse = { x: -10, y: -10 }
    const orbit = { yaw: 0.4, pitch: 0.5, tyaw: 0.4, tpitch: 0.5 }
    let dragging = false
    let moved = 0
    let lastX = 0
    let lastY = 0
    let downAt = 0

    const setMouseFrom = (e: { clientX: number; clientY: number }) => {
      const rect = host.getBoundingClientRect()
      mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1
      mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1
    }
    const onDown = (e: PointerEvent) => {
      dragging = true
      moved = 0
      downAt = performance.now()
      lastX = e.clientX
      lastY = e.clientY
      setMouseFrom(e)  // 审查 m5：触屏 tap 无 move 事件，按下即更新拾取坐标
    }
    const onDrag = (e: PointerEvent) => {
      if (dragging) {
        const dx = e.clientX - lastX
        const dy = e.clientY - lastY
        moved += Math.abs(dx) + Math.abs(dy)
        orbit.tyaw += dx * 0.005
        orbit.tpitch = Math.max(-1.3, Math.min(1.3, orbit.tpitch + dy * 0.005))
        lastX = e.clientX
        lastY = e.clientY
      }
      setMouseFrom(e)
    }
    const onUp = () => {
      if (dragging && moved < 6 && performance.now() - downAt < 500) {
        const hit = pick()
        setSelName(hit)
        selRef.current = hit
        applySelection(hit)
      }
      dragging = false
    }
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      tdist = Math.max(150, Math.min(4000, tdist + e.deltaY * 1.4))  // 用户反馈：可飞进球团内部看细节（平滑 zoom）
    }
    const onResize = () => {
      const w = host.clientWidth
      const h = host.clientHeight
      if (!w || !h) return
      camera.aspect = w / h
      camera.updateProjectionMatrix()
      renderer.setSize(w, h)
      composer.setSize(w, h)
    }
    const pick = (): string | null => {
      raycaster.setFromCamera(new THREE.Vector2(mouse.x, mouse.y), camera)
      const hits = raycaster.intersectObject(pickPoints)
      return hits.length ? order[hits[0].index ?? -1] ?? null : null
    }
    host.addEventListener('pointerdown', onDown)
    window.addEventListener('pointermove', onDrag)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
    host.addEventListener('wheel', onWheel, { passive: false })
    window.addEventListener('resize', onResize)

    const composer = new EffectComposer(renderer)
    composer.addPass(new RenderPass(scene, camera))
    const bloom = new UnrealBloomPass(
      new THREE.Vector2(host.clientWidth, host.clientHeight),
      0.3,  // 视觉评审：0.85→0.3 压白斑
      0.6,
      0.55, // threshold 0.1→0.55 只让真正亮的核出辉光
    )
    composer.addPass(bloom)
    composer.addPass(new OutputPass())

    let raf = 0
    const clock = new THREE.Clock()
    const tick = () => {
      mat.uniforms.uTime.value = clock.getElapsedTime()
      orbit.yaw += (orbit.tyaw - orbit.yaw) * 0.07
      orbit.pitch += (orbit.tpitch - orbit.pitch) * 0.07
      dist += (tdist - dist) * 0.10  // 相机平滑：zoom 追目标（动画打磨）
      group.rotation.y += 0.00035
      const cp = Math.cos(orbit.pitch)
      camera.position.set(
        Math.sin(orbit.yaw) * cp * dist,
        Math.sin(orbit.pitch) * dist,
        Math.cos(orbit.yaw) * cp * dist,
      )
      camera.lookAt(0, 0, 0)
      // 悬停拾取（每帧，用当前相机）；tooltip 位置指令式更新（审查 n4：不逐 move 重渲染）
      const hit = pick()
      setHoverName((prev) => {
        const next = dragging ? null : hit
        return prev === next ? prev : next
      })
      if (tooltipRef.current) {
        if (hit && !dragging) {
          const idx = order.indexOf(hit)
          const v = new THREE.Vector3(pickPos[idx * 3], pickPos[idx * 3 + 1], pickPos[idx * 3 + 2]).project(camera)
          const rect = host.getBoundingClientRect()
          tooltipRef.current.style.left = `${((v.x + 1) / 2) * rect.width + 14}px`
          tooltipRef.current.style.top = `${((1 - v.y) / 2) * rect.height + 10}px`
          tooltipRef.current.style.display = 'block'
        } else {
          tooltipRef.current.style.display = 'none'
        }
      }
      composer.render()
      raf = requestAnimationFrame(tick)
    }
    tick()

    return () => {
      cancelAnimationFrame(raf)
      host.removeEventListener('pointerdown', onDown)
      window.removeEventListener('pointermove', onDrag)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
      host.removeEventListener('wheel', onWheel)
      window.removeEventListener('resize', onResize)
      geo.dispose()
      mat.dispose()
      phGeo.dispose()
      phMat.dispose()
      dotTex.dispose()
      bloom.dispose()
      composer.dispose()
      renderer.dispose()
      renderer.forceContextLoss()
      host.removeChild(renderer.domElement)
    }
  }, [map])

  const sel: Skill | null = selName ? map?.byName.get(selName) ?? null : null
  const hov: Skill | null = hoverName ? map?.byName.get(hoverName) ?? null : null

  return (
    <div className="particles view-enter">
      <div ref={hostRef} className="particle-host" />
      <label className="view-toggle">
        <input
          type="checkbox"
          checked={realOnly}
          onChange={(e) => setRealOnly(e.target.checked)}
        />
        只看真技能
      </label>
      {hov && !selName && (
        <div className="tooltip" ref={tooltipRef} style={{ display: 'none' }}>
          <b>{hov.name}</b> <span className="dim">v{hov.version} · {hov.fate} · 被引 {hov.indeg}</span>
          <div className="dim">{hov.desc.slice(0, 44)}{hov.desc.length > 44 ? '…' : ''}</div>
        </div>
      )}
      {sel && (
        <div className="info-panel">
          <div className="ip-title">{sel.name}</div>
          <div className="ip-row">v{sel.version} · {sel.status} · {sel.fate} · 被引 {sel.indeg}{sel.dup ? ' · 有副本' : ''}</div>
          <div className="ip-desc">{sel.desc || '（无描述）'}</div>
          {(() => {
            const kids = map?.childrenOf.get(sel.name) ?? []
            const ps = map?.parentsOf.get(sel.name) ?? []
            return (
              <div className="ip-lineage">
                {ps.length > 0 && <div>↑ 源自：{ps.join('、')}</div>}
                {kids.length > 0 && <div>↓ 演化出：{kids.join('、')}</div>}
                {sel.merged_into && <div>⇒ 已并入：{sel.merged_into}</div>}
                {sel.related.length > 0 && (
                  <div>⇄ 关联 {sel.related.length}：{sel.related.slice(0, 6).join('、')}{sel.related.length > 6 ? ' 等' : ''}</div>
                )}
                {!ps.length && !kids.length && !sel.merged_into && !sel.related.length && (
                  <div className="dim">（无血缘/关联）</div>
                )}
              </div>
            )
          })()}
          <div className="ip-hint">点空白处取消选中</div>
        </div>
      )}
      {err && <div className="empty">数据不可达：{err}（请确认观测舱服务已启动）</div>}
      {!map && !err && <div className="empty">技能星图加载中…</div>}
      <div className="particle-hint">
        球状星系团 · 一个技能一个星系 · 暖色占位星留给未来技能 —— 拖拽旋转 · 滚轮缩放 · 悬停看名 · 点选看血缘
      </div>
    </div>
  )
}
