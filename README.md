# MoviePilot-Plugins

MoviePilot 第三方插件仓库，按 [仓库与发布指南](./docs/Repository_Guide.md) 的目录、元数据与
发布规范维护，供 MoviePilot 插件市场读取（`PLUGIN_MARKET` 指向本仓库 `main` 分支）。

当前开发目标是 MoviePilot **V3**。本仓库只保留 V3 专用实现，历史 V2 实现不再维护。

## 当前插件

| 插件 ID | 名称 | 版本 | 目录 |
|---------|------|------|------|
| `LxMusicDownloader` | LX 音源下载 | 3.2.1 | [`plugins.v3/lxmusicdownloader/`](./plugins.v3/lxmusicdownloader/) |
| `StrmScraper` | STRM监控刮削 | 3.3.0 | [`plugins.v3/strmscraper/`](./plugins.v3/strmscraper/) |

- **LX 音源下载**：调用自建 LX Sync Server 的 HTTP API 完成歌曲搜索、歌单浏览与直链解析下载，
  提供 `/lx_search`、`/lx_download`、`/lx_playlist`、`/lx_stats` 远程命令，并内置 Vue 联邦界面。
- **STRM监控刮削**：监控配置目录中新增的 `.strm` 文件，自动调用主程序刮削链
  （`ScrapingChain`）补齐元数据，界面按分类目录分组并支持按分类刷新。

## 目录结构

```text
MoviePilot-Plugins/
├── plugins.v3/                 # V3 专用插件源码（一个插件一个目录）
│   ├── lxmusicdownloader/      # 目录名 = 主类名 LxMusicDownloader 的小写
│   └── strmscraper/            # 目录名 = 主类名 StrmScraper 的小写
├── icons/                      # 插件图标，文件名与插件 ID 小写一致
├── package.v3.json             # V3 插件市场索引（键名 = 插件 ID）
├── package.json                # 默认历史索引（本仓库无条目）
├── package.v2.json             # V2 历史索引（本仓库无条目）
├── docs/
│   └── Repository_Guide.md     # 仓库维护与发布规范
├── LICENSE                     # GPL-3.0
└── README.md
```

每个插件目录统一遵循以下约定：

- 主类必须定义在目录的 `__init__.py` 中，目录名为主类名的小写形式。
- `README.md`：插件专属使用说明。
- `dist/assets/`：Vue 联邦构建产物（已提交），`get_render_mode()` 检测到
  `dist/assets/remoteEntry.js` 时返回 `vue`，否则回退 Vuetify 表单。
- 仅当插件有额外 Python 依赖时才放 `pyproject.toml`；本仓库两个插件均只使用宿主提供的
  `httpx2`、`apscheduler`、`watchdog` 等，因此**不需要** `pyproject.toml`。

## 元数据一致性（提交前必查）

发布前至少核对三处一致，仓库自带的版本门禁脚本会做校验：

- 索引 `package.v3.json` 中的 `version`
- 插件类中的 `plugin_version`
- 该条目 `history` 的最新一条（首项，且整体按语义版本降序排列）

```bash
# 版本门禁
python .github/scripts/check_plugin_versions.py package.json package.v2.json package.v3.json

# V3 插件语法检查
python -m compileall plugins.v3/lxmusicdownloader plugins.v3/strmscraper
```

## 发布

索引条目声明 `"release": true` 的插件参与自动打包，Release Tag 为 `插件ID_v版本号`，
压缩包名为 `插件目录小写_v版本号.zip`。详见
[仓库与发布指南 §8](./docs/Repository_Guide.md)。
