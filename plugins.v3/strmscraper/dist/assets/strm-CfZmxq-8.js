// 前端通用工具：接口调用、海报地址与格式化
// AppPage（侧栏全页）与 Page（插件中心详情）共用，避免逻辑重复。

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

/** 取出响应体（不拆信封），用于 plugin/form 这类直接返回结构的接口 */
function bodyOf(response) {
  return response?.data ?? response ?? {}
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

/** 目录刮削状态：ok=已刮全 / pend=部分 / fail=未刮 */
function statusOf(item) {
  if (!item?.unscraped) return 'ok'
  if (item.unscraped >= item.total_files) return 'fail'
  return 'pend'
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

export { coverUrl as a, bodyOf as b, categoryColor as c, formatSize as f, makeApiCall as m, posterStyle as p, statusOf as s, unwrap as u, versionLabel as v };
