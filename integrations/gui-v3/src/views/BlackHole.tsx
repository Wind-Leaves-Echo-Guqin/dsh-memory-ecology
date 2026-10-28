import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js'
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js'
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js'
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js'
import { fetchSkillMap, fateColor, hash01, type Skill, type SkillMap } from '../lib/skillmap'

/** 星系视界 · 史瓦西黑洞实时光线追踪 + 技能恒星层（用户指定方向）。
 * 物理（参考：UE5 史瓦西实时渲染 / sonicether lstSRS / Baopinsui 4XcfR2 /
 * Eric Bruneton 2020）：光子测地线半隐式积分 a=-1.5·h²·r/r⁵；吸积盘黑体色温 +
 * 多普勒束射 + 开普勒差速旋转；背景透镜化。
 * 技能层：每个 skill = 星系内一颗恒星，血缘分量分轨道带，角速度与盘条纹同律
 * （ω ∝ r^-1.5，真实开普勒剪切——同族会随时间缓慢错开，这是特性不是 bug）。
 * 主 skill 居内，子 skill 外圈公转。 */

const VERT = /* glsl */ `
varying vec2 vUv;
void main() {
  vUv = uv;
  gl_Position = vec4(position.xy, 0.0, 1.0);
}
`

const FRAG = /* glsl */ `
precision highp float;
varying vec2 vUv;
uniform float uTime;
uniform vec2 uRes;
uniform vec3 uCamPos;
uniform vec3 uFwd;
uniform vec3 uRight;
uniform vec3 uUp;
uniform sampler2D uSky;
uniform float uHasSky;
uniform samplerCube uStars;  // 技能恒星层（立方体贴图，按弯曲方向采样）

#define DISK_IN 2.6
#define DISK_OUT 9.6
#define ESCAPE_R 60.0
#define STEPS 200  // 用户要求：增加近距细节（自适应 dt 使成本增幅有限，远距 ray 提前 break 不受影响）

float hash13(vec3 p) {
  p = fract(p * 0.1031);
  p += dot(p, p.zyx + 31.32);
  return fract((p.x + p.y) * p.z);
}
float hash12(vec2 p) {
  vec3 q = fract(vec3(p.xyx) * 0.1031);
  q += dot(q, q.yzx + 33.33);
  return fract((q.x + q.y) * q.z);
}
float vnoise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  float a = hash12(i), b = hash12(i + vec2(1, 0));
  float c = hash12(i + vec2(0, 1)), d = hash12(i + vec2(1, 1));
  return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}
float fbm(vec2 p) {
  float v = 0.0, amp = 0.55;
  for (int i = 0; i < 4; i++) {
    v += amp * vnoise(p);
    p = p * 2.15 + 17.7;
    amp *= 0.5;
  }
  return v;
}

vec3 diskRamp(float t) {
  vec3 inner = vec3(1.55, 1.48, 1.38);
  vec3 mid = vec3(1.35, 0.78, 0.28);
  vec3 outer = vec3(0.55, 0.16, 0.05);
  return t < 0.35 ? mix(inner, mid, t / 0.35) : mix(mid, outer, (t - 0.35) / 0.65);
}

vec4 diskSample(vec3 p, vec3 rd) {
  float r = length(p.xz);
  float t = clamp((r - DISK_IN) / (DISK_OUT - DISK_IN), 0.0, 1.0);
  // 开普勒差速旋转。审查 M2：fbm 输入环形参数化（atan 支线会犁出固定接缝）；
  // 审查 M1：负号使条纹流向与多普勒速度场 +θ̂ 一致。
  float ang = uTime * 3.0 * pow(r, -1.5);  // 用户反馈降速：13.2→3.0（内轨 18s→82s，接近 Sgr A* 时标）
  float a = atan(p.z, p.x) - ang;
  vec2 tp = vec2(cos(a), sin(a));
  // —— 真实感重做（用户反馈「过于规整的圆盘」）——
  // 三层噪声：大尺度团块（绕环 2~3 团）＋ 差速剪切拉出的细丝 ＋ 方位角明暗扇区。
  // 真实吸积盘从不是均匀亮环：M87*/Sgr A* 成像都是"一侧亮、团块斑驳、边缘消散"。
  float clump = fbm(tp * 2.1 + vec2(r * 0.30, 0.0));
  float streaky = fbm(tp * (r * 1.25) + 13.7 + vec2(clump * 2.2, -clump * 1.3));
  float sector = fbm(tp * 1.15 + vec2(7.3, -2.1));
  // 内外边缘噪声扰动：不再是完美圆形边界（ISCO 附近湍流 + 外缘不规则消散）
  float edgeN = (fbm(tp * 2.6 + vec2(21.7, 5.2)) - 0.5) * 0.15;
  float tN = clamp(t + edgeN, 0.0, 1.0);
  float dens = smoothstep(0.0, 0.10, tN) * (1.0 - smoothstep(0.55, 1.0, tN));
  dens *= (0.32 + 0.68 * clump) * (0.42 + 0.58 * sector) * (0.5 + 0.5 * streaky);
  // 环内暗缝（中区一道，真实盘的分环结构；随扇区调制，不形成机械圆环）
  float gap = smoothstep(0.26, 0.30, tN) * (1.0 - smoothstep(0.34, 0.38, tN));
  dens *= 1.0 - 0.6 * gap * sector;
  vec3 vel = normalize(vec3(-p.z, 0.0, p.x));
  float dop = clamp(1.0 + 1.75 * dot(vel, -rd), 0.18, 3.2);
  vec3 c = diskRamp(tN) * (0.45 + 0.75 * streaky) * dop;
  c = mix(c, c * vec3(0.75, 0.9, 1.25), clamp((dop - 1.0) * 0.5, 0.0, 0.6));
  c = mix(c, c * vec3(1.2, 0.8, 0.6), clamp((0.75 - dop) * 0.8, 0.0, 0.5));
  vec4 outc = vec4(c, clamp(dens * 1.35, 0.0, 0.92));
  // 视觉评审：HDR 预钳制防多普勒增亮侧白爆，保住盘丝纹理
  outc.rgb = min(outc.rgb, vec3(2.0));
  // 用户反馈「过亮」：盘亮度整体回压（多普勒峰值 3.2× 叠加 bloom 会淹没盘纹）
  outc.rgb *= 1.05;  // 用户反馈「过暗」：0.8→1.05（盘回提；ACES 已在渲染端压缩高光，不会白爆）
  return outc;
}

vec3 sky(vec3 d) {
  if (uHasSky > 0.5) {
    vec2 uv = vec2(atan(d.z, d.x) / 6.2831853 + 0.5, asin(clamp(d.y, -1.0, 1.0)) / 3.1415926 + 0.5);
    vec3 t = texture2D(uSky, uv).rgb;
    return t * vec3(1.05, 1.0, 1.1);
  }
  vec3 c = vec3(0.006, 0.008, 0.016);
  vec3 bn = normalize(vec3(0.35, 0.9, 0.2));
  float bandd = abs(dot(d, bn));
  float band = exp(-bandd * 5.5) * (0.55 + 0.45 * fbm(d.xz * 2.4 + vec2(0.0, d.y * 1.7)));
  c += vec3(0.10, 0.12, 0.22) * band + vec3(0.09, 0.06, 0.13) * band * band;
  // 用户反馈「以宇宙为视角，周围难道没有星系吗」：远景星系场。
  // 三层：① 旋臂星系（方向对数格点，椭圆核 + 盘面朝向的短轴压缩）② 椭圆星系（软核）
  // ③ 弥漫星云团（fbm 低频块，银河带附近增密）。全部程序化、随 uTime 极缓慢转动。
  {
    // ① 旋臂/盘星系：格点 8.5，每格最多 1 个，盘面朝向由方向哈希决定
    vec3 g = d * 8.5;
    vec3 id = floor(g);
    float h = hash13(id + 3.7);
    if (h > 0.955) {  // 约 4.5% 格点有星系
      vec3 sp = id + 0.2 + 0.6 * vec3(hash13(id + 1.3), hash13(id + 5.1), hash13(id + 9.7));
      vec3 rel = g - sp;
      // 盘面法线（哈希方向）→ 把 rel 投影到盘面坐标：短轴沿法线
      vec3 nrm = normalize(vec3(hash13(id + 13.1), hash13(id + 17.3), hash13(id + 19.9)) * 2.0 - 1.0 + 0.001);
      vec3 inPlane = rel - dot(rel, nrm) * nrm;
      float rIn = length(inPlane);
      float rOut = abs(dot(rel, nrm));
      float rad = length(rel);
      if (rad < 1.4) {
        float glow = exp(-rIn * rIn * 5.0) * exp(-rOut * rOut * 42.0);   // 盘（薄）
        float core = exp(-rad * rad * 26.0) * 1.6;                        // 核球
        // 旋臂：盘面内两条对数螺旋密度增强。
        // 盘面切基稳健构造：法线近 (0,1,0) 时换参考轴，避免 cross 退化除零（NaN）。
        vec3 ref = abs(nrm.y) < 0.9 ? vec3(0.0, 1.0, 0.0) : vec3(1.0, 0.0, 0.0);
        vec3 t1 = normalize(cross(nrm, ref));
        vec3 t2 = cross(nrm, t1);
        float armPh = atan(dot(inPlane, t2), dot(inPlane, t1));
        float arm = 0.5 + 0.5 * sin(2.0 * armPh + 5.5 * log(max(rIn, 0.03)) + h * 40.0);
        glow *= 0.55 + 0.85 * arm;
        float bright = 0.35 + 0.65 * hash13(id + 23.3);
        vec3 tint = mix(vec3(0.95, 0.86, 0.72), vec3(0.72, 0.82, 1.0), hash13(id + 29.1));
        c += tint * (glow * 0.55 + core * 0.5) * bright;
      }
    }
    // ② 椭圆星系：更稀（2%），软核无盘
    float h2 = hash13(id + 41.7);
    if (h2 > 0.978 && h2 <= 0.998) {
      vec3 sp2 = id + 0.25 + 0.5 * vec3(hash13(id + 43.1), hash13(id + 47.9), hash13(id + 53.3));
      vec3 rel2 = g - sp2;
      float rad2 = length(rel2);
      if (rad2 < 1.1) {
        float e = exp(-rad2 * rad2 * 14.0);
        c += vec3(0.98, 0.9, 0.82) * e * 0.42;
      }
    }
    // ③ 弥漫星云团：低频 fbm 块，银河带附近更密（宇宙网感）
    float neb = fbm(d.xz * 3.1 + d.y * 2.2 + vec2(31.0, 17.0));
    neb = pow(clamp(neb - 0.42, 0.0, 1.0) * 1.7, 1.6);
    c += vec3(0.10, 0.13, 0.26) * neb * (0.35 + 0.9 * band) * 1.3;
    float neb2 = fbm(d.xy * 2.4 - d.z * 1.8 + vec2(5.0, 44.0));
    neb2 = pow(clamp(neb2 - 0.5, 0.0, 1.0) * 2.0, 1.8);
    c += vec3(0.20, 0.10, 0.16) * neb2 * 0.7;   // 暖色星云（HII 区）
  }
  for (int i = 0; i < 3; i++) {
    float sc = 42.0 + float(i) * 46.0;
    vec3 g = d * sc;
    vec3 id = floor(g);
    float h = hash13(id);
    vec3 sp = id + 0.15 + 0.7 * vec3(hash13(id + 11.1), hash13(id + 27.7), hash13(id + 41.3));
    float dd = length(g - sp);
    float b = step(0.9965, h) * pow(clamp((h - 0.9965) / 0.0035, 0.0, 1.0), 2.0);
    c += vec3(0.85, 0.9, 1.0) * b * exp(-dd * dd * 6.0) * (1.2 - float(i) * 0.3);
  }
  return c;
}

void main() {
  vec2 uv = vUv * 2.0 - 1.0;
  uv.x *= uRes.x / uRes.y;
  vec3 rd = normalize(uFwd + uv.x * 0.62 * uRight + uv.y * 0.62 * uUp);
  vec3 p = uCamPos;
  vec3 v = rd;
  vec3 h = cross(p, v);
  float h2 = dot(h, h);

  vec3 col = vec3(0.0);
  float trans = 1.0;
  bool captured = false;

  for (int i = 0; i < STEPS; i++) {
    float r2 = dot(p, p);
    float r = sqrt(r2);
    if (r < 1.0) { captured = true; break; }
    if (r2 > ESCAPE_R * ESCAPE_R && dot(p, v) > 0.0) break;
    float dt = clamp((r - 1.4) * 0.13, 0.035, 0.6);
    vec3 a = -1.5 * h2 * p / (r2 * r2 * r);
    vec3 pPrev = p;
    v = normalize(v + a * dt);
    p += v * dt;
    if (pPrev.y * p.y < 0.0) {
      float k = pPrev.y / (pPrev.y - p.y);
      vec3 hit = mix(pPrev, p, k);
      float rr = length(hit.xz);
      if (rr > DISK_IN && rr < DISK_OUT) {
        vec4 d = diskSample(hit, v);
        col += trans * d.rgb * d.a;
        trans *= 1.0 - d.a;
        if (trans < 0.02) break;
      }
    }
  }
  if (dot(p, p) < 1.0) captured = true;  // 审查 m1：掠射光线先补判再采样
  if (!captured && trans > 0.02) {
    vec3 esc = normalize(v);
    col += trans * sky(esc);
    // 用户反馈「后面的光还是没有弯曲效果」修复：恒星层按同一条弯曲后方向采样。
    // 贴图存 HDR 原值（发光恒星），采样侧压曝光——否则加法混合整体爆白（实测）。
    col += trans * textureCube(uStars, esc).rgb * 0.18;
  }

  float vig = 1.0 - 0.22 * pow(clamp(length(uv * vec2(1.0, 0.85)), 0.0, 1.0), 2.4);
  col *= max(vig, 0.0);
  gl_FragColor = vec4(col, 1.0);
}
`

const STAR_VERT = /* glsl */ `
attribute float aSize;
attribute float aPhase;
uniform float uTime;
uniform float uPointScale;  // 立方体贴图像素密度补偿（保持恒星角尺寸与主视图一致）
uniform float uEncExp;      // 编码曝光预压（贴图内亮度，防镜头采样爆白）
varying vec3 vColor;
varying float vTwinkle;
varying float vFade;
void main() {
  vColor = color;
  vTwinkle = 0.8 + 0.2 * sin(uTime * 1.3 + aPhase);
  vec4 mv = modelViewMatrix * vec4(position, 1.0);  // 审查 B1：调参时误删致 GLSL 编译失败、恒星层整体死亡——恢复
  float d = -mv.z;
  // 用户反馈「周围星体太亮 + 缩放到一定程度渲染 bug」：近距恒星淡出 + 尺寸钳。
  // 本层不再直接画到屏幕：渲染进立方体贴图，由测地线着色器按弯曲方向采样
  // （用户反馈「后面的光没有弯曲」修复）——遮挡随之自动成立，无需相机距离判定。
  vFade = smoothstep(0.8, 5.0, d) * uEncExp;  // 贴图侧预压（采样侧再压，双重防爆）
  if (d < 0.3) gl_PointSize = 0.0;
  else gl_PointSize = min(aSize * vTwinkle * uPointScale * (350.0 / d), 64.0 * uPointScale);
  gl_Position = projectionMatrix * mv;
}
`

const STAR_FRAG = /* glsl */ `
varying vec3 vColor;
varying float vTwinkle;
varying float vFade;
void main() {
  vec2 uv = gl_PointCoord - 0.5;
  float d = length(uv);
  float glow = smoothstep(0.48, 0.0, d);
  glow *= glow;
  float core = smoothstep(0.14, 0.0, d);
  // 用户反馈：放大时显示恒星表面细节（大尺寸点素加噪声纹理模拟色球层）
  if (gl_PointCoord.x >= 0.0) {
    float surfaceN = sin(uv.x * 24.0 + vColor.g * 15.0) * cos(uv.y * 20.0 + vColor.b * 12.0);
    glow *= 0.82 + surfaceN * 0.18;
  }
  // 用户反馈「周围星体太亮」：核心/光晕整体压暗 + 近距淡出
  float a = (glow * 0.5 + core * 0.85) * vTwinkle * vFade;
  if (a < 0.01) discard;
  gl_FragColor = vec4(vColor * (0.62 + core * 1.1), a);
}
`

/** 轨道半径：血缘分量按规模降序占带（最大族最内——审查 M2：compOf 已重映射为规模序，
 * 带位置按分量总数归一化，杜绝线性外推钳边堆叠）；族内主 skill（被引最高）居内缘，
 * 其余按代际+哈希外扩——"主 skill 居中心、其余围绕公转"。 */
function starRadius(map: SkillMap, name: string): number {
  const ci = map.compOf.get(name) ?? 0
  const compCount = Math.max(1, map.components.length)
  const compFrac = compCount <= 1 ? 0 : ci / (compCount - 1)
  const bandInner = 11.5 + compFrac * 24
  const bandWidth = Math.max(1.3, 5.5 - compFrac * 2)
  const comp = map.components[ci] ?? []
  const primary = comp.reduce((best, n) => {
    const s = map.byName.get(n)
    const b = map.byName.get(best)
    return (s?.indeg ?? 0) > (b?.indeg ?? -1) ? n : best
  }, comp[0] ?? name)
  const d = map.depth.get(name) ?? 0
  const off = name === primary ? 0 : Math.min(bandWidth, 0.9 + d * 0.8 + hash01(name + 'r2') * bandWidth * 0.55)
  return Math.min(bandInner + off, 42)
}

export default function BlackHole() {
  const hostRef = useRef<HTMLDivElement>(null)
  const [map, setMap] = useState<SkillMap | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [hoverName, setHoverName] = useState<string | null>(null)
  const [selName, setSelName] = useState<string | null>(null)
  const [realOnly, setRealOnly] = useState(false)
  const buildStarsRef = useRef<((m: SkillMap) => void) | null>(null)
  const pickRef = useRef<{ points: THREE.Points; order: string[] } | null>(null)
  const mapRef = useRef<SkillMap | null>(null)
  const tooltipRef = useRef<HTMLDivElement>(null)
  const placeholderRef = useRef<THREE.Points | null>(null)

  useEffect(() => {
    if (placeholderRef.current) placeholderRef.current.visible = !realOnly
  }, [realOnly, map])

  useEffect(() => {
    let alive = true
    fetchSkillMap()
      .then((m) => {
        if (!alive) return
        mapRef.current = m  // 审查 M1：applySelection 闭包必须读 ref，否则恒走 !map 早退
        setMap(m)
        buildStarsRef.current?.(m)
      })
      .catch((e) => {
        if (alive) setErr(String(e))
      })
    return () => {
      alive = false
    }
  }, [])

  useEffect(() => {
    const host = hostRef.current!
    const renderer = new THREE.WebGLRenderer({ antialias: false })
    // 用户反馈「过亮/白爆」根治：HDR 场景经 ACES 电影级色调映射压缩高光，
    // 超 1.0 的盘心/多普勒增亮不再硬剪切为纯白（此前 NoToneMapping 直接 clip）
    renderer.toneMapping = THREE.ACESFilmicToneMapping
    renderer.toneMappingExposure = 0.72
    // 光线追踪逐像素贵：自适应内部分辨率（审查 m7）
    renderer.setPixelRatio(Math.min(1, 1920 / Math.max(host.clientWidth, 1)))
    renderer.setSize(host.clientWidth, host.clientHeight)
    renderer.autoClear = false
    host.appendChild(renderer.domElement)

    // 用户反馈「后面的光还是没有弯曲效果」：技能恒星层渲染进立方体贴图，
    // 由测地线着色器按弯曲后的逃逸方向采样——恒星与背景共享同一套引力透镜
    // （爱因斯坦环随弯曲自然出现；被视界捕获的光线永不采样 → 遮挡自动成立）。
    const STAR_CUBE_SIZE = 1024
    const starCubeRT = new THREE.WebGLCubeRenderTarget(STAR_CUBE_SIZE, { type: THREE.HalfFloatType })
    const starCubeCam = new THREE.CubeCamera(0.1, 800, starCubeRT)

    // —— 场景 1：测地线背景（正交全屏四边形）——
    const bhScene = new THREE.Scene()
    const orthoCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1)
    const skyTex = new THREE.TextureLoader().load(
      'textures/milkyway.jpg',
      (t) => {
        t.colorSpace = THREE.SRGBColorSpace
        t.wrapS = THREE.RepeatWrapping
        mat.uniforms.uHasSky.value = 1
      },
      undefined,
      () => {
        mat.uniforms.uHasSky.value = 0
      },
    )
    const mat = new THREE.ShaderMaterial({
      vertexShader: VERT,
      fragmentShader: FRAG,
      uniforms: {
        uTime: { value: 0 },
        uRes: { value: new THREE.Vector2(host.clientWidth || 1, host.clientHeight || 1) },
        uCamPos: { value: new THREE.Vector3() },
        uFwd: { value: new THREE.Vector3(0, 0, -1) },
        uRight: { value: new THREE.Vector3(1, 0, 0) },
        uUp: { value: new THREE.Vector3(0, 1, 0) },
        uSky: { value: skyTex },
        uHasSky: { value: 0 },
        uStars: { value: starCubeRT.texture },
      },
      depthTest: false,
      depthWrite: false,
    })
    const quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), mat)
    quad.frustumCulled = false
    bhScene.add(quad)

    const composer = new EffectComposer(renderer)
    composer.addPass(new RenderPass(bhScene, orthoCam))
    const bloom = new UnrealBloomPass(
      new THREE.Vector2(host.clientWidth, host.clientHeight),
      0.42, // 用户反馈「中心过暗」：0.3→0.42（盘心辉光回提）
      0.5,
      0.88, // 透镜星环集中易泛光：阈值抬回（只留盘心/环最亮处）
    )
    composer.addPass(bloom)
    composer.addPass(new OutputPass())

    // —— 场景 2：技能恒星层（透视相机，叠加在测地线背景上）——
    const starScene = new THREE.Scene()
    const starCam = new THREE.PerspectiveCamera(63.6, (host.clientWidth || 1) / (host.clientHeight || 1), 0.1, 600)  // 审查 m3：与测地线 2·atan(0.62) 对齐

    // 立方体面 90° FOV / 1024px vs 主视图 fovY=63.6° / clientHeight：
    // 让恒星在贴图里占的角尺寸与直接画屏时一致（否则放大 ~1.25 倍）
    let starPointScale = (STAR_CUBE_SIZE * 1.1101) / Math.max(host.clientHeight || 1, 1)
    const syncStarScale = () => {
      starPointScale = (STAR_CUBE_SIZE * 1.1101) / Math.max(host.clientHeight || 1, 1)
      starScene.traverse((o) => {
        const m = (o as THREE.Points).material as THREE.ShaderMaterial | undefined
        if (m && m.uniforms && m.uniforms.uPointScale) m.uniforms.uPointScale.value = starPointScale
      })
    }

    const orbit = { yaw: 0.6, pitch: 0.18, tyaw: 0.6, tpitch: 0.18 }
    let dist = 16.5, tdist = 16.5  // tdist=滚轮目标，dist 每帧 lerp 追（相机平滑）
    let dragging = false
    let moved = 0
    let lastX = 0
    let lastY = 0
    let wheelCool = 0
    let downAt = 0
    const raycaster = new THREE.Raycaster()
    raycaster.params.Points = { threshold: 0.8 }  // 用户反馈：降速后扩大拾取半径确保可点选
    const mouse = { x: -10, y: -10 }
    const pick = (): string | null => {
      if (!pickRef.current) return null
      raycaster.setFromCamera(new THREE.Vector2(mouse.x, mouse.y), starCam)
      const hits = raycaster.intersectObject(pickRef.current.points)
      return hits.length ? pickRef.current.order[hits[0].index ?? -1] ?? null : null
    }
    const onDown = (e: PointerEvent) => {
      dragging = true
      moved = 0
      downAt = performance.now()
      lastX = e.clientX
      lastY = e.clientY
      const rect = host.getBoundingClientRect()  // 审查 m5：触屏 tap 无 move，按下即更新拾取坐标
      mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1
      mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1
    }
    const onDrag = (e: PointerEvent) => {
      const dx = e.clientX - lastX
      const dy = e.clientY - lastY
      if (dragging) {
        orbit.tyaw += dx * 0.006
        orbit.tpitch = Math.max(-1.35, Math.min(1.35, orbit.tpitch + dy * 0.005))
        moved += Math.abs(dx) + Math.abs(dy)
      }
      lastX = e.clientX
      lastY = e.clientY
    }
    const onUp = () => {
      if (dragging && moved < 6 && performance.now() - downAt < 500) {
        const hit = pick()
        setSelName(hit)
        applySelectionRef.current?.(hit)
      }
      dragging = false
    }
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      // 用户反馈「缩放到一定程度渲染 bug」修复：下限 1.5≈光子球，相机进入 2.6（盘内缘）
      // 以内后视线与薄盘共面、多圈环绕光线叠满整屏白带——下限提到盘内缘，保留经典近景
      tdist = Math.max(1.5, Math.min(120, tdist + e.deltaY * 0.02))  // 用户：放大到视界边缘（1.5≈光子球），视角自由
      wheelCool = 3.0
    }
    const onResize = () => {
      const w = host.clientWidth
      const h = host.clientHeight
      if (!w || !h) return
      renderer.setSize(w, h)
      composer.setSize(w, h)
      starCam.aspect = w / h
      starCam.updateProjectionMatrix()
      syncStarScale()
      mat.uniforms.uRes.value.set(w, h)
    }
    host.addEventListener('pointerdown', onDown)
    window.addEventListener('pointermove', onDrag)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
    window.addEventListener('blur', onUp)
    host.addEventListener('wheel', onWheel, { passive: false })
    window.addEventListener('resize', onResize)
    mat.uniforms.uRes.value.set(host.clientWidth || 1, host.clientHeight || 1)

    // —— 恒星层构建（数据到达后）——
    const starState: {
      geo?: THREE.BufferGeometry
      mat?: THREE.ShaderMaterial
      baseCol?: Float32Array
      order?: string[]
      radii?: Map<string, number>
      rings: THREE.LineLoop[]
    } = { rings: [] }

    const clearStars = () => {
      for (const r of starState.rings) {
        starScene.remove(r)
        r.geometry.dispose()
        ;(r.material as THREE.Material).dispose()
      }
      starState.rings = []
    }
    const buildStars = (m: SkillMap) => {
      const order = m.skills.map((s) => s.name)
      const radii = new Map<string, number>()
      for (const n of order) radii.set(n, starRadius(m, n))
      const pos = new Float32Array(order.length * 3)
      const col = new Float32Array(order.length * 3)
      const size = new Float32Array(order.length)
      const phase = new Float32Array(order.length)
      order.forEach((n, i) => {
        const s = m.byName.get(n)!
        const theme = fateColor(s.fate)
        const bright = 0.8 + Math.min(1.2, s.indeg * 0.25)
        col[i * 3] = theme[0] * bright
        col[i * 3 + 1] = theme[1] * bright
        col[i * 3 + 2] = theme[2] * bright
        size[i] = Math.min(20, 3.5 + s.indeg * 1.2 + s.chars / 400)  // 用户反馈：放大看内部细节——基础尺寸加大
        phase[i] = hash01(n + 'p') * Math.PI * 2
      })
      const geo = new THREE.BufferGeometry()
      geo.setAttribute('position', new THREE.BufferAttribute(pos, 3))
      const colAttr = new THREE.BufferAttribute(col, 3)
      geo.setAttribute('color', colAttr)
      geo.setAttribute('aSize', new THREE.BufferAttribute(size, 1))
      geo.setAttribute('aPhase', new THREE.BufferAttribute(phase, 1))
      const smat = new THREE.ShaderMaterial({
        vertexShader: STAR_VERT,
        fragmentShader: STAR_FRAG,
        uniforms: { uTime: { value: 0 }, uPointScale: { value: starPointScale }, uEncExp: { value: 0.45 } },
        vertexColors: true,
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      })
      const points = new THREE.Points(geo, smat)
      points.frustumCulled = false
      starScene.add(points)
      starState.geo = geo
      starState.mat = smat
      starState.baseCol = col.slice()
      starState.order = order
      starState.radii = radii
      pickRef.current = { points, order }
    }
    buildStarsRef.current = buildStars

    // —— 占位盘星（留给未来技能）：指数盘分布填缝，同律公转 ——
    const phN = 12000  // 用户反馈：星体数量较少→大幅扩编（12000×32B=384KB，VRAM 忽略不计）
    const phPos = new Float32Array(phN * 3)
    const phCol = new Float32Array(phN * 3)
    const phSize = new Float32Array(phN)
    const phPhase = new Float32Array(phN)
    const phR = new Float32Array(phN)
    const phTh0 = new Float32Array(phN)
    const phY = new Float32Array(phN)
    const phW = new Float32Array(phN)  // 审查 N1：角速度预计算，免每帧 pow
    for (let i = 0; i < phN; i++) {
      phR[i] = 11.5 + 30.5 * Math.pow(Math.random(), 1.6)
      phTh0[i] = Math.random() * Math.PI * 2
      phY[i] = (hash01('ph' + i) - 0.5) * 0.5
      phW[i] = 3.0 * Math.pow(phR[i], -1.5)  // 用户反馈降速：与盘/真星同步
      const cool = Math.random()
      const b = 0.35 + Math.random() * 0.3  // 用户反馈：占位星也要可见（但暗于真技能星）
      phCol[i * 3] = (cool < 0.6 ? 1.0 : 0.7) * b
      phCol[i * 3 + 1] = (cool < 0.6 ? 0.9 : 0.8) * b
      phCol[i * 3 + 2] = (cool < 0.6 ? 0.78 : 1.0) * b
      phSize[i] = 2.5 + Math.random() * 2.0  // 用户反馈：占位星也要可辨+可放大看
      phPhase[i] = Math.random() * Math.PI * 2
    }
    const phGeo = new THREE.BufferGeometry()
    phGeo.setAttribute('position', new THREE.BufferAttribute(phPos, 3))
    phGeo.setAttribute('color', new THREE.BufferAttribute(phCol, 3))
    phGeo.setAttribute('aSize', new THREE.BufferAttribute(phSize, 1))
    phGeo.setAttribute('aPhase', new THREE.BufferAttribute(phPhase, 1))
    const phMat = new THREE.ShaderMaterial({
      vertexShader: STAR_VERT,
      fragmentShader: STAR_FRAG,
      uniforms: { uTime: { value: 0 }, uPointScale: { value: starPointScale }, uEncExp: { value: 0.45 } },
      vertexColors: true,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    })
    const phPoints = new THREE.Points(phGeo, phMat)
    phPoints.frustumCulled = false
    phPoints.visible = !realOnly
    placeholderRef.current = phPoints
    starScene.add(phPoints)

    const applySelectionRef = { current: null as ((sel: string | null) => void) | null }
    applySelectionRef.current = (sel: string | null) => {
      const mp = mapRef.current  // 审查 M1：挂载帧 map 恒 null，必须走 ref
      if (!mp) return
      const lit = sel
        ? new Set<string>([sel, ...(mp.edges.filter((e) => e.child === sel || e.parent === sel)
            .flatMap((e) => [e.child, e.parent]))])
        : null
      if (starState.baseCol && starState.geo) {
        const arr = (starState.geo.getAttribute('color') as THREE.BufferAttribute).array as Float32Array
        starState.order?.forEach((n, i) => {
          const f = !lit || lit.has(n) ? 1.5 : 0.22
          arr[i * 3] = starState.baseCol![i * 3] * f
          arr[i * 3 + 1] = starState.baseCol![i * 3 + 1] * f
          arr[i * 3 + 2] = starState.baseCol![i * 3 + 2] * f
        })
        ;(starState.geo.getAttribute('color') as THREE.BufferAttribute).needsUpdate = true
      }
      clearStars()
      if (!sel) return
      // 选中恒星系：家族成员轨道环（半径已按分量规模分带）
      const fam = [...(lit ?? [])].filter((n) => mp.byName.has(n))
      for (const n of fam) {
        const r = starState.radii?.get(n)
        if (!r) continue
        const seg = 96
        const rp = new Float32Array(seg * 3)
        for (let k = 0; k < seg; k++) {
          const a = (k / seg) * Math.PI * 2
          rp[k * 3] = Math.cos(a) * r
          rp[k * 3 + 1] = 0
          rp[k * 3 + 2] = Math.sin(a) * r
        }
        const rg = new THREE.BufferGeometry()
        rg.setAttribute('position', new THREE.BufferAttribute(rp, 3))
        const ring = new THREE.LineLoop(
          rg,
          new THREE.LineBasicMaterial({ color: 0x7dd3fc, transparent: true, opacity: 0.28, blending: THREE.AdditiveBlending }),
        )
        starScene.add(ring)
        starState.rings.push(ring)
      }
    }

    let raf = 0
    const clock = new THREE.Clock()
    const fwd = new THREE.Vector3()
    const right = new THREE.Vector3()
    const up = new THREE.Vector3()
    const WORLD_UP = new THREE.Vector3(0, 1, 0)
    const onMove = (e: MouseEvent) => {
      const rect = host.getBoundingClientRect()
      mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1
      mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1
    }
    window.addEventListener('mousemove', onMove)
    const tick = () => {
      const dt = Math.min(clock.getDelta(), 0.05)
      const t = clock.getElapsedTime()
      mat.uniforms.uTime.value = t
      if (starState.mat) starState.mat.uniforms.uTime.value = t
      if (phMat && phPoints.visible) {  // 审查 N1：守卫切 Object3D.visible，realOnly 隐藏时真跳过
        phMat.uniforms.uTime.value = t
        // 占位盘星同律公转（与真技能恒星、盘条纹一致——真实剪切）；realOnly 隐藏时跳过（审查 N1）
        for (let i = 0; i < phN; i++) {
          const th = phTh0[i] + phW[i] * t
          phPos[i * 3] = Math.cos(th) * phR[i]
          phPos[i * 3 + 1] = phY[i]
          phPos[i * 3 + 2] = Math.sin(th) * phR[i]
        }
        ;(phGeo.getAttribute('position') as THREE.BufferAttribute).needsUpdate = true
      }
      wheelCool = Math.max(0, wheelCool - dt)
      if (!dragging && wheelCool <= 0) orbit.tyaw += dt * 0.03  // 用户反馈降速：自动漂移也放慢
      orbit.yaw += (orbit.tyaw - orbit.yaw) * 0.07
      orbit.pitch += (orbit.tpitch - orbit.pitch) * 0.07
      dist += (tdist - dist) * 0.10  // 相机平滑：zoom 追目标（动画打磨）
      const cp = Math.cos(orbit.pitch)
      const camPos = new THREE.Vector3(
        Math.sin(orbit.yaw) * cp * dist,
        Math.sin(orbit.pitch) * dist,
        Math.cos(orbit.yaw) * cp * dist,
      )
      fwd.copy(camPos).multiplyScalar(-1).normalize()
      right.crossVectors(fwd, WORLD_UP).normalize()
      up.crossVectors(right, fwd)
      mat.uniforms.uCamPos.value.copy(camPos)
      mat.uniforms.uFwd.value.copy(fwd)
      mat.uniforms.uRight.value.copy(right)
      mat.uniforms.uUp.value.copy(up)
      // 恒星开普勒轨道（与盘条纹同律：真实剪切）
      if (starState.geo && starState.order && starState.radii) {
        const arr = (starState.geo.getAttribute('position') as THREE.BufferAttribute).array as Float32Array
        starState.order.forEach((n, i) => {
          const r = starState.radii!.get(n) ?? 12
          const th = hash01(n + 'th') * Math.PI * 2 + 3.0 * Math.pow(r, -1.5) * t  // 用户反馈降速：同步
          // 用户反馈「星系过于规整」：每星独立轨道面倾角（±10°）+ 轻微偏心率（±5%）——
          // 真实星系内恒星不共面、轨道非正圆，整体才有立体层次
          const inc = (hash01(n + 'inc') - 0.5) * 0.34
          const ecc = 1 + (hash01(n + 'ecc') - 0.5) * 0.1
          const rx = Math.cos(th) * r * ecc
          const rz = Math.sin(th) * r
          arr[i * 3] = rx
          arr[i * 3 + 1] = rz * Math.sin(inc) + (hash01(n + 'h') - 0.5) * 0.5
          arr[i * 3 + 2] = rz * Math.cos(inc)
        })
        ;(starState.geo.getAttribute('position') as THREE.BufferAttribute).needsUpdate = true
        starCam.position.copy(camPos)
        starCam.lookAt(0, 0, 0)
        // 恒星层 → 立方体贴图（同一相机位置；autoClear 临时开启，六面各自清屏）
        starCubeCam.position.copy(camPos)
        const prevAutoClear = renderer.autoClear
        renderer.autoClear = true
        starCubeCam.update(renderer, starScene)
        renderer.autoClear = prevAutoClear
        syncStarScale()
        renderer.clear()
        composer.render()
        // 悬停拾取
        if (pickRef.current && !dragging) {
          raycaster.setFromCamera(new THREE.Vector2(mouse.x, mouse.y), starCam)
          const hits = raycaster.intersectObject(pickRef.current.points)
          const hit = hits.length ? pickRef.current.order[hits[0].index ?? -1] ?? null : null
          setHoverName((prev) => (prev === hit ? prev : hit))
          if (tooltipRef.current) {
            if (hit && hits[0].index != null && starState.geo) {
              const pa = (starState.geo.getAttribute('position') as THREE.BufferAttribute).array as Float32Array
              const i3 = hits[0].index * 3
              const v = new THREE.Vector3(pa[i3], pa[i3 + 1], pa[i3 + 2]).project(starCam)
              const rect = host.getBoundingClientRect()
              tooltipRef.current.style.left = `${((v.x + 1) / 2) * rect.width + 14}px`
              tooltipRef.current.style.top = `${((1 - v.y) / 2) * rect.height + 10}px`
              tooltipRef.current.style.display = 'block'
            } else {
              tooltipRef.current.style.display = 'none'
            }
          }
        } else if (tooltipRef.current) {
          tooltipRef.current.style.display = 'none'
        }
      } else {
        renderer.clear()
        composer.render()
      }
      raf = requestAnimationFrame(tick)
    }
    tick()

    return () => {
      cancelAnimationFrame(raf)
      host.removeEventListener('pointerdown', onDown)
      window.removeEventListener('pointermove', onDrag)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
      window.removeEventListener('blur', onUp)
      host.removeEventListener('wheel', onWheel)
      window.removeEventListener('resize', onResize)
      window.removeEventListener('mousemove', onMove)
      if (starState.geo) starState.geo.dispose()
      if (starState.mat) starState.mat.dispose()
      phGeo.dispose()
      phMat.dispose()
      clearStars()
      quad.geometry.dispose()
      mat.dispose()
      skyTex.dispose()
      bloom.dispose()
      composer.dispose()
      starCubeRT.dispose()
      renderer.dispose()
      renderer.forceContextLoss()
      host.removeChild(renderer.domElement)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

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
            return (
              <div className="ip-lineage">
                {ps.length > 0 && <div>↑ 源自：{ps.join('、')}</div>}
                {kids.length > 0 && <div>↓ 演化出：{kids.join('、')}</div>}
                {!ps.length && !kids.length && <div className="dim">（独立恒星，无血缘）</div>}
              </div>
            )
          })()}
          <div className="ip-hint">轨道环＝血缘家族 · 点空白取消</div>
        </div>
      )}
      {err && <div className="empty">数据不可达：{err}</div>}
      <div className="particle-hint">
        星系中心黑洞 + 技能恒星（血缘分量分轨道带 · 开普勒差速公转 · 暖色占位星留给未来技能）—— 拖拽环绕 · 滚轮推拉 · 悬停看名 · 点选看恒星系
      </div>
    </div>
  )
}
