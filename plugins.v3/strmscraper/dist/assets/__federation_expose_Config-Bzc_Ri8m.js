import { importShared } from './__federation_fn_import-JrT3xvdd.js';
import { _ as _export_sfc } from './_plugin-vue_export-helper-pcqpp-6-.js';

const {createElementVNode:_createElementVNode,toDisplayString:_toDisplayString,openBlock:_openBlock,createElementBlock:_createElementBlock,createCommentVNode:_createCommentVNode,vModelCheckbox:_vModelCheckbox,withDirectives:_withDirectives,renderList:_renderList,Fragment:_Fragment,vModelSelect:_vModelSelect,vModelText:_vModelText,createTextVNode:_createTextVNode} = await importShared('vue');


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
const _hoisted_8 = { class: "cfg-switch" };
const _hoisted_9 = { class: "cfg-switch" };
const _hoisted_10 = { class: "cfg-switch" };
const _hoisted_11 = { class: "cfg-field" };
const _hoisted_12 = ["value"];
const _hoisted_13 = { class: "cfg-field" };
const _hoisted_14 = { class: "cfg-field" };
const _hoisted_15 = { class: "cfg-foot" };
const _hoisted_16 = ["disabled"];
const _hoisted_17 = ["disabled"];

const {reactive,ref,watch} = await importShared('vue');



const _sfc_main = {
  __name: 'Config',
  props: {
  initialConfig: { type: Object, default: () => ({}) },
  api: { type: Object, default: () => ({}) },
  pluginId: { type: String, default: 'StrmScraper' },
  sourcePluginId: { type: String, default: '' },
},
  emits: ['save', 'close', 'switch'],
  setup(__props, { emit: __emit }) {

const props = __props;

const emit = __emit;

const DEFAULTS = {
  enabled: false,
  onlyonce: false,
  overwrite: false,
  mode: 'compatibility',
  monitor_dirs: '',
  exclude_keywords: '',
  sidebar_enabled: true,
  record_enabled: true,
};

const config = reactive({ ...DEFAULTS });
const scanning = ref(false);
const error = ref('');
const notice = ref('');

const MODES = [
  { title: '兼容模式（轮询，网络盘用）', value: 'compatibility' },
  { title: '性能模式（inotify，仅本地盘）', value: 'fast' },
];

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

const apiCall = (method, path, payload) => {
  if (typeof props.api?.[method] === 'function') return props.api[method](`plugin/${props.pluginId}${path}`, payload)
  return Promise.reject(new Error('MoviePilot API 客户端未注入'))
};

function submit() {
  emit('save', { ...config });
}

function close() {
  emit('close');
}

async function scanNow(force) {
  scanning.value = true;
  error.value = '';
  notice.value = '';
  try {
    await apiCall('get', force ? '/scan?force=true' : '/scan');
    notice.value = force ? '已在后台启动强制全量扫描' : '已在后台启动全量扫描（跳过已刮削）';
  } catch (scanError) {
    error.value = scanError?.message || '触发全量扫描失败';
  } finally {
    scanning.value = false;
  }
}

return (_ctx, _cache) => {
  return (_openBlock(), _createElementBlock("div", _hoisted_1, [
    _createElementVNode("div", { class: "cfg-head" }, [
      _cache[10] || (_cache[10] = _createElementVNode("h2", null, "STRM 刮削配置", -1)),
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
      _cache[19] || (_cache[19] = _createElementVNode("div", { class: "cfg-section" }, "运行状态", -1)),
      _createElementVNode("div", _hoisted_5, [
        _createElementVNode("label", _hoisted_6, [
          _withDirectives(_createElementVNode("input", {
            "onUpdate:modelValue": _cache[0] || (_cache[0] = $event => ((config.enabled) = $event)),
            type: "checkbox"
          }, null, 512), [
            [_vModelCheckbox, config.enabled]
          ]),
          _cache[11] || (_cache[11] = _createElementVNode("span", null, [
            _createElementVNode("b", null, "启用插件"),
            _createElementVNode("em", null, "开启目录实时监控")
          ], -1))
        ]),
        _createElementVNode("label", _hoisted_7, [
          _withDirectives(_createElementVNode("input", {
            "onUpdate:modelValue": _cache[1] || (_cache[1] = $event => ((config.onlyonce) = $event)),
            type: "checkbox"
          }, null, 512), [
            [_vModelCheckbox, config.onlyonce]
          ]),
          _cache[12] || (_cache[12] = _createElementVNode("span", null, [
            _createElementVNode("b", null, "立即全量扫描一次"),
            _createElementVNode("em", null, "保存后 3 秒执行一次")
          ], -1))
        ]),
        _createElementVNode("label", _hoisted_8, [
          _withDirectives(_createElementVNode("input", {
            "onUpdate:modelValue": _cache[2] || (_cache[2] = $event => ((config.overwrite) = $event)),
            type: "checkbox"
          }, null, 512), [
            [_vModelCheckbox, config.overwrite]
          ]),
          _cache[13] || (_cache[13] = _createElementVNode("span", null, [
            _createElementVNode("b", null, "覆盖已有元数据"),
            _createElementVNode("em", null, "重刮时覆盖已存在的 NFO 与图片")
          ], -1))
        ]),
        _createElementVNode("label", _hoisted_9, [
          _withDirectives(_createElementVNode("input", {
            "onUpdate:modelValue": _cache[3] || (_cache[3] = $event => ((config.sidebar_enabled) = $event)),
            type: "checkbox"
          }, null, 512), [
            [_vModelCheckbox, config.sidebar_enabled]
          ]),
          _cache[14] || (_cache[14] = _createElementVNode("span", null, [
            _createElementVNode("b", null, "显示侧栏入口"),
            _createElementVNode("em", null, "在主界面左侧导航栏显示「STRM刮削」，关闭后仍可从插件中心进入")
          ], -1))
        ]),
        _createElementVNode("label", _hoisted_10, [
          _withDirectives(_createElementVNode("input", {
            "onUpdate:modelValue": _cache[4] || (_cache[4] = $event => ((config.record_enabled) = $event)),
            type: "checkbox"
          }, null, 512), [
            [_vModelCheckbox, config.record_enabled]
          ]),
          _cache[15] || (_cache[15] = _createElementVNode("span", null, [
            _createElementVNode("b", null, "记录刮削历史"),
            _createElementVNode("em", null, "把每次刮削写入插件数据目录，可在侧栏页查看")
          ], -1))
        ])
      ]),
      _cache[20] || (_cache[20] = _createElementVNode("div", { class: "cfg-section" }, "监控", -1)),
      _createElementVNode("div", _hoisted_11, [
        _cache[16] || (_cache[16] = _createElementVNode("span", { class: "cfg-label" }, "监控模式", -1)),
        _withDirectives(_createElementVNode("select", {
          "onUpdate:modelValue": _cache[5] || (_cache[5] = $event => ((config.mode) = $event)),
          class: "cfg-select"
        }, [
          (_openBlock(), _createElementBlock(_Fragment, null, _renderList(MODES, (item) => {
            return _createElementVNode("option", {
              key: item.value,
              value: item.value
            }, _toDisplayString(item.title), 9, _hoisted_12)
          }), 64))
        ], 512), [
          [_vModelSelect, config.mode]
        ])
      ]),
      _createElementVNode("div", _hoisted_13, [
        _cache[17] || (_cache[17] = _createElementVNode("span", { class: "cfg-label" }, "监控目录", -1)),
        _withDirectives(_createElementVNode("textarea", {
          "onUpdate:modelValue": _cache[6] || (_cache[6] = $event => ((config.monitor_dirs) = $event)),
          class: "cfg-textarea",
          rows: "4",
          placeholder: "每行一个目录"
        }, null, 512), [
          [_vModelText, config.monitor_dirs]
        ])
      ]),
      _createElementVNode("div", _hoisted_14, [
        _cache[18] || (_cache[18] = _createElementVNode("span", { class: "cfg-label" }, "排除关键词", -1)),
        _withDirectives(_createElementVNode("textarea", {
          "onUpdate:modelValue": _cache[7] || (_cache[7] = $event => ((config.exclude_keywords) = $event)),
          class: "cfg-textarea",
          rows: "2",
          placeholder: "每行一个关键词（正则）"
        }, null, 512), [
          [_vModelText, config.exclude_keywords]
        ])
      ]),
      _cache[21] || (_cache[21] = _createElementVNode("p", { class: "cfg-tip" }, "网络挂载目录（CloudDrive2 / rclone / SMB 等）请选择兼容模式。", -1)),
      _cache[22] || (_cache[22] = _createElementVNode("p", { class: "cfg-tip" }, [
        _createTextVNode(" 电视剧分类分组按监控目录下的"),
        _createElementVNode("strong", null, "一级子目录"),
        _createTextVNode("自动识别（国漫 / 日番 / 国产剧 / 欧美剧 等）， 无需配置；全量扫描可在侧栏页按分类单独执行。 ")
      ], -1))
    ]),
    _createElementVNode("div", _hoisted_15, [
      _createElementVNode("button", {
        class: "cfg-btn ghost",
        disabled: scanning.value,
        onClick: _cache[8] || (_cache[8] = $event => (scanNow(false)))
      }, "全量扫描", 8, _hoisted_16),
      _createElementVNode("button", {
        class: "cfg-btn ghost",
        disabled: scanning.value,
        onClick: _cache[9] || (_cache[9] = $event => (scanNow(true)))
      }, "强制全量", 8, _hoisted_17),
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
const ConfigPanel = /*#__PURE__*/_export_sfc(_sfc_main, [['__scopeId',"data-v-aed9cbf4"]]);

export { ConfigPanel as default };
