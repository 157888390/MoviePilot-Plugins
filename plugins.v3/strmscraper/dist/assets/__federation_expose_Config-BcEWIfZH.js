import { importShared } from './__federation_fn_import-JrT3xvdd.js';
import { _ as _export_sfc, u as unwrap } from './_plugin-vue_export-helper-Ddow2Nd0.js';

const {createElementVNode:_createElementVNode,toDisplayString:_toDisplayString,openBlock:_openBlock,createElementBlock:_createElementBlock,createCommentVNode:_createCommentVNode,vModelCheckbox:_vModelCheckbox,withDirectives:_withDirectives,renderList:_renderList,Fragment:_Fragment,vModelSelect:_vModelSelect,vModelText:_vModelText} = await importShared('vue');


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
const _hoisted_10 = { class: "cfg-field" };
const _hoisted_11 = ["value"];
const _hoisted_12 = { class: "cfg-field" };
const _hoisted_13 = { class: "cfg-field" };
const _hoisted_14 = { class: "cfg-field" };
const _hoisted_15 = { class: "cfg-foot" };
const _hoisted_16 = ["disabled", "title"];

const {onMounted,reactive,ref} = await importShared('vue');


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

// 字段必须与后端 init_plugin / __save_config 读取的键完全一致，
// 后端不认的键（例如历史遗留的 skip_scraped / cron_* / sidebar_enabled）不再保留，
// 否则界面会给出「看起来能配、其实无效」的死开关。
const DEFAULTS = {
  enabled: false,
  onlyonce: false,
  overwrite: false,
  mode: 'compatibility',
  monitor_dirs: '',
  exclude_keywords: '',
  record_enabled: true,
  cron_expression: '',
};

const config = reactive({ ...DEFAULTS });
const scanning = ref(false);
const error = ref('');
const notice = ref('');

const MODES = [
  { title: '兼容模式（轮询，网络盘用）', value: 'compatibility' },
  { title: '性能模式（inotify，仅本地盘）', value: 'fast' },
];

onMounted(() => {
  const source = props.initialConfig || {};
  Object.keys(DEFAULTS).forEach(key => {
    if (key in source && source[key] !== undefined && source[key] !== null) config[key] = source[key];
  });
});

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

/**
 * 手动触发一次全量扫描并入队。
 *
 * 是否覆盖直接复用本页「覆盖已有元数据」开关的当前取值，因此不再需要「全量 / 强制全量」
 * 两个按钮 —— 它们本来就只差这个布尔值，并排摆放极易点错并触发全库重刮。
 */
async function scanNow() {
  scanning.value = true;
  error.value = '';
  notice.value = '';
  try {
    unwrap(await apiCall('get', `/scan?overwrite=${Boolean(config.overwrite)}`));
    notice.value = config.overwrite
      ? '已加入队列：全量覆盖重刮'
      : '已加入队列：全量补缺失（跳过已有元数据）';
  } catch (scanError) {
    error.value = scanError?.message || '触发全量扫描失败';
  } finally {
    scanning.value = false;
  }
}

return (_ctx, _cache) => {
  return (_openBlock(), _createElementBlock("div", _hoisted_1, [
    _createElementVNode("div", { class: "cfg-head" }, [
      _cache[8] || (_cache[8] = _createElementVNode("h2", null, "STRM 刮削配置", -1)),
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
      _cache[17] || (_cache[17] = _createElementVNode("div", { class: "cfg-section" }, "运行状态", -1)),
      _createElementVNode("div", _hoisted_5, [
        _createElementVNode("label", _hoisted_6, [
          _withDirectives(_createElementVNode("input", {
            "onUpdate:modelValue": _cache[0] || (_cache[0] = $event => ((config.enabled) = $event)),
            type: "checkbox"
          }, null, 512), [
            [_vModelCheckbox, config.enabled]
          ]),
          _cache[9] || (_cache[9] = _createElementVNode("span", null, [
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
          _cache[10] || (_cache[10] = _createElementVNode("span", null, [
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
          _cache[11] || (_cache[11] = _createElementVNode("span", null, [
            _createElementVNode("b", null, "覆盖已有元数据"),
            _createElementVNode("em", null, "重刮时覆盖已存在的 NFO 与图片")
          ], -1))
        ]),
        _createElementVNode("label", _hoisted_9, [
          _withDirectives(_createElementVNode("input", {
            "onUpdate:modelValue": _cache[3] || (_cache[3] = $event => ((config.record_enabled) = $event)),
            type: "checkbox"
          }, null, 512), [
            [_vModelCheckbox, config.record_enabled]
          ]),
          _cache[12] || (_cache[12] = _createElementVNode("span", null, [
            _createElementVNode("b", null, "记录刮削历史"),
            _createElementVNode("em", null, "把每次刮削的时间、目标、分类与结果落到插件数据目录")
          ], -1))
        ])
      ]),
      _cache[18] || (_cache[18] = _createElementVNode("div", { class: "cfg-section" }, "监控", -1)),
      _createElementVNode("div", _hoisted_10, [
        _cache[13] || (_cache[13] = _createElementVNode("span", { class: "cfg-label" }, "监控模式", -1)),
        _withDirectives(_createElementVNode("select", {
          "onUpdate:modelValue": _cache[4] || (_cache[4] = $event => ((config.mode) = $event)),
          class: "cfg-select"
        }, [
          (_openBlock(), _createElementBlock(_Fragment, null, _renderList(MODES, (item) => {
            return _createElementVNode("option", {
              key: item.value,
              value: item.value
            }, _toDisplayString(item.title), 9, _hoisted_11)
          }), 64))
        ], 512), [
          [_vModelSelect, config.mode]
        ])
      ]),
      _createElementVNode("div", _hoisted_12, [
        _cache[14] || (_cache[14] = _createElementVNode("span", { class: "cfg-label" }, "监控目录", -1)),
        _withDirectives(_createElementVNode("textarea", {
          "onUpdate:modelValue": _cache[5] || (_cache[5] = $event => ((config.monitor_dirs) = $event)),
          class: "cfg-textarea",
          rows: "4",
          placeholder: "每行一个目录"
        }, null, 512), [
          [_vModelText, config.monitor_dirs]
        ])
      ]),
      _createElementVNode("div", _hoisted_13, [
        _cache[15] || (_cache[15] = _createElementVNode("span", { class: "cfg-label" }, "排除关键词", -1)),
        _withDirectives(_createElementVNode("textarea", {
          "onUpdate:modelValue": _cache[6] || (_cache[6] = $event => ((config.exclude_keywords) = $event)),
          class: "cfg-textarea",
          rows: "2",
          placeholder: "每行一个关键词（正则）"
        }, null, 512), [
          [_vModelText, config.exclude_keywords]
        ])
      ]),
      _createElementVNode("div", _hoisted_14, [
        _cache[16] || (_cache[16] = _createElementVNode("span", { class: "cfg-label" }, "定时扫描（cron 表达式）", -1)),
        _withDirectives(_createElementVNode("input", {
          "onUpdate:modelValue": _cache[7] || (_cache[7] = $event => ((config.cron_expression) = $event)),
          class: "cfg-input",
          type: "text",
          placeholder: "留空关闭，例如 0 3 * * * 表示每天 03:00 全量补漏扫描一次"
        }, null, 512), [
          [_vModelText, config.cron_expression]
        ])
      ]),
      _cache[19] || (_cache[19] = _createElementVNode("p", { class: "cfg-tip" }, "网络挂载目录（CloudDrive2 / rclone / SMB 等）请选择兼容模式。", -1)),
      _cache[20] || (_cache[20] = _createElementVNode("div", { class: "cfg-section" }, "界面", -1)),
      _cache[21] || (_cache[21] = _createElementVNode("p", { class: "cfg-tip" }, " 海报墙、分类筛选、单集/版本刮削与刮削记录都在插件详情页内，不再注册主界面侧栏入口。 ", -1))
    ]),
    _createElementVNode("div", _hoisted_15, [
      _createElementVNode("button", {
        class: "cfg-btn ghost",
        disabled: scanning.value,
        title: config.overwrite ? '重下全部 NFO 与图片' : '已有元数据不动，只补未刮的',
        onClick: scanNow
      }, "全量扫描" + _toDisplayString(config.overwrite ? '（覆盖重刮）' : '（仅补缺失）'), 9, _hoisted_16),
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
const Config = /*#__PURE__*/_export_sfc(_sfc_main, [['__scopeId',"data-v-b7c4eabf"]]);

export { Config as default };
