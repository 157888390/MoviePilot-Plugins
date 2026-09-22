import { importShared } from './__federation_fn_import-JrT3xvdd.js';
import ConfigPanel from './__federation_expose_Config-eY35RbqA.js';
import { m as makeApiCall, v as versionLabel, c as categoryColor, p as posterStyle, a as coverUrl, s as statusOf, f as formatSize, u as unwrap, b as bodyOf } from './strm-CfZmxq-8.js';
import { _ as _export_sfc } from './_plugin-vue_export-helper-pcqpp-6-.js';

const {createElementVNode:_createElementVNode,toDisplayString:_toDisplayString,normalizeClass:_normalizeClass,openBlock:_openBlock,createElementBlock:_createElementBlock,createCommentVNode:_createCommentVNode,normalizeStyle:_normalizeStyle,createTextVNode:_createTextVNode,renderList:_renderList,Fragment:_Fragment,unref:_unref,vModelText:_vModelText,withDirectives:_withDirectives,createVNode:_createVNode,withModifiers:_withModifiers,Teleport:_Teleport,createBlock:_createBlock} = await importShared('vue');


const _hoisted_1 = { class: "strm-page" };
const _hoisted_2 = { class: "strm-head" };
const _hoisted_3 = { class: "strm-head-chips" };
const _hoisted_4 = { class: "strm-chip is-muted" };
const _hoisted_5 = { class: "strm-head-actions" };
const _hoisted_6 = ["disabled"];
const _hoisted_7 = ["disabled"];
const _hoisted_8 = ["disabled"];
const _hoisted_9 = ["disabled"];
const _hoisted_10 = {
  key: 0,
  class: "strm-alert is-error"
};
const _hoisted_11 = {
  key: 1,
  class: "strm-alert is-ok"
};
const _hoisted_12 = {
  key: 2,
  class: "strm-task"
};
const _hoisted_13 = { class: "strm-task-text" };
const _hoisted_14 = { class: "strm-task-bar" };
const _hoisted_15 = { class: "strm-stats" };
const _hoisted_16 = { class: "strm-stat" };
const _hoisted_17 = { class: "strm-stat is-movie" };
const _hoisted_18 = { class: "strm-stat is-tv" };
const _hoisted_19 = { class: "strm-stat is-ok" };
const _hoisted_20 = { class: "strm-stat is-fail" };
const _hoisted_21 = {
  key: 3,
  class: "strm-cats"
};
const _hoisted_22 = ["onClick"];
const _hoisted_23 = {
  key: 0,
  class: "strm-cat-pend"
};
const _hoisted_24 = {
  key: 0,
  class: "strm-cat-right"
};
const _hoisted_25 = ["disabled"];
const _hoisted_26 = {
  key: 4,
  class: "strm-records"
};
const _hoisted_27 = { class: "strm-records-head" };
const _hoisted_28 = { class: "strm-records-count" };
const _hoisted_29 = { class: "strm-records-actions" };
const _hoisted_30 = ["disabled"];
const _hoisted_31 = {
  key: 0,
  class: "strm-records-empty"
};
const _hoisted_32 = {
  key: 1,
  class: "strm-records-list"
};
const _hoisted_33 = { class: "strm-record-time" };
const _hoisted_34 = ["title"];
const _hoisted_35 = ["title"];
const _hoisted_36 = { class: "strm-toolbar" };
const _hoisted_37 = { class: "strm-seg" };
const _hoisted_38 = {
  key: 5,
  class: "strm-empty"
};
const _hoisted_39 = {
  key: 6,
  class: "strm-empty"
};
const _hoisted_40 = {
  key: 7,
  class: "strm-grid"
};
const _hoisted_41 = ["src", "alt", "onError"];
const _hoisted_42 = { class: "strm-poster-foot" };
const _hoisted_43 = {
  key: 0,
  class: "strm-pill"
};
const _hoisted_44 = {
  key: 1,
  class: "strm-pill is-blue"
};
const _hoisted_45 = {
  key: 2,
  class: "strm-pill is-blue"
};
const _hoisted_46 = { key: 3 };
const _hoisted_47 = { class: "strm-card-body" };
const _hoisted_48 = ["title"];
const _hoisted_49 = { class: "strm-card-meta" };
const _hoisted_50 = { class: "strm-card-actions" };
const _hoisted_51 = ["onClick"];
const _hoisted_52 = ["onClick"];
const _hoisted_53 = ["disabled", "onClick"];
const _hoisted_54 = ["disabled", "onClick"];
const _hoisted_55 = { class: "strm-cfg-wrap" };
const _hoisted_56 = { class: "strm-drawer-head" };
const _hoisted_57 = ["src", "alt"];
const _hoisted_58 = { class: "strm-drawer-info" };
const _hoisted_59 = { class: "strm-drawer-sub" };
const _hoisted_60 = {
  key: 0,
  class: "strm-season-bar"
};
const _hoisted_61 = ["onClick"];
const _hoisted_62 = { class: "strm-list" };
const _hoisted_63 = {
  key: 0,
  class: "strm-empty"
};
const _hoisted_64 = ["checked", "onChange"];
const _hoisted_65 = { class: "strm-row-no" };
const _hoisted_66 = ["title"];
const _hoisted_67 = { class: "strm-row-size" };
const _hoisted_68 = ["disabled", "onClick"];
const _hoisted_69 = { class: "strm-drawer-foot" };
const _hoisted_70 = { class: "strm-sel" };
const _hoisted_71 = { class: "strm-foot-right" };
const _hoisted_72 = ["disabled"];
const _hoisted_73 = ["disabled"];

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
const categories = ref([]);
const categoryFilter = ref('');
const keyword = ref('');
const typeFilter = ref('all');
const showRecords = ref(false);
const records = ref([]);

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
const loadingSettings = ref(false);
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

// 必须先拿到配置再挂载面板：Config 只在初值到达后才渲染，避免先闪一屏默认值
async function openSettings() {
  if (loadingSettings.value) return
  loadingSettings.value = true;
  try {
    const raw = await props.api.get(`plugin/form/${props.pluginId}`);
    settingsModel.value = bodyOf(raw)?.model || {};
    showSettings.value = true;
  } catch (readError) {
    error.value = `读取配置失败：${readError?.message || readError}`;
  } finally {
    loadingSettings.value = false;
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

// 分类分组固定由后端下发（含「未分类」），这里只负责把当前选中的分类挑出来
const activeCategory = computed(
  () => categories.value.find(c => c.name === categoryFilter.value) || null,
);

const visibleItems = computed(() => {
  const kw = keyword.value.trim().toLowerCase();
  return items.value.filter(item => {
    if (typeFilter.value !== 'all' && item.type !== typeFilter.value) return false
    // 「未分类」是 path 为空的松散分组，按 category 字段判空匹配
    if (categoryFilter.value) {
      const current = activeCategory.value;
      const itemCategory = item.category || '未分类';
      if (current && !current.path) {
        if (itemCategory !== '未分类') return false
      } else if (itemCategory !== categoryFilter.value) {
        return false
      }
    }
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
    const [overviewResponse, itemsResponse, categoryResponse] = await Promise.all([
      apiCall('get', '/overview'),
      apiCall('get', '/items'),
      apiCall('get', '/categories'),
    ]);
    overview.value = unwrap(overviewResponse);
    items.value = unwrap(itemsResponse) || [];
    categories.value = unwrap(categoryResponse) || [];
    // 选中的分类如果已经消失（目录被删/改名），回到「全部」避免列表空着让人困惑
    if (categoryFilter.value && !categories.value.some(c => c.name === categoryFilter.value)) {
      categoryFilter.value = '';
    }
  } catch (loadError) {
    error.value = loadError?.message || '加载媒体清单失败';
  } finally {
    loading.value = false;
  }
}

async function refreshItems() {
  const [itemsResponse, overviewResponse, categoryResponse] = await Promise.all([
    apiCall('get', '/items?refresh=true'),
    apiCall('get', '/overview'),
    apiCall('get', '/categories'),
  ]);
  items.value = unwrap(itemsResponse) || [];
  overview.value = unwrap(overviewResponse);
  categories.value = unwrap(categoryResponse) || [];
}

async function triggerFullScan(force) {
  busy.value = true;
  try {
    unwrap(await apiCall('get', force ? '/scan?force=true' : '/scan'));
    notice.value = '已在后台启动全量扫描';
  } catch (scanError) {
    error.value = scanError?.message || '触发全量扫描失败';
  } finally {
    busy.value = false;
  }
}

// 按分类刷新：只把该分类目录交给后端，避免为了一个新番把整个库重扫一遍
async function scanCategory(category) {
  if (!category?.path) return
  busy.value = true;
  error.value = '';
  notice.value = '';
  try {
    const response = unwrap(
      await apiCall('get', `/scan?scope=category&paths=${encodeURIComponent(category.path)}`),
    );
    notice.value = response?.message || `已启动「${category.name}」分类扫描`;
  } catch (scanError) {
    error.value = scanError?.message || `触发「${category.name}」扫描失败`;
  } finally {
    busy.value = false;
  }
}

async function loadRecords() {
  try {
    records.value = unwrap(await apiCall('get', '/records?limit=100')) || [];
  } catch (recordError) {
    error.value = recordError?.message || '读取刮削记录失败';
  }
}

async function toggleRecords() {
  showRecords.value = !showRecords.value;
  if (showRecords.value) await loadRecords();
}

async function clearRecords() {
  try {
    unwrap(await apiCall('get', '/records/clear'));
    records.value = [];
    notice.value = '刮削记录已清空';
  } catch (clearError) {
    error.value = clearError?.message || '清空刮削记录失败';
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
      _cache[12] || (_cache[12] = _createElementVNode("div", { class: "strm-logo" }, "S", -1)),
      _cache[13] || (_cache[13] = _createElementVNode("div", { class: "strm-heading" }, [
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
          disabled: loadingSettings.value,
          onClick: openSettings
        }, _toDisplayString(loadingSettings.value ? '读取中…' : '设置'), 9, _hoisted_6),
        _createElementVNode("button", {
          class: _normalizeClass(["strm-btn ghost", { 'is-on': showRecords.value }]),
          onClick: toggleRecords
        }, "刮削记录", 2),
        _createElementVNode("button", {
          class: "strm-btn ghost",
          disabled: loading.value || busy.value,
          onClick: loadEverything
        }, "刷新", 8, _hoisted_7),
        _createElementVNode("button", {
          class: "strm-btn ghost",
          disabled: busy.value,
          onClick: _cache[0] || (_cache[0] = $event => (triggerFullScan(false)))
        }, "全量扫描", 8, _hoisted_8),
        _createElementVNode("button", {
          class: "strm-btn",
          disabled: busy.value,
          onClick: _cache[1] || (_cache[1] = $event => (triggerFullScan(true)))
        }, "强制全量", 8, _hoisted_9)
      ])
    ]),
    (error.value)
      ? (_openBlock(), _createElementBlock("div", _hoisted_10, _toDisplayString(error.value), 1))
      : (notice.value)
        ? (_openBlock(), _createElementBlock("div", _hoisted_11, _toDisplayString(notice.value), 1))
        : _createCommentVNode("", true),
    (task.value)
      ? (_openBlock(), _createElementBlock("div", _hoisted_12, [
          _createElementVNode("span", _hoisted_13, " 刮削中 " + _toDisplayString(task.value.done || 0) + "/" + _toDisplayString(task.value.total || 0) + " · 成功 " + _toDisplayString(task.value.success || 0) + " · 失败 " + _toDisplayString(task.value.failed || 0), 1),
          _createElementVNode("span", _hoisted_14, [
            _createElementVNode("i", {
              style: _normalizeStyle({ width: `${task.value.total ? Math.round(((task.value.done || 0) / task.value.total) * 100) : 0}%` })
            }, null, 4)
          ])
        ]))
      : _createCommentVNode("", true),
    _createElementVNode("div", _hoisted_15, [
      _createElementVNode("div", _hoisted_16, [
        _createElementVNode("b", null, _toDisplayString(overview.value.total || 0), 1),
        _cache[14] || (_cache[14] = _createElementVNode("span", null, "媒体总数", -1))
      ]),
      _createElementVNode("div", _hoisted_17, [
        _createElementVNode("b", null, _toDisplayString(overview.value.movie || 0), 1),
        _cache[15] || (_cache[15] = _createElementVNode("span", null, "电影", -1))
      ]),
      _createElementVNode("div", _hoisted_18, [
        _createElementVNode("b", null, _toDisplayString(overview.value.tv || 0), 1),
        _cache[16] || (_cache[16] = _createElementVNode("span", null, "电视剧", -1))
      ]),
      _createElementVNode("div", _hoisted_19, [
        _createElementVNode("b", null, _toDisplayString(overview.value.dir_scraped || 0), 1),
        _cache[17] || (_cache[17] = _createElementVNode("span", null, "已刮削目录", -1))
      ]),
      _createElementVNode("div", _hoisted_20, [
        _createElementVNode("b", null, _toDisplayString(overview.value.unscraped_items || 0), 1),
        _cache[18] || (_cache[18] = _createElementVNode("span", null, "待刮削", -1))
      ])
    ]),
    (categories.value.length)
      ? (_openBlock(), _createElementBlock("div", _hoisted_21, [
          _createElementVNode("button", {
            class: _normalizeClass(['strm-cat', !categoryFilter.value && 'active']),
            onClick: _cache[2] || (_cache[2] = $event => (categoryFilter.value = ''))
          }, [
            _cache[19] || (_cache[19] = _createTextVNode(" 全部分类 ", -1)),
            _createElementVNode("em", null, _toDisplayString(items.value.length), 1)
          ], 2),
          (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(categories.value, (cat) => {
            return (_openBlock(), _createElementBlock("button", {
              key: cat.path || cat.name,
              class: _normalizeClass(['strm-cat', categoryFilter.value === cat.name && 'active']),
              style: _normalizeStyle(categoryFilter.value === cat.name ? { background: _unref(categoryColor)(cat.name), borderColor: _unref(categoryColor)(cat.name) } : { color: _unref(categoryColor)(cat.name), borderColor: `${_unref(categoryColor)(cat.name)}55` }),
              onClick: $event => (categoryFilter.value = categoryFilter.value === cat.name ? '' : cat.name)
            }, [
              _createTextVNode(_toDisplayString(cat.name) + " ", 1),
              _createElementVNode("em", null, _toDisplayString(cat.total), 1),
              (cat.unscraped)
                ? (_openBlock(), _createElementBlock("i", _hoisted_23, _toDisplayString(cat.unscraped) + " 待刮", 1))
                : _createCommentVNode("", true)
            ], 14, _hoisted_22))
          }), 128)),
          (activeCategory.value && activeCategory.value.path)
            ? (_openBlock(), _createElementBlock("div", _hoisted_24, [
                _createElementVNode("button", {
                  class: "strm-mini",
                  disabled: busy.value,
                  onClick: _cache[3] || (_cache[3] = $event => (scanCategory(activeCategory.value)))
                }, " 刷新「" + _toDisplayString(activeCategory.value.name) + "」 ", 9, _hoisted_25)
              ]))
            : _createCommentVNode("", true)
        ]))
      : _createCommentVNode("", true),
    (showRecords.value)
      ? (_openBlock(), _createElementBlock("div", _hoisted_26, [
          _createElementVNode("div", _hoisted_27, [
            _cache[20] || (_cache[20] = _createElementVNode("b", null, "刮削记录", -1)),
            _createElementVNode("span", _hoisted_28, "最近 " + _toDisplayString(records.value.length) + " 条", 1),
            _createElementVNode("div", _hoisted_29, [
              _createElementVNode("button", {
                class: "strm-mini",
                onClick: loadRecords
              }, "刷新"),
              _createElementVNode("button", {
                class: "strm-mini",
                disabled: !records.value.length,
                onClick: clearRecords
              }, "清空", 8, _hoisted_30),
              _createElementVNode("button", {
                class: "strm-mini",
                onClick: _cache[4] || (_cache[4] = $event => (showRecords.value = false))
              }, "收起")
            ])
          ]),
          (!records.value.length)
            ? (_openBlock(), _createElementBlock("div", _hoisted_31, "还没有刮削记录"))
            : (_openBlock(), _createElementBlock("div", _hoisted_32, [
                (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(records.value, (row, index) => {
                  return (_openBlock(), _createElementBlock("div", {
                    key: `${row.time}-${index}`,
                    class: "strm-record-row"
                  }, [
                    _createElementVNode("span", _hoisted_33, _toDisplayString(row.time), 1),
                    _createElementVNode("span", {
                      class: _normalizeClass(['strm-record-type', row.type === 'dir' ? 'is-dir' : 'is-file'])
                    }, _toDisplayString(row.type === 'dir' ? '目录' : '单集'), 3),
                    _createElementVNode("span", {
                      class: "strm-record-title",
                      title: row.target
                    }, _toDisplayString(row.title), 9, _hoisted_34),
                    (row.category)
                      ? (_openBlock(), _createElementBlock("span", {
                          key: 0,
                          class: "strm-record-cat",
                          style: _normalizeStyle({ color: _unref(categoryColor)(row.category) })
                        }, _toDisplayString(row.category), 5))
                      : _createCommentVNode("", true),
                    _createElementVNode("span", {
                      class: _normalizeClass(['strm-record-state', row.success ? 'is-ok' : 'is-fail'])
                    }, _toDisplayString(row.success ? '成功' : '失败'), 3),
                    _createElementVNode("span", {
                      class: "strm-record-msg",
                      title: row.message
                    }, _toDisplayString(row.message || '—'), 9, _hoisted_35)
                  ]))
                }), 128))
              ]))
        ]))
      : _createCommentVNode("", true),
    _createElementVNode("div", _hoisted_36, [
      _createElementVNode("div", _hoisted_37, [
        _createElementVNode("button", {
          class: _normalizeClass(['strm-seg-item', typeFilter.value === 'all' && 'active']),
          onClick: _cache[5] || (_cache[5] = $event => (typeFilter.value = 'all'))
        }, [
          _cache[21] || (_cache[21] = _createTextVNode(" 全部 ", -1)),
          _createElementVNode("em", null, _toDisplayString(counts.value.all), 1)
        ], 2),
        _createElementVNode("button", {
          class: _normalizeClass(['strm-seg-item', typeFilter.value === 'movie' && 'active mv']),
          onClick: _cache[6] || (_cache[6] = $event => (typeFilter.value = 'movie'))
        }, [
          _cache[22] || (_cache[22] = _createTextVNode(" 电影 ", -1)),
          _createElementVNode("em", null, _toDisplayString(counts.value.movie), 1)
        ], 2),
        _createElementVNode("button", {
          class: _normalizeClass(['strm-seg-item', typeFilter.value === 'tv' && 'active tv']),
          onClick: _cache[7] || (_cache[7] = $event => (typeFilter.value = 'tv'))
        }, [
          _cache[23] || (_cache[23] = _createTextVNode(" 电视剧 ", -1)),
          _createElementVNode("em", null, _toDisplayString(counts.value.tv), 1)
        ], 2)
      ]),
      _withDirectives(_createElementVNode("input", {
        "onUpdate:modelValue": _cache[8] || (_cache[8] = $event => ((keyword).value = $event)),
        class: "strm-search",
        type: "text",
        placeholder: "搜索标题或路径"
      }, null, 512), [
        [_vModelText, keyword.value]
      ])
    ]),
    (loading.value)
      ? (_openBlock(), _createElementBlock("div", _hoisted_38, "正在扫描监控目录…"))
      : (!visibleItems.value.length)
        ? (_openBlock(), _createElementBlock("div", _hoisted_39, "没有匹配的媒体"))
        : (_openBlock(), _createElementBlock("div", _hoisted_40, [
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
                      }, null, 40, _hoisted_41))
                    : _createCommentVNode("", true),
                  _createElementVNode("span", {
                    class: _normalizeClass(['strm-type', item.type === 'tv' ? 'is-tv' : 'is-mv'])
                  }, _toDisplayString(item.type === 'tv' ? '电视剧' : '电影'), 3),
                  _createElementVNode("span", {
                    class: _normalizeClass(['strm-dot', `is-${_unref(statusOf)(item)}`])
                  }, null, 2),
                  _createElementVNode("div", _hoisted_42, [
                    (item.type === 'tv')
                      ? (_openBlock(), _createElementBlock("span", _hoisted_43, _toDisplayString(item.total_episodes || item.total_files) + " 集 · " + _toDisplayString((item.seasons || []).length) + " 季", 1))
                      : (item.multi_version)
                        ? (_openBlock(), _createElementBlock("span", _hoisted_44, _toDisplayString(item.total_files) + " 版本", 1))
                        : (_openBlock(), _createElementBlock("span", _hoisted_45, "单版本")),
                    (item.unscraped)
                      ? (_openBlock(), _createElementBlock("em", _hoisted_46, _toDisplayString(item.unscraped) + " 待刮", 1))
                      : _createCommentVNode("", true)
                  ])
                ], 4),
                _createElementVNode("div", _hoisted_47, [
                  _createElementVNode("div", {
                    class: "strm-card-title",
                    title: item.path
                  }, _toDisplayString(item.title), 9, _hoisted_48),
                  (item.category)
                    ? (_openBlock(), _createElementBlock("div", {
                        key: 0,
                        class: "strm-card-cat",
                        style: _normalizeStyle({ color: _unref(categoryColor)(item.category) })
                      }, _toDisplayString(item.category), 5))
                    : _createCommentVNode("", true),
                  _createElementVNode("div", _hoisted_49, _toDisplayString(item.path), 1),
                  _createElementVNode("div", _hoisted_50, [
                    (item.type === 'tv')
                      ? (_openBlock(), _createElementBlock("button", {
                          key: 0,
                          class: "strm-btn small is-tv",
                          onClick: $event => (openDrawer(item))
                        }, "剧集预览", 8, _hoisted_51))
                      : (item.multi_version)
                        ? (_openBlock(), _createElementBlock("button", {
                            key: 1,
                            class: "strm-btn small is-mv",
                            onClick: $event => (openDrawer(item))
                          }, "版本预览", 8, _hoisted_52))
                        : (_openBlock(), _createElementBlock("button", {
                            key: 2,
                            class: "strm-btn small",
                            disabled: busy.value,
                            onClick: $event => (scrapeCard(item))
                          }, "刮削", 8, _hoisted_53)),
                    (item.type === 'tv' || item.multi_version)
                      ? (_openBlock(), _createElementBlock("button", {
                          key: 3,
                          class: "strm-btn small ghost",
                          disabled: busy.value,
                          onClick: $event => (scrapeCard(item))
                        }, "整部重刮", 8, _hoisted_54))
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
            onClick: _cache[10] || (_cache[10] = _withModifiers($event => (showSettings.value = false), ["self"]))
          }, [
            _createElementVNode("div", _hoisted_55, [
              _createVNode(ConfigPanel, {
                "initial-config": settingsModel.value,
                api: props.api,
                "plugin-id": props.pluginId,
                onSave: saveSettings,
                onClose: _cache[9] || (_cache[9] = $event => (showSettings.value = false))
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
              _createElementVNode("div", _hoisted_56, [
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
                        onError: _cache[11] || (_cache[11] = $event => (markPosterFailed(current.value.path)))
                      }, null, 40, _hoisted_57))
                    : _createCommentVNode("", true)
                ], 4),
                _createElementVNode("div", _hoisted_58, [
                  _createElementVNode("h2", null, _toDisplayString(current.value.title), 1),
                  _createElementVNode("div", _hoisted_59, [
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
                ? (_openBlock(), _createElementBlock("div", _hoisted_60, [
                    (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(seasons.value, (season) => {
                      return (_openBlock(), _createElementBlock("button", {
                        key: season.no,
                        class: _normalizeClass(['strm-season-chip', String(season.no) === seasonKey.value && 'active']),
                        onClick: $event => (seasonKey.value = String(season.no))
                      }, [
                        _createTextVNode(_toDisplayString(season.name) + " ", 1),
                        _createElementVNode("em", null, _toDisplayString(season.files.length), 1)
                      ], 10, _hoisted_61))
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
              _createElementVNode("div", _hoisted_62, [
                (!rows.value.length)
                  ? (_openBlock(), _createElementBlock("div", _hoisted_63, "暂无文件"))
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
                    }, null, 40, _hoisted_64),
                    _createElementVNode("span", _hoisted_65, _toDisplayString(current.value.type === 'tv' ? `E${String(row.episode ?? 0).padStart(2, '0')}` : (row.label || '')), 1),
                    _createElementVNode("span", {
                      class: "strm-row-name",
                      title: row.name
                    }, _toDisplayString(row.name), 9, _hoisted_66),
                    _createElementVNode("span", _hoisted_67, _toDisplayString(_unref(formatSize)(row.size)), 1),
                    _createElementVNode("span", {
                      class: _normalizeClass(['strm-row-state', row.scraped ? 'is-ok' : 'is-none'])
                    }, [
                      _cache[24] || (_cache[24] = _createElementVNode("i", null, null, -1)),
                      _createTextVNode(_toDisplayString(row.scraped ? '已刮削' : '未刮削'), 1)
                    ], 2),
                    _createElementVNode("button", {
                      class: "strm-mini",
                      disabled: busy.value,
                      onClick: $event => (scrapeOne(row))
                    }, "刮削", 8, _hoisted_68)
                  ], 2))
                }), 128))
              ]),
              _createElementVNode("div", _hoisted_69, [
                _createElementVNode("span", _hoisted_70, [
                  _cache[25] || (_cache[25] = _createTextVNode("已选 ", -1)),
                  _createElementVNode("b", null, _toDisplayString(selectedRows.value.length), 1),
                  _cache[26] || (_cache[26] = _createTextVNode(" 项", -1))
                ]),
                _createElementVNode("div", _hoisted_71, [
                  _createElementVNode("button", {
                    class: "strm-btn ghost",
                    disabled: busy.value || !selectedRows.value.length,
                    onClick: scrapeSelected
                  }, " 刮削选中" + _toDisplayString(current.value.type === 'tv' ? '单集' : '版本'), 9, _hoisted_72),
                  _createElementVNode("button", {
                    class: "strm-btn",
                    disabled: busy.value,
                    onClick: scrapeWhole
                  }, _toDisplayString(current.value.type === 'tv' ? '整剧重新刮削' : '整部重新刮削'), 9, _hoisted_73)
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
const AppPage = /*#__PURE__*/_export_sfc(_sfc_main, [['__scopeId',"data-v-fc3f4952"]]);

export { AppPage as default };
