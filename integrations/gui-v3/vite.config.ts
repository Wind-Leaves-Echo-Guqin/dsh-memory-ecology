import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'

/**
 * gui-v3 = 液态玻璃（Liquid Glass）版 · **当前发布版**（版本号对齐核心 3.2.0）。
 * iOS / iPadOS 操作系统外壳承载观测舱视图：原有九视图 + v3.2.0 新增七视图
 * （四门实况 / 圈的视角 / 注入观测 / 记忆包 / 圈管理 / 成本面板 / 急救箱）。
 *
 * base=/v3/ ：观测舱服务（8788）的 /v3/ 路由指向本目录 dist；`/` 也指到这里。
 *
 * 教训：`base` **必须与目录/路由一致**。从 gui-v2.3 复制过来时忘了改这一行，
 * 产出的 index.html 仍然引用 `/v2.3/assets/*.js`，于是 /v3/ 页面白屏
 * （资源 404）——构建能成功、服务能返回 200，但页面什么都不渲染（实测踩到）。
 *
 * `npm run dev` 固定 5178（与 v2.1 的 5175、v2.2 的 5176、v2.3 的 5177 并排）；
 * /api 代理到 8788。
 * cacheDir 指到本目录：node_modules 是指向 gui-v2 的目录联接，默认缓存会写穿联接污染 gui-v2。
 *
 * R8 拆包（2026-11-05）：视图改 React.lazy 后，再把 three 单独分成一个 chunk。
 * 只用动态 import 时 rolldown 仍可能把 three 塞进首个使用者所在的 chunk（Timeline
 * 静态引用 BlackHole → 首屏 1009KB 的教训），所以显式分组：
 *   - `react`：react / react-dom 运行时（发版只改业务代码时浏览器仍能命中缓存）
 *   - `three`：three.js 及其 addons（Nebula / BlackHole / LineageView 共用）
 *   - `charts`：自家图表 SVG 生成器（被时间线/驾驶舱共用，单独成块便于缓存）
 * 分组不能写成"所有 node_modules 一个 vendor"——那只是换个名字继续首屏 700KB。
 */
export default defineConfig({
  plugins: [react()],
  base: '/v3/',
  cacheDir: fileURLToPath(new URL('./.vite-cache', import.meta.url)),
  build: {
    // 拆包后单视图 chunk 仍可能超过默认 500KB 告警线（three 本身约 550KB），
    // 这是预期结果，不是缺陷；提到 800KB 让告警只对真正的回潮生效。
    chunkSizeWarningLimit: 800,
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            { name: 'react', test: /node_modules[\\/](react|react-dom|scheduler)[\\/]/ },
            { name: 'three', test: /node_modules[\\/]three[\\/]/ },
            { name: 'charts', test: /src[\\/]lib[\\/]charts\.ts$/ },
          ],
        },
      },
    },
  },
  server: {
    port: 5178,
    proxy: { '/api': { target: 'http://127.0.0.1:8788', changeOrigin: true } },
  },
})
