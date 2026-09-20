# LX 音源下载（LxMusicDownloader）

在 MoviePilot V3 里搜索歌曲、浏览歌单并下载到自定义目录。音源能力由自建的 **LX Sync Server** 通过
HTTP API 提供，插件本身不运行洛雪自定义源 JavaScript，**运行环境不需要 Node.js**。

## 目录结构

在 MoviePilot-Plugins 仓库中的组织方式（V3 规范）：

```text
MoviePilot-Plugins/
├── plugins.v3/
│   └── lxmusicdownloader/          # 目录名必须是主类名小写
│       ├── __init__.py             # 主类 LxMusicDownloader，必须在 __init__.py
│       ├── lxserver.py             # LX Sync Server API 客户端
│       ├── downloader.py           # 落盘：格式嗅探、文件名清洗、原子写入
│       ├── src/                    # Vue 前端源码（侧栏工作台 / 设置 / 详情）
│       ├── dist/                   # vite 联邦构建产物（随插件附带，勿手改）
│       ├── vite.config.js          # 联邦配置（exposes ./AppPage ./Page ./Config）
│       ├── package.json
│       └── README.md
├── tests/
│   └── v3/
│       └── lxmusicdownloader/
│           ├── test_plugin.py      # 不放进源码目录，避免被市场同步到运行目录
│           └── live_check.py       # 手动联调脚本（只读，直连服务端）
└── package.v3.json                 # 仓库根目录，键名用插件 ID
```

**没有 `pyproject.toml`**：插件只用到 `httpx2` 与 `apscheduler`，两者都由 MoviePilot
宿主提供，因此不存在额外 Python 依赖，无需声明。

`plugin_version` 必须与 `package.v3.json` 里该插件的 `version` 一致，测试中有断言守着。

## Vue 前端（联邦模块）

插件内置 Vue 联邦前端，构建产物在 `dist/`。`get_render_mode()` 检测到
`dist/assets/remoteEntry.js` 时返回 `vue`，主程序即用远程组件渲染；否则回退 Vuetify 表单。

```bash
cd plugins.v3/lxmusicdownloader
npm install
npm run build          # 产出 dist/，需一并提交
```

- 暴露三个入口：`./AppPage`（侧栏全页工作台）、`./Page`（插件中心详情）、`./Config`（设置面板）。
- 侧栏入口由 `get_sidebar_nav()` 声明，标题「LX 音源下载」，可用 `sidebar_enabled` 开关关闭（关闭后仍可从插件中心进入）。
- 前端通过注入的 API 客户端调用 `plugin/LxMusicDownloader/*`（全部 bear 认证）；封面直接用服务端返回的公网 `img` URL。
- 设置面板读写走 `plugin/form/<id>`（取 model）与 `PUT plugin/<id>`（保存）。

## 鉴权契约（实测，关键）

服务端 `verify` 允许只带 token，会反查返回绑定用户名；但**业务接口 `/api/music/url`
靠 `x-user-name` 定位该用户名下的自定义音源**，只带 token 会退回公开用户并报
`HTTP 500 未找到支持 xx 平台的自定义源`。实测三种变体：

| 请求头 | verify | /api/music/url |
| --- | --- | --- |
| `x-user-name` + `x-user-token` | valid=true | ✅ 解析成功 |
| 仅 `x-user-token` | valid=true（反查得绑定用户） | ❌ 500，与匿名同 |
| 无鉴权头 | valid=false | ❌ 500 |

因此客户端 `_headers()` 的策略是：**有 token 就发**，用户名已知时一并带上；
构造客户端时若「有 token 无用户名」，先调一次 `verify()` 反查补全用户名（`lxserver.py` 的
`elif self.token and not self.username` 分支）。表单占位符与提示也已明确「用户名仍需填写」。


## 依赖的接口

| 用途 | 接口 |
| --- | --- |
| 校验 token | `GET /api/user/auth/verify` |
| 换取 token | `POST /api/user/login` `{username, password}` |
| 搜索 | `GET /api/music/search?name=&source=&type=song&page=&pages=&limit=` |
| 解析直链 | `POST /api/music/url` `{songInfo, quality}` |
| 代理下载 | `GET /api/music/download?url=&filename=&tag=1&name=&singer=&album=&pic=` |
| 服务端缓存 | `POST /api/music/cache/download`、`GET /api/music/cache/stats` |
| 歌单标签 | `GET /api/music/songList/tags?source=` |
| 浏览歌单 | `GET /api/music/songList/list?source=&tagId=&sortId=hot&page=` |
| 歌单详情 | `GET /api/music/songList/detail?id=&source=&page=` |
| 搜索歌单 | `GET /api/music/songList/search?text=&source=&page=` |

## 歌单（实测）

**歌单与自定义音源脚本无关**，全部由服务端内置 `musicSdk` 提供，因此直链解析不可用时
歌单浏览仍然可用。

### 平台能力矩阵

服务端对不支持的方法**不做优雅降级**，直接抛 `Source X does not support songList`（HTTP 500）。
插件侧按同一张表前置校验，翻译成「该平台不支持 XX 操作，请更换音源平台」：

| 平台 | tags | list | detail | search |
| --- | --- | --- | --- | --- |
| `wy` 网易云 | ✅ | ✅ | ✅ | ✅ |
| `tx` QQ 音乐 | ✅ | ✅ | ✅ | ✅ |
| `kg` 酷狗 | ✅ | ✅ | ✅ | ✅ |
| `kw` 酷我 | ✅ | ✅ | ✅ | ❌ |
| `mg` 咪咕 | ✅ | ✅ | ✅ | ✅ |
| `bd` 百度 | ✅ | ✅ | ✅ | ❌ |

### 关键行为

1. **`detail` 可直接传歌单链接**。各平台模块用正则从 URL 提取 id：`wy` 支持
   `?id=123`、`/playlist/123/1/` 与短链；`tx` 支持 `/playlist/123`；`kg` 支持
   `/songlist/xxx/`、`?global_collection_id=`、`?chain=` 等多种形态。网易云还支持
   **`id###MUSIC_U值`** 写法注入 cookie，用于访问需登录的私密歌单。
2. **必须翻页**。`wy` 单页 1000 首，其余平台多为 30 首。`songlist_all()` 循环翻页直到
   `page * limit >= total`，并以 `max_songs` 兜底（服务端存在 `limit_song=100000` 的极端场景）。
3. **歌单内每首歌已带齐解析所需字段**（`source`、`songmid`/`hash`、`singer`、`albumName`、
   `interval`、`img`、`types`），所以拿到曲目列表后可直接逐首 `POST /api/music/url`。
4. `types` / `_types` 是该歌**实际可用**的音质（kg 的 `_types` 还含各音质独立 `hash`），
   插件据此在请求前做音质降级，避免无效请求。

### 整单下载

- 前端「歌单」标签页：热门/搜索歌单列表 → 点开详情 → 全选或勾选 → 下载。
- 落到与单曲下载**同一个扁平目录**（`download_path` 或插件数据目录下 `music`），
  **不再按歌单名另建子目录**——目录整理交给 MoviePilot 本体，避免两套规则打架；
  `subdir_by_artist` 也只在单曲下载路径生效。
- 并发线程池按 `playlist_concurrency` 执行（默认 3，与服务端下载队列默认一致），
  下载前用「文件名模板 + 任意音频后缀」探测目标目录，**已存在则跳过**，整单下载幂等。
- 逐首返回 `ok / skipped / failed` 明细，失败不影响其余曲目。

## 已知的接口行为（实测）

1. `GET /api/music/search` **返回裸数组**，不是 `{list: [...]}`。
2. `x-user-name` 与 `x-user-token` 必须成对携带；都不传则按公开用户 `open` 走。
3. `POST /api/music/url` **强依赖服务端已启用的自定义音源**。公开用户没有音源时固定返回
   `HTTP 500 未找到支持 xx 平台的自定义源`，所以必须配置 token。
4. `search` 的 `limit` 参数服务端不严格截断（实测请求 5 条仍返回 20 条），插件侧补了本地截断。
5. 下载响应的**真实容器与请求音质可能不一致**（请求 320k 可能返回 ogg，请求 flac 可能返回 mp3），
   因此扩展名按响应文件头嗅探决定，不写死。
6. **`tag=1`（注入 ID3）不是转码开关**。实测咪咕 `flac24bit`：不带 tag 得到 3,577,106 B 的 MP3，
   带 tag 得到 3,578,018 B，多的 912 B 只是 ID3 标签。降质发生在上游平台，与服务端代理无关。
7. 服务端部分接口存在偶发 SSL 握手超时（`_ssl.c:1015`），属网络层抖动，重试即可。

## 源码级事实（依据 `src/server/server.ts`）

搜索与直链解析走的是**两套完全不同的链路**，排查时要分开看：

| 能力 | 实现位置 | 依赖 |
| --- | --- | --- |
| 搜索 `/api/music/search` | 内置 `musicSdk` | **不需要**自定义源 |
| 解析 `/api/music/url` | `callUserApiGetMusicUrl` | **强依赖**服务端已导入的自定义源 |

由此得到几条与直觉相反的结论：

1. **搜索的 `limit` 参数对歌曲无效**。源码里歌曲分支 `PAGE_SIZE` 恒为 20，`limit` 只用于
   歌手/专辑/歌单。要多拿候选必须加大 `pages`，插件已按 `ceil(limit/20)` 自动换算。
2. **`GET /api/custom-source/list` 的用户名取自 URL 参数 `username`**（缺省 `default`），
   **不读 `x-user-name` 头**。早期版本没传该参数，导致诊断恒返回空数组，被误判成
   「服务端没有启用任何音源」。修复后可见该用户名下已启用
   `聆澜音源(赞助版) v8.5`，支持 `git/kg/kw/mg/tx/wy`。
3. **`/api/music/url` 的真实音质在 `type` 字段**，`quality` 字段服务端从不填写。
   服务端本身也会按 `master > atmos_plus > atmos > hires > flac24bit > flac > 320k > 192k > 128k`
   逐级降级（`downloadQuality.ts`），因此 `type` 才是最终到手的音质。
4. **`/api/music/download` 是纯透传**，`tag=1` 仅用 node-id3 追加标签，不重新编码音频。
   降质若发生，源头在上游平台，与服务端代理无关。
5. **`lyric=1` 可一并嵌入歌词**（写入 `USLT` 帧），需同时提供 `source` 与 `songmid`，
   kg 还需 `hash`。服务端取歌词失败会静默降级，不影响下载。
   实测 kw《稻香》嵌入成功，得到完整 `[00:00.000]` 时间轴歌词。

## 平台支持（实测）

以下为在自建 LX Sync Server 上以管理员凭据实测的结果。服务端地址、用户名与令牌属于私有信息，
**不入库、不写进代码或文档**，请在插件配置里自行填写。

| 平台 | key | 搜索 | 128k | 320k | flac | flac24bit |
| --- | --- | --- | --- | --- | --- | --- |
| 酷我 | `kw` | 可用 | 可用 | 可用 | 可用 | — |
| 酷狗 | `kg` | 可用 | 可用 | 可用 | 可用 | 可用 |
| QQ 音乐 | `tx` | **不可用** | — | — | — | — |
| 网易云 | `wy` | 可用 | 可用 | 可用 | 可用 | — |
| 咪咕 | `mg` | 可用 | 可用 | 可用 | 可用 | 可用 |

`tx` 的搜索接口在服务端长期返回 `HTTP 500 搜索失败`，插件会提示更换平台。

根因在源码里：`tx/musicSearch.js` 请求 QQ 移动版 `u.y.qq.com/cgi-bin/musicu.fcg`，
设备在请求体里被硬编码为全 0（`QIMEI36`/`OpenUDID`/`udid` 等），触发风控返回非 0 code；
SDK 内置重试 5 次后直接 `reject(new Error('搜索失败'))`，与账号权限、与是否导入自定义源
**均无关**。也就是说：重试无用，服务端也无法自行恢复，除非更新 lxserver 的 tx 实现。
注意解析端不受影响——若能自行构造 tx 的 `songInfo`，`/api/music/url` 仍可能解析成功，
坏掉的只是「搜索」这一步。
另外 `GET /api/custom-source/list` 返回空数组，与 `/api/music/url` 实际可用并不一致，
排查解析失败时不要以该列表为空作为依据。

## 远程命令

| 命令 | 说明 |
| --- | --- |
| `/lx_search 歌曲名` | 搜索并列出候选（含各条可用音质） |
| `/lx_download 序号` | 按上一次搜索结果的序号下载 |
| `/lx_download 歌手 - 歌名` | 直接搜索第一首并下载 |
| `/lx_playlist` | 列出当前平台热门歌单 |
| `/lx_playlist 歌单名` | 搜索歌单；参数是链接或纯数字 ID 时直接展示曲目 |
| `/lx_playlist dl 歌单名` | 下载该歌单前 50 首（`COMMAND_PLAYLIST_LIMIT`） |
| `/lx_stats` | 查看服务端缓存统计 |

## 插件 API

基础路径：`/api/v1/plugin/LxMusicDownloader`

- `GET /overview` — 运行状态总览（含 `playlist_concurrency`）
- `GET /verify` — 校验服务端凭据
- `GET /search?keyword=稻香&limit=10`
- `POST /resolve` body: `{"song": {...}, "quality": "flac"}`
- `POST /download` body: `{"keyword": "稻香"}`（或直接回传完整 `song`）
- `GET /stats`
- `GET /playlist/list?keyword=&source=&tag_id=&sort_id=hot&page=1` — 有关键词则搜索，否则按标签/热门
- `GET /playlist/tags?source=`
- `GET /playlist/detail?playlist_id=<链接或ID>&source=&max_songs=1000` — 自动翻页拉全量
- `POST /playlist/download` body: `{"playlist_id": "...", "source": "wy", "quality": "flac", "concurrency": 3, "limit": 0, "skip_existing": true}`

## 配置项

| 键 | 说明 |
| --- | --- |
| `enabled` | 是否启用插件 |
| `host` | LX 服务端地址，如 `https://music.example.com` 或 `http://127.0.0.1:23332` |
| `username` / `password` / `token` | 优先用 token；只填账号密码时自动登录换取 token |
| `source` | 音源：`kw` `kg` `tx` `wy` `mg` |
| `quality` | 音质；不可用时按 `flac24bit > hires > flac > 320k > 192k > 128k` 自动降级 |
| `download_path` | 下载位置，留空使用插件数据目录下的 `music` |
| `name_template` | 文件名模板，支持 `{name} {singer} {album} {source} {songmid}` |
| `max_results` | 搜索结果条数 |
| `playlist_concurrency` | 歌单整单下载并发（1-8，默认 3） |
| `subdir_by_artist` | 是否按歌手建立子目录（仅单曲下载路径生效） |
| `save_cover` | 是否额外保存封面图 |
| `embed_tag` | 下载时带 `tag=1`，由服务端注入 ID3 标签（封面/标题/艺术家/专辑），不改变音频本体 |
| `embed_lyric` | 额外带 `lyric=1`，把歌词写入 `USLT` 帧；需 `embed_tag` 同时开启。建议开启，失败会自动降级 |
| `use_server_cache` | 改为提交服务端缓存任务，而不是下载到本地 |

## 实现注意

- 下载前先判定 `status_code`，错误响应体是 JSON，若不判断会把错误页写成音频文件。
- 落盘使用 `.part` 临时文件 + 原子替换；内容小于 4KB 判定失败并删除。
- 定时服务每天 09:00 校验一次凭据，token 失效时发出通知。
- 配置变更后 `init_plugin()` 会丢弃旧客户端，避免继续用上一份凭据。
- 歌单整单下载用线程池并发，去重靠「目标目录已存在同名文件」判定，**不依赖服务端**；
  单首失败只记明细不中断整批。
- 去重按文件名模板匹配，因此模板里带 `{songmid}` 时最准确；不带时同名不同版本可能被跳过，
  这正是整单重复下载想要的幂等效果。
