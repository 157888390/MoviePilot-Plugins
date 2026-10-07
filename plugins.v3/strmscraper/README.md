# STRM监控刮削（StrmScraper）· V3

监控用户配置的目录，检测新出现的 `.strm` 文件，自动调用 MoviePilot **主程序刮削链**补齐元数据。  
V3 版本在原 V2 基础上完成合同迁移，并新增「电影多版本 / 电视剧单集」的识别与按需刮削能力。

## V3 迁移要点（v3.0.0 起）

本次在保留既有能力（CloudDrive2 等挂载的存储解析、刮削记录、Vuetify 详情页）的基础上新增：

| 项目   | V2                                                                  | V3                                                                                                                               |
| ---- | ------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| 插件目录 | `plugins.v2/strmscraper`                                            | `plugins.v3/strmscraper`                                                                                                         |
| 索引文件 | `package.v2.json`                                                   | `package.v3.json`（`system_version: ">=3.0.0"`）                                                                                   |
| 插件版本 | `1.1.1`                                                             | `3.3.7`（主版本跃迁）                                                                                                                   |
| 刮削入口 | `MediaChain().scrape_metadata()`                                    | `ScrapingChain().scrape_metadata()`                                                                                              |
| 日志   | `from app.log import logger`                                        | `from app.sdk.logging import logger`                                                                                             |
| 插件基类 | `from app.plugins import _PluginBase`（走 Compat 层）                   | `from app.sdk.plugin import _PluginBase`                                                                                         |
| 数据结构 | `from app import schemas` → `schemas.FileItem` / `schemas.Response` | `from app.schemas.file import FileItem`、`from app.schemas.response import Response`（canonical 归属模块）                              |
| 文件项  | `StorageChain().get_file_item()`                                    | 先用 `StorageChain().get_file_item()` 按 local→已配置存储（CloudDrive2/alist/rclone）顺序解析，取不到时兜底手工构造 `FileItem`（参考 `libraryscraper` V3 写法） |
| 并发   | 裸 `threading.Thread`                                                | 主程序共享线程池 `app.runtime.thread.ThreadHelper`                                                                                       |
| 旧索引  | 同名条目新增 `"v3": false`，避免 V3 回退加载旧合同实现                                | —                                                                                                                                |

> **不要修改 `plugins.v2/` 下的旧实现。** `package.v2.json` 中同名条目已标记 `"v3": false`，V3 只加载 `plugins.v3`。

新增的保留能力：

- **存储解析不写死 local**：先试 `local`（CloudDrive2 FUSE 挂载时命中），取不到再枚举其它已配置存储兜底
- **详情页**：未构建联邦产物时给出最简运行状态提示

## 版本变更摘要

### v3.3.7

- **修复匿名海报接口可被符号链接绕出监控目录**：`/cover` 找到本地海报后会 `resolve()` 出真实路径并重新确认仍在监控根内，指向根外文件的 `poster.jpg` 之类的软链不再被匿名读取。
- **修复插件重载期间新旧消费者并发刮削**：`stop_service()` 现在置停止标记 → 停监控 → 递增中止代次 → 等待旧 worker 真正退出（最多 30 秒）；`init_plugin()` 改为先停止再重置运行态，重载窗口内不会有两个线程同时刮同一目录。
- **重做取消机制，取消不再污染后续任务**：取消改为「队列代次」判定 —— 只有取消发生时仍在途的项会被跳过，之后新提交的任务不受影响（修复原先 `cancel all` 后所有后续任务被永久跳过的问题）；同时**正在展开的 scan 也能被取消**，其后续展开项不再继续刮。
- **状态表与刮削记录改原子写入**：临时文件 + `fsync` + `os.replace` 替换，进程中断不再留下半截 JSON 导致状态/历史整体丢失；损坏文件会改名备份并告警。
- **健壮性**：排除关键词为非法正则时只忽略该条并告警，不再中断整次扫描；扫描统一用大小写不敏感匹配，`.STRM` / `.Strm` 不再漏扫；`/scrape` 入队前统一 `resolve()`，相对路径与软链路径不会重复入队。

### v3.3.6

- **手动刮削改为默认覆盖**：界面上「整剧 / 整目录重新刮削」「刮削」「刮削选中」四个按钮现在一律带 `overwrite=true`，与宿主原生手动刮削（`api/endpoints/media.py` 恒 `overwrite=True`）语义一致 —— 点一次就把目标范围内已存在的 NFO 与图片重下一遍，不必先去配置页打开开关。
- **`/scrape` 默认值随之改为 `true`**：不传 `overwrite` 时按覆盖处理，只有显式传 `false` 才是「仅补缺失」。
- **「覆盖已有元数据」开关收窄职责**：现在只决定 `/scan` 不传 `overwrite` 时的默认值、`/retry_failed` 与外部脚本未显式指定的场景；配置页说明同步更新。

### v3.3.5

- **修复定时扫描服务一直注册失败**：`get_service()` 的 `trigger` 之前填的是 cron 字符串，宿主会把它原样当成 APScheduler 的触发器别名去查表，直接报 `No trigger by the name "0 4 * * *" was found`，界面上表现为「插件服务注册失败」、定时扫描从不执行。现改为 `CronTrigger.from_crontab()` 构造真实触发器（官方 FAQ 04 的写法），`0 4 * * *` 这类表达式可正常注册并显示下次运行时间。
- **cron 表达式增加校验与提示**：新增 `normalize_cron()` 先按「五段 + 字符集」挡手误、再由 `CronTrigger.from_crontab()` 权威解析，留空或非法（如只写了 `0 4`）不再向宿主注册，只在日志给出 WARN，并自动收敛多余空白。配置页 cron 输入框同步做行内校验：段数不对或含非法字符时标红提示，非法时禁用「保存」。

### v3.3.4

- **媒体库支持末级分类筛选**：`/items` 新增 `category` 查询参数；`__collect_items` 新增 `deepest_category()`，把 `category` 字段改为媒体所在的**最末级分类目录名**（日番 / 国产剧 / 国漫 / 欧美剧 / 动画电影 / 华语电影 / 外语电影），层级不足 2 时回退一级分类名；`category_dir` 仍指向一级分类目录，`/overview` 的分类统计口径不变。
- **详情页 UI 全新重做**：媒体库改为「左侧固定分类侧边栏 + 右侧内容独立滚动」，顶栏改为面包屑 + 大标题 + `媒体库 / 刮削记录` 分段控件，统计条精简为「待刮削 / 已刮 / 失败」三卡片。
- **队列面板与刮削记录改版**：队列状态条改为状态点 + 渐变流光进度条，统计拆成成功 / 失败 / 已取消 / 排队四个胶囊；刮削记录改为时间轴式两行布局（状态点 + 标题标签 + 消息 + 右侧徽章与时间），每行左侧带状态色条。
- **修复扫描下拉被海报遮挡**：`.strm-head` 补 `z-index`，下拉菜单不再被海报卡浮层盖住；扫描菜单改为点击展开 + 点外部 / Esc 关闭。
- **修复队列状态条被压扁**：`.strm-queue` / `.strm-alert` 补 `flex-shrink: 0`，避免在 flex 列布局里被内容区挤成细缝。
- **加载态与反馈优化**：扫描时显示骨架卡网格（shimmer）替代单行文字；统计卡数字改渐变文字、hover 上浮并展开顶部彩条；按钮补 hover 上浮与按压回落。

### v3.3.0

- **移除侧栏入口，能力并入详情页**：`get_sidebar_nav()` 恒返回空列表，删除 `sidebar_enabled` 开关与 `./AppPage` 联邦暴露，海报墙 / 分类筛选 / 搜索 / 单集与版本刮削 / 刮削记录全部由 `./Page` 承载。详情页在挂载时通过宿主的 `layout` 事件把弹窗宽度从默认 `80rem` 提到 `96rem`，补回侧栏全页的可视面积。
- **修复音乐目录被显示成「电影」**：音乐分类下的条目既没有季目录也没有集号，原先「无集号即电影」的判定会把它们落进 `movie` 分支，界面上出现「分类=音乐、类型=电影」的自相矛盾。现按分类目录名（音乐 / music / 歌曲 / 原声 / 原声带 / ost / 古典 / 古典音乐）单列 `type=music`，前端新增音乐徽标、筛选页签与统计卡。
- **全量扫描不再被去重窗口吞掉**：`full_scan()` 是显式动作，现在执行前会清掉该目录的去重键；此前刚被文件事件刮过的目录会在 10 分钟窗口内被静默跳过，界面上表现为「点了全量刷新没反应」。
- **删除目录后界面不再残留**：`/overview` 新增 `refresh` 参数（与 `/items` 对齐），详情页打开时先强制重扫清单再取统计与分类，绕开 60 秒清单缓存；`/records` 为每条记录补 `exists` 标记，目标目录已不在时界面显示「目录已删除」，记录本身作为历史保留。
- **规范化**：`app.core.config` → `app.sdk.config`（2 处）；裸 `threading.Timer` → `ThreadHelper`（并复用 `_running` 互斥）；`_scraped` / `_tasks` / `_poster_cache` 三个无界缓存补淘汰；删除从未被调用的 `__already_scraped()` 与未被引用的 `DEFAULT_TV_CATEGORIES`；前端删除后端并不存在的 `skip_scraped` / `cron_enabled` / `cron_expression` 死配置。

### v3.2.0

- **按分类目录分组**：自动识别主程序 `category.yaml` 生成的分类层（国漫 / 日番 / 国产剧 / 欧美剧 / 日韩剧 / 纪录片 / 儿童 / 综艺 / 未分类，也兼容自定义分类名），AppPage 与 Page 都新增分类筛选条，每个分类显示「总数 / 待刮数」，卡片带分类标签。
- **全量刷新可分目录执行**：`/scan` 新增 `scope` 参数，`scope=category` 时配合 `paths=<逗号分隔的分类目录绝对路径>` 只刷指定分类；`full_scan()` 支持 `paths` 过滤。并加 `_running` 互斥守卫，重复触发直接拒绝。
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
- 海报与历史改由 Vue 联邦界面基于 `/items`、`/files` 接口自行实现（v3.2.0 起刮削记录由插件数据目录承载；v3.3.0 起侧栏全页 `AppPage.vue` 已合并进详情页 `Page.vue`）

## 两种刮削目标

主程序刮削链对「目录」与「文件」的产出不同，按场景选择：

| 目标           | `target` | 构造方式                                              | 产出                                                                                                                      |
| ------------ | -------- | ------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| 剧集根目录 / 电影目录 | `dir`    | `FileItem(type="dir", path="<目录>/")`，路径必须以 `/` 结尾 | `tvshow.nfo` / `movie.nfo`、`poster`、`backdrop`、`logo`、`banner`、`thumb`、`season01-poster`、`Season 1/season.nfo` 及各单集 nfo |
| 单个 `.strm`   | `file`   | `FileItem(type="file", extension="strm")`         | 该单集/该版本的 `.nfo` 与单集图                                                                                                    |

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

未归入任何已知分类的媒体（含直接挂在监控根下的剧）统一计入 **未分类**，排在列表最后。该分组在二级分类里的 `path` 回落为分类目录本身，因此对它做 `scope=category` 扫描等价于扫整个分类目录。

分类层可以**嵌套**：对已识别的分类目录再判一次，若其下仍构成分类层（如  
`<监控目录>/电视剧/日番/<剧名>/`），这一层即为**二级分类**。`/categories` 会把它们放进父分类的  
`children`（同样带总数 / 待刮数 / 电影数 / 电视剧数 / 音乐数），界面在选中「电影」「电视剧」类型时  
以二级分类抽屉呈现；散装在分类目录下、没进任何二级分类的媒体归入该分类下的「未分类」子分组。

## 媒体识别规则

扫描监控目录时按以下规则聚合：

1. `.strm` 向上跳过 `Season N` / `S01` / `Specials` / `Extras` 等季目录，定位到剧集根目录。
2. 条目所在**分类目录名**命中音乐类名单（`MUSIC_CATEGORIES`：音乐 / music / 歌曲 / 原声 / 原声带 / ost / 古典 / 古典音乐）→ **音乐**，该判定优先级最高：音乐目录里可能出现形如 `01 - 某曲.strm` 的文件名，只按集号特征判断会被误判成电视剧。
3. 目录下存在季目录，或文件名带 `SxxExx` / `第N集` / `.E02.` 等集号特征 → **电视剧**，按季分组、解析季号与集号。
4. 否则 → **电影**，目录下所有 `.strm` 作为**版本文件**列出，多于 1 个即标记为多版本。
5. 单个文件的刮削状态由同级同名 `.nfo` 是否存在判定；目录级状态由 `tvshow.nfo` / `movie.nfo` 判定，音乐目录用「全部曲目均已刮削」代表整目录完成。

扫描有 `MAX_SCAN_FILES = 20000` 上限保护，结果缓存 `LIST_CACHE_TTL = 60` 秒。

## 接口（全部 `auth: "bear"`）

前端用宿主注入的 `api` 调用，路径前缀 `plugin/StrmScraper`：

| 方法   | 路径                                                                                       | 说明                                                                                                                                                                                                    |
| ---- | ---------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET  | `/overview?refresh=false`                                                                | 媒体总数、电影/电视剧/音乐/多版本数量、刮削状态四计数 `status`（`scraped`/`failed`/`skipped`/`pending`）、监控目录、是否在监控，以及队列轻量摘要 `busy` / `queued`；`refresh=true` 绕过 60 秒清单缓存重扫                                                      |
| GET  | `/items?refresh=false`                                                                   | 媒体聚合清单。每条含条目级状态 `status` / `error_code` / `error_message`（取自 `scrape_state.json`，`.nfo` 只作兜底）与两个时间 `last_file_change`（文件 mtime）/ `last_scrape`（真实刮削时间）；文件明细（单集/版本）同样带 `status` / `error_code`         |
| GET  | `/files?path=<目录>&season=<季号>`                                                           | 指定媒体的单集或版本文件明细（每个文件带 `status` / `error_code` / `error_message` 四态状态）                                                                                                                                  |
| GET  | `/categories`                                                                            | 按分类聚合的总数 / 待刮数 / 电影数 / 电视剧数 / 音乐数；每个分类的 `children` 为其二级分类的同名字段，空的「未分类」子分组会被剔除                                                                                                                         |
| GET  | `/records?limit=200&success=true&type=dir&category=日番&since=2026-09-01&until=2026-09-26` | 最近刮削记录（时间 / 类型 / 标题 / 分类 / 结果 / 消息 / `error_code` / `exists` 目标是否仍存在）；`success` 只取成功/失败，`type` 只取 `dir`/`file`，`category` 只取该分类，`since`/`until` 为 `YYYY-MM-DD` 闭区间                                    |
| GET  | `/records/clear`                                                                         | 清空刮削记录                                                                                                                                                                                                |
| GET  | `/queue`                                                                                 | 队列快照：`running`（正在执行的目标，含 `kind`）、`queued`（前 50 条）、`queued_total`、`stats`（`done` / `failed` / `canceled` 累计）、`recent`（最近 20 条完成项）、`busy`。界面只轮询这一个接口                                                    |
| POST | `/scrape`                                                                                | body：`{"paths": [...], "target": "dir"\|"file", "overwrite": true}`。这是**手动刮削**入口，`overwrite` 省略时默认为 `true`（恒覆盖，与宿主原生手动刮削一致），显式传 `false` 才是「仅补缺失」；返回 `{"queued": n, "deduped": m}`                                                                        |
| POST | `/queue/cancel`                                                                          | body：`{"mode": "all"\|"one", "target": "目录路径", "scope": "扫描范围"}`。`mode=all` 清空整个队列，`mode=one` 只取消匹配 `target`（含子树）或 `scope` 的项；正在执行的项不强行中断，只停止取后续项。返回 `{"canceled": n}`                                |
| POST | `/retry_failed`                                                                          | body：`{"category": "分类路径"}`（可省略）。读状态表把 `status=failed` 的目录批量重新入队，返回 `{"queued": n, "deduped": m}`                                                                                                     |
| GET  | `/scan?overwrite=false&scope=all&paths=<目录1,目录2>`                                        | 把一次扫描并入队列并立即返回。`scope` 四种：`all`=全部监控目录；`category`=只扫 `paths` 给出的分类目录（逗号分隔绝对路径，须在监控目录内）；`incremental`=只补新增与未刮的目录；`unscraped`=只刮状态非「已刮」的目录。`overwrite` 不传时跟随插件配置的「覆盖已有元数据」开关。重复的同类扫描只计一次（返回 `queued=0`） |
| GET  | `/strm_scan?overwrite=false`                                                             | 同上（等价 `scope=all`），apikey 认证（旧接口，保留兼容）                                                                                                                                                                |

响应统一为主程序 REST 信封：`{"success": true, "message": "", "data": {}}`。

所有 `/scrape`、`/files` 与 `scope=category` 传入的路径都会校验是否在已配置的监控目录内，越权路径直接拒绝。

## 刮削队列

所有刮削都走同一条队列，界面上的按钮、文件监控事件、扫描展开项最终都变成队列里的一项：

```
用户点「全量扫描」 ─┐
文件监控事件     ─┼─→ _queue（单一队列，带 key/kind/target/overwrite/source）
界面刮削按钮     ─┘        │
                           ▼
                 __worker_loop（唯一消费线程，串行取出）
                           │
              ┌────────────┴────────────┐
        kind=scan                  kind=dir / file
     枚举目录后重新入队             __scrape_target（唯一刮削入口）
```

关键性质：

- **同一目录不会被并发刮削**由「只有一个消费者」结构性保证，不依赖互斥标记位。
- **去重按目标**：同一 `key` 重复入队只计 `deduped`；`overwrite` 更高时就地升级已有待办项；目录项自动吞掉队内位于其下的文件项（目录级 `recursive=True` 本就递归整棵子树）。正在执行的那一项也算「在队内」，不会被重复排一份。
- **扫描判重不靠时间窗口**：`scan` 项被 worker 取走展开后就离开队列了，仅靠 `key` 判重挡不住「几秒内连点两次」。因此展开出的目录项都带 `origin=<scan key>` 标记，只要该扫描还有一个展开项没跑完，重复提交同一范围的扫描就直接判重；展开项全部离开队列后令牌立即失效，**跑完后再点一次一定能提交**（不引入任何 TTL，不会误吞健康的重扫）。
- **用户源优先**：`source=user` 的项插在 `source=event` 之前 —— 用户正等着看结果，而文件事件还可能持续涌入。
- **单项异常不终止队列**：worker 逐项 `try/except`，失败项记入 `stats.failed` 后继续下一项。
- **重载即退场**：worker 持有启动时的 `_abort_epoch`，插件重载后旧消费线程在下一轮循环自行退出；`stop_service()` 会等待其真正退出（最多 30 秒）后再让新实例接管，避免新旧消费者并发刮削。
- **取消不硬杀当前项**：`/queue/cancel` 把待办项移出队列并登记到「取消代次」（`_cancel_record`），worker 取项时命中即跳过；正在执行的那一项不强行中断（主程序刮削链不支持半路打断），只停止取后续项。取消状态只在本次取消之后、新任务入队之前有效，**不会永久跳过后续任务**；正在展开的 scan 也会被登记，其展开出的目录项一并跳过。取消计数记入 `stats.canceled`。
- **失败可批量重试**：`/retry_failed` 读状态表把 `status=failed` 的目录重新入队（`source=user`），可选 `category` 限定范围。
- **进度只读 `/queue`**：界面按「忙 2s / 闲 8s」轮询该接口，队列由忙转闲的那一次静默重算清单与统计。

## 界面

**已构建联邦产物**（当前状态）：`plugins.v3/strmscraper/dist/assets/remoteEntry.js` 存在 → `get_render_mode()` 返回 `("vue", "dist/assets")`。

插件**不注册主界面侧栏入口**（`get_sidebar_nav()` 恒返回空），全部界面收敛在插件中心的详情弹窗，只暴露两个远程模块：

| 模块         | 文件                          | 作用                                                                                                                                                                      |
| ---------- | --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `./Page`   | `src/components/Page.vue`   | 详情页：统计/类型/状态筛选合并成一行可点击 chip（数字即筛选入口）、二级分类下拉（多选）+ 面包屑、关键词搜索、海报墙（四态状态圆点 + 失败红字）、内联详情（文件更新/上次刮削两行时间、季/集与多版本四态图标 + tooltip）、可展开队列面板（逐项取消/取消全部/重试失败项）、刮削记录页（状态/类型/分类/时间范围筛选） |
| `./Config` | `src/components/Config.vue` | 插件配置弹窗（**必须有**：Vue 模式下宿主只渲染远程 `Config`，不会回退 Vuetify 表单）                                                                                                                 |


头部按钮只有四个：**设置 / 刷新 / 全量扫描（下拉）/ 关闭**。

- **刷新**只重建清单（重扫目录 + 刷新统计 + 拉一次 `/queue`），**不触发任何刮削** —— 刮削是重活，刷新按钮刻意保持廉价，避免一次误点把整个媒体库重刮一遍。
- **全量扫描**是一个下拉：`仅补缺失`（跳过已有元数据）/ `覆盖重刮`（重下全部 NFO 与图片）/ `增量扫描`（`scope=incremental`，只补新增与未刮）/ `补漏扫描`（`scope=unscraped`，只刮状态非「已刮」）；当前选中的二级分类存在时，菜单额外给出「仅分类『X』」下的补缺失/覆盖两个选项（对应 `scope=category`）。原先并排的「全量扫描 / 强制全量」两个按钮只差一个布尔值，已合并。
- 顶部「队列」chip 可点击展开**队列面板**：显示正在执行项、排队列表（每项可单独取消）、最近完成、`本次成功/失败/已取消` 统计，以及「取消全部」「重试失败项」按钮；队列忙碌时显示不确定进度条，此时所有写按钮禁用。

`Page` 会收到宿主传入的 `api` / `pluginId` / `sourcePluginId` / `nativeSubscribe` / `show_switch`，并可向上抛 `action`（请求宿主重载）、`switch`（请求切到配置弹窗）、`close`（关闭弹窗）、`layout`（声明弹窗宽度，本插件声明 `{ maxWidth: '96rem' }`，宿主默认 `80rem`）。

> `show_switch` 是宿主唯一用 snake_case 下发的 prop（`PluginDataDialog` 绑定 `:show_switch`），组件内必须按原样声明 `show_switch`；写成 `showSwitch` 会因 Vue 的 prop 归一化规则匹配不上，设置按钮永远不显示。

若删除 `dist/` 则自动回退 `("vuetify", None)`，继续使用原有 Vuetify 配置表单，插件功能不受影响。

### 前端工程

```
plugins.v3/strmscraper/
├── package.json / package-lock.json
├── vite.config.js           # federation: name=StrmScraper, exposes=./Page + ./Config, esm/esnext/minify:false/cssCodeSplit
├── index.html               # 仅 <div id="app">，本地调试用
├── src/main.js              # 本地调试入口（挂载 Page）
├── src/lib/strm.js          # 共享工具：api 调用封装、海报地址、分类配色、类型标签（typeLabel/typeClass）、时间格式化
├── src/components/          # Page.vue（详情页，唯一 UI 入口） / Config.vue（配置弹窗）
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

10 分钟去重窗口 `DEDUP_TTL` **只作用于文件事件源**，避免一季多集落盘时整剧反复重刮。

> **显式动作不受此窗口限制**：全量扫描与界面上的重刮走队列，队列本身按目标 key 去重，不再经过时间窗口。此前刚被事件刮过的目录会在 10 分钟内被窗口静默跳过，界面上表现为「点了全量刷新没反应」。  
> 去重表在条目数超过 `DEDUP_PRUNE_THRESHOLD = 128` 时淘汰已过期条目，不会随运行时长无限增长。

「立即全量扫描一次」的 3 秒延迟由 `ThreadHelper` 线程池承担（不再用裸 `threading.Timer`），延迟结束后把一次 `scope=all` 扫描并入队列。

文件事件回调与扫描入队都提交到主程序的共享线程池 `ThreadHelper`（`app/runtime/thread.py`，最大并发数取主程序 `CONF.threadpool`），**不在 watchdog 的分发线程上直接跑刮削**，避免重任务把后续文件事件全部堵死；实际刮削仍由队列里那唯一一个消费线程串行执行。

## 刮削记录

每次目录级 / 文件级刮削完成后（成功或失败）都会向 `self.get_data_path() / "scrape_records.json"` 追加一条记录，字段：时间、类型（`dir` / `file`）、标题、分类、是否成功、消息、`error_code`（结构化失败原因：`not_matched` / `timeout` / `permission` / `path_gone` / `unknown`，成功为空串）。保留最近 `RECORD_LIMIT = 500` 条（超出后从最旧开始裁剪）。读-改-写整体由 `_record_lock` 保护，避免并发写丢记录。

可在界面点「刮削记录」查看，或调 `/records`、`/records/clear`。配置里的「记录刮削历史」可整体关闭写入。

## 配置项

| 配置             | 说明                                                                                                                          |
| -------------- | --------------------------------------------------------------------------------------------------------------------------- |
| 启用插件           | 开启目录实时监控                                                                                                                    |
| 立即全量扫描一次       | 对监控目录内所有 .strm 全量刮削一次                                                                                                       |
| 覆盖已有刮削结果       | 对应 `scrape_metadata` 的 `overwrite` 参数。**关**则跳过已有元数据、只补缺失项；**开**则整目录重刮覆盖。同时是 `/scan` 不传 `overwrite` 时的默认值，也是 `/retry_failed` 与外部脚本未显式指定时的取值。**界面上的单条 / 整剧刮削按钮不受此开关影响，恒为覆盖** |
| 记录刮削历史         | 把每次刮削的结果写入插件数据目录并在界面展示                                                                                                      |
| 监控模式           | 性能模式（inotify）/ 兼容模式（轮询）                                                                                                     |
| 监控目录           | 每行一个目录                                                                                                                      |
| 排除关键词          | 每行一个（正则），匹配的路径不刮削                                                                                                           |
| 定时扫描（cron 表达式） | 留空关闭；配置后由宿主调度器（`get_service` 契约）到点把一次 `scope=all` 全量扫描并入队列，例如 `0 3 * * *` 表示每天 03:00 补漏扫描一次                                 |

> 关于「覆盖已有刮削结果」：V3 的覆盖粒度由宿主「设置 → 刮削」的**逐类型策略**（`ScrapingConfig`，系统配置键 `ScrapingSwitchs`）决定，默认全部为 `MISSINGONLY` —— 文件已存在就跳过，且 **NFO 与每一张图片分别判断**（`ScrapingChain._should_scrape`）；仅 `music_lyrics` 默认为 `UPGRADE`。传 `overwrite=True` 时该值**压过**逐类型策略，已存在的 NFO 与全部图片一并重写。  
> 实测：13 个媒体（10 电影 + 3 电视剧）重扫一次，下载 134 张图片，耗时 **4 分 35 秒**，其中约 88% 花在图片下载上。  
> 因此**日常维护建议关闭该开关**，只在图片损坏 / 想换图源时才开启；界面上的「全量扫描 → 覆盖重刮」等价于按次开启。  
> 注意：**界面上的单条 / 整剧刮削按钮恒为覆盖**（v3.3.6 起，与宿主原生手动刮削一致），不随该开关变化；只想补缺失请走「全量扫描 → 仅补缺失 / 增量扫描 / 补漏扫描」。
