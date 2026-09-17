import { importShared } from './__federation_fn_import-JrT3xvdd.js';
import ConfigPanel from './__federation_expose_Config-oicJGwFG.js';
import { m as makeApiCall, v as versionLabel, p as posterStyle, c as coverUrl, s as statusOf, f as formatSize, u as unwrap, b as bodyOf } from './strm-Drmjz18_.js';
import { _ as _export_sfc } from './_plugin-vue_export-helper-pcqpp-6-.js';

const {createElementVNode:_createElementVNode,toDisplayString:_toDisplayString,normalizeClass:_normalizeClass,openBlock:_openBlock,createElementBlock:_createElementBlock,createCommentVNode:_createCommentVNode,normalizeStyle:_normalizeStyle,createTextVNode:_createTextVNode,vModelText:_vModelText,withDirectives:_withDirectives,renderList:_renderList,Fragment:_Fragment,unref:_unref,createVNode:_createVNode,withModifiers:_withModifiers,Teleport:_Teleport,createBlock:_createBlock} = await importShared('vue');


const _hoisted_1 = { class: "strm-page" };
const _hoisted_2 = { class: "strm-head" };
const _hoisted_3 = { class: "strm-head-chips" };
const _hoisted_4 = { class: "strm-chip is-muted" };
const _hoisted_5 = { class: "strm-head-actions" };
const _hoisted_6 = ["disabled"];
const _hoisted_7 = ["disabled"];
const _hoisted_8 = ["disabled"];
const _hoisted_9 = {
  key: 0,
  class: "strm-alert is-error"
};
const _hoisted_10 = {
  key: 1,
  class: "strm-alert is-ok"
};
const _hoisted_11 = {
  key: 2,
  class: "strm-task"
};
const _hoisted_12 = { class: "strm-task-text" };
const _hoisted_13 = { class: "strm-task-bar" };
const _hoisted_14 = { class: "strm-stats" };
const _hoisted_15 = { class: "strm-stat" };
const _hoisted_16 = { class: "strm-stat is-movie" };
const _hoisted_17 = { class: "strm-stat is-tv" };
const _hoisted_18 = { class: "strm-stat is-ok" };
const _hoisted_19 = { class: "strm-stat is-fail" };
const _hoisted_20 = { class: "strm-toolbar" };
const _hoisted_21 = { class: "strm-seg" };
const _hoisted_22 = {
  key: 3,
  class: "strm-empty"
};
const _hoisted_23 = {
  key: 4,
  class: "strm-empty"
};
const _hoisted_24 = {
  key: 5,
  class: "strm-grid"
};
const _hoisted_25 = ["src", "alt", "onError"];
const _hoisted_26 = { class: "strm-poster-foot" };
const _hoisted_27 = {
  key: 0,
  class: "strm-pill"
};
const _hoisted_28 = {
  key: 1,
  class: "strm-pill is-blue"
};
const _hoisted_29 = {
  key: 2,
  class: "strm-pill is-blue"
};
const _hoisted_30 = { key: 3 };
const _hoisted_31 = { class: "strm-card-body" };
const _hoisted_32 = ["title"];
const _hoisted_33 = { class: "strm-card-meta" };
const _hoisted_34 = { class: "strm-card-actions" };
const _hoisted_35 = ["onClick"];
const _hoisted_36 = ["onClick"];
const _hoisted_37 = ["disabled", "onClick"];
const _hoisted_38 = ["disabled", "onClick"];
const _hoisted_39 = { class: "strm-cfg-wrap" };
const _hoisted_40 = { class: "strm-drawer-head" };
const _hoisted_41 = ["src", "alt"];
const _hoisted_42 = { class: "strm-drawer-info" };
const _hoisted_43 = { class: "strm-drawer-sub" };
const _hoisted_44 = {
  key: 0,
  class: "strm-season-bar"
};
const _hoisted_45 = ["onClick"];
const _hoisted_46 = { class: "strm-list" };
const _hoisted_47 = {
  key: 0,
  class: "strm-empty"
};
const _hoisted_48 = ["checked", "onChange"];
const _hoisted_49 = { class: "strm-row-no" };
const _hoisted_50 = ["title"];
const _hoisted_51 = { class: "strm-row-size" };
const _hoisted_52 = ["disabled", "onClick"];
const _hoisted_53 = { class: "strm-drawer-foot" };
const _hoisted_54 = { class: "strm-sel" };
const _hoisted_55 = { class: "strm-foot-right" };
const _hoisted_56 = ["disabled"];
const _hoisted_57 = ["disabled"];

const {computed,onBeforeUnmount,onMounted,reactive,ref} = await importShared('vue');


const _sfc_main = {
  __name: 'AppPage',
  props: {
  api: { type: Object, default: () => ({}) },
  pluginId: { type: String, default: 'StrmScraper' },
  navKey: { type: String, default: 'main' },
  nativeSubscribe: { type: Function, default: null },
  sourcePluginId: { type: String, default: '' },
},
  setup(__props) {

const props = __props;

const loading = ref(true);
const busy = ref(false);
const error = ref('');
const notice = ref('');
const overview = ref({});
const items = ref([]);
const keyword = ref('');
const typeFilter = ref('all');

const drawer = ref(false);
const current = ref(null);
const detail = ref(null);
const seasonKey = ref('');
const selected = ref(new Set());

const task = ref(null);
let taskTimer = null;

const apiCall = makeApiCall(props.api, props.pluginId);

// 海报加载失败（无本地海报且 TMDB 未命中）时回退渐变占位图
const posterFailed = reactive({});

function markPosterFailed(path) {
  posterFailed[path] = true;
}

// 页面内设置面板：直接复用设置弹窗的 Config 组件
const showSettings = ref(false);
const settingsModel = ref({});

/*
 * MP 会给插件页面套一层「包含块祖先」（容器查询 / transform / contain 之类），
 * 于是 position:fixed 不再是相对窗口定位，而是被关进「内容区」这一个盒子里：
 *   - 顶栏与侧栏在盒子之外 → 无论 z-index 多大都盖不住它们
 *   - 浮层按内容区居中/铺满，而不是按窗口
 * 设置弹窗与详情抽屉都必须 Teleport 出去才能跳出这层上下文。优先挂到
 * .v-application（保留 Vuetify 的 CSS 变量），兜底 body。
 */
const overlayTarget = (typeof document !== 'undefined' && document.querySelector('.v-application'))
  ? '.v-application'
  : 'body';

async function openSettings() {
  showSettings.value = true;
  try {
    const raw = await props.api.get(`plugin/form/${props.pluginId}`);
    settingsModel.value = bodyOf(raw)?.model || {};
  } catch (readError) {
    error.value = `读取配置失败：${readError?.message || readError}`;
  }
}

async function saveSettings(config) {
  try {
    await props.api.put(`plugin/${props.pluginId}`, config);
    showSettings.value = false;
    notice.value = '配置已保存';
    await loadEverything();
  } catch (saveError) {
    error.value = `保存配置失败：${saveError?.message || saveError}`;
  }
}

const counts = computed(() => ({
  all: items.value.length,
  movie: items.value.filter(i => i.type === 'movie').length,
  tv: items.value.filter(i => i.type === 'tv').length,
}));

const visibleItems = computed(() => {
  const kw = keyword.value.trim().toLowerCase();
  return items.value.filter(item => {
    if (typeFilter.value !== 'all' && item.type !== typeFilter.value) return false
    if (kw && !`${item.title} ${item.path}`.toLowerCase().includes(kw)) return false
    return true
  })
});

const seasons = computed(() => {
  if (!detail.value || detail.value.type !== 'tv') return []
  const map = new Map();
  for (const file of detail.value.files || []) {
    const key = String(file.season_no ?? 1);
    if (!map.has(key)) map.set(key, { no: file.season_no ?? 1, name: file.season_name || `Season ${file.season_no ?? 1}`, files: [] });
    map.get(key).files.push(file);
  }
  return [...map.values()].sort((a, b) => a.no - b.no)
});

const currentSeason = computed(() => seasons.value.find(s => String(s.no) === seasonKey.value) || seasons.value[0] || null);

const rows = computed(() => {
  if (!current.value) return []
  if (current.value.type === 'tv') return currentSeason.value?.files || []
  return (detail.value?.files || []).map(file => ({ ...file, label: versionLabel(file.name) }))
});

const selectedRows = computed(() => rows.value.filter(row => selected.value.has(row.path)));

async function loadEverything() {
  loading.value = true;
  error.value = '';
  try {
    const [overviewResponse, itemsResponse] = await Promise.all([
      apiCall('get', '/overview'),
      apiCall('get', '/items'),
    ]);
    overview.value = unwrap(overviewResponse);
    items.value = unwrap(itemsResponse) || [];
  } catch (loadError) {
    error.value = loadError?.message || '加载媒体清单失败';
  } finally {
    loading.value = false;
  }
}

async function refreshItems() {
  const response = await apiCall('get', '/items?refresh=true');
  items.value = unwrap(response) || [];
  const overviewResponse = await apiCall('get', '/overview');
  overview.value = unwrap(overviewResponse);
}

async function triggerFullScan(force) {
  busy.value = true;
  try {
    unwrap(await apiCall('get', force ? '/scan?force=true' : '/scan'));
    notice.value = force ? '已在后台启动强制全量扫描' : '已在后台启动全量扫描（跳过已刮削）';
  } catch (scanError) {
    error.value = scanError?.message || '触发全量扫描失败';
  } finally {
    busy.value = false;
  }
}

async function openDrawer(item) {
  current.value = item;
  drawer.value = true;
  selected.value = new Set();
  seasonKey.value = '';
  detail.value = null;
  try {
    const response = await apiCall('get', `/files?path=${encodeURIComponent(item.path)}`);
    detail.value = unwrap(response);
    if (item.type === 'tv' && seasons.value.length) seasonKey.value = String(seasons.value[0].no);
  } catch (detailError) {
    error.value = detailError?.message || '读取文件明细失败';
  }
}

function closeDrawer() {
  drawer.value = false;
  current.value = null;
  detail.value = null;
  selected.value = new Set();
}

function toggleRow(path) {
  const next = new Set(selected.value);
  if (next.has(path)) next.delete(path);
  else next.add(path);
  selected.value = next;
}

function selectAll() {
  selected.value = new Set(rows.value.map(row => row.path));
}

function selectUnscraped() {
  selected.value = new Set(rows.value.filter(row => !row.scraped).map(row => row.path));
}

function clearSelection() {
  selected.value = new Set();
}

function pollTask(taskId) {
  clearInterval(taskTimer);
  taskTimer = setInterval(async () => {
    try {
      const data = unwrap(await apiCall('get', `/tasks?task_id=${taskId}`));
      task.value = data;
      if (data && data.status !== 'running') {
        clearInterval(taskTimer);
        taskTimer = null;
        busy.value = false;
        notice.value = `刮削完成：成功 ${data.success || 0} 个，失败 ${data.failed || 0} 个`;
        try {
          await refreshItems();
        } catch (refreshError) {
          error.value = refreshError?.message || '刷新列表失败';
        }
        setTimeout(() => { task.value = null; }, 6000);
      }
    } catch (pollError) {
      clearInterval(taskTimer);
      taskTimer = null;
      busy.value = false;
      error.value = pollError?.message || '查询任务状态失败';
    }
  }, 1500);
}

async function submitScrape(paths, target) {
  if (!paths.length) return
  busy.value = true;
  error.value = '';
  notice.value = '';
  try {
    const data = unwrap(await apiCall('post', '/scrape', { paths, target }));
    if (!data?.task_id) throw new Error('未返回任务 ID')
    task.value = { id: data.task_id, status: 'running', total: paths.length, done: 0, success: 0, failed: 0 };
    pollTask(data.task_id);
  } catch (submitError) {
    busy.value = false;
    error.value = submitError?.message || '提交刮削任务失败';
  }
}

function scrapeSelected() {
  submitScrape(selectedRows.value.map(row => row.path), 'file');
}

function scrapeWhole() {
  if (!current.value) return
  submitScrape([current.value.path], 'dir');
}

function scrapeOne(row) {
  submitScrape([row.path], 'file');
}

function scrapeCard(item) {
  submitScrape([item.path], 'dir');
}

onMounted(() => { loadEverything(); });
onBeforeUnmount(() => { clearInterval(taskTimer); });

return (_ctx, _cache) => {
  return (_openBlock(), _createElementBlock("div", _hoisted_1, [
    _createElementVNode("div", _hoisted_2, [
      _cache[9] || (_cache[9] = _createElementVNode("div", { class: "strm-logo" }, "S", -1)),
      _cache[10] || (_cache[10] = _createElementVNode("div", { class: "strm-heading" }, [
        _createElementVNode("h1", null, "STRM 刮削"),
        _createElementVNode("p", null, "监控目录内的 .strm 文件，按媒体聚合、按单集或版本补齐元数据")
      ], -1)),
      _createElementVNode("div", _hoisted_3, [
        _createElementVNode("span", {
          class: _normalizeClass(['strm-chip', overview.value.monitoring ? 'is-ok' : 'is-muted'])
        }, _toDisplayString(overview.value.monitoring ? '监控运行中' : '监控未启动'), 3),
        _createElementVNode("span", _hoisted_4, _toDisplayString((overview.value.monitor_dirs || []).length) + " 个监控目录", 1)
      ]),
      _createElementVNode("div", _hoisted_5, [
        _createElementVNode("button", {
          class: "strm-btn ghost",
          onClick: openSettings
        }, "设置"),
        _createElementVNode("button", {
          class: "strm-btn ghost",
          disabled: loading.value || busy.value,
          onClick: loadEverything
        }, "刷新", 8, _hoisted_6),
        _createElementVNode("button", {
          class: "strm-btn ghost",
          disabled: busy.value,
          onClick: _cache[0] || (_cache[0] = $event => (triggerFullScan(false)))
        }, "全量扫描", 8, _hoisted_7),
        _createElementVNode("button", {
          class: "strm-btn",
          disabled: busy.value,
          onClick: _cache[1] || (_cache[1] = $event => (triggerFullScan(true)))
        }, "强制全量", 8, _hoisted_8)
      ])
    ]),
    (error.value)
      ? (_openBlock(), _createElementBlock("div", _hoisted_9, _toDisplayString(error.value), 1))
      : (notice.value)
        ? (_openBlock(), _createElementBlock("div", _hoisted_10, _toDisplayString(notice.value), 1))
        : _createCommentVNode("", true),
    (task.value)
      ? (_openBlock(), _createElementBlock("div", _hoisted_11, [
          _createElementVNode("span", _hoisted_12, " 刮削中 " + _toDisplayString(task.value.done || 0) + "/" + _toDisplayString(task.value.total || 0) + " · 成功 " + _toDisplayString(task.value.success || 0) + " · 失败 " + _toDisplayString(task.value.failed || 0), 1),
          _createElementVNode("span", _hoisted_13, [
            _createElementVNode("i", {
              style: _normalizeStyle({ width: `${task.value.total ? Math.round(((task.value.done || 0) / task.value.total) * 100) : 0}%` })
            }, null, 4)
          ])
        ]))
      : _createCommentVNode("", true),
    _createElementVNode("div", _hoisted_14, [
      _createElementVNode("div", _hoisted_15, [
        _createElementVNode("b", null, _toDisplayString(overview.value.total || 0), 1),
        _cache[11] || (_cache[11] = _createElementVNode("span", null, "媒体总数", -1))
      ]),
      _createElementVNode("div", _hoisted_16, [
        _createElementVNode("b", null, _toDisplayString(overview.value.movie || 0), 1),
        _cache[12] || (_cache[12] = _createElementVNode("span", null, "电影", -1))
      ]),
      _createElementVNode("div", _hoisted_17, [
        _createElementVNode("b", null, _toDisplayString(overview.value.tv || 0), 1),
        _cache[13] || (_cache[13] = _createElementVNode("span", null, "电视剧", -1))
      ]),
      _createElementVNode("div", _hoisted_18, [
        _createElementVNode("b", null, _toDisplayString(overview.value.dir_scraped || 0), 1),
        _cache[14] || (_cache[14] = _createElementVNode("span", null, "已刮削目录", -1))
      ]),
      _createElementVNode("div", _hoisted_19, [
        _createElementVNode("b", null, _toDisplayString(overview.value.unscraped_items || 0), 1),
        _cache[15] || (_cache[15] = _createElementVNode("span", null, "待刮削", -1))
      ])
    ]),
    _createElementVNode("div", _hoisted_20, [
      _createElementVNode("div", _hoisted_21, [
        _createElementVNode("button", {
          class: _normalizeClass(['strm-seg-item', typeFilter.value === 'all' && 'active']),
          onClick: _cache[2] || (_cache[2] = $event => (typeFilter.value = 'all'))
        }, [
          _cache[16] || (_cache[16] = _createTextVNode(" 全部 ", -1)),
          _createElementVNode("em", null, _toDisplayString(counts.value.all), 1)
        ], 2),
        _createElementVNode("button", {
          class: _normalizeClass(['strm-seg-item', typeFilter.value === 'movie' && 'active mv']),
          onClick: _cache[3] || (_cache[3] = $event => (typeFilter.value = 'movie'))
        }, [
          _cache[17] || (_cache[17] = _createTextVNode(" 电影 ", -1)),
          _createElementVNode("em", null, _toDisplayString(counts.value.movie), 1)
        ], 2),
        _createElementVNode("button", {
          class: _normalizeClass(['strm-seg-item', typeFilter.value === 'tv' && 'active tv']),
          onClick: _cache[4] || (_cache[4] = $event => (typeFilter.value = 'tv'))
        }, [
          _cache[18] || (_cache[18] = _createTextVNode(" 电视剧 ", -1)),
          _createElementVNode("em", null, _toDisplayString(counts.value.tv), 1)
        ], 2)
      ]),
      _withDirectives(_createElementVNode("input", {
        "onUpdate:modelValue": _cache[5] || (_cache[5] = $event => ((keyword).value = $event)),
        class: "strm-search",
        type: "text",
        placeholder: "搜索标题或路径"
      }, null, 512), [
        [_vModelText, keyword.value]
      ])
    ]),
    (loading.value)
      ? (_openBlock(), _createElementBlock("div", _hoisted_22, "正在扫描监控目录…"))
      : (!visibleItems.value.length)
        ? (_openBlock(), _createElementBlock("div", _hoisted_23, "没有匹配的媒体"))
        : (_openBlock(), _createElementBlock("div", _hoisted_24, [
            (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(visibleItems.value, (item) => {
              return (_openBlock(), _createElementBlock("div", {
                key: item.path,
                class: "strm-card"
              }, [
                _createElementVNode("div", {
                  class: "strm-poster",
                  style: _normalizeStyle(_unref(posterStyle)(item.title))
                }, [
                  (!posterFailed[item.path])
                    ? (_openBlock(), _createElementBlock("img", {
                        key: 0,
                        class: "strm-poster-img",
                        src: _unref(coverUrl)(props.api, props.pluginId, item),
                        alt: item.title,
                        loading: "lazy",
                        onError: $event => (markPosterFailed(item.path))
                      }, null, 40, _hoisted_25))
                    : _createCommentVNode("", true),
                  _createElementVNode("span", {
                    class: _normalizeClass(['strm-type', item.type === 'tv' ? 'is-tv' : 'is-mv'])
                  }, _toDisplayString(item.type === 'tv' ? '电视剧' : '电影'), 3),
                  _createElementVNode("span", {
                    class: _normalizeClass(['strm-dot', `is-${_unref(statusOf)(item)}`])
                  }, null, 2),
                  _createElementVNode("div", _hoisted_26, [
                    (item.type === 'tv')
                      ? (_openBlock(), _createElementBlock("span", _hoisted_27, _toDisplayString(item.total_episodes || item.total_files) + " 集 · " + _toDisplayString((item.seasons || []).length) + " 季", 1))
                      : (item.multi_version)
                        ? (_openBlock(), _createElementBlock("span", _hoisted_28, _toDisplayString(item.total_files) + " 版本", 1))
                        : (_openBlock(), _createElementBlock("span", _hoisted_29, "单版本")),
                    (item.unscraped)
                      ? (_openBlock(), _createElementBlock("em", _hoisted_30, _toDisplayString(item.unscraped) + " 待刮", 1))
                      : _createCommentVNode("", true)
                  ])
                ], 4),
                _createElementVNode("div", _hoisted_31, [
                  _createElementVNode("div", {
                    class: "strm-card-title",
                    title: item.path
                  }, _toDisplayString(item.title), 9, _hoisted_32),
                  _createElementVNode("div", _hoisted_33, _toDisplayString(item.path), 1),
                  _createElementVNode("div", _hoisted_34, [
                    (item.type === 'tv')
                      ? (_openBlock(), _createElementBlock("button", {
                          key: 0,
                          class: "strm-btn small is-tv",
                          onClick: $event => (openDrawer(item))
                        }, "剧集预览", 8, _hoisted_35))
                      : (item.multi_version)
                        ? (_openBlock(), _createElementBlock("button", {
                            key: 1,
                            class: "strm-btn small is-mv",
                            onClick: $event => (openDrawer(item))
                          }, "版本预览", 8, _hoisted_36))
                        : (_openBlock(), _createElementBlock("button", {
                            key: 2,
                            class: "strm-btn small",
                            disabled: busy.value,
                            onClick: $event => (scrapeCard(item))
                          }, "刮削", 8, _hoisted_37)),
                    (item.type === 'tv' || item.multi_version)
                      ? (_openBlock(), _createElementBlock("button", {
                          key: 3,
                          class: "strm-btn small ghost",
                          disabled: busy.value,
                          onClick: $event => (scrapeCard(item))
                        }, "整部重刮", 8, _hoisted_38))
                      : _createCommentVNode("", true)
                  ])
                ])
              ]))
            }), 128))
          ])),
    (_openBlock(), _createBlock(_Teleport, { to: _unref(overlayTarget) }, [
      (showSettings.value)
        ? (_openBlock(), _createElementBlock("div", {
            key: 0,
            class: "strm-cfg-mask",
            onClick: _cache[7] || (_cache[7] = _withModifiers($event => (showSettings.value = false), ["self"]))
          }, [
            _createElementVNode("div", _hoisted_39, [
              _createVNode(ConfigPanel, {
                "initial-config": settingsModel.value,
                api: props.api,
                "plugin-id": props.pluginId,
                onSave: saveSettings,
                onClose: _cache[6] || (_cache[6] = $event => (showSettings.value = false))
              }, null, 8, ["initial-config", "api", "plugin-id"])
            ])
          ]))
        : _createCommentVNode("", true),
      _createElementVNode("div", {
        class: _normalizeClass(['strm-mask', drawer.value && 'is-open']),
        onClick: closeDrawer
      }, null, 2),
      _createElementVNode("div", {
        class: _normalizeClass(['strm-drawer', drawer.value && 'is-open'])
      }, [
        (current.value)
          ? (_openBlock(), _createElementBlock(_Fragment, { key: 0 }, [
              _createElementVNode("div", _hoisted_40, [
                _createElementVNode("div", {
                  class: "strm-drawer-poster",
                  style: _normalizeStyle(_unref(posterStyle)(current.value.title))
                }, [
                  (!posterFailed[current.value.path])
                    ? (_openBlock(), _createElementBlock("img", {
                        key: 0,
                        class: "strm-poster-img",
                        src: _unref(coverUrl)(props.api, props.pluginId, current.value),
                        alt: current.value.title,
                        onError: _cache[8] || (_cache[8] = $event => (markPosterFailed(current.value.path)))
                      }, null, 40, _hoisted_41))
                    : _createCommentVNode("", true)
                ], 4),
                _createElementVNode("div", _hoisted_42, [
                  _createElementVNode("h2", null, _toDisplayString(current.value.title), 1),
                  _createElementVNode("div", _hoisted_43, [
                    _createElementVNode("span", null, _toDisplayString(current.value.type === 'tv' ? '电视剧' : '电影'), 1),
                    _createElementVNode("span", null, _toDisplayString(current.value.total_files) + " 个文件", 1),
                    _createElementVNode("span", null, "待刮削 " + _toDisplayString(current.value.unscraped), 1)
                  ])
                ]),
                _createElementVNode("button", {
                  class: "strm-close",
                  onClick: closeDrawer
                }, "×")
              ]),
              (current.value.type === 'tv')
                ? (_openBlock(), _createElementBlock("div", _hoisted_44, [
                    (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(seasons.value, (season) => {
                      return (_openBlock(), _createElementBlock("button", {
                        key: season.no,
                        class: _normalizeClass(['strm-season-chip', String(season.no) === seasonKey.value && 'active']),
                        onClick: $event => (seasonKey.value = String(season.no))
                      }, [
                        _createTextVNode(_toDisplayString(season.name) + " ", 1),
                        _createElementVNode("em", null, _toDisplayString(season.files.length), 1)
                      ], 10, _hoisted_45))
                    }), 128)),
                    _createElementVNode("div", { class: "strm-season-right" }, [
                      _createElementVNode("button", {
                        class: "strm-mini",
                        onClick: selectAll
                      }, "全选本季"),
                      _createElementVNode("button", {
                        class: "strm-mini",
                        onClick: selectUnscraped
                      }, "选中未刮削"),
                      _createElementVNode("button", {
                        class: "strm-mini",
                        onClick: clearSelection
                      }, "清空")
                    ])
                  ]))
                : _createCommentVNode("", true),
              _createElementVNode("div", _hoisted_46, [
                (!rows.value.length)
                  ? (_openBlock(), _createElementBlock("div", _hoisted_47, "暂无文件"))
                  : _createCommentVNode("", true),
                (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(rows.value, (row) => {
                  return (_openBlock(), _createElementBlock("div", {
                    key: row.path,
                    class: _normalizeClass(['strm-row', selected.value.has(row.path) && 'is-selected'])
                  }, [
                    _createElementVNode("input", {
                      type: "checkbox",
                      checked: selected.value.has(row.path),
                      onChange: $event => (toggleRow(row.path))
                    }, null, 40, _hoisted_48),
                    _createElementVNode("span", _hoisted_49, _toDisplayString(current.value.type === 'tv' ? `E${String(row.episode ?? 0).padStart(2, '0')}` : (row.label || '')), 1),
                    _createElementVNode("span", {
                      class: "strm-row-name",
                      title: row.name
                    }, _toDisplayString(row.name), 9, _hoisted_50),
                    _createElementVNode("span", _hoisted_51, _toDisplayString(_unref(formatSize)(row.size)), 1),
                    _createElementVNode("span", {
                      class: _normalizeClass(['strm-row-state', row.scraped ? 'is-ok' : 'is-none'])
                    }, [
                      _cache[19] || (_cache[19] = _createElementVNode("i", null, null, -1)),
                      _createTextVNode(_toDisplayString(row.scraped ? '已刮削' : '未刮削'), 1)
                    ], 2),
                    _createElementVNode("button", {
                      class: "strm-mini",
                      disabled: busy.value,
                      onClick: $event => (scrapeOne(row))
                    }, "刮削", 8, _hoisted_52)
                  ], 2))
                }), 128))
              ]),
              _createElementVNode("div", _hoisted_53, [
                _createElementVNode("span", _hoisted_54, [
                  _cache[20] || (_cache[20] = _createTextVNode("已选 ", -1)),
                  _createElementVNode("b", null, _toDisplayString(selectedRows.value.length), 1),
                  _cache[21] || (_cache[21] = _createTextVNode(" 项", -1))
                ]),
                _createElementVNode("div", _hoisted_55, [
                  _createElementVNode("button", {
                    class: "strm-btn ghost",
                    disabled: busy.value || !selectedRows.value.length,
                    onClick: scrapeSelected
                  }, " 刮削选中" + _toDisplayString(current.value.type === 'tv' ? '单集' : '版本'), 9, _hoisted_56),
                  _createElementVNode("button", {
                    class: "strm-btn",
                    disabled: busy.value,
                    onClick: scrapeWhole
                  }, _toDisplayString(current.value.type === 'tv' ? '整剧重新刮削' : '整部重新刮削'), 9, _hoisted_57)
                ])
              ])
            ], 64))
          : _createCommentVNode("", true)
      ], 2)
    ], 8, ["to"]))
  ]))
}
}

};
const AppPage = /*#__PURE__*/_export_sfc(_sfc_main, [['__scopeId',"data-v-53b40a90"]]);

export { AppPage as default };
