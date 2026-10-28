# 记忆生态 · 观测舱 v3.2.0（integrations/gui-v3）

**液态玻璃（Liquid Glass）版 · 当前发布版**——把观测舱做成一台「iOS / iPadOS」：
锁屏 → 主屏 → 应用，每个观测视图各自是一个 App，外壳尽可能复刻 iOS 的交互与视觉语言。

**版本号与核心对齐 3.2.0**（目录 `gui-v3`，路由 `/v3/`，`/` 也指到这里）。

## 视图

- **原有九视图**：驾驶舱 / 记忆库 / 经验笔记 / 技能星图 / 血缘谱系 / 候选孵化 / 时间线 / 体检 / 动作日志
- **v3.2.0 新增七视图**：四门实况 / 圈的视角 / 注入观测 / 记忆包 / 圈管理 / 成本面板 / 急救箱

新七视图走 `/api/v3.2.0/*`（读 dsh 根），旧九视图继续走 `/api/*`（读 Hermes 根）。
**两边口径不混算**，每个视图角标都写明来源。

- `src/lib/api.ts`：旧根数据层（九个旧视图）
- `src/lib/api_v3_2_0.ts`：新根数据层（七个新视图）
- localStorage 键 `eco_v23_*`（沿用，避免用户设置丢失），与其他四套前端隔离。

## 历史版本

`gui-v2` / `gui-v2.1` / `gui-v2.2` / `gui-v2.3` 全部保留（路由 `/v2/`、`/v2.1/`、`/v2.2/`、`/v2.3/` 仍可用），
但**只有 `gui-v3` 是发布入口**。桌面临捷方式指向 `启动生态观测舱v3.2.0.pyw`。

## 访问

```sh
# 观测舱服务（8788）在跑即可：
http://127.0.0.1:8788/v3/          # 当前发布版
http://127.0.0.1:8788/             # 同上（默认入口 = 最新版）

npm run dev     # http://localhost:5178/v3/（/api 代理到 8788）
npm run build   # tsc -b && vite build → dist/（dist 不入库）
```

> **改 `base` 的坑**：`vite.config.ts` 里的 `base` 必须与目录/路由一致。
> 从 gui-v2.3 复制时忘了改这一行，产出的 index.html 仍引用 `/v2.3/assets/*.js`，
> 于是 `/v3/` 白屏（资源 404），但构建成功、服务返回 200，很难一眼看出。

## iOS 元素 → 观测舱功能 对照

| iOS 元素 | 实现 | 映射到观测舱 |
|---|---|---|
| 锁定屏幕 | `components/Centers.tsx` LockScreen | 大字时钟 + 体检/水位仪表 + 最近通知；单击 / 上滑 / 任意键解锁（设置里可关） |
| 状态栏 | `components/StatusBar.tsx` | Wi-Fi = 8788 连通；信号格 = cron 近 7 天失败数；电池 = 体检评分；左侧时间开通知中心，右侧开控制中心 |
| 灵动岛 | 同上 Island | toast 在岛上展开；有告警时显示铃铛+数量；点击展开生态摘要（评分环/水位/快照/fps） |
| 主屏小组件 + 图标 + Dock | `components/HomeScreen.tsx` | 体检评分 / 记忆水位 / 告警 / 生态计数四个小组件；图标角标 = 各应用告警数；Dock 末尾显示最近使用 |
| 应用启动/返回动画 | `components/AppWindow.tsx` | 从图标位置 clip-path 放大启动，返回时缩回图标 |
| 大标题 + 悬浮玻璃导航 | 同上 | 返回按钮 / 刷新 / 导出（分享菜单）/ 更多（多任务、巡航、诊断、快捷键…） |
| iOS 26 悬浮标签栏 | 同上 TabBar | Dock 应用常驻 + 当前应用；下滑收缩为单胶囊；右侧独立搜索按钮 |
| Home 条 | 同上 HomeIndicator | 单击/上滑回主屏，双击多任务 |
| 底部卡片（Sheet） | `components/Sheets.tsx` | 替代 v2.2 侧抽屉：L2/经验/候选详情；背后应用缩小退后；拖把手下滑关闭 |
| 控制中心 | `components/Centers.tsx` | 连接模块（服务/写入方/检索 CLI/巡检）、生态卡、深浅色、锁定、字号/动效竖滑块、墙纸、多任务、待机、急救箱、导出 |
| 通知中心 | 同上 | 五源告警聚合（cron 失败/记忆越线/候选/报告 P0/悬空引用），可拷贝/去处理/本次会话忽略 |
| Spotlight | `components/Spotlight.tsx` | v2.2 顶栏三模式检索 + Ctrl+K 命令面板合一：搜应用、搜操作、回车走只读 CLI 检索 |
| 多任务 | `components/Switcher.tsx` | 最近使用的应用卡片横排；点开、上划关闭 |
| StandBy 待机 / 巡航 | 同上 | v2.2 「固定大屏」→ StandBy；「轮播九视图」保留 |
| 设置 App | `views/Settings.tsx` | iPad 双栏：墙纸 / 显示与亮度 / 动效 / 通知 / 锁定屏幕 / 关于本机 / 帮助 / 其他版本 |
| 新功能介绍 | `components/Sheets.tsx` WhatsNew | 替代 v2.2 五步引导，首次解锁后弹出 |

## 液态玻璃实现要点

- `components/kit.tsx` 的 `<Glass>`：四层结构 = 折射层（`backdrop-filter` + SVG `feDisplacementMap` 位移滤镜 `#lg-refract`）
  + 着色层 + 高光边（内阴影模拟镜面边缘，指针位置驱动高光 `--gx/--gy`）+ 内容层。
- 墙纸 = 纯 CSS 多层模糊光斑缓慢漂移（5 套：极光/晨曦/深海/暮光/石墨），离线可用。
- 配色 = iOS 系统色两套（`html[data-appearance=light|dark]`，支持跟随系统）；v1/v2 语义 token（`--panel/--text/--accent`…）
  在 `index.css` §1 统一映射到 iOS token，所以九视图类名不改就换上 iOS 组件语言（分段控件、圆角卡片、胶囊标签、iOS 列表）。
- 动效强度「轻」= 关闭全部动画与启动缩放；「中」= 关闭墙纸漂移。

## Known Gaps（明说没做的）

- **折射位移只在 Chromium 内核生效**（Edge/Chrome/Electron/Tauri-WebView2）；其余浏览器退化为磨砂模糊。
  3D 应用（技能星图 / 血缘 / 时间线）上的玻璃关掉了位移折射——画布每帧重绘，逐帧滤镜代价过高。
- **写通道仍未做**：同 v2.x，急救箱/重跑/挤出/采纳全部代理到 v0.3。
- **3D 特效未按 iOS 重着色**：Nebula/BlackHole/LineageView 原样承载（技能星图改为沉浸式全屏）。
- **主屏只有一页**，没有图标拖拽排序 / 文件夹 / 小组件编辑。
- **SF Pro 字体未内置**（版权），Windows 上回退 Segoe UI Variable / 微软雅黑。
- 构建产物单 chunk 约 950 KB（three.js 占大头），vite 有体积提示，未做代码分割。
