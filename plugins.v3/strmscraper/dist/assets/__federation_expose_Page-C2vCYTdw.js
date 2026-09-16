import { importShared } from './__federation_fn_import-JrT3xvdd.js';
import { m as makeApiCall, p as posterStyle, c as coverUrl, s as statusOf, u as unwrap } from './strm-Drmjz18_.js';
import { _ as _export_sfc } from './_plugin-vue_export-helper-pcqpp-6-.js';

const {toDisplayString:_toDisplayString,normalizeClass:_normalizeClass,createElementVNode:_createElementVNode,openBlock:_openBlock,createElementBlock:_createElementBlock,createCommentVNode:_createCommentVNode,createTextVNode:_createTextVNode,vModelText:_vModelText,withDirectives:_withDirectives,renderList:_renderList,Fragment:_Fragment,unref:_unref,normalizeStyle:_normalizeStyle} = await importShared('vue');


const _hoisted_1 = { class: "pg" };
const _hoisted_2 = { class: "pg-head" };
const _hoisted_3 = { class: "pg-chip is-muted" };
const _hoisted_4 = { class: "pg-actions" };
const _hoisted_5 = ["disabled"];
const _hoisted_6 = {
  key: 0,
  class: "pg-alert"
};
const _hoisted_7 = {
  key: 1,
  class: "pg-alert is-ok"
};
const _hoisted_8 = { class: "pg-stats" };
const _hoisted_9 = { class: "pg-stat" };
const _hoisted_10 = { class: "pg-stat is-movie" };
const _hoisted_11 = { class: "pg-stat is-tv" };
const _hoisted_12 = { class: "pg-stat is-ok" };
const _hoisted_13 = { class: "pg-stat is-fail" };
const _hoisted_14 = { class: "pg-toolbar" };
const _hoisted_15 = { class: "pg-seg" };
const _hoisted_16 = {
  key: 2,
  class: "pg-empty"
};
const _hoisted_17 = {
  key: 3,
  class: "pg-empty"
};
const _hoisted_18 = {
  key: 4,
  class: "pg-list"
};
const _hoisted_19 = ["src", "alt", "onError"];
const _hoisted_20 = { class: "pg-info" };
const _hoisted_21 = ["title"];
const _hoisted_22 = { class: "pg-path" };
const _hoisted_23 = { class: "pg-tags" };
const _hoisted_24 = {
  key: 0,
  class: "pg-tag is-plain"
};
const _hoisted_25 = {
  key: 1,
  class: "pg-tag is-blue"
};
const _hoisted_26 = {
  key: 2,
  class: "pg-tag is-blue"
};
const _hoisted_27 = {
  key: 3,
  class: "pg-tag is-fail"
};
const _hoisted_28 = {
  key: 4,
  class: "pg-tag is-ok"
};

const {computed,onMounted,reactive,ref} = await importShared('vue');


const _sfc_main = {
  __name: 'Page',
  props: {
  api: { type: Object, default: () => ({}) },
  pluginId: { type: String, default: 'StrmScraper' },
},
  setup(__props) {

const props = __props;

const apiCall = makeApiCall(props.api, props.pluginId);

const loading = ref(true);
const error = ref('');
const notice = ref('');
const overview = ref({});
const items = ref([]);
const keyword = ref('');
const typeFilter = ref('all');
const posterFailed = reactive({});

function markPosterFailed(path) {
  posterFailed[path] = true;
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

async function loadData() {
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

onMounted(() => { loadData(); });

return (_ctx, _cache) => {
  return (_openBlock(), _createElementBlock("div", _hoisted_1, [
    _createElementVNode("div", _hoisted_2, [
      _createElementVNode("div", {
        class: _normalizeClass(['pg-chip', overview.value.monitoring ? 'is-ok' : 'is-muted'])
      }, _toDisplayString(overview.value.monitoring ? '监控运行中' : '监控未启动'), 3),
      _createElementVNode("div", _hoisted_3, _toDisplayString((overview.value.monitor_dirs || []).length) + " 个监控目录", 1),
      _createElementVNode("div", _hoisted_4, [
        _createElementVNode("button", {
          class: "pg-btn",
          disabled: loading.value,
          onClick: loadData
        }, "刷新", 8, _hoisted_5)
      ])
    ]),
    (error.value)
      ? (_openBlock(), _createElementBlock("div", _hoisted_6, _toDisplayString(error.value), 1))
      : (notice.value)
        ? (_openBlock(), _createElementBlock("div", _hoisted_7, _toDisplayString(notice.value), 1))
        : _createCommentVNode("", true),
    _createElementVNode("div", _hoisted_8, [
      _createElementVNode("div", _hoisted_9, [
        _createElementVNode("b", null, _toDisplayString(overview.value.total || 0), 1),
        _cache[4] || (_cache[4] = _createElementVNode("span", null, "媒体总数", -1))
      ]),
      _createElementVNode("div", _hoisted_10, [
        _createElementVNode("b", null, _toDisplayString(overview.value.movie || 0), 1),
        _cache[5] || (_cache[5] = _createElementVNode("span", null, "电影", -1))
      ]),
      _createElementVNode("div", _hoisted_11, [
        _createElementVNode("b", null, _toDisplayString(overview.value.tv || 0), 1),
        _cache[6] || (_cache[6] = _createElementVNode("span", null, "电视剧", -1))
      ]),
      _createElementVNode("div", _hoisted_12, [
        _createElementVNode("b", null, _toDisplayString(overview.value.dir_scraped || 0), 1),
        _cache[7] || (_cache[7] = _createElementVNode("span", null, "已刮削目录", -1))
      ]),
      _createElementVNode("div", _hoisted_13, [
        _createElementVNode("b", null, _toDisplayString(overview.value.unscraped_items || 0), 1),
        _cache[8] || (_cache[8] = _createElementVNode("span", null, "待刮削", -1))
      ])
    ]),
    _createElementVNode("div", _hoisted_14, [
      _createElementVNode("div", _hoisted_15, [
        _createElementVNode("button", {
          class: _normalizeClass(['pg-seg-item', typeFilter.value === 'all' && 'active']),
          onClick: _cache[0] || (_cache[0] = $event => (typeFilter.value = 'all'))
        }, [
          _cache[9] || (_cache[9] = _createTextVNode(" 全部 ", -1)),
          _createElementVNode("em", null, _toDisplayString(counts.value.all), 1)
        ], 2),
        _createElementVNode("button", {
          class: _normalizeClass(['pg-seg-item', typeFilter.value === 'movie' && 'active']),
          onClick: _cache[1] || (_cache[1] = $event => (typeFilter.value = 'movie'))
        }, [
          _cache[10] || (_cache[10] = _createTextVNode(" 电影 ", -1)),
          _createElementVNode("em", null, _toDisplayString(counts.value.movie), 1)
        ], 2),
        _createElementVNode("button", {
          class: _normalizeClass(['pg-seg-item', typeFilter.value === 'tv' && 'active']),
          onClick: _cache[2] || (_cache[2] = $event => (typeFilter.value = 'tv'))
        }, [
          _cache[11] || (_cache[11] = _createTextVNode(" 电视剧 ", -1)),
          _createElementVNode("em", null, _toDisplayString(counts.value.tv), 1)
        ], 2)
      ]),
      _withDirectives(_createElementVNode("input", {
        "onUpdate:modelValue": _cache[3] || (_cache[3] = $event => ((keyword).value = $event)),
        class: "pg-search",
        type: "text",
        placeholder: "搜索标题或路径"
      }, null, 512), [
        [_vModelText, keyword.value]
      ])
    ]),
    (loading.value)
      ? (_openBlock(), _createElementBlock("div", _hoisted_16, "正在扫描监控目录…"))
      : (!visibleItems.value.length)
        ? (_openBlock(), _createElementBlock("div", _hoisted_17, "没有匹配的媒体"))
        : (_openBlock(), _createElementBlock("div", _hoisted_18, [
            (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(visibleItems.value, (item) => {
              return (_openBlock(), _createElementBlock("div", {
                key: item.path,
                class: "pg-item"
              }, [
                _createElementVNode("div", {
                  class: "pg-poster",
                  style: _normalizeStyle(posterFailed[item.path] ? _unref(posterStyle)(item.title) : '')
                }, [
                  (!posterFailed[item.path])
                    ? (_openBlock(), _createElementBlock("img", {
                        key: 0,
                        src: _unref(coverUrl)(props.api, props.pluginId, item),
                        alt: item.title,
                        loading: "lazy",
                        onError: $event => (markPosterFailed(item.path))
                      }, null, 40, _hoisted_19))
                    : _createCommentVNode("", true),
                  _createElementVNode("span", {
                    class: _normalizeClass(['pg-dot', `is-${_unref(statusOf)(item)}`])
                  }, null, 2)
                ], 4),
                _createElementVNode("div", _hoisted_20, [
                  _createElementVNode("div", {
                    class: "pg-title",
                    title: item.path
                  }, _toDisplayString(item.title), 9, _hoisted_21),
                  _createElementVNode("div", _hoisted_22, _toDisplayString(item.path), 1),
                  _createElementVNode("div", _hoisted_23, [
                    _createElementVNode("span", {
                      class: _normalizeClass(['pg-tag', item.type === 'tv' ? 'is-tv' : 'is-mv'])
                    }, _toDisplayString(item.type === 'tv' ? '电视剧' : '电影'), 3),
                    (item.type === 'tv')
                      ? (_openBlock(), _createElementBlock("span", _hoisted_24, _toDisplayString(item.total_episodes || item.total_files) + " 集 · " + _toDisplayString((item.seasons || []).length) + " 季", 1))
                      : (item.multi_version)
                        ? (_openBlock(), _createElementBlock("span", _hoisted_25, _toDisplayString(item.total_files) + " 版本", 1))
                        : (_openBlock(), _createElementBlock("span", _hoisted_26, "单版本")),
                    (item.unscraped)
                      ? (_openBlock(), _createElementBlock("span", _hoisted_27, _toDisplayString(item.unscraped) + " 待刮", 1))
                      : (_openBlock(), _createElementBlock("span", _hoisted_28, "已刮削"))
                  ])
                ])
              ]))
            }), 128))
          ])),
    _cache[12] || (_cache[12] = _createElementVNode("div", { class: "pg-foot" }, "完整刮削操作（单集/版本重刮、强制全量）请使用左侧导航的「STRM 刮削」页面。", -1))
  ]))
}
}

};
const Page = /*#__PURE__*/_export_sfc(_sfc_main, [['__scopeId',"data-v-6d88c543"]]);

export { Page as default };
