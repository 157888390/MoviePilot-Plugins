# STRM监控刮削（StrmScraper）· V3

监控用户配置的目录，检测新出现的 `.strm` 文件，自动调用 MoviePilot **主程序刮削链**补齐元数据。
V3 版本在原 V2 基础上完成合同迁移，并新增「电影多版本 / 电视剧单集」的识别与按需刮削能力。

## V3 迁移要点（v3.0.0 起）

本次在保留既有能力（CloudDrive2 等挂载的存储解析、刮削记录、Vuetify 详情页）的基础上新增：

| 项目 | V2 | V3 |
|------|------|------|
| 插件目录 | `plugins.v2/strmscraper` | `plugins.v3/strmscraper` |
| 索引文件 | `package.v2.json` | `package.v3.json`（`system_version: ">=3.0.0"`） |
| 插件版本 | `1.1.1` | `3.2.0`（主版本跃迁） |
| 刮削入口 | `MediaChain().scrape_metadata()` | `ScrapingChain().scrape_metadata()` |
| 日志 | `from app.log import logger` | `from app.sdk.logging import logger` |
| 文件项 | `StorageChain().get_file_item()` | 先用 `StorageChain().get_file_item()` 按 local→已配置存储（CloudDrive2/alist/rclone）顺序解析，取不到时兜底手工构造 `schemas.FileItem`（参考 `libraryscraper` V3 写法） |
| 并发 | 裸 `threading.Thread` | 主程序共享线程池 `app.runtime.thread.ThreadHelper` |
| 旧索引 | 同名条目新增 `"v3": false`，避免 V3 回退加载旧合同实现 | — |

> **不要修改 `plugins.v2/` 下的旧实现。** `package.v2.json` 中同名条目已标记 `"v3": false`，V3 只加载 `plugins.v3`。

新增的保留能力：

- **存储解析不写死 local**：先试 `local`（CloudDrive2 FUSE 挂载时命中），取不到再枚举其它已配置存储兜底
- **详情页**：未构建联邦产物时给出最简运行状态提示

## 版本变更摘要

### v3.2.0

- **按分类目录分组**：自动识别主程序 `category.yaml` 生成的分类层（国漫 / 日番 / 国产剧 / 欧美剧 / 日韩剧 / 纪录片 / 儿童 / 综艺 / 未分类，也兼容自定义分类名），AppPage 与 Page 都新增分类筛选条，每个分类显示「总数 / 待刮数」，卡片带分类标签。
- **全量刷新可分目录执行**：`/scan` 新增 `scope` 参数（`all` / `category:<分类名>` / `path:<目录>`），可只刷某个分类或某个剧集目录；`full_scan()` 支持 `paths` 过滤。并加 `_running` 互斥守卫，重复触发直接拒绝。
- **刮削记录**：用 `self.get_data_path() / "scrape_records.json"` 持久化每次刮削的时间 / 类型 / 标题 / 分类 / 结果 / 消息，上限 500 条；界面新增「刮削记录」面板，接口新增 `/records`、`/records/clear`，配置新增「记录刮削历史」开关。
- **并发改用主程序线程池**：`event_handler`、异步任务、全量刷新统一走 `ThreadHelper().submit(...)`（原先裸 `threading.Thread`）；异步任务包 `try/except/finally`，确保任务状态必然收敛，不会卡在 `running`。
- **移除定时刷新**：删除 `cron_enabled` / `cron_expression` 配置项与 `get_service()` / `scheduled_full_scan()`。
- **移除「跳过已刮削」**：删除 `skip_scraped` 开关。是否重写元数据**完全由「覆盖已有刮削结果」开关决定**。
- `/rescrape` 不再修改全局 `overwrite` 状态，改为按次强制覆盖。

### v3.1.1

按 `create-moviepilot-plugin` 规范对齐：补齐模块 / 类 / 方法的中文 docstring，新增 `plugin_label` 与索引 `labels` 保持一致。

### v3.1.0

新增插件详情页状态面板、侧栏入口开关（`sidebar_enabled`）、`/cover` 海报接口（优先本地海报 → TMDB 302），AppPage 头部内嵌设置按钮。

### v3.0.0 移除项

海报墙与刮削历史（`save_data("scrape_history")` 那版）整体移除，原因：Vuetify JSON 详情页里的海报实测不显示（TMDB URL / 图片代理 / SVG 兜底三种方式都没生效），继续维护性价比低。

- 删除 `__record_scrape_history()`、`__get_poster_path()` 及其调用点
- 删除详情页的统计卡片与合集卡片构建方法（`__build_stat_cards` / `__build_series_card`）
- 海报与历史改由 Vue 侧栏页（`AppPage.vue`）基于 `/items`、`/files` 接口自行实现（v3.2.0 起刮削记录由插件数据目录承载）

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

## 目录结构与分类识别

支持两种形态，插件会自动判定：

```
<监控目录>/
├── 国产剧/                 ← 分类目录（主程序 category.yaml 生成）
│   ├── 某剧A/              ← 剧集根目录
│   │   └── Season 1/
│   └── 某剧B/
├── 日番/
│   └── 某番C/
└── 无分类剧/               ← 没有分类层时，剧名直接挂在监控根下
    └── Season 1/
```

分类目录的判据（**不是简单的「有子目录」**，因为 `无分类剧/Season 1/` 也有子目录）：

1. 该目录**直接**含 `.strm` 文件 → 说明它自己就是剧集/电影目录，**不是**分类层；
2. 该目录至少有一个**非季目录名**（不匹配 `Season N` / `S01` / `Specials` / `Extras`）的子目录 → 才判定为分类层。

未归入任何已知分类的媒体（含直接挂在监控根下的剧）统一计入 **未分类**，排在列表最后；点它的「刷新」即对监控根做一次扫描。

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
| GET | `/overview` | 媒体总数、电影/电视剧/多版本数量、刮削状态统计、监控目录、是否在监控、是否正在扫描、当前分类 |
| GET | `/items?refresh=false` | 媒体聚合清单（含分类、季、集数、版本列表、未刮削数量） |
| GET | `/files?path=<目录>&season=<季号>` | 指定媒体的单集或版本文件明细 |
| GET | `/categories` | 按分类聚合的总数 / 待刮数 / 电影数 / 电视剧数 |
| GET | `/records?limit=100` | 最近刮削记录（时间 / 类型 / 标题 / 分类 / 结果 / 消息） |
| GET | `/records/clear` | 清空刮削记录 |
| POST | `/scrape` | body：`{"paths": [...], "target": "dir"\|"file", "overwrite": true}`，返回 `task_id` |
| GET | `/tasks?task_id=` | 任务进度与每条路径的结果；不带 `task_id` 返回最近 50 条 |
| GET | `/scan?force=false&scope=all` | 触发全量扫描；`scope` 可传 `all` / `category:<分类名>` / `path:<目录>`；`force=true` 强制重刮；已有扫描在跑时直接拒绝 |
| GET | `/strm_scan?force=false` | 同上（等价 `scope=all`），apikey 认证（旧接口，保留兼容） |

响应统一为主程序 REST 信封：`{"success": true, "message": "", "data": {}}`。

所有 `/scrape` 与 `scope=path:` 传入的路径都会校验是否在已配置的监控目录内，越权路径直接拒绝。

## 界面

**已构建联邦产物**（当前状态）：`plugins.v3/strmscraper/dist/assets/remoteEntry.js` 存在 → `get_render_mode()` 返回 `("vue", "dist/assets")`，并注册侧栏入口 `STRM刮削`（`organize` 分组）。

暴露三个远程模块：

| 模块 | 文件 | 作用 |
|------|------|------|
| `./AppPage` | `src/components/AppPage.vue` | 侧栏全页工作台：分类筛选条、统计、卡片网格、剧集/版本抽屉、刮削任务进度、刮削记录面板 |
| `./Config` | `src/components/Config.vue` | 插件配置弹窗（**必须有**：Vue 模式下宿主只渲染远程 `Config`，不会回退 Vuetify 表单） |
| `./Page` | `src/components/Page.vue` | 插件详情弹窗的概览（含分类筛选） |

若删除 `dist/` 则自动回退 `("vuetify", None)`，继续使用原有 Vuetify 配置表单，插件功能不受影响。

### 前端工程

```
plugins.v3/strmscraper/
├── package.json / package-lock.json
├── vite.config.js           # federation: name=StrmScraper, esm/esnext/minify:false/cssCodeSplit
├── index.html               # 仅 <div id="app">，本地调试用
├── src/main.js              # 本地调试入口
├── src/lib/strm.js          # 共享工具（含 categoryColor 分类配色）
├── src/components/          # AppPage.vue / Config.vue / Page.vue
└── dist/assets/             # 构建产物（已提交）
```

构建（需 Node 20+）：

```bash
cd plugins.v3/strmscraper
npm install
npm run build
```

> 在本机 Git Bash 下 `npm run build` 可能因 shim 找不到 bash 而失败，可直接调用 vite：
> `node node_modules/vite/bin/vite.js build`，再手动清掉 `dist/assets/remoteEntry.js` 的尾部空白。

产物文件名必须落在宿主的上传白名单 `__federation_*` / `_plugin-vue_export-helper-*` / `remoteEntry.js` 内 —— 当前全部符合。

约定：

- 主题色一律走 `rgb(var(--v-theme-*))` / `rgba(var(--v-theme-on-surface, ...), var(--v-medium-emphasis-opacity, ...))`，自动适配明暗。
- 全部样式 `<style scoped>`，**不得使用 `.v-` / `.mdi-` 前缀的类名**（vite.config.js 的 postcss 会剔除这些规则）。
- 不引入 Vuetify 组件，纯 HTML + CSS，避免与宿主组件注册冲突。
- 调用后端统一用 `props.api` + `pluginId` 拼 `plugin/<pluginId>/...`，不要用 `window.MoviePilotAPI`。

## 监控与并发

与 V2 一致：watchdog 递归监控多个目录，支持

- 兼容模式（轮询）：适用 CD2 / rclone / SMB 等网络挂载
- 性能模式（inotify）：仅本地磁盘

同一目标 10 分钟内不重复刮削（去重窗口 `DEDUP_TTL`），避免一季多集落盘时整剧反复重刮。

文件事件回调与异步任务都提交到主程序的共享线程池 `ThreadHelper`（`app/runtime/thread.py`，最大并发数取主程序 `CONF.threadpool`），**不在 watchdog 的分发线程上直接跑刮削**，避免重任务把后续文件事件全部堵死。

## 刮削记录

每次目录级 / 文件级刮削完成后（成功或失败）都会向 `self.get_data_path() / "scrape_records.json"` 追加一条记录，字段：时间、类型（`dir` / `file`）、标题、分类、是否成功、消息。保留最近 `RECORD_LIMIT = 500` 条（超出后从最旧开始裁剪）。

可在界面点「刮削记录」查看，或调 `/records`、`/records/clear`。配置里的「记录刮削历史」可整体关闭写入。

## 配置项

| 配置 | 说明 |
|------|------|
| 启用插件 | 开启目录实时监控 |
| 立即全量扫描一次 | 对监控目录内所有 .strm 全量刮削一次 |
| 覆盖已有刮削结果 | 对应 `scrape_metadata` 的 `overwrite` 参数。**关**则跳过已有元数据、只补缺失项；**开**则整目录重刮覆盖 |
| 记录刮削历史 | 把每次刮削的结果写入插件数据目录并在界面展示 |
| 监控模式 | 性能模式（inotify）/ 兼容模式（轮询） |
| 监控目录 | 每行一个目录 |
| 排除关键词 | 每行一个（正则），匹配的路径不刮削 |

> 关于「覆盖已有刮削结果」：宿主的「文件已存在，跳过」**只对 `backdrop.jpg` 生效**，poster / fanart / clearart / logo / thumb / banner / disc / landscape 每次都会重新下载。
> 实测：13 个媒体（10 电影 + 3 电视剧）重扫一次，下载 134 张图片，耗时 **4 分 35 秒**，其中约 88% 花在图片下载上。
> 因此**日常维护建议关闭该开关**，只在图片损坏 / 想换图源时才开启；界面上的「强制刷新」按钮等价于按次开启。
