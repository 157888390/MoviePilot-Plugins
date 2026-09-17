import { importShared } from './__federation_fn_import-JrT3xvdd.js';
import ConfigPanel from './__federation_expose_Config-EiQJsWBF.js';
import { _ as _export_sfc, m as makeApiCall, f as formatSize, S as SOURCES, Q as QUALITIES, s as songKey, c as coverOf, a as singerOf, q as qualitiesOf, b as bodyOf, u as unwrap } from './_plugin-vue_export-helper-CcgWpvTR.js';

const {createElementVNode:_createElementVNode,toDisplayString:_toDisplayString,normalizeClass:_normalizeClass,openBlock:_openBlock,createElementBlock:_createElementBlock,createCommentVNode:_createCommentVNode,unref:_unref,vModelText:_vModelText,withKeys:_withKeys,withDirectives:_withDirectives,renderList:_renderList,Fragment:_Fragment,vModelSelect:_vModelSelect,createTextVNode:_createTextVNode,createVNode:_createVNode,withModifiers:_withModifiers,Teleport:_Teleport,createBlock:_createBlock} = await importShared('vue');


const _hoisted_1 = { class: "lx-page" };
const _hoisted_2 = { class: "lx-head" };
const _hoisted_3 = { class: "lx-head-chips" };
const _hoisted_4 = { class: "lx-chip is-muted" };
const _hoisted_5 = { class: "lx-head-actions" };
const _hoisted_6 = ["disabled"];
const _hoisted_7 = ["disabled"];
const _hoisted_8 = {
  key: 0,
  class: "lx-alert is-error"
};
const _hoisted_9 = {
  key: 1,
  class: "lx-alert is-ok"
};
const _hoisted_10 = { class: "lx-stats" };
const _hoisted_11 = { class: "lx-stat is-ok" };
const _hoisted_12 = { class: "lx-stat" };
const _hoisted_13 = { class: "lx-stat" };
const _hoisted_14 = { class: "lx-stat is-mv" };
const _hoisted_15 = { class: "lx-search" };
const _hoisted_16 = ["value"];
const _hoisted_17 = ["value"];
const _hoisted_18 = ["disabled"];
const _hoisted_19 = { class: "lx-tip" };
const _hoisted_20 = {
  key: 0,
  class: "lx-warn"
};
const _hoisted_21 = {
  key: 2,
  class: "lx-empty"
};
const _hoisted_22 = {
  key: 3,
  class: "lx-empty"
};
const _hoisted_23 = {
  key: 4,
  class: "lx-list"
};
const _hoisted_24 = ["src", "alt", "onError"];
const _hoisted_25 = {
  key: 1,
  class: "lx-cover-ph"
};
const _hoisted_26 = { class: "lx-info" };
const _hoisted_27 = ["title"];
const _hoisted_28 = { class: "lx-sub" };
const _hoisted_29 = { class: "lx-tags" };
const _hoisted_30 = {
  key: 0,
  class: "lx-tag is-plain"
};
const _hoisted_31 = { class: "lx-row-actions" };
const _hoisted_32 = ["onClick"];
const _hoisted_33 = ["disabled", "onClick"];
const _hoisted_34 = {
  key: 5,
  class: "lx-resolved"
};
const _hoisted_35 = { class: "lx-resolved-head" };
const _hoisted_36 = { class: "lx-tag is-ok" };
const _hoisted_37 = {
  key: 0,
  class: "lx-tag is-plain"
};
const _hoisted_38 = { class: "lx-resolved-url" };
const _hoisted_39 = { class: "lx-cfg-wrap" };

const {onBeforeUnmount,onMounted,reactive,ref} = await importShared('vue');


const _sfc_main = {
  __name: 'AppPage',
  props: {
  api: { type: Object, default: () => ({}) },
  pluginId: { type: String, default: 'LxMusicDownloader' },
  navKey: { type: String, default: 'main' },
  nativeSubscribe: { type: Function, default: null },
  sourcePluginId: { type: String, default: '' },
},
  setup(__props) {

const props = __props;

const apiCall = makeApiCall(props.api, props.pluginId);

const loading = ref(false);
const searching = ref(false);
const error = ref('');
const notice = ref('');
let noticeTimer = null;

const keyword = ref('');
const source = ref('kw');
const quality = ref('320k');
const limit = ref(10);

const songs = ref([]);
const overview = ref({});
const stats = ref(null);

const downloading = reactive({});
const coverFailed = reactive({});
const lastResolved = ref(null);

function flash(text, isError = false) {
  clearTimeout(noticeTimer);
  if (isError) { error.value = text; notice.value = ''; }
  else { notice.value = text; error.value = ''; }
  noticeTimer = setTimeout(() => { notice.value = ''; error.value = ''; }, 8000);
}

function markCoverFailed(song) {
  coverFailed[songKey(song)] = true;
}

async function loadOverview() {
  try {
    overview.value = unwrap(await apiCall('get', '/overview'));
    if (overview.value?.source) source.value = overview.value.source;
    if (overview.value?.quality) quality.value = overview.value.quality;
  } catch (loadError) {
    error.value = loadError?.message || '读取运行状态失败';
  }
}

async function loadStats() {
  try {
    stats.value = unwrap(await apiCall('get', '/stats'));
  } catch (loadError) {
    stats.value = null;
  }
}

async function loadEverything() {
  loading.value = true;
  error.value = '';
  try {
    await Promise.all([loadOverview(), loadStats()]);
  } finally {
    loading.value = false;
  }
}

async function doSearch() {
  const kw = keyword.value.trim();
  if (!kw) { flash('请输入歌曲名或歌手', true); return }
  searching.value = true;
  error.value = '';
  notice.value = '';
  songs.value = [];
  lastResolved.value = null;
  try {
    const path = `/search?keyword=${encodeURIComponent(kw)}&limit=${limit.value}&source=${source.value}`;
    songs.value = unwrap(await apiCall('get', path)) || [];
    if (!songs.value.length) flash(`「${source.value}」没有搜索到与「${kw}」相关的歌曲`, true);
  } catch (searchError) {
    error.value = searchError?.message || '搜索失败';
  } finally {
    searching.value = false;
  }
}

async function doDownload(song) {
  const key = songKey(song);
  downloading[key] = true;
  error.value = '';
  notice.value = '';
  try {
    const response = await apiCall('post', '/download', { song, quality: quality.value });
    const body = response?.data ?? response;
    if (body?.success === false) throw new Error(body.message || '下载失败')
    flash(body?.message || '下载完成');
    loadStats();
  } catch (downloadError) {
    error.value = downloadError?.message || '下载失败';
  } finally {
    downloading[key] = false;
  }
}

async function resolveSong(song) {
  error.value = '';
  notice.value = '';
  try {
    const data = unwrap(await apiCall('post', '/resolve', { song, quality: quality.value }));
    lastResolved.value = { ...data, name: song?.name, singer: singerOf(song), key: songKey(song) };
    flash(`解析成功：实际音质 ${data.type}`);
  } catch (resolveError) {
    error.value = resolveError?.message || '解析直链失败';
  }
}

function copyUrl(url) {
  if (!url) return
  navigator.clipboard?.writeText(url).then(
    () => flash('直链已复制到剪贴板'),
    () => flash('复制失败，请手动选择', true),
  );
}

// 设置面板
const showSettings = ref(false);
const loadingSettings = ref(false);
const settingsModel = ref({});

/*
 * MP 会给插件页面套一层「包含块祖先」（容器查询 / transform / contain 之类），
 * 于是 position:fixed 不再是相对窗口定位，而是被关进「内容区」这一个盒子里：
 *   - 顶栏与侧栏在盒子之外 → 无论 z-index 多大都盖不住它们
 *   - 弹窗按内容区居中，而不是按窗口居中
 * 把浮层 Teleport 出去才能跳出这层上下文。优先挂到 .v-application（保留 Vuetify 的
 * CSS 变量），兜底 body。
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
    flash('配置已保存');
    await loadEverything();
  } catch (saveError) {
    error.value = `保存配置失败：${saveError?.message || saveError}`;
  }
}

const cacheInfo = () => stats.value || {};

onMounted(() => { loadEverything(); });
onBeforeUnmount(() => { clearTimeout(noticeTimer); });

return (_ctx, _cache) => {
  return (_openBlock(), _createElementBlock("div", _hoisted_1, [
    _createElementVNode("div", _hoisted_2, [
      _cache[8] || (_cache[8] = _createElementVNode("div", { class: "lx-logo" }, "L", -1)),
      _cache[9] || (_cache[9] = _createElementVNode("div", { class: "lx-heading" }, [
        _createElementVNode("h1", null, "LX 音源下载"),
        _createElementVNode("p", null, "调用自建 LX Sync Server 搜索歌曲，解析直链并下载到指定目录")
      ], -1)),
      _createElementVNode("div", _hoisted_3, [
        _createElementVNode("span", {
          class: _normalizeClass(['lx-chip', overview.value.auth_state === 'ok' ? 'is-ok' : 'is-warn'])
        }, _toDisplayString(overview.value.auth_state === 'ok' ? '鉴权正常' : (overview.value.auth_state === 'anonymous' ? '未配置凭据' : '鉴权异常')), 3),
        _createElementVNode("span", _hoisted_4, _toDisplayString(overview.value.source_name || '-') + " · " + _toDisplayString(overview.value.quality || '-'), 1)
      ]),
      _createElementVNode("div", _hoisted_5, [
        _createElementVNode("button", {
          class: "lx-btn ghost",
          disabled: loadingSettings.value,
          onClick: openSettings
        }, _toDisplayString(loadingSettings.value ? '读取中…' : '设置'), 9, _hoisted_6),
        _createElementVNode("button", {
          class: "lx-btn ghost",
          disabled: loading.value,
          onClick: loadEverything
        }, "刷新", 8, _hoisted_7)
      ])
    ]),
    (error.value)
      ? (_openBlock(), _createElementBlock("div", _hoisted_8, _toDisplayString(error.value), 1))
      : (notice.value)
        ? (_openBlock(), _createElementBlock("div", _hoisted_9, _toDisplayString(notice.value), 1))
        : _createCommentVNode("", true),
    _createElementVNode("div", _hoisted_10, [
      _createElementVNode("div", _hoisted_11, [
        _createElementVNode("b", null, _toDisplayString(_unref(formatSize)(cacheInfo().totalSize)), 1),
        _cache[10] || (_cache[10] = _createElementVNode("span", null, "服务端缓存占用", -1))
      ]),
      _createElementVNode("div", _hoisted_12, [
        _createElementVNode("b", null, _toDisplayString(cacheInfo().fileCount || 0), 1),
        _cache[11] || (_cache[11] = _createElementVNode("span", null, "缓存文件数", -1))
      ]),
      _createElementVNode("div", _hoisted_13, [
        _createElementVNode("b", null, _toDisplayString(songs.value.length), 1),
        _cache[12] || (_cache[12] = _createElementVNode("span", null, "本次搜索候选", -1))
      ]),
      _createElementVNode("div", _hoisted_14, [
        _createElementVNode("b", null, _toDisplayString(overview.value.max_results || 10), 1),
        _cache[13] || (_cache[13] = _createElementVNode("span", null, "搜索上限", -1))
      ])
    ]),
    _createElementVNode("div", _hoisted_15, [
      _withDirectives(_createElementVNode("input", {
        "onUpdate:modelValue": _cache[0] || (_cache[0] = $event => ((keyword).value = $event)),
        class: "lx-input",
        type: "text",
        placeholder: "输入歌曲名或「歌手 - 歌名」，回车搜索",
        onKeyup: _withKeys(doSearch, ["enter"])
      }, null, 544), [
        [_vModelText, keyword.value]
      ]),
      _withDirectives(_createElementVNode("select", {
        "onUpdate:modelValue": _cache[1] || (_cache[1] = $event => ((source).value = $event)),
        class: "lx-select"
      }, [
        (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(_unref(SOURCES), (item) => {
          return (_openBlock(), _createElementBlock("option", {
            key: item.value,
            value: item.value
          }, _toDisplayString(item.label), 9, _hoisted_16))
        }), 128))
      ], 512), [
        [_vModelSelect, source.value]
      ]),
      _withDirectives(_createElementVNode("select", {
        "onUpdate:modelValue": _cache[2] || (_cache[2] = $event => ((quality).value = $event)),
        class: "lx-select"
      }, [
        (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(_unref(QUALITIES), (item) => {
          return (_openBlock(), _createElementBlock("option", {
            key: item.value,
            value: item.value
          }, _toDisplayString(item.label), 9, _hoisted_17))
        }), 128))
      ], 512), [
        [_vModelSelect, quality.value]
      ]),
      _withDirectives(_createElementVNode("input", {
        "onUpdate:modelValue": _cache[3] || (_cache[3] = $event => ((limit).value = $event)),
        class: "lx-input is-num",
        type: "number",
        min: "1",
        max: "50"
      }, null, 512), [
        [
          _vModelText,
          limit.value,
          void 0,
          { number: true }
        ]
      ]),
      _createElementVNode("button", {
        class: "lx-btn",
        disabled: searching.value,
        onClick: doSearch
      }, _toDisplayString(searching.value ? '搜索中…' : '搜索'), 9, _hoisted_18)
    ]),
    _createElementVNode("p", _hoisted_19, [
      _cache[14] || (_cache[14] = _createTextVNode(" 下载目录：", -1)),
      _createElementVNode("code", null, _toDisplayString(overview.value.download_dir || '-'), 1),
      (overview.value.source === 'tx')
        ? (_openBlock(), _createElementBlock("span", _hoisted_20, "（酷狗/酷我等平台可用；QQ 音乐搜索在服务端长期故障）"))
        : _createCommentVNode("", true)
    ]),
    (searching.value)
      ? (_openBlock(), _createElementBlock("div", _hoisted_21, "正在搜索…"))
      : (!songs.value.length)
        ? (_openBlock(), _createElementBlock("div", _hoisted_22, "输入关键词开始搜索"))
        : (_openBlock(), _createElementBlock("div", _hoisted_23, [
            (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(songs.value, (song) => {
              return (_openBlock(), _createElementBlock("div", {
                key: _unref(songKey)(song),
                class: "lx-row"
              }, [
                _createElementVNode("div", {
                  class: _normalizeClass(["lx-cover", coverFailed[_unref(songKey)(song)] ? 'is-fallback' : ''])
                }, [
                  (_unref(coverOf)(song) && !coverFailed[_unref(songKey)(song)])
                    ? (_openBlock(), _createElementBlock("img", {
                        key: 0,
                        src: _unref(coverOf)(song),
                        alt: song.name,
                        loading: "lazy",
                        referrerpolicy: "no-referrer",
                        onError: $event => (markCoverFailed(song))
                      }, null, 40, _hoisted_24))
                    : (_openBlock(), _createElementBlock("span", _hoisted_25, "♪"))
                ], 2),
                _createElementVNode("div", _hoisted_26, [
                  _createElementVNode("div", {
                    class: "lx-title",
                    title: song.name
                  }, _toDisplayString(song.name), 9, _hoisted_27),
                  _createElementVNode("div", _hoisted_28, _toDisplayString(_unref(singerOf)(song)) + " · " + _toDisplayString(song.albumName || '未知专辑'), 1),
                  _createElementVNode("div", _hoisted_29, [
                    (song.interval)
                      ? (_openBlock(), _createElementBlock("span", _hoisted_30, _toDisplayString(song.interval), 1))
                      : _createCommentVNode("", true),
                    (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(_unref(qualitiesOf)(song), (q) => {
                      return (_openBlock(), _createElementBlock("span", {
                        key: q,
                        class: "lx-tag"
                      }, _toDisplayString(q), 1))
                    }), 128))
                  ])
                ]),
                _createElementVNode("div", _hoisted_31, [
                  _createElementVNode("button", {
                    class: "lx-btn small ghost",
                    onClick: $event => (resolveSong(song))
                  }, "解析", 8, _hoisted_32),
                  _createElementVNode("button", {
                    class: "lx-btn small",
                    disabled: downloading[_unref(songKey)(song)],
                    onClick: $event => (doDownload(song))
                  }, _toDisplayString(downloading[_unref(songKey)(song)] ? '下载中…' : '下载'), 9, _hoisted_33)
                ])
              ]))
            }), 128))
          ])),
    (lastResolved.value)
      ? (_openBlock(), _createElementBlock("div", _hoisted_34, [
          _createElementVNode("div", _hoisted_35, [
            _createElementVNode("b", null, _toDisplayString(lastResolved.value.name), 1),
            _createElementVNode("span", _hoisted_36, _toDisplayString(lastResolved.value.type), 1),
            (lastResolved.value.source_name)
              ? (_openBlock(), _createElementBlock("span", _hoisted_37, _toDisplayString(lastResolved.value.source_name), 1))
              : _createCommentVNode("", true),
            _createElementVNode("button", {
              class: "lx-mini",
              onClick: _cache[4] || (_cache[4] = $event => (lastResolved.value = null))
            }, "关闭")
          ]),
          _createElementVNode("div", _hoisted_38, [
            _createElementVNode("code", null, _toDisplayString(lastResolved.value.url), 1),
            _createElementVNode("button", {
              class: "lx-mini",
              onClick: _cache[5] || (_cache[5] = $event => (copyUrl(lastResolved.value.url)))
            }, "复制")
          ])
        ]))
      : _createCommentVNode("", true),
    (_openBlock(), _createBlock(_Teleport, { to: _unref(overlayTarget) }, [
      (showSettings.value)
        ? (_openBlock(), _createElementBlock("div", {
            key: 0,
            class: "lx-cfg-mask",
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
        : _createCommentVNode("", true)
    ], 8, ["to"]))
  ]))
}
}

};
const AppPage = /*#__PURE__*/_export_sfc(_sfc_main, [['__scopeId',"data-v-f5fcda24"]]);

export { AppPage as default };
