# AGENTS.md

本文件是本仓库（`MoviePilot-Plugins`）面向所有 AI 智能体的首要指令集。
仓库内的文档优先于通用经验；目录、元数据与发布规则的权威来源是
[`docs/Repository_Guide.md`](./docs/Repository_Guide.md)。

---

## 1. 仓库职责

本仓库是**插件市场与插件源码仓库，不是独立运行时**。

- `MoviePilot` 宿主负责插件加载、生命周期、事件、API、服务、数据与工作流。
- `MoviePilot-Frontend` 负责插件市场卡片、配置页、详情页与 Vue 联邦组件渲染。
- 本仓库只负责插件源码、市场索引、图标、测试与文档。
- **不要**把宿主公共能力复制进本仓库；插件应复用 `_PluginBase`、`eventmanager`、
  稳定 SDK、Chain、Oper 等既有抽象。

## 2. 目录结构

```text
MoviePilot-Plugins/
├── plugins.v3/          # V3 专用插件源码，一个插件一个目录
├── icons/               # 插件图标
├── package.v3.json      # V3 市场索引（键名 = 插件 ID）
├── package.json         # 默认历史索引
├── package.v2.json      # V2 历史索引
├── docs/                # 规范与参考文档
├── .github/             # 发布工作流与门禁脚本（workflows/release.yml、scripts/）
├── LICENSE
└── README.md
```

## 3. 硬性约定

1. **一个插件一个目录**；目录名必须是**插件主类名的小写**（如 `StrmScraper` → `strmscraper/`）。
2. **插件主类必须定义在该目录的 `__init__.py` 中**。
3. V3 源码放 `plugins.v3/<plugin_id_lower>/`，元数据写入 `package.v3.json`。
4. **无额外 Python 依赖时不放 `pyproject.toml`**；有依赖时依赖写入 `[project].dependencies`，
   版本用 `dynamic = ["version"]`，且不提交 `uv.lock`。
5. 插件专属说明写在该目录的 `README.md`。
6. Vue 联邦产物放 `dist/assets/`，**文件名必须落在 `__federation_*` /
   `_plugin-vue_export-helper-*` / `remoteEntry.js` 白名单内**。
7. 插件运行数据写入插件数据目录，不得写回源码目录。

## 4. 图标规则（重要）

- **非官方仓库（含本仓库）必须把图标写死成完整 URL**：`plugin_icon` 与
  `package.v3.json` 的 `icon` 都要写死完整 HTTP URL。
- **只有官方仓库 `jxxghp/MoviePilot-Plugins` 才允许使用裸文件名**；裸文件名只会去官方
  `icons/` 目录里查找，第三方仓库用裸名会解析不到、回退成占位图。
- 二者**同源，必须同步**：宿主 `metadata.py` 把索引 `icon` 直接赋给 `plugin.plugin_icon`。
- 仓库变更地址时，需同步修改插件类与索引中的全部图标 URL。

## 5. V3 导入边界（重要）

V3 插件的导入以宿主 `app/runtime/compat/manifest.py` 为唯一口径：**凡需经 Compat 层
解析的模块或符号都算旧写法**。新增代码一律走 canonical / SDK 入口。

| 旧写法（走 Compat 层） | V3 canonical |
| --- | --- |
| `from app.plugins import _PluginBase` | `from app.sdk.plugin import _PluginBase` |
| `from app import schemas` → `schemas.FileItem` | `from app.schemas.file import FileItem` |
| `from app import schemas` → `schemas.Response` | `from app.schemas.response import Response` |
| `app.schemas.types.NotificationType` | `app.schemas.types.MessageType` |
| `app.schemas.types.MessageChannel` | `app.schemas.types.NotificationChannel` |
| `app.core.*` / `app.helper.*` / `app.utils.*` / `app.log` | `app.sdk.*`（`config` / `events` / `logging` / `cache` / `media` / `network` / `services` / `utilities` …） |
| `app.sdk._legacy.*` | **禁止**，仅供宿主迁移桥接 |

要点：

- `_PluginBase` 的契约本体在 `app/sdk/plugin/base.py`；`app.plugins` 只是插件安装命名
  空间，其包根符号由 Compat 惰性承接（见上游 ADR-0008）。
- 根包 `app.schemas` 只是**惰性兼容导出入口**，具体符号的 canonical 归属是它的**所有者
  子模块**（`FileItem` → `app.schemas.file`、`Response` → `app.schemas.response`）。
- **不要直连宿主数据**：不得导入 `app.db.models.*`，也不得使用 `SessionFactory` /
  `AsyncSessionFactory` / `ScopedSession`；宿主数据经 Oper、Chain 或稳定 SDK 访问。
- 不得导入**其它插件**的模块（含字符串动态导入）、不得修改 `sys.path`、不得直接打开
  宿主 `user.db`。导入**本插件自己**的包路径 `app.plugins.<本插件小写>` 不算旧导入。
- 仍是稳定公开入口、可正常使用：`app.schemas.types`、`app.chain.*`、`app.modules.*`、
  `app.agent.*`、`app.db.oper.*`。

校验（上游门禁脚本以宿主 Compat 清单为准，旧导入存量只减不增）：

```bash
# 从上游 jxxghp/MoviePilot-Plugins 取 scripts/check_v3_imports.py 后执行
MOVIEPILOT_BACKEND_PATH=<MoviePilot 后端目录> python check_v3_imports.py
```

## 6. 版本与版本历史

三处必须一致：**索引 `package.v3.json` 的 `version`、插件类的 `plugin_version`、
该条目 `history` 的首项**。`history` 以当前版本置顶，其余按语义版本**降序排列**。

**生成或更新版本历史时，必须使用精简模式：**

- 每版 **1 行**，**≤40 字**
- 只保留**用户可感知变化、公开 API、配置项、破坏性变更**
- **不写**背景、原因、评价、内部实现

### 6.1 `system_version` 下界

`system_version` 是宿主安装 / 更新检测 / 本地插件同步的版本闸门（未声明则不检查），
格式同 pip 依赖约束（`">=3.0.0"`、`">=2.12.0,<3"`）。

**V3 插件声明 `">=3.0.0"` 只是基线；一旦用到某个 3.x 版本才提供的宿主能力，必须把下界
抬到该版本**，否则旧宿主仍会看到更新入口、装完却加载失败。

本仓库现状：两个插件都从 `app.sdk.plugin` 导入 `_PluginBase`（ADR-0008 后的 canonical
路径），该模块 **v3.0.2 不存在、v3.0.3 才引入**，因此二者均为 `">=3.0.3"`。
下表是已核实的宿主能力引入版本，新增依赖时照此抬高下界：

| 宿主能力 | 引入版本 |
| --- | --- |
| `app.sdk.plugin`（`_PluginBase` canonical） | **v3.0.3** |
| `app.sdk.logging` / `app.sdk.events` | v3.0.0 |
| `app.schemas.types.MessageType`（旧名 `NotificationType`） | v3.0.0 |
| `app.chain.scraping.ScrapingChain` | v3.0.0 |
| `app.runtime.thread.ThreadHelper`（旧名 `app.helper.thread`） | v3.0.0 |
| `app.schemas.file` / `app.schemas.response` | V2 时期既有 |
| `get_sidebar_nav` | v2.9.27 |

## 7. 提交前校验

```bash
# 语法检查
python -m compileall plugins.v3/<plugin_id_lower>

# 版本门禁（索引 ↔ 目录 ↔ plugin_version ↔ history）
python .github/scripts/check_plugin_versions.py package.json package.v2.json package.v3.json

# 联邦 CSS 门禁
python .github/scripts/check_federation_css.py

# V3 导入边界（见第 5 节，需要 MoviePilot 后端提供 app/runtime/compat/manifest.py）
MOVIEPILOT_BACKEND_PATH=<MoviePilot 后端目录> python check_v3_imports.py

# 空白符检查
git diff --check
```

## 8. 发布

- 索引条目声明 `"release": true` 的插件参与自动打包。
- Release Tag 格式：`插件ID_v版本号`；压缩包名：`插件目录小写_v版本号.zip`。
- 详见 [`docs/Repository_Guide.md`](./docs/Repository_Guide.md) 第 8 节。

### 8.1 宿主只从 GitHub Releases 取安装包（重要）

宿主 `app/adapters/external/plugin/client.py` 的 `__build_plugin_release_item()` 决定候选：
**tag 必须以 `<插件ID>_v` 开头，且该 Release 必须存在名为 `<tag 全小写>.zip` 的资产**；
两者缺一，市场就没有这个插件的候选，界面报
**「没有找到插件 X 的可用安装包」**（文案出自 `app/application/plugin/source.py`）。

因此：**改了插件代码却没有对应的 Release = 市场装不上**，`package.v3.json` 只提供
名称/描述/图标/历史，不提供下载地址。

### 8.2 自动打包

- `.github/workflows/release.yml`：push 到 `main` 且改动命中 `package.json` /
  `package.v2.json` / `package.v3.json`（或本工作流与 `.github/scripts/**`）时触发，
  用 `.github/scripts/check_plugin_versions.py`、`check_federation_css.py` 门禁后，
  按 `release: true` 逐插件打包并创建 Release。
- 自上次 tag 以来该插件目录无变更且 Release 已存在时会跳过；同版本重跑会替换该 Release 资产。
- **推论：只改插件代码、不动 `package*.json` 不会触发发布**。要发新版必须抬高该插件的
  `version`（并同步 `plugin_version` 与 `history` 首项，见第 6 节）。
- 手动重跑：Actions → **Plugin Release** → Run workflow。
- 前置条件：仓库已启用 Actions，且 Settings → Actions → Workflow permissions 为
  **Read and write**（工作流声明 `permissions: contents: write`）。

```bash
# 本地复现打包结果（tag / 资产名必须与 8.1 的匹配规则一致）
tag="<插件ID>_v<版本号>"; asset="$(echo "$tag" | tr '[:upper:]' '[:lower:]').zip"
(cd plugins.v3 && zip -r "$asset" "<插件目录小写>" -x "*/__pycache__/*" -x "*.pyc")
```
