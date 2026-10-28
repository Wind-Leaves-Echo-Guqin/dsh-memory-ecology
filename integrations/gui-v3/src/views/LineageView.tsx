import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js'
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js'
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js'
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js'
import { fetchSkillMap, hash01, type Skill, type SkillMap } from '../lib/skillmap'

/** 记忆脉络 · 血缘谱系 v2（彻底重做——视觉评审判"丑"后全面翻新）。
 * 手法参照 vasturiano/3d-force-graph：glow sprite shader 节点 + 渐变色边 + bloom。
 * 与技能星图区分：非点云宇宙，是有机脉络网（力导向聚簇 + 曲线边 + 丰富生物荧光色板）。 */

/* 丰富生物荧光色板——每 fate 独立色相（不是单调绿） */
function palette(s: Skill): [number, number, number] {
  switch (s.fate) {
    case 'active':    return [0.0, 0.95, 0.85]  // 荧光青
    case 'retained':  return [0.2, 0.9, 0.45]   // 翡翠绿
    case 'archived':  return [0.95, 0.45, 0.25] // 珊瑚橙
    case 'merged':    return [0.7, 0.4, 0.95]   // 亮紫
    default: {
      // 未声明技能：按名字散布多色相（丰富视觉）
      const h = hash01(s.name + 'hue')
      if (h < 0.25) return [0.35, 0.7, 1.0]      // 冰蓝
      if (h < 0.5) return [0.55, 0.4, 1.0]       // 长春花
      if (h < 0.75) return [0.2, 0.85, 0.6]      // 薄荷
      return [0.9, 0.55, 0.35]                    // 琥珀
    }
  }
}

/* 力导向布局（300 轮，与之前相同算法但参数优化） */
function forceLayout(map: SkillMap): Map<string, [number, number, number]> {
  const nodes = map.skills.map((s) => s.name)
  const n = nodes.length
  const pos = new Float32Array(n * 3)
  nodes.forEach((name, i) => {
    const u = hash01(name) * 2 - 1
    const t = hash01(name + 't') * Math.PI * 2
    const r = Math.sqrt(1 - u * u)
    const d = 10 + hash01(name + 'd') * 10
    pos[i * 3] = d * r * Math.cos(t)
    pos[i * 3 + 1] = d * u * 0.6
    pos[i * 3 + 2] = d * r * Math.sin(t)
  })
  const idx = new Map(nodes.map((n, i) => [n, i]))
  const adj = new Map<string, Set<string>>()
  for (const e of map.edges) {
    if (!adj.has(e.child)) adj.set(e.child, new Set())
    if (!adj.has(e.parent)) adj.set(e.parent, new Set())
    adj.get(e.child)!.add(e.parent)
    adj.get(e.parent)!.add(e.child)
  }
  for (let iter = 0; iter < 300; iter++) {
    const fx = new Float32Array(n), fy = new Float32Array(n), fz = new Float32Array(n)
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const dx = pos[j * 3] - pos[i * 3], dy = pos[j * 3 + 1] - pos[i * 3 + 1], dz = pos[j * 3 + 2] - pos[i * 3 + 2]
        const d2 = dx * dx + dy * dy + dz * dz + 0.01
        const f = 30.0 / d2, d = Math.sqrt(d2)
        fx[i] -= dx / d * f; fy[i] -= dy / d * f; fz[i] -= dz / d * f
        fx[j] += dx / d * f; fy[j] += dy / d * f; fz[j] += dz / d * f
      }
    }
    for (const e of map.edges) {
      const i = idx.get(e.child), j = idx.get(e.parent)
      if (i === undefined || j === undefined) continue
      const dx = pos[j * 3] - pos[i * 3], dy = pos[j * 3 + 1] - pos[i * 3 + 1], dz = pos[j * 3 + 2] - pos[i * 3 + 2]
      const d = Math.sqrt(dx * dx + dy * dy + dz * dz + 0.01)
      const f = 0.02 * (d - 4.0)
      fx[i] += dx / d * f; fy[i] += dy / d * f; fz[i] += dz / d * f
      fx[j] -= dx / d * f; fy[j] -= dy / d * f; fz[j] -= dz / d * f
    }
    for (let i = 0; i < n; i++) {
      fx[i] -= pos[i * 3] * 0.004
      fy[i] -= pos[i * 3 + 1] * 0.004
      fz[i] -= pos[i * 3 + 2] * 0.004
      pos[i * 3] += fx[i] * 0.88; pos[i * 3 + 1] += fy[i] * 0.88; pos[i * 3 + 2] += fz[i] * 0.88
    }
  }
  const out = new Map<string, [number, number, number]>()
  nodes.forEach((name, i) => out.set(name, [pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2]]))
  return out
}

/* 节点 glow sprite 着色器（vasturiano 风格：bright core + soft halo + fresnel rim） */
const NODE_VERT = /* glsl */ `
attribute float aSize;
attribute vec3 aColor;
attribute float aBirth;   // 入场 stagger：0~0.6s 错峰出生（hash 分布）
uniform float uTime;
varying vec3 vColor;
varying float vPulse;
float nhash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
void main() {
  vColor = aColor;
  // 4D 悬浮：每节点在 3D 空间中缓慢漂浮（x/y/z 各自独立正弦，相位由位置哈希决定）
  vec3 fp = position;
  float ph = nhash(vec2(position.x, position.z)) * 6.28;
  fp.y += sin(uTime * 0.4 + ph) * 0.6;
  fp.x += cos(uTime * 0.3 + ph * 1.3) * 0.4;
  fp.z += sin(uTime * 0.35 + ph * 0.7) * 0.5;
  // 节点入场：从小到大长出来 + 淡入（ease-out，每节点错峰 0.7s 内完成）
  float born = clamp((uTime - aBirth) / 0.7, 0.0, 1.0);
  float ease = born * born * (3.0 - 2.0 * born);
  vPulse = (0.85 + 0.15 * sin(uTime * 1.2 + ph * 6.28)) * (0.15 + 0.85 * ease);
  vec4 mv = modelViewMatrix * vec4(fp, 1.0);
  gl_PointSize = aSize * (0.25 + 0.75 * ease) * (180.0 / -mv.z);
  gl_Position = projectionMatrix * mv;
}
`

const NODE_FRAG = /* glsl */ `
varying vec3 vColor;
varying float vPulse;
void main() {
  vec2 uv = gl_PointCoord - 0.5;
  float d = length(uv) * 2.0;  // 0=center, 1=edge
  if (d > 1.0) discard;
  // 三层：核心（亮）→ 主体（色）→ 边缘光（fresnel 模拟）
  float core = smoothstep(0.22, 0.0, d);
  float body = smoothstep(0.55, 0.15, d);
  float rim  = smoothstep(0.42, 0.5, d) * smoothstep(1.0, 0.6, d); // 环形边缘增亮
  float alpha = body * 0.85 + core + rim * 0.4;
  vec3 col = vColor * (0.6 + core * 1.2) + vec3(rim * 0.3);
  gl_FragColor = vec4(col * vPulse, alpha * vPulse);
}
`

/* 边着色器（渐变色+流动脉冲） */
const EDGE_VERT = /* glsl */ `
attribute vec3 aColor;
attribute float aT;
uniform float uTime;
varying vec3 vColor;
varying float vT;
void main() {
  vColor = aColor;
  vT = aT;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`

const EDGE_FRAG = /* glsl */ `
uniform float uTime;
varying vec3 vColor;
varying float vT;
void main() {
  // 沿边流动的脉冲亮带；边整体随节点入场淡入（0.3s 后开始）
  float pulse = 0.7 + 0.3 * sin(vT * 20.0 - uTime * 3.0);
  float enter = smoothstep(0.3, 1.1, uTime);
  gl_FragColor = vec4(vColor * pulse, 0.6 * enter);
}
`

export default function LineageView() {
  const hostRef = useRef<HTMLDivElement>(null)
  const [map, setMap] = useState<SkillMap | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [hoverName, setHoverName] = useState<string | null>(null)
  const [selName, setSelName] = useState<string | null>(null)
  const tooltipRef = useRef<HTMLDivElement>(null)

  useEffect(() => { fetchSkillMap().then(setMap).catch((e) => setErr(String(e))) }, [])

  useEffect(() => {
    if (!map) return
    const host = hostRef.current!
    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5))
    renderer.setSize(host.clientWidth, host.clientHeight)
    host.appendChild(renderer.domElement)

    const scene = new THREE.Scene()
    scene.background = new THREE.Color(0x010608)
    const camera = new THREE.PerspectiveCamera(55, host.clientWidth / host.clientHeight, 0.1, 1000)
    let dist = 55
    camera.position.set(0, 15, dist)

    // —— 力导向布局 ——
    const centroids = forceLayout(map)
    const order = map.skills.map((s) => s.name)
    const centroidsArr = new Float32Array(order.length * 3)
    order.forEach((name, i) => {
      const c = centroids.get(name)!
      centroidsArr[i * 3] = c[0]; centroidsArr[i * 3 + 1] = c[1]; centroidsArr[i * 3 + 2] = c[2]
    })

    // —— 节点：glow sprite Points（非球体——每节点是多层发光圆盘）——
    const N = order.length
    const nPos = new Float32Array(N * 3)
    const nCol = new Float32Array(N * 3)
    const nSize = new Float32Array(N)
    const nBirth = new Float32Array(N)
    order.forEach((name, i) => {
      const s = map.byName.get(name)!
      const c = centroids.get(name)!
      const tc = palette(s)
      nPos[i * 3] = c[0]; nPos[i * 3 + 1] = c[1]; nPos[i * 3 + 2] = c[2]
      // 色彩饱和度提升（评审：不要洗白）
      nCol[i * 3] = tc[0]; nCol[i * 3 + 1] = tc[1]; nCol[i * 3 + 2] = tc[2]
      // 大小：被引多的节点大且亮
      nSize[i] = 4.5 + Math.min(8, s.indeg * 0.9 + s.chars / 3000)
      // 入场 stagger：按名字哈希错峰出生（同名每次渲染同序，确定性）
      nBirth[i] = hash01(name + 'birth') * 0.6
    })
    const nodeGeo = new THREE.BufferGeometry()
    nodeGeo.setAttribute('position', new THREE.BufferAttribute(nPos, 3))
    nodeGeo.setAttribute('aColor', new THREE.BufferAttribute(nCol, 3))
    nodeGeo.setAttribute('aSize', new THREE.BufferAttribute(nSize, 1))
    nodeGeo.setAttribute('aBirth', new THREE.BufferAttribute(nBirth, 1))
    const nodeMat = new THREE.ShaderMaterial({
      vertexShader: NODE_VERT,
      fragmentShader: NODE_FRAG,
      uniforms: { uTime: { value: 0 } },
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    })
    const nodePoints = new THREE.Points(nodeGeo, nodeMat)
    scene.add(nodePoints)

    // —— 边：渐变色直线 + 流动脉冲（shader 里的 pulse 亮带）——
    const edgeList = map.edges.filter((e) => centroids.has(e.child) && centroids.has(e.parent))
    const ePos: number[] = []
    const eCol: number[] = []
    const eT: number[] = []
    for (const e of edgeList) {
      const a = centroids.get(e.child)!
      const b = centroids.get(e.parent)!
      const ca = palette(map.byName.get(e.child)!)
      const cb = palette(map.byName.get(e.parent)!)
      const dim = e.kind === 'related' ? 0.3 : 0.7
      // 起点=child 色，终点=parent 色（渐变）
      ePos.push(a[0], a[1], a[2], b[0], b[1], b[2])
      eCol.push(ca[0] * dim, ca[1] * dim, ca[2] * dim, cb[0] * dim, cb[1] * dim, cb[2] * dim)
      eT.push(0, 1)
    }
    const edgeGeo = new THREE.BufferGeometry()
    edgeGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(ePos), 3))
    edgeGeo.setAttribute('aColor', new THREE.BufferAttribute(new Float32Array(eCol), 3))
    edgeGeo.setAttribute('aT', new THREE.BufferAttribute(new Float32Array(eT), 1))
    const edgeMat = new THREE.ShaderMaterial({
      vertexShader: EDGE_VERT,
      fragmentShader: EDGE_FRAG,
      uniforms: { uTime: { value: 0 } },
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    })
    scene.add(new THREE.LineSegments(edgeGeo, edgeMat))

    // —— 流动粒子（星火沿脉络流动）——
    const fpN = Math.min(1500, edgeList.length * 10)
    const fpPos = new Float32Array(fpN * 3)
    const fpCol = new Float32Array(fpN * 3)
    const fpSpeed = new Float32Array(fpN)
    const fpEdge = new Int32Array(fpN)
    const fpT = new Float32Array(fpN)
    for (let i = 0; i < fpN; i++) {
      const ei = i % Math.max(1, edgeList.length)
      fpEdge[i] = ei
      fpT[i] = Math.random()
      fpSpeed[i] = 0.1 + Math.random() * 0.2
      const e = edgeList[ei]
      const c = palette(map.byName.get(e.child)!)
      fpCol[i * 3] = c[0]; fpCol[i * 3 + 1] = c[1]; fpCol[i * 3 + 2] = c[2]
    }
    const fpGeo = new THREE.BufferGeometry()
    fpGeo.setAttribute('position', new THREE.BufferAttribute(fpPos, 3))
    fpGeo.setAttribute('color', new THREE.BufferAttribute(fpCol, 3))
    const fpMat = new THREE.PointsMaterial({
      size: 0.3, vertexColors: true, transparent: true, opacity: 0.9,
      blending: THREE.AdditiveBlending, depthWrite: false, sizeAttenuation: true,
    })
    const fpPoints = new THREE.Points(fpGeo, fpMat)
    scene.add(fpPoints)

    // —— 背景微尘（深海底悬浮颗粒）——
    {
      const dn = 400
      const dp = new Float32Array(dn * 3)
      const dc = new Float32Array(dn * 3)
      for (let i = 0; i < dn; i++) {
        dp[i * 3] = (Math.random() - 0.5) * 120
        dp[i * 3 + 1] = (Math.random() - 0.5) * 80
        dp[i * 3 + 2] = (Math.random() - 0.5) * 120
        const b = 0.1 + Math.random() * 0.2
        dc[i * 3] = 0.15 * b; dc[i * 3 + 1] = 0.4 * b; dc[i * 3 + 2] = 0.35 * b
      }
      const dg = new THREE.BufferGeometry()
      dg.setAttribute('position', new THREE.BufferAttribute(dp, 3))
      dg.setAttribute('color', new THREE.BufferAttribute(dc, 3))
      scene.add(new THREE.Points(dg, new THREE.PointsMaterial({
        size: 0.15, vertexColors: true, transparent: true, depthWrite: false,
      })))
    }

    // —— 拾取 ——
    const raycaster = new THREE.Raycaster()
    raycaster.params.Points = { threshold: 1.5 }
    const mouse = { x: -10, y: -10 }
    const pick = (): string | null => {
      raycaster.setFromCamera(new THREE.Vector2(mouse.x, mouse.y), camera)
      const hits = raycaster.intersectObject(nodePoints)
      return hits.length ? order[hits[0].index ?? -1] ?? null : null
    }

    // —— 交互（4D 惯性：松手后角速度衰减旋转）——
    const orbit = { yaw: 0.2, pitch: 0.2, tyaw: 0.2, tpitch: 0.2 }
    const angVel = { x: 0, y: 0 }  // 拖拽角速度（惯性）
    let dragging = false, moved = 0, downAt = 0, lastX = 0, lastY = 0
    const onDown = (e: PointerEvent) => {
      dragging = true; moved = 0; downAt = performance.now(); lastX = e.clientX; lastY = e.clientY
      angVel.x = 0; angVel.y = 0  // 重置惯性
      const rect = host.getBoundingClientRect()
      mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1
      mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1
    }
    const onDrag = (e: PointerEvent) => {
      if (dragging) {
        const dx = e.clientX - lastX, dy = e.clientY - lastY
        moved += Math.abs(dx) + Math.abs(dy)
        orbit.tyaw += dx * 0.005
        orbit.tpitch = Math.max(-1.2, Math.min(1.2, orbit.tpitch + dy * 0.005))
        angVel.y = dx * 0.003  // 记录角速度（惯性源）
        angVel.x = dy * 0.003
      }
      lastX = e.clientX; lastY = e.clientY
      const rect = host.getBoundingClientRect()
      mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1
      mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1
    }
    const onUp = () => {
      if (dragging && moved < 6 && performance.now() - downAt < 500) {
        setSelName(pick())
      }
      dragging = false  // 松手后 angVel 在 tick 中衰减——4D 惯性旋转
    }
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      dist = Math.max(8, Math.min(220, dist + e.deltaY * 0.06))
    }
    const onResize = () => {
      const w = host.clientWidth, h = host.clientHeight
      if (!w || !h) return
      camera.aspect = w / h; camera.updateProjectionMatrix()
      renderer.setSize(w, h); composer.setSize(w, h)
    }
    host.addEventListener('pointerdown', onDown)
    window.addEventListener('pointermove', onDrag)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
    host.addEventListener('wheel', onWheel, { passive: false })
    window.addEventListener('resize', onResize)

    // —— 选中高亮（调暗无关，提亮相关）——
    const applySelection = (sel: string | null) => {
      const lit = sel
        ? new Set([sel, ...map.edges.filter((e) => e.child === sel || e.parent === sel).flatMap((e) => [e.child, e.parent])])
        : null
      order.forEach((name, i) => {
        const s = map.byName.get(name)!
        const tc = palette(s)
        const f = !lit || lit.has(name) ? 1.0 : 0.12
        nCol[i * 3] = tc[0] * f; nCol[i * 3 + 1] = tc[1] * f; nCol[i * 3 + 2] = tc[2] * f
      })
      ;(nodeGeo.getAttribute('aColor') as THREE.BufferAttribute).needsUpdate = true
    }
    applySelection(null)

    // —— Composer ——
    const composer = new EffectComposer(renderer)
    composer.addPass(new RenderPass(scene, camera))
    const bloom = new UnrealBloomPass(
      new THREE.Vector2(host.clientWidth, host.clientHeight),
      0.5, 0.5, 0.2,
    )
    composer.addPass(bloom)
    composer.addPass(new OutputPass())

    // —— 动画循环 ——
    let raf = 0
    const clock = new THREE.Clock()
    const tick = () => {
      const t = clock.getElapsedTime()
      nodeMat.uniforms.uTime.value = t
      edgeMat.uniforms.uTime.value = t
      // 流动粒子沿边移动
      for (let i = 0; i < fpN; i++) {
        const e = edgeList[fpEdge[i]]
        if (!e) continue
        fpT[i] = (fpT[i] + fpSpeed[i] * 0.006) % 1
        const a = centroids.get(e.child)
        const b = centroids.get(e.parent)
        if (!a || !b) continue
        fpPos[i * 3] = a[0] + (b[0] - a[0]) * fpT[i]
        fpPos[i * 3 + 1] = a[1] + (b[1] - a[1]) * fpT[i]
        fpPos[i * 3 + 2] = a[2] + (b[2] - a[2]) * fpT[i]
      }
      ;(fpGeo.getAttribute('position') as THREE.BufferAttribute).needsUpdate = true
      // 4D 惯性：非拖拽时角速度衰减（松手后持续旋转渐渐停下）
      if (!dragging) {
        orbit.tyaw += angVel.y
        orbit.tpitch = Math.max(-1.2, Math.min(1.2, orbit.tpitch + angVel.x))
        angVel.y *= 0.95
        angVel.x *= 0.95
      }
      // 相机
      orbit.yaw += (orbit.tyaw - orbit.yaw) * 0.05  // 4D：更平滑的追踪
      orbit.pitch += (orbit.tpitch - orbit.pitch) * 0.05
      const cp = Math.cos(orbit.pitch)
      camera.position.set(
        Math.sin(orbit.yaw) * cp * dist,
        Math.sin(orbit.pitch) * dist * 0.7,
        Math.cos(orbit.yaw) * cp * dist,
      )
      camera.lookAt(0, 0, 0)
      const hit = pick()
      setHoverName((prev) => (prev === hit ? prev : hit))
      if (tooltipRef.current) {
        if (hit && !dragging) {
          const idx = order.indexOf(hit)
          const v = new THREE.Vector3(centroidsArr[idx * 3], centroidsArr[idx * 3 + 1], centroidsArr[idx * 3 + 2]).project(camera)
          const rect = host.getBoundingClientRect()
          tooltipRef.current.style.left = `${((v.x + 1) / 2) * rect.width + 14}px`
          tooltipRef.current.style.top = `${((1 - v.y) / 2) * rect.height + 10}px`
          tooltipRef.current.style.display = 'block'
        } else tooltipRef.current.style.display = 'none'
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
      nodeGeo.dispose(); nodeMat.dispose()
      edgeGeo.dispose(); edgeMat.dispose()
      fpGeo.dispose(); fpMat.dispose()
      bloom.dispose(); composer.dispose()
      renderer.dispose(); renderer.forceContextLoss()
      host.removeChild(renderer.domElement)
    }
  }, [map])

  const sel: Skill | null = selName ? map?.byName.get(selName) ?? null : null
  const hov: Skill | null = hoverName ? map?.byName.get(hoverName) ?? null : null

  return (
    <div className="particles view-enter">
      <div ref={hostRef} className="particle-host" />
      {hov && (
        <div className="tooltip" ref={tooltipRef} style={{ display: 'none' }}>
          <b>{hov.name}</b> <span className="dim">v{hov.version} · {hov.fate} · 被引 {hov.indeg}</span>
          <div className="dim">{hov.desc.slice(0, 44)}{hov.desc.length > 44 ? '…' : ''}</div>
        </div>
      )}
      {sel && (
        <div className="info-panel">
          <div className="ip-title">{sel.name}</div>
          <div className="ip-row">v{sel.version} · {sel.status} · {sel.fate} · 被引 {sel.indeg}</div>
          <div className="ip-desc">{sel.desc || '（无描述）'}</div>
          {(() => {
            const kids = map?.childrenOf.get(sel.name) ?? []
            const ps = map?.parentsOf.get(sel.name) ?? []
            const rel = sel.related.slice(0, 8)
            return (
              <div className="ip-lineage">
                {ps.length > 0 && <div>↑ 源自：{ps.join('、')}</div>}
                {kids.length > 0 && <div>↓ 演化出：{kids.join('、')}</div>}
                {sel.merged_into && <div>⇒ 已并入：{sel.merged_into}</div>}
                {rel.length > 0 && <div>⇄ 关联 {sel.related.length}：{rel.join('、')}{sel.related.length > 8 ? '…' : ''}</div>}
                {!ps.length && !kids.length && !sel.merged_into && !rel.length && (
                  <div className="dim">（孤立节点，暂无关联）</div>
                )}
              </div>
            )
          })()}
          <div className="ip-hint">点空白处取消 · 进化启动后此视图将自动生长</div>
        </div>
      )}
      {err && <div className="empty">数据不可达：{err}</div>}
      {!map && !err && <div className="empty">记忆脉络加载中…</div>}
      <div className="particle-hint">
        记忆脉络 · 技能 = 发光节点 · 有机脉络 = 关系网络 —— 拖拽旋转 · 滚轮缩放 · 悬停看名 · 点选看脉络
      </div>
    </div>
  )
}
