import { importShared } from './__federation_fn_import-JrT3xvdd.js';

// 前端通用工具：接口调用、海报地址与格式化
// 供详情页 Page 与配置页 Config 共用，避免逻辑重复。
//
// 说明：本文件因 .gitignore 的 lib/ 规则从未被发布仓跟踪，本次由 dist/assets/strm-CfZmxq-8.js
// 还原（构建配置为 minify: false，产物保留了原始注释与函数体，仅导出名被混淆为 a/b/c/f/m/p/s/u/v）。

const GRADIENTS = [
  ['#667EEA', '#764BA2'], ['#F093FB', '#F5576C'], ['#4FACFE', '#00C6FB'],
  ['#43E97B', '#38F9D7'], ['#FA709A', '#FBC2A0'], ['#30CFD0', '#330867'],
  ['#FF9A9E', '#FECFEF'], ['#A18CD1', '#FBC2EB'],
];

/** 组装注入的 API 客户端调用，自动补 plugin/<id> 前缀 */
function makeApiCall(api, pluginId) {
  return (method, path, payload) => {
    if (typeof api?.[method] === 'function') return api[method](`plugin/${pluginId}${path}`, payload)
    return Promise.reject(new Error('MoviePilot API 客户端未注入'))
  }
}

/** 拆掉统一响应信封 / axios 包装，返回业务数据 */
function unwrap(response) {
  const body = response?.data ?? response;
  if (body?.success === false) throw new Error(body.message || '请求失败')
  return body?.data ?? body ?? {}
}

/** 接口基址：优先用注入客户端的 baseURL，兜底 /api/v1 */
function apiBase(api) {
  const raw = api?.defaults?.baseURL || api?.defaults?.baseUrl || '';
  if (raw && typeof raw === 'string') return raw.replace(/\/+$/, '')
  return '/api/v1'
}

/** 媒体海报地址：命中本地海报直接返回图片，未命中由后端回退 TMDB */
function coverUrl(api, pluginId, item) {
  const base = apiBase(api);
  const path = encodeURIComponent(item?.path || '');
  const type = item?.type || '';
  // 刮削后海报可能被替换，使用元数据变更时间避开旧图片的长缓存。
  const revision = encodeURIComponent(item?.last_scrape || item?.last_file_change || 0);
  return `${base}/plugin/${pluginId}/cover?path=${path}&type=${type}&v=${revision}`
}

function hashOf(text) {
  let hash = 0;
  const value = String(text || '');
  for (let i = 0; i < value.length; i += 1) hash = (hash * 31 + value.charCodeAt(i)) >>> 0;
  return hash
}

/** 海报加载失败时的渐变占位图 */
function posterStyle(title) {
  const [from, to] = GRADIENTS[hashOf(title) % GRADIENTS.length];
  return { background: `linear-gradient(135deg, ${from}, ${to})` }
}

function formatSize(size) {
  const value = Number(size || 0);
  if (!value) return '-'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(0)} KB`
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`
  return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`
}

/** 失败原因错误码 → 中文文案（与后端 ERROR_CODE_LABELS 保持一致，前端统一引用） */
const ERROR_CODE_LABELS = {
  not_matched: '未匹配到 TMDB',
  timeout: '网络超时',
  permission: '权限不足',
  path_gone: '路径已失效',
  unknown: '未知错误',
};

/** 错误码中文映射：取不到时回落 unknown 的文案 */
function errorLabel(code) {
  return ERROR_CODE_LABELS[code] || ERROR_CODE_LABELS.unknown
}

/** 条目级状态 → 中文标签（status 四态 + busy 刮削中） */
const STATUS_LABELS = {
  scraped: '已刮削',
  failed: '失败',
  skipped: '跳过',
  pending: '未刮削',
  busy: '刮削中',
};

/** 状态 → 样式类后缀：ok=绿 / fail=红 / busy=黄 / skip=灰 / none=白 */
function statusClass(status) {
  if (status === 'scraped') return 'ok'
  if (status === 'failed') return 'fail'
  if (status === 'busy') return 'busy'
  if (status === 'skipped') return 'skip'
  return 'none'
}

/**
 * 目录刮削状态（数据源 item.status，缺 status 时回落 .nfo 判定）。
 *
 * 四态语义：scraped=已刮全 / failed=失败 / skipped=跳过 / pending=未刮。
 * 「刮削中」不在 item.status 里，由调用方结合队列状态叠加，见 Page.vue 的 displayStatus。
 */
function statusOf(item) {
  const status = item?.status;
  if (status === 'scraped') return 'scraped'
  if (status === 'failed') return 'failed'
  if (status === 'skipped') return 'skipped'
  if (!status) return item?.dir_scraped ? 'scraped' : 'pending'
  return 'pending'
}

function versionLabel(name) {
  const matched = String(name || '').match(/(2160p|1080p|720p|480p|4K|8K|DoVi|HDR|REMUX|BluRay|WEB|导演剪辑|加长)/i);
  return matched ? matched[1].toUpperCase() : (String(name || '').replace(/\.strm$/i, '') || '版本')
}

/** 媒体类型中文标签：movie=电影 / tv=电视剧 / music=音乐 */
function typeLabel(type) {
  if (type === 'tv') return '电视剧'
  if (type === 'music') return '音乐'
  return '电影'
}

/** 媒体类型样式类后缀：is-mv / is-tv / is-mu */
function typeClass(type) {
  if (type === 'tv') return 'is-tv'
  if (type === 'music') return 'is-mu'
  return 'is-mv'
}

/** 时间戳（秒或毫秒）格式化为 YYYY-MM-DD HH:mm，空值返回 - */
function formatTime(value) {
  const num = Number(value || 0);
  if (!num) return '-'
  // 后端 last_scrape 是秒级时间戳，兼容误传毫秒的情况
  const date = new Date(num > 1e12 ? num : num * 1000);
  if (Number.isNaN(date.getTime())) return '-'
  const pad = part => String(part).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} `
    + `${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** 时间戳格式化为 YYYY-MM-DD（F6 详情页「文件更新」用，丢弃时分） */
function formatDate(value) {
  const full = formatTime(value);
  return full === '-' ? '-' : full.slice(0, 10)
}

const _export_sfc = (sfc, props) => {
  const target = sfc.__vccOpts || sfc;
  for (const [key, val] of props) {
    target[key] = val;
  }
  return target;
};

const {createElementVNode:_createElementVNode,openBlock:_openBlock,createElementBlock:_createElementBlock} = await importShared('vue');


const _hoisted_1 = ["width", "height"];
const _hoisted_2 = ["d"];


const _sfc_main = {
  __name: 'StrmIcon',
  props: {
  name: { type: String, default: 'grid' },
  size: { type: [Number, String], default: 20 },
},
  setup(__props) {

/** 纯 SVG 图标，避免依赖宿主的图标字体与组件注册。 */

const paths = {
  library: 'M4 4h6v7H4z M14 4h6v7h-6z M4 15h6v5H4z M14 15h6v5h-6z',
  grid: 'M4 4h6v6H4z M14 4h6v6h-6z M4 14h6v6H4z M14 14h6v6h-6z',
  list: 'M8 6h12 M8 12h12 M8 18h12 M4 6h.01 M4 12h.01 M4 18h.01',
  film: 'M4 4h16v16H4z M4 8h16 M4 16h16 M8 4v4 M16 4v4 M8 16v4 M16 16v4',
  tv: 'M4 7h16v13H4z M8 3l4 4 4-4',
  music:
    'M9 18V5l11-2v13 M9 7l11-2 M9 18a3 3 0 1 1-3-3c1.7 0 3 1.3 3 3 M20 16a3 3 0 1 1-3-3c1.7 0 3 1.3 3 3',
  folder: 'M3 7V5h6l2 2h10v13H3z',
  check: 'M5 12l4 4L19 6',
  checkCircle: 'M21 11v1a9 9 0 1 1-5.3-8.2 M21 4L11 14l-3-3',
  clock: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0 M12 7v5l3 2',
  alert:
    'M12 8v5 M12 17h.01 M10.3 3.9L2.2 18a2 2 0 0 0 1.7 3h16.2a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0',
  search: 'M20 20l-5-5 M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0',
  refresh: 'M20 7v5h-5 M4 17v-5h5 M6.1 6a8 8 0 0 1 13 2l.9 4 M4 12l.9 4a8 8 0 0 0 13 2',
  sparkles: 'M12 3l2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z M20 2v4 M18 4h4',
  chevron: 'M8 10l4 4 4-4',
  arrow: 'M5 12h14 M13 6l6 6-6 6',
  back: 'M19 12H5 M11 6l-6 6 6 6',
  settings:
    'M9 4l1-2h4l1 2 3 2 2 1v4l-2 1-1 3v2l-3 2h-4l-1-2-3-2-2-1v-4l2-1 1-3V4z M15 10a3 3 0 1 1-6 0 3 3 0 0 1 6 0',
  close: 'M6 6l12 12 M6 18L18 6',
  history: 'M3 11a9 9 0 1 1 2.6 7.4 M3 4v7h7 M12 7v5l3 2',
  queue: 'M8 5h12 M8 12h12 M8 19h8 M3 5h.01 M3 12h.01 M3 19h.01',
  activity: 'M2 12h4l3-8 6 16 3-8h4',
  sort: 'M8 4v16 M4 16l4 4 4-4 M15 5h6 M15 10h4 M15 15h2',
  moon: 'M20.8 13.2A9 9 0 0 1 10.8 3.2a9 9 0 1 0 10 10',
  sun: 'M12 8a4 4 0 1 1 0 8 4 4 0 0 1 0-8 M12 2v2 M12 20v2 M2 12h2 M20 12h2 M5 5l1.5 1.5 M17.5 17.5L19 19 M5 19l1.5-1.5 M17.5 6.5L19 5',
  monitor: 'M3 4h18v13H3z M8 21h8 M12 17v4',
};

return (_ctx, _cache) => {
  return (_openBlock(), _createElementBlock("svg", {
    class: "strm-icon",
    width: __props.size,
    height: __props.size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    "stroke-width": "1.65",
    "stroke-linecap": "round",
    "stroke-linejoin": "round",
    "aria-hidden": "true"
  }, [
    _createElementVNode("path", {
      d: paths[__props.name] || paths.grid
    }, null, 8, _hoisted_2)
  ], 8, _hoisted_1))
}
}

};
const StrmIcon = /*#__PURE__*/_export_sfc(_sfc_main, [['__scopeId',"data-v-8e12ebd5"]]);

export { StrmIcon as S, _export_sfc as _, STATUS_LABELS as a, typeLabel as b, coverUrl as c, statusOf as d, errorLabel as e, formatDate as f, formatTime as g, formatSize as h, makeApiCall as m, posterStyle as p, statusClass as s, typeClass as t, unwrap as u, versionLabel as v };
