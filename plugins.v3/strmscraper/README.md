# STRM监控刮削（StrmScraper）· V3

监控用户配置的目录，检测新出现的 `.strm` 文件，自动调用 MoviePilot **主程序刮削链**补齐元数据。
V3 版本在原 V2 基础上完成合同迁移，并新增「电影多版本 / 电视剧单集」的识别与按需刮削能力。

## V3 迁移要点（v3.0.0 起）

本次在保留既有能力（CloudDrive2 等挂载的存储解析、定时全量刷新、刮削历史、Vuetify 详情页）的基础上新增：

| 项目 | V2 | V3 |
|------|------|------|
| 插件目录 | `plugins.v2/strmscraper` | `plugins.v3/strmscraper` |
| 索引文件 | `package.v2.json` | `package.v3.json`（`system_version: ">=3.0.0"`） |
| 插件版本 | `1.1.1` | `3.0.0`（主版本跃迁） |
| 刮削入口 | `MediaChain().scrape_metadata()` | `ScrapingChain().scrape_metadata()` |
| 日志 | `from app.log import logger` | `from app.sdk.logging import logger` |
| 文件项 | `StorageChain().get_file_item()` | 先用 `StorageChain().get_file_item()` 按 local→已配置存储（CloudDrive2/alist/rclone）顺序解析，取不到时兜底手工构造 `schemas.FileItem`（参考 `libraryscraper` V3 写法） |
| 旧索引 | 同名条目新增 `"v3": false`，避免 V3 回退加载旧合同实现 | — |

> **不要修改 `plugins.v2/` 下的旧实现。** `package.v2.json` 中同名条目已标记 `"v3": false`，V3 只加载 `plugins.v3`。

新增的保留能力：

- **存储解析不写死 local**：先试 `local`（CloudDrive2 FUSE 挂载时命中），取不到再枚举其它已配置存储兜底
- **定时全量刷新**：`get_service()` 注册本体 APScheduler 的 cron 任务
- ~~**刮削历史**：`save_data("scrape_history")`，供 Vuetify 详情页展示海报/集数/时间~~（v3.0.0 已移除，见下）
- **详情页**：未构建联邦产物时给出最简运行状态提示

## v3.0.0 移除项

海报墙与刮削历史**已整体移除**，原因：Vuetify JSON 详情页里的海报实测不显示（TMDB URL / 图片代理 / SVG 兜底三种方式都没生效），继续维护性价比低。

- 删除 `__record_scrape_history()`、`__get_poster_path()` 及其调用点
- 删除详情页的统计卡片与合集卡片构建方法（`__build_stat_cards` / `__build_series_card`）
- 删除残留的 `scrape_history` 插件数据（插件初始化时清理一次）
- 清理不再使用的导入：`json`、`datetime`、`MediaChain`、`MediaType`
- 海报与历史改由 Vue 侧栏页（`AppPage.vue`）基于 `/items`、`/files` 接口自行实现

## 两种刮削目标

主程序刮削链对「目录」与「文件」的产出不同，按场景选择：

| 目标 | `target` | 构造方式 | 产出 |
|------|----------|----------|------|
| 剧集根目录 / 电影目录 | `dir` | `FileItem(type="dir", path="<目录>/")`，路径必须以 `/` 结尾 | `tvshow.nfo` / `movie.nfo`、`poster`、`backdrop`、`logo`、`banner`、`thumb`、`season01-poster`、`Season 1/season.nfo` 及各单集 nfo |
| 单个 `.strm` | `file` | `FileItem(type="file", extension="strm")` | 该单集/该版本的 `.nfo` 与单集图 |

`.strm` 位于主程序 `settings.RMT_MEDIAEXT` 白名单内（`app/runtime/config.py`），因此**单文件刮削受支持**，可直接用于：

- 电视剧：只补某几集（例如刮削失败或后加入的单集）
- 电影多版本：只刮削指定的那个版本文件

实践建议：**先单文件补齐缺失项，剧集级文件缺失时再对根目录做一次目录级刮削**，两套产物互补、不冲突。

## 媒体识别规则

扫描监控目录时按以下规则聚合：

1. `.strm` 向上跳过 `Season N` / `S01` / `Specials` / `Extras` 等季目录，定位到剧集根目录。
2. 目录下存在季目录，或文件名带 `SxxExx` / `第N集` / `.E02.` 等集号特征 → **电视剧**，按季分组、解析季号与集号。
3. 否则 → **电影**，目录下所有 `.strm` 作为**版本文件**列出，多于 1 个即标记为多版本。
4. 单个文件的刮削状态由同级同名 `.nfo` 是否存在判定；目录级状态由 `tvshow.nfo` / `movie.nfo` 判定。

扫描有 `MAX_SCAN_FILES = 20000` 上限保护，结果缓存 `LIST_CACHE_TTL = 60` 秒。

## 接口（全部 `auth: "bear"`）

前端用宿主注入的 `api` 调用，路径前缀 `plugin/StrmScraper`：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/overview` | 媒体总数、电影/电视剧/多版本数量、刮削状态统计、监控目录与是否在监控 |
| GET | `/items?refresh=false` | 媒体聚合清单（含季、集数、版本列表、未刮削数量） |
| GET | `/files?path=<目录>&season=<季号>` | 指定媒体的单集或版本文件明细 |
| POST | `/scrape` | body：`{"paths": [...], "target": "dir"\|"file", "overwrite": true}`，返回 `task_id` |
| GET | `/tasks?task_id=` | 任务进度与每条路径的结果；不带 `task_id` 返回最近 50 条 |
| GET | `/scan?force=false` | 触发一次全量扫描（目录级刮削）；`force=true` 时忽略「跳过已刮削」开关 |
| GET | `/strm_scan?force=false` | 同上，apikey 认证（旧接口，保留兼容） |

响应统一为主程序 REST 信封：`{"success": true, "message": "", "data": {}}`。

所有 `/scrape` 传入的路径都会校验是否在已配置的监控目录内，越权路径直接拒绝。

## 界面

**已构建联邦产物**（当前状态）：`plugins.v3/strmscraper/dist/assets/remoteEntry.js` 存在 → `get_render_mode()` 返回 `("vue", "dist/assets")`，并注册侧栏入口 `STRM刮削`（`organize` 分组）。

暴露三个远程模块：

| 模块 | 文件 | 作用 |
|------|------|------|
| `./AppPage` | `src/components/AppPage.vue` | 侧栏全页工作台：统计、筛选、卡片网格、剧集/版本抽屉、刮削任务进度 |
| `./Config` | `src/components/Config.vue` | 插件配置弹窗（**必须有**：Vue 模式下宿主只渲染远程 `Config`，不会回退 Vuetify 表单） |
| `./Page` | `src/components/Page.vue` | 插件详情弹窗的概览 |

若删除 `dist/` 则自动回退 `("vuetify", None)`，继续使用原有 Vuetify 配置表单，插件功能不受影响。

### 前端工程

```
plugins.v3/strmscraper/
├── package.json / package-lock.json
├── vite.config.js           # federation: name=StrmScraper, esm/esnext/minify:false/cssCodeSplit
├── index.html               # 仅 <div id="app">，本地调试用
├── src/main.js              # 本地调试入口
├── src/components/          # AppPage.vue / Config.vue / Page.vue
└── dist/assets/             # 构建产物（已提交）
```

构建（需 Node 20+）：

```bash
cd plugins.v3/strmscraper
npm install
npm run build
```

产物文件名必须落在宿主的上传白名单 `__federation_*` / `_plugin-vue_export-helper-*` / `remoteEntry.js` 内 —— 当前全部符合。

约定：

- 主题色一律走 `rgb(var(--v-theme-*))` / `rgba(var(--v-theme-on-surface, ...), var(--v-medium-emphasis-opacity, ...))`，自动适配明暗。
- 全部样式 `<style scoped>`，**不得使用 `.v-` / `.mdi-` 前缀的类名**（vite.config.js 的 postcss 会剔除这些规则）。
- 不引入 Vuetify 组件，纯 HTML + CSS，避免与宿主组件注册冲突。
- 调用后端统一用 `props.api` + `pluginId` 拼 `plugin/<pluginId>/...`，不要用 `window.MoviePilotAPI`。

## 监控

与 V2 一致：watchdog 递归监控多个目录，支持

- 兼容模式（轮询）：适用 CD2 / rclone / SMB 等网络挂载
- 性能模式（inotify）：仅本地磁盘

同一目标 10 分钟内不重复刮削（去重窗口 `DEDUP_TTL`），避免一季多集落盘时整剧反复重刮。

### 全量扫描跳过已刮削（默认开启）

宿主的「文件已存在，跳过」**只对 `backdrop.jpg` 生效**，poster / fanart / clearart / logo / thumb / banner / disc / landscape 每次全量扫描都会重新下载。

实测：13 个媒体（10 电影 + 3 电视剧）重扫一次，下载 134 张图片，耗时 **4 分 35 秒**，其中约 88% 花在图片下载上；已刮过和首次刮削的耗时差距只有 12~20%。

因此插件侧在 `full_scan()` 里加了整目录跳过：

- **电影**：目录下每个 `.strm` 都有同名 `.nfo` → 跳过
- **电视剧**：每个单集都有同名 `.nfo`，**且**根目录存在 `tvshow.nfo` → 跳过

任一条件不满足就照常刮削，所以补新集、补缺失图片不会漏。日志会打印 `共 N 个目录，刮削 X 个，跳过 Y 个已刮削`。

需要强制重刮时二选一：关掉「全量扫描跳过已刮削」开关，或调用 `/strm_scan?force=true`（`/scan` 同）。

## 配置项

| 配置 | 说明 |
|------|------|
| 启用插件 | 开启目录实时监控 |
| 立即全量扫描一次 | 对监控目录内所有 .strm 全量刮削一次 |
| 覆盖已有元数据 | 对应 `scrape_metadata` 的 `overwrite` 参数 |
| 全量扫描跳过已刮削 | 默认开启，跳过 nfo 已齐全的目录（详见上文） |
| 监控模式 | 性能模式（inotify）/ 兼容模式（轮询） |
| 监控目录 | 每行一个目录 |
| 排除关键词 | 每行一个（正则），匹配的路径不刮削 |
