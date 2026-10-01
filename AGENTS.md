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

## 5. 版本与版本历史

三处必须一致：**索引 `package.v3.json` 的 `version`、插件类的 `plugin_version`、
该条目 `history` 的首项**。`history` 以当前版本置顶，其余按语义版本**降序排列**。

**生成或更新版本历史时，必须使用精简模式：**

- 每版 **1 行**，**≤40 字**
- 只保留**用户可感知变化、公开 API、配置项、破坏性变更**
- **不写**背景、原因、评价、内部实现

## 6. 提交前校验

```bash
# 语法检查
python -m compileall plugins.v3/<plugin_id_lower>

# 版本门禁（索引 ↔ 目录 ↔ plugin_version ↔ history）
python .github/scripts/check_plugin_versions.py package.json package.v2.json package.v3.json

# 空白符检查
git diff --check
```

## 7. 发布

- 索引条目声明 `"release": true` 的插件参与自动打包。
- Release Tag 格式：`插件ID_v版本号`；压缩包名：`插件目录小写_v版本号.zip`。
- 详见 [`docs/Repository_Guide.md`](./docs/Repository_Guide.md) 第 8 节。
