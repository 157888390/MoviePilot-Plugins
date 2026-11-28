import { importShared } from './__federation_fn_import-JrT3xvdd.js';
import { _ as _export_sfc, S as StrmIcon, u as unwrap } from './_plugin-vue_export-helper-StrmIcon-CCn3HE3U.js';

const {createVNode:_createVNode,createElementVNode:_createElementVNode,toDisplayString:_toDisplayString,openBlock:_openBlock,createElementBlock:_createElementBlock,createCommentVNode:_createCommentVNode,renderList:_renderList,Fragment:_Fragment,vModelCheckbox:_vModelCheckbox,withDirectives:_withDirectives,vModelRadio:_vModelRadio,createTextVNode:_createTextVNode,normalizeClass:_normalizeClass,vModelText:_vModelText,withModifiers:_withModifiers} = await importShared('vue');


const _hoisted_1 = { class: "cfg-head" };
const _hoisted_2 = { class: "cfg-mark" };
const _hoisted_3 = { class: "cfg-body" };
const _hoisted_4 = {
  key: 0,
  class: "cfg-alert is-error",
  role: "alert"
};
const _hoisted_5 = {
  key: 1,
  class: "cfg-alert is-ok",
  role: "status"
};
const _hoisted_6 = { class: "cfg-section" };
const _hoisted_7 = { class: "cfg-grid" };
const _hoisted_8 = ["onUpdate:modelValue"];
const _hoisted_9 = { class: "cfg-section" };
const _hoisted_10 = { class: "cfg-modes" };
const _hoisted_11 = { class: "cfg-field" };
const _hoisted_12 = { class: "cfg-field" };
const _hoisted_13 = { class: "cfg-section" };
const _hoisted_14 = { class: "cfg-field" };
const _hoisted_15 = { class: "cfg-cron-wrap" };
const _hoisted_16 = ["aria-invalid"];
const _hoisted_17 = {
  key: 0,
  id: "cfg-cron-help",
  class: "cfg-error"
};
const _hoisted_18 = {
  key: 1,
  id: "cfg-cron-help",
  class: "cfg-field-tip"
};
const _hoisted_19 = { class: "cfg-presets" };
const _hoisted_20 = { class: "cfg-foot" };
const _hoisted_21 = ["disabled"];
const _hoisted_22 = { class: "cfg-foot-right" };
const _hoisted_23 = ["disabled"];

const {computed,onMounted,reactive,ref} = await importShared('vue');


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

// cron 校验。宿主是把这段字符串**直接当 APScheduler 的触发器**用的，只有 CronTrigger
// 实例或 "cron"/"interval"/"date" 别名才认，所以手填的表达式必须是标准五段式；
// 少写几段（例如只想填「0 4」）不会定时跑，而是让宿主直接报服务注册失败。
// 这里只挡字段数与字符集这类手误，语义正确性仍以后端 CronTrigger.from_crontab 为准。
const CRON_FIELD = /^[0-9*,\-/A-Za-z]+$/;
const cronError = computed(() => {
  const raw = String(config.cron_expression || '').trim();
  if (!raw) return ''
  const fields = raw.split(/\s+/);
  if (fields.length !== 5) return `必须是 5 段（分 时 日 月 周），当前只有 ${fields.length} 段`
  if (!fields.every((field) => CRON_FIELD.test(field))) return '只允许数字、* , - / 以及英文缩写'
  return ''
});

/** 失焦时把多余空白收成一个空格，避免「0  3 * * *」这类看不出问题的写法。 */
function normalizeCron() {
  const raw = String(config.cron_expression || '').trim();
  config.cron_expression = raw ? raw.split(/\s+/).join(' ') : '';
}

onMounted(() => {
  const source = props.initialConfig || {};
  Object.keys(DEFAULTS).forEach((key) => {
    if (key in source && source[key] !== undefined && source[key] !== null)
      config[key] = source[key];
  });
});

const apiCall = (method, path, payload) => {
  if (typeof props.api?.[method] === 'function')
    return props.api[method](`plugin/${props.pluginId}${path}`, payload)
  return Promise.reject(new Error('MoviePilot API 客户端未注入'))
};

function submit() {
  if (cronError.value) return
  normalizeCron();
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
  return (_openBlock(), _createElementBlock("form", {
    class: "cfg",
    onSubmit: _withModifiers(submit, ["prevent"])
  }, [
    _createElementVNode("header", _hoisted_1, [
      _createElementVNode("span", _hoisted_2, [
        _createVNode(StrmIcon, {
          name: "settings",
          size: 24
        })
      ]),
      _cache[8] || (_cache[8] = _createElementVNode("div", null, [
        _createElementVNode("span", { class: "cfg-eyebrow" }, "PLUGIN SETTINGS"),
        _createElementVNode("h2", null, "刮削设置"),
        _createElementVNode("p", null, "按你的存储方式，让媒体资料自动保持完整。")
      ], -1)),
      _createElementVNode("button", {
        type: "button",
        class: "cfg-close",
        "aria-label": "关闭设置",
        onClick: close
      }, [
        _createVNode(StrmIcon, {
          name: "close",
          size: 20
        })
      ])
    ]),
    _createElementVNode("div", _hoisted_3, [
      (error.value)
        ? (_openBlock(), _createElementBlock("div", _hoisted_4, _toDisplayString(error.value), 1))
        : (notice.value)
          ? (_openBlock(), _createElementBlock("div", _hoisted_5, _toDisplayString(notice.value), 1))
          : _createCommentVNode("", true),
      _createElementVNode("section", _hoisted_6, [
        _cache[10] || (_cache[10] = _createElementVNode("div", { class: "cfg-section-head" }, [
          _createElementVNode("span", null, "01"),
          _createElementVNode("div", null, [
            _createElementVNode("h3", null, "运行方式"),
            _createElementVNode("p", null, "设置自动监控与刮削行为")
          ])
        ], -1)),
        _createElementVNode("div", _hoisted_7, [
          (_openBlock(), _createElementBlock(_Fragment, null, _renderList([
              { key: 'enabled', title: '启用目录监控', hint: '发现新增文件后自动刮削' },
              { key: 'record_enabled', title: '记录刮削历史', hint: '保留每次刮削的时间与结果' },
              { key: 'onlyonce', title: '保存后扫描一次', hint: '保存配置后执行一次全量扫描' },
              { key: 'overwrite', title: '覆盖已有元数据', hint: '全量扫描时重新下载已有资料' },
            ], (option) => {
            return _createElementVNode("label", {
              key: option.key,
              class: "cfg-switch"
            }, [
              _createElementVNode("span", null, [
                _createElementVNode("b", null, _toDisplayString(option.title), 1),
                _createElementVNode("small", null, _toDisplayString(option.hint), 1)
              ]),
              _withDirectives(_createElementVNode("input", {
                "onUpdate:modelValue": $event => ((config[option.key]) = $event),
                type: "checkbox",
                role: "switch"
              }, null, 8, _hoisted_8), [
                [_vModelCheckbox, config[option.key]]
              ]),
              _cache[9] || (_cache[9] = _createElementVNode("i", {
                class: "cfg-toggle",
                "aria-hidden": "true"
              }, null, -1))
            ])
          }), 64))
        ])
      ]),
      _createElementVNode("section", _hoisted_9, [
        _cache[17] || (_cache[17] = _createElementVNode("div", { class: "cfg-section-head" }, [
          _createElementVNode("span", null, "02"),
          _createElementVNode("div", null, [
            _createElementVNode("h3", null, "监控目录"),
            _createElementVNode("p", null, "选择适合你存储环境的监控方式")
          ])
        ], -1)),
        _createElementVNode("div", _hoisted_10, [
          _createElementVNode("label", {
            class: _normalizeClass(['cfg-mode', config.mode === 'compatibility' && 'active'])
          }, [
            _withDirectives(_createElementVNode("input", {
              "onUpdate:modelValue": _cache[0] || (_cache[0] = $event => ((config.mode) = $event)),
              type: "radio",
              value: "compatibility",
              name: "monitor-mode"
            }, null, 512), [
              [_vModelRadio, config.mode]
            ]),
            _createVNode(StrmIcon, {
              name: "folder",
              size: 21
            }),
            _cache[11] || (_cache[11] = _createElementVNode("span", null, [
              _createElementVNode("b", null, [
                _createTextVNode(" 兼容模式 "),
                _createElementVNode("em", null, "推荐")
              ]),
              _createElementVNode("small", null, "适用于 SMB、rclone、CloudDrive2 等网络挂载")
            ], -1)),
            _cache[12] || (_cache[12] = _createElementVNode("i", null, null, -1))
          ], 2),
          _createElementVNode("label", {
            class: _normalizeClass(['cfg-mode', config.mode === 'fast' && 'active'])
          }, [
            _withDirectives(_createElementVNode("input", {
              "onUpdate:modelValue": _cache[1] || (_cache[1] = $event => ((config.mode) = $event)),
              type: "radio",
              value: "fast",
              name: "monitor-mode"
            }, null, 512), [
              [_vModelRadio, config.mode]
            ]),
            _createVNode(StrmIcon, {
              name: "activity",
              size: 21
            }),
            _cache[13] || (_cache[13] = _createElementVNode("span", null, [
              _createElementVNode("b", null, "性能模式"),
              _createElementVNode("small", null, "使用文件事件监控，适用于本地磁盘")
            ], -1)),
            _cache[14] || (_cache[14] = _createElementVNode("i", null, null, -1))
          ], 2)
        ]),
        _createElementVNode("label", _hoisted_11, [
          _cache[15] || (_cache[15] = _createElementVNode("span", { class: "cfg-label" }, [
            _createTextVNode(" 监控路径 "),
            _createElementVNode("small", null, "每行一个绝对路径")
          ], -1)),
          _withDirectives(_createElementVNode("textarea", {
            "onUpdate:modelValue": _cache[2] || (_cache[2] = $event => ((config.monitor_dirs) = $event)),
            class: "cfg-textarea cfg-code",
            rows: "3",
            placeholder: "/media/电影\n/media/电视剧",
            spellcheck: "false"
          }, null, 512), [
            [_vModelText, config.monitor_dirs]
          ])
        ]),
        _createElementVNode("label", _hoisted_12, [
          _cache[16] || (_cache[16] = _createElementVNode("span", { class: "cfg-label" }, [
            _createTextVNode(" 排除关键词 "),
            _createElementVNode("small", null, "可选，支持正则表达式")
          ], -1)),
          _withDirectives(_createElementVNode("textarea", {
            "onUpdate:modelValue": _cache[3] || (_cache[3] = $event => ((config.exclude_keywords) = $event)),
            class: "cfg-textarea",
            rows: "2",
            placeholder: "每行一个关键词，例如：预告片、花絮"
          }, null, 512), [
            [_vModelText, config.exclude_keywords]
          ])
        ])
      ]),
      _createElementVNode("section", _hoisted_13, [
        _cache[20] || (_cache[20] = _createElementVNode("div", { class: "cfg-section-head" }, [
          _createElementVNode("span", null, "03"),
          _createElementVNode("div", null, [
            _createElementVNode("h3", null, "定时扫描"),
            _createElementVNode("p", null, "定期检查目录，补齐遗漏的媒体资料")
          ])
        ], -1)),
        _createElementVNode("label", _hoisted_14, [
          _cache[18] || (_cache[18] = _createElementVNode("span", { class: "cfg-label" }, [
            _createTextVNode(" Cron 表达式 "),
            _createElementVNode("small", null, "留空则关闭定时扫描")
          ], -1)),
          _createElementVNode("div", _hoisted_15, [
            _createVNode(StrmIcon, {
              name: "clock",
              size: 17
            }),
            _withDirectives(_createElementVNode("input", {
              "onUpdate:modelValue": _cache[4] || (_cache[4] = $event => ((config.cron_expression) = $event)),
              class: _normalizeClass(["cfg-input cfg-code", { 'is-invalid': cronError.value }]),
              type: "text",
              placeholder: "0 3 * * *",
              "aria-invalid": !!cronError.value,
              "aria-describedby": "cfg-cron-help",
              onBlur: normalizeCron
            }, null, 42, _hoisted_16), [
              [_vModelText, config.cron_expression]
            ])
          ]),
          (cronError.value)
            ? (_openBlock(), _createElementBlock("p", _hoisted_17, _toDisplayString(cronError.value), 1))
            : (_openBlock(), _createElementBlock("p", _hoisted_18, " 标准五段格式：分 · 时 · 日 · 月 · 周 "))
        ]),
        _createElementVNode("div", _hoisted_19, [
          _cache[19] || (_cache[19] = _createElementVNode("span", null, "快捷设置", -1)),
          _createElementVNode("button", {
            type: "button",
            onClick: _cache[5] || (_cache[5] = $event => (config.cron_expression = '0 3 * * *'))
          }, "每天 03:00"),
          _createElementVNode("button", {
            type: "button",
            onClick: _cache[6] || (_cache[6] = $event => (config.cron_expression = '0 */6 * * *'))
          }, "每 6 小时"),
          _createElementVNode("button", {
            type: "button",
            onClick: _cache[7] || (_cache[7] = $event => (config.cron_expression = ''))
          }, "关闭定时")
        ])
      ])
    ]),
    _createElementVNode("footer", _hoisted_20, [
      _createElementVNode("button", {
        type: "button",
        class: "cfg-scan",
        disabled: scanning.value,
        onClick: scanNow
      }, [
        _createVNode(StrmIcon, {
          name: "refresh",
          size: 15
        }),
        _createTextVNode(" " + _toDisplayString(scanning.value ? '正在入队…' : config.overwrite ? '立即覆盖扫描' : '立即补全缺失'), 1)
      ], 8, _hoisted_21),
      _createElementVNode("div", _hoisted_22, [
        _createElementVNode("button", {
          type: "button",
          class: "cfg-btn ghost",
          onClick: close
        }, "取消"),
        _createElementVNode("button", {
          type: "submit",
          class: "cfg-btn",
          disabled: !!cronError.value
        }, [
          _createVNode(StrmIcon, {
            name: "check",
            size: 15
          }),
          _cache[21] || (_cache[21] = _createTextVNode(" 保存设置 ", -1))
        ], 8, _hoisted_23)
      ])
    ])
  ], 32))
}
}

};
const Config = /*#__PURE__*/_export_sfc(_sfc_main, [['__scopeId',"data-v-05eba343"]]);

export { Config as default };
