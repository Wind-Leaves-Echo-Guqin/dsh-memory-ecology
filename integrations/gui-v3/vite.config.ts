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
 */
export default defineConfig({
  plugins: [react()],
  base: '/v3/',
  cacheDir: fileURLToPath(new URL('./.vite-cache', import.meta.url)),
  server: {
    port: 5178,
    proxy: { '/api': { target: 'http://127.0.0.1:8788', changeOrigin: true } },
  },
})
