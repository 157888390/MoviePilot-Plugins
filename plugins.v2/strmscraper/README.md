# STRM监控刮削（StrmScraper）

监控用户配置的目录，检测新出现的 `.strm` 文件，并自动调用 MoviePilot **主程序**的刮削功能补齐元数据。

## 设计原则

1. **刮削完全走本体**：插件只负责"发现 `.strm` + 定位剧集根目录"，实际刮削通过 `MediaChain().scrape_metadata(fileitem=目录)` 完成——这与 UI 手动刮削目录、文件整理后自动刮削、工作流"刮削文件"动作是**同一个入口**。
2. **记录统一由本体管理**：NFO 文件、海报图片、刮削历史等全部由主程序生成和管理，插件自身不落任何元数据、不维护私有刮削记录，与其他方式触发的刮削记录保持完全一致。
3. **按"目录"刮削才能出完整产物**：主程序对**单文件**刮削（`_handle_tv_episode_file`）只写单集 `.nfo`/单集图，**不会**写 `tvshow.nfo`、也不会下载 `poster/backdrop/logo/banner/thumb/season01-poster`、`Season 1/season.nfo`；只有对**目录**刮削（`_handle_tv_directory`）才会产出上述剧集级文件。因此本插件在发现 `.strm` 后会向上回退到剧集根目录，对该目录整体刮削，从而与本体手动刮削产物完全一致。
4. **`.strm` 是本体一等公民**：`.strm` 位于主程序 `settings.RMT_MEDIAEXT` 媒体扩展名白名单中，`scrape_metadata` 原生支持。

## 功能

- **实时监控**：基于 watchdog 监控多个目录（递归），支持：
  - 性能模式（inotify，本地磁盘）
  - 兼容模式（轮询，适用于 CD2 / rclone / SMB 等网络挂载）
- **就地刮削、不转移**：发现 `.strm` 后只在它所在的当前目录（向上定位到剧集根目录）就地调用主程序刮削，**不移动/复制文件**。
- **轻量去重**：同一剧集根目录 10 分钟内不重复刮削，避免一季多集落盘时整剧反复重刮。
- **全量扫描**："立即运行一次"开关（或 `/strm_scan` API）对所有监控目录做一次 `*.strm` 全量刮削。
- **排除关键词**、**覆盖模式** 可配置。

## 实现说明（借鉴目录实时监控插件）

整体流程与官方「目录实时监控」(`CloudLinkMonitor`) 一致，但去掉了转移/软链/Strm 联动等逻辑，只保留"监控→刮削"：

- `__init__.py` 内 `_StrmHandler(FileSystemEventHandler)` 把 watchdog 的 `on_created` / `on_moved` 事件直接转给 `event_handler`。
- `event_handler` 过滤 `.strm`、排除关键词、回收站/隐藏文件后，同步调用 `__scrape`。
- `__scrape` 用主程序 `StorageChain().get_file_item(storage="local", path=目录)` 取得 `type=dir` 的文件项，再调用 `MediaChain().scrape_metadata(fileitem=目录, overwrite=...)`。
- 不引入 APScheduler、去抖 worker 线程、单文件识别/取图等额外机制——刮削识别由主程序在目录刮削分支内部完成。

## 刮削链路

```
watchdog 发现新 .strm（on_created / on_moved）
  → event_handler 过滤 .strm / 排除关键词 / 回收站
  → __series_root() 向上回退到剧集根目录
      （电视剧：跳过 Season 1 / S01 / Specials / Extras 等季目录；电影：直接停在电影目录）
  → storagechain.get_file_item(目录) → type=dir 文件项
  → MediaChain().scrape_metadata(fileitem=目录)   ← 与手动刮削目录同一入口
      → 主程序递归处理 + 初始化目录元数据
      → 写出 tvshow.nfo / poster / backdrop / logo / banner / thumb
              / season01-poster.jpg / Season 1/season.nfo + 各单集 .nfo/.jpg
      → 记录管理全部由本体负责
```

## 依赖

无额外依赖。`watchdog` 为主程序自带（与目录实时监控插件共用同一套机制）。

## 配置项

| 配置 | 说明 |
|------|------|
| 启用插件 | 开启目录实时监控 |
| 立即全量扫描一次 | 对监控目录内所有 .strm 全量刮削一次 |
| 覆盖已有元数据 | 对应 scrape_metadata 的 overwrite 参数 |
| 监控模式 | 性能模式（inotify）/ 兼容模式（轮询） |
| 监控目录 | 每行一个目录 |
| 排除关键词 | 每行一个（正则），匹配的路径不刮削 |
