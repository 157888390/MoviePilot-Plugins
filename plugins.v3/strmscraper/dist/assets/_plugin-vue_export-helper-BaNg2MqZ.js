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
  return `${base}/plugin/${pluginId}/cover?path=${path}&type=${type}`
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

/** 分类分组的固定配色，保证同名分类在不同页面/不同刷新间颜色稳定 */
const CATEGORY_COLORS = {
  国漫: '#EC4899',
  日番: '#8B5CF6',
  国产剧: '#EF4444',
  欧美剧: '#3B82F6',
  日韩剧: '#14B8A6',
  纪录片: '#F59E0B',
  儿童: '#22C55E',
  综艺: '#F97316',
  音乐: '#F43F5E',
  未分类: '#94A3B8',
};

/**
 * 分类标签配色：内置分类用固定色，用户自建分类按名称哈希落到调色板，
 * 保证同一分类名始终得到同一颜色（而不是每次渲染随机变）。
 */
function categoryColor(name) {
  const key = String(name || '').trim();
  if (!key) return CATEGORY_COLORS['未分类']
  if (CATEGORY_COLORS[key]) return CATEGORY_COLORS[key]
  const palette = ['#7C5CFC', '#0EA5E9', '#D946EF', '#F43F5E', '#10B981', '#EAB308'];
  return palette[hashOf(key) % palette.length]
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

export { STATUS_LABELS as S, _export_sfc as _, coverUrl as a, typeLabel as b, categoryColor as c, formatTime as d, errorLabel as e, formatDate as f, formatSize as g, statusOf as h, makeApiCall as m, posterStyle as p, statusClass as s, typeClass as t, unwrap as u, versionLabel as v };
