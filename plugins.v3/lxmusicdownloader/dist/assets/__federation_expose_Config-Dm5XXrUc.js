import { importShared } from './__federation_fn_import-JrT3xvdd.js';
import { _ as _export_sfc, m as makeApiCall, S as SOURCES, Q as QUALITIES, u as unwrap } from './_plugin-vue_export-helper-DXLvoj9D.js';

const {createElementVNode:_createElementVNode,toDisplayString:_toDisplayString,openBlock:_openBlock,createElementBlock:_createElementBlock,createCommentVNode:_createCommentVNode,vModelCheckbox:_vModelCheckbox,withDirectives:_withDirectives,vModelText:_vModelText,unref:_unref,renderList:_renderList,Fragment:_Fragment,vModelSelect:_vModelSelect} = await importShared('vue');


const _hoisted_1 = { class: "cfg" };
const _hoisted_2 = { class: "cfg-body" };
const _hoisted_3 = {
  key: 0,
  class: "cfg-alert is-error"
};
const _hoisted_4 = {
  key: 1,
  class: "cfg-alert is-ok"
};
const _hoisted_5 = { class: "cfg-grid" };
const _hoisted_6 = { class: "cfg-switch" };
const _hoisted_7 = { class: "cfg-switch" };
const _hoisted_8 = { class: "cfg-field" };
const _hoisted_9 = { class: "cfg-grid" };
const _hoisted_10 = { class: "cfg-field" };
const _hoisted_11 = { class: "cfg-field" };
const _hoisted_12 = { class: "cfg-field" };
const _hoisted_13 = { class: "cfg-grid" };
const _hoisted_14 = { class: "cfg-field" };
const _hoisted_15 = ["value"];
const _hoisted_16 = { class: "cfg-field" };
const _hoisted_17 = ["value"];
const _hoisted_18 = { class: "cfg-field" };
const _hoisted_19 = { class: "cfg-field" };
const _hoisted_20 = { class: "cfg-field" };
const _hoisted_21 = { class: "cfg-field" };
const _hoisted_22 = { class: "cfg-grid" };
const _hoisted_23 = { class: "cfg-switch" };
const _hoisted_24 = { class: "cfg-switch" };
const _hoisted_25 = { class: "cfg-switch" };
const _hoisted_26 = { class: "cfg-switch" };
const _hoisted_27 = { class: "cfg-switch" };
const _hoisted_28 = { class: "cfg-foot" };
const _hoisted_29 = ["disabled"];

const {reactive,ref,watch} = await importShared('vue');


const _sfc_main = {
  __name: 'Config',
  props: {
  initialConfig: { type: Object, default: () => ({}) },
  api: { type: Object, default: () => ({}) },
  pluginId: { type: String, default: 'LxMusicDownloader' },
  sourcePluginId: { type: String, default: '' },
},
  emits: ['save', 'close', 'switch'],
  setup(__props, { emit: __emit }) {

const props = __props;

const emit = __emit;

const DEFAULTS = {
  enabled: false,
  host: 'http://127.0.0.1:23332',
  username: '',
  password: '',
  token: '',
  source: 'kw',
  quality: '320k',
  download_path: '',
  name_template: '{name} - {singer}',
  max_results: 10,
  playlist_concurrency: 3,
  subdir_by_artist: true,
  save_cover: false,
  embed_tag: true,
  embed_lyric: true,
  use_server_cache: false,
  sidebar_enabled: true,
};

const config = reactive({ ...DEFAULTS });
const testing = ref(false);
const error = ref('');
const notice = ref('');

const apiCall = makeApiCall(props.api, props.pluginId);

/*
 * 用 watch(immediate) 而不是 onMounted 读取初值：宿主可能在本组件挂载之后才
 * 拿到配置（例如页面内设置弹窗是「先弹出、后请求」），只读一次会永远读到空对象，
 * 表现为「第一次打开显示默认值、第二次打开才对」。
 */
function applyInitial(source) {
  if (!source || typeof source !== 'object') return
  if (!Object.keys(source).length) return
  Object.keys(DEFAULTS).forEach(key => {
    if (key in source && source[key] !== undefined && source[key] !== null) config[key] = source[key];
  });
}

watch(() => props.initialConfig, applyInitial, { immediate: true, deep: true });

function submit() {
  emit('save', { ...config });
}

function close() {
  emit('close');
}

async function testConnection() {
  testing.value = true;
  error.value = '';
  notice.value = '';
  try {
    const data = unwrap(await apiCall('get', '/verify'));
    if (data?.valid) notice.value = `连接正常，鉴权用户：${data.username}`;
    else if (config.token) error.value = 'Token 无效或未生效（请确认用户名与 token 匹配）';
    else error.value = '未配置凭据，将以公开用户访问，无法解析直链';
  } catch (testError) {
    error.value = `连接失败：${testError?.message || testError}`;
  } finally {
    testing.value = false;
  }
}

return (_ctx, _cache) => {
  return (_openBlock(), _createElementBlock("div", _hoisted_1, [
    _createElementVNode("div", { class: "cfg-head" }, [
      _cache[17] || (_cache[17] = _createElementVNode("h2", null, "LX 音源下载配置", -1)),
      _createElementVNode("button", {
        class: "cfg-close",
        onClick: close
      }, "×")
    ]),
    _createElementVNode("div", _hoisted_2, [
      (error.value)
        ? (_openBlock(), _createElementBlock("div", _hoisted_3, _toDisplayString(error.value), 1))
        : (notice.value)
          ? (_openBlock(), _createElementBlock("div", _hoisted_4, _toDisplayString(notice.value), 1))
          : _createCommentVNode("", true),
      _cache[35] || (_cache[35] = _createElementVNode("div", { class: "cfg-section" }, "运行状态", -1)),
      _createElementVNode("div", _hoisted_5, [
        _createElementVNode("label", _hoisted_6, [
          _withDirectives(_createElementVNode("input", {
            "onUpdate:modelValue": _cache[0] || (_cache[0] = $event => ((config.enabled) = $event)),
            type: "checkbox"
          }, null, 512), [
            [_vModelCheckbox, config.enabled]
          ]),
          _cache[18] || (_cache[18] = _createElementVNode("span", null, [
            _createElementVNode("b", null, "启用插件"),
            _createElementVNode("em", null, "关闭后远程命令与接口不再响应")
          ], -1))
        ]),
        _createElementVNode("label", _hoisted_7, [
          _withDirectives(_createElementVNode("input", {
            "onUpdate:modelValue": _cache[1] || (_cache[1] = $event => ((config.sidebar_enabled) = $event)),
            type: "checkbox"
          }, null, 512), [
            [_vModelCheckbox, config.sidebar_enabled]
          ]),
          _cache[19] || (_cache[19] = _createElementVNode("span", null, [
            _createElementVNode("b", null, "显示侧栏入口"),
            _createElementVNode("em", null, "在主界面左侧导航栏显示「LX 音源下载」，关闭后仍可从插件中心进入")
          ], -1))
        ])
      ]),
      _cache[36] || (_cache[36] = _createElementVNode("div", { class: "cfg-section" }, "服务端", -1)),
      _createElementVNode("div", _hoisted_8, [
        _cache[20] || (_cache[20] = _createElementVNode("span", { class: "cfg-label" }, "LX 服务端地址", -1)),
        _withDirectives(_createElementVNode("input", {
          "onUpdate:modelValue": _cache[2] || (_cache[2] = $event => ((config.host) = $event)),
          class: "cfg-input",
          placeholder: "https://music.example.com 或 http://127.0.0.1:23332"
        }, null, 512), [
          [_vModelText, config.host]
        ])
      ]),
      _createElementVNode("div", _hoisted_9, [
        _createElementVNode("div", _hoisted_10, [
          _cache[21] || (_cache[21] = _createElementVNode("span", { class: "cfg-label" }, "用户名", -1)),
          _withDirectives(_createElementVNode("input", {
            "onUpdate:modelValue": _cache[3] || (_cache[3] = $event => ((config.username) = $event)),
            class: "cfg-input",
            placeholder: "服务端登录用户名"
          }, null, 512), [
            [_vModelText, config.username]
          ])
        ]),
        _createElementVNode("div", _hoisted_11, [
          _cache[22] || (_cache[22] = _createElementVNode("span", { class: "cfg-label" }, "密码", -1)),
          _withDirectives(_createElementVNode("input", {
            "onUpdate:modelValue": _cache[4] || (_cache[4] = $event => ((config.password) = $event)),
            class: "cfg-input",
            type: "password",
            placeholder: "与用户名配对，留空则用 token"
          }, null, 512), [
            [_vModelText, config.password]
          ])
        ])
      ]),
      _createElementVNode("div", _hoisted_12, [
        _cache[23] || (_cache[23] = _createElementVNode("span", { class: "cfg-label" }, "持久化 Token", -1)),
        _withDirectives(_createElementVNode("input", {
          "onUpdate:modelValue": _cache[5] || (_cache[5] = $event => ((config.token) = $event)),
          class: "cfg-input",
          placeholder: "填了可免密码；用户名仍需填写（服务端靠用户名定位音源）"
        }, null, 512), [
          [_vModelText, config.token]
        ])
      ]),
      _cache[37] || (_cache[37] = _createElementVNode("p", { class: "cfg-tip" }, " 下载直链解析依赖「用户名 + token」定位自定义音源：只填 token 不填用户名会退回公开用户并报「未找到支持 xx 平台的自定义源」。 只填账号密码时会自动登录换取 token。 ", -1)),
      _cache[38] || (_cache[38] = _createElementVNode("div", { class: "cfg-section" }, "下载", -1)),
      _createElementVNode("div", _hoisted_13, [
        _createElementVNode("div", _hoisted_14, [
          _cache[24] || (_cache[24] = _createElementVNode("span", { class: "cfg-label" }, "音源平台", -1)),
          _withDirectives(_createElementVNode("select", {
            "onUpdate:modelValue": _cache[6] || (_cache[6] = $event => ((config.source) = $event)),
            class: "cfg-select"
          }, [
            (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(_unref(SOURCES), (item) => {
              return (_openBlock(), _createElementBlock("option", {
                key: item.value,
                value: item.value
              }, _toDisplayString(item.label), 9, _hoisted_15))
            }), 128))
          ], 512), [
            [_vModelSelect, config.source]
          ])
        ]),
        _createElementVNode("div", _hoisted_16, [
          _cache[25] || (_cache[25] = _createElementVNode("span", { class: "cfg-label" }, "下载音质", -1)),
          _withDirectives(_createElementVNode("select", {
            "onUpdate:modelValue": _cache[7] || (_cache[7] = $event => ((config.quality) = $event)),
            class: "cfg-select"
          }, [
            (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(_unref(QUALITIES), (item) => {
              return (_openBlock(), _createElementBlock("option", {
                key: item.value,
                value: item.value
              }, _toDisplayString(item.label), 9, _hoisted_17))
            }), 128))
          ], 512), [
            [_vModelSelect, config.quality]
          ])
        ]),
        _createElementVNode("div", _hoisted_18, [
          _cache[26] || (_cache[26] = _createElementVNode("span", { class: "cfg-label" }, "搜索结果数", -1)),
          _withDirectives(_createElementVNode("input", {
            "onUpdate:modelValue": _cache[8] || (_cache[8] = $event => ((config.max_results) = $event)),
            class: "cfg-input",
            type: "number",
            min: "1",
            max: "50"
          }, null, 512), [
            [
              _vModelText,
              config.max_results,
              void 0,
              { number: true }
            ]
          ])
        ]),
        _createElementVNode("div", _hoisted_19, [
          _cache[27] || (_cache[27] = _createElementVNode("span", { class: "cfg-label" }, "歌单下载并发", -1)),
          _withDirectives(_createElementVNode("input", {
            "onUpdate:modelValue": _cache[9] || (_cache[9] = $event => ((config.playlist_concurrency) = $event)),
            class: "cfg-input",
            type: "number",
            min: "1",
            max: "8"
          }, null, 512), [
            [
              _vModelText,
              config.playlist_concurrency,
              void 0,
              { number: true }
            ]
          ])
        ])
      ]),
      _cache[39] || (_cache[39] = _createElementVNode("p", { class: "cfg-tip" }, "音质不可用时按 flac24bit > hires > flac > 320k > 192k > 128k 自动降级；真实容器以响应文件头为准。", -1)),
      _cache[40] || (_cache[40] = _createElementVNode("p", { class: "cfg-tip" }, "歌单下载并发建议保持 3（与服务端队列默认一致）；过高容易触发上游音源限流。", -1)),
      _createElementVNode("div", _hoisted_20, [
        _cache[28] || (_cache[28] = _createElementVNode("span", { class: "cfg-label" }, "下载位置", -1)),
        _withDirectives(_createElementVNode("input", {
          "onUpdate:modelValue": _cache[10] || (_cache[10] = $event => ((config.download_path) = $event)),
          class: "cfg-input",
          placeholder: "/media/music，留空使用插件数据目录下的 music"
        }, null, 512), [
          [_vModelText, config.download_path]
        ])
      ]),
      _createElementVNode("div", _hoisted_21, [
        _cache[29] || (_cache[29] = _createElementVNode("span", { class: "cfg-label" }, "文件名模板", -1)),
        _withDirectives(_createElementVNode("input", {
          "onUpdate:modelValue": _cache[11] || (_cache[11] = $event => ((config.name_template) = $event)),
          class: "cfg-input",
          placeholder: "支持 {name} {singer} {album} {source} {songmid}"
        }, null, 512), [
          [_vModelText, config.name_template]
        ])
      ]),
      _createElementVNode("div", _hoisted_22, [
        _createElementVNode("label", _hoisted_23, [
          _withDirectives(_createElementVNode("input", {
            "onUpdate:modelValue": _cache[12] || (_cache[12] = $event => ((config.subdir_by_artist) = $event)),
            type: "checkbox"
          }, null, 512), [
            [_vModelCheckbox, config.subdir_by_artist]
          ]),
          _cache[30] || (_cache[30] = _createElementVNode("span", null, [
            _createElementVNode("b", null, "按歌手分目录"),
            _createElementVNode("em", null, "在下载目录下按歌手建子目录")
          ], -1))
        ]),
        _createElementVNode("label", _hoisted_24, [
          _withDirectives(_createElementVNode("input", {
            "onUpdate:modelValue": _cache[13] || (_cache[13] = $event => ((config.save_cover) = $event)),
            type: "checkbox"
          }, null, 512), [
            [_vModelCheckbox, config.save_cover]
          ]),
          _cache[31] || (_cache[31] = _createElementVNode("span", null, [
            _createElementVNode("b", null, "保存封面"),
            _createElementVNode("em", null, "额外保存同名封面图")
          ], -1))
        ]),
        _createElementVNode("label", _hoisted_25, [
          _withDirectives(_createElementVNode("input", {
            "onUpdate:modelValue": _cache[14] || (_cache[14] = $event => ((config.embed_tag) = $event)),
            type: "checkbox"
          }, null, 512), [
            [_vModelCheckbox, config.embed_tag]
          ]),
          _cache[32] || (_cache[32] = _createElementVNode("span", null, [
            _createElementVNode("b", null, "注入 ID3 标签"),
            _createElementVNode("em", null, "由服务端写入标题/艺术家/专辑/封面，不改变音频本体")
          ], -1))
        ]),
        _createElementVNode("label", _hoisted_26, [
          _withDirectives(_createElementVNode("input", {
            "onUpdate:modelValue": _cache[15] || (_cache[15] = $event => ((config.embed_lyric) = $event)),
            type: "checkbox"
          }, null, 512), [
            [_vModelCheckbox, config.embed_lyric]
          ]),
          _cache[33] || (_cache[33] = _createElementVNode("span", null, [
            _createElementVNode("b", null, "嵌入歌词"),
            _createElementVNode("em", null, "写入 USLT 帧，需同时开启 ID3 标签；失败自动降级")
          ], -1))
        ]),
        _createElementVNode("label", _hoisted_27, [
          _withDirectives(_createElementVNode("input", {
            "onUpdate:modelValue": _cache[16] || (_cache[16] = $event => ((config.use_server_cache) = $event)),
            type: "checkbox"
          }, null, 512), [
            [_vModelCheckbox, config.use_server_cache]
          ]),
          _cache[34] || (_cache[34] = _createElementVNode("span", null, [
            _createElementVNode("b", null, "下到服务端缓存"),
            _createElementVNode("em", null, "不落本地，改为提交服务端缓存任务")
          ], -1))
        ])
      ])
    ]),
    _createElementVNode("div", _hoisted_28, [
      _createElementVNode("button", {
        class: "cfg-btn ghost",
        disabled: testing.value,
        onClick: testConnection
      }, _toDisplayString(testing.value ? '测试中…' : '测试连接'), 9, _hoisted_29),
      _createElementVNode("div", { class: "cfg-foot-right" }, [
        _createElementVNode("button", {
          class: "cfg-btn ghost",
          onClick: close
        }, "取消"),
        _createElementVNode("button", {
          class: "cfg-btn",
          onClick: submit
        }, "保存")
      ])
    ])
  ]))
}
}

};
const ConfigPanel = /*#__PURE__*/_export_sfc(_sfc_main, [['__scopeId',"data-v-b8c9000e"]]);

export { ConfigPanel as default };
