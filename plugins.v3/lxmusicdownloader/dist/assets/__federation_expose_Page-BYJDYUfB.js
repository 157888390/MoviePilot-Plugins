import { importShared } from './__federation_fn_import-JrT3xvdd.js';
import { _ as _export_sfc, m as makeApiCall, f as formatSize, u as unwrap } from './_plugin-vue_export-helper-DXLvoj9D.js';

const {toDisplayString:_toDisplayString,normalizeClass:_normalizeClass,createElementVNode:_createElementVNode,openBlock:_openBlock,createElementBlock:_createElementBlock,createCommentVNode:_createCommentVNode,unref:_unref,createStaticVNode:_createStaticVNode} = await importShared('vue');


const _hoisted_1 = { class: "pg" };
const _hoisted_2 = { class: "pg-head" };
const _hoisted_3 = { class: "pg-actions" };
const _hoisted_4 = ["disabled"];
const _hoisted_5 = {
  key: 0,
  class: "pg-alert"
};
const _hoisted_6 = { class: "pg-stats" };
const _hoisted_7 = { class: "pg-stat is-ok" };
const _hoisted_8 = { class: "pg-stat" };
const _hoisted_9 = { class: "pg-stat is-mv" };
const _hoisted_10 = { class: "pg-stat" };
const _hoisted_11 = { class: "pg-table" };
const _hoisted_12 = { class: "pg-line" };
const _hoisted_13 = { class: "pg-line" };
const _hoisted_14 = { class: "pg-line" };
const _hoisted_15 = { class: "pg-line" };
const _hoisted_16 = { class: "pg-line" };
const _hoisted_17 = { class: "pg-line" };

const {onMounted,ref} = await importShared('vue');


const _sfc_main = {
  __name: 'Page',
  props: {
  api: { type: Object, default: () => ({}) },
  pluginId: { type: String, default: 'LxMusicDownloader' },
},
  setup(__props) {

const props = __props;

const apiCall = makeApiCall(props.api, props.pluginId);

const loading = ref(true);
const error = ref('');
const overview = ref({});
const stats = ref(null);

async function loadData() {
  loading.value = true;
  error.value = '';
  try {
    const [overviewResponse, statsResponse] = await Promise.all([
      apiCall('get', '/overview'),
      apiCall('get', '/stats').catch(() => null),
    ]);
    overview.value = unwrap(overviewResponse);
    stats.value = statsResponse ? unwrap(statsResponse) : null;
  } catch (loadError) {
    error.value = loadError?.message || '读取运行状态失败';
  } finally {
    loading.value = false;
  }
}

onMounted(() => { loadData(); });

return (_ctx, _cache) => {
  return (_openBlock(), _createElementBlock("div", _hoisted_1, [
    _createElementVNode("div", _hoisted_2, [
      _createElementVNode("div", {
        class: _normalizeClass(['pg-chip', overview.value.auth_state === 'ok' ? 'is-ok' : 'is-warn'])
      }, _toDisplayString(overview.value.auth_state === 'ok' ? '鉴权正常' : (overview.value.auth_state === 'anonymous' ? '未配置凭据' : '鉴权异常')), 3),
      _createElementVNode("div", {
        class: _normalizeClass(['pg-chip', overview.value.enabled ? 'is-ok' : 'is-muted'])
      }, _toDisplayString(overview.value.enabled ? '插件已启用' : '插件未启用'), 3),
      _createElementVNode("div", _hoisted_3, [
        _createElementVNode("button", {
          class: "pg-btn",
          disabled: loading.value,
          onClick: loadData
        }, "刷新", 8, _hoisted_4)
      ])
    ]),
    (error.value)
      ? (_openBlock(), _createElementBlock("div", _hoisted_5, _toDisplayString(error.value), 1))
      : _createCommentVNode("", true),
    _createElementVNode("div", _hoisted_6, [
      _createElementVNode("div", _hoisted_7, [
        _createElementVNode("b", null, _toDisplayString(_unref(formatSize)(stats.value?.totalSize)), 1),
        _cache[0] || (_cache[0] = _createElementVNode("span", null, "服务端缓存", -1))
      ]),
      _createElementVNode("div", _hoisted_8, [
        _createElementVNode("b", null, _toDisplayString(stats.value?.fileCount || 0), 1),
        _cache[1] || (_cache[1] = _createElementVNode("span", null, "缓存文件", -1))
      ]),
      _createElementVNode("div", _hoisted_9, [
        _createElementVNode("b", null, _toDisplayString(overview.value.source_name || '-'), 1),
        _cache[2] || (_cache[2] = _createElementVNode("span", null, "音源平台", -1))
      ]),
      _createElementVNode("div", _hoisted_10, [
        _createElementVNode("b", null, _toDisplayString(overview.value.quality || '-'), 1),
        _cache[3] || (_cache[3] = _createElementVNode("span", null, "下载音质", -1))
      ])
    ]),
    _createElementVNode("div", _hoisted_11, [
      _createElementVNode("div", _hoisted_12, [
        _cache[4] || (_cache[4] = _createElementVNode("span", null, "服务端地址", -1)),
        _createElementVNode("b", null, _toDisplayString(overview.value.host || '未配置'), 1)
      ]),
      _createElementVNode("div", _hoisted_13, [
        _cache[5] || (_cache[5] = _createElementVNode("span", null, "鉴权状态", -1)),
        _createElementVNode("b", null, _toDisplayString(overview.value.auth_text || '-'), 1)
      ]),
      _createElementVNode("div", _hoisted_14, [
        _cache[6] || (_cache[6] = _createElementVNode("span", null, "下载位置", -1)),
        _createElementVNode("b", null, _toDisplayString(overview.value.download_dir || '-'), 1)
      ]),
      _createElementVNode("div", _hoisted_15, [
        _cache[7] || (_cache[7] = _createElementVNode("span", null, "搜索结果数", -1)),
        _createElementVNode("b", null, _toDisplayString(overview.value.max_results || '-'), 1)
      ]),
      _createElementVNode("div", _hoisted_16, [
        _cache[8] || (_cache[8] = _createElementVNode("span", null, "歌单并发", -1)),
        _createElementVNode("b", null, _toDisplayString(overview.value.playlist_concurrency || 3), 1)
      ]),
      _createElementVNode("div", _hoisted_17, [
        _cache[9] || (_cache[9] = _createElementVNode("span", null, "侧栏入口", -1)),
        _createElementVNode("b", null, _toDisplayString(overview.value.sidebar_enabled ? '已开启' : '已关闭'), 1)
      ])
    ]),
    _cache[10] || (_cache[10] = _createStaticVNode("<div class=\"pg-commands\" data-v-cb70a03f><div class=\"pg-commands-title\" data-v-cb70a03f>远程命令</div><div class=\"pg-cmd\" data-v-cb70a03f><code data-v-cb70a03f>/lx_search 歌曲名</code><span data-v-cb70a03f>搜索并列出候选</span></div><div class=\"pg-cmd\" data-v-cb70a03f><code data-v-cb70a03f>/lx_download 序号</code><span data-v-cb70a03f>按上次搜索序号下载</span></div><div class=\"pg-cmd\" data-v-cb70a03f><code data-v-cb70a03f>/lx_download 歌手 - 歌名</code><span data-v-cb70a03f>直接搜索首条并下载</span></div><div class=\"pg-cmd\" data-v-cb70a03f><code data-v-cb70a03f>/lx_playlist 歌单名</code><span data-v-cb70a03f>搜索歌单 / 粘贴链接查看曲目</span></div><div class=\"pg-cmd\" data-v-cb70a03f><code data-v-cb70a03f>/lx_playlist dl 歌单名</code><span data-v-cb70a03f>下载歌单前 50 首</span></div><div class=\"pg-cmd\" data-v-cb70a03f><code data-v-cb70a03f>/lx_stats</code><span data-v-cb70a03f>查看服务端缓存统计</span></div></div>", 1))
  ]))
}
}

};
const Page = /*#__PURE__*/_export_sfc(_sfc_main, [['__scopeId',"data-v-cb70a03f"]]);

export { Page as default };
