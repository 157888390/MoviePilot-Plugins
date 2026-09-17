// 前端通用工具：接口调用、格式化与常量。
// AppPage（侧栏全页）、Page（插件中心详情）与 Config（设置面板）共用。

const SOURCES = [
  { value: 'kw', label: '酷我' },
  { value: 'kg', label: '酷狗' },
  { value: 'tx', label: 'QQ 音乐' },
  { value: 'wy', label: '网易云' },
  { value: 'mg', label: '咪咕' },
];

const QUALITIES = [
  { value: '128k', label: '128k' },
  { value: '320k', label: '320k' },
  { value: 'flac', label: 'flac' },
  { value: 'flac24bit', label: 'flac24bit' },
  { value: 'hires', label: 'hires' },
];

/** 组装注入的 API 客户端调用，自动补 plugin/<id> 前缀 */
function makeApiCall(api, pluginId) {
  return (method, path, payload) => {
    if (typeof api?.[method] === 'function') return api[method](`plugin/${pluginId}${path}`, payload)
    return Promise.reject(new Error('MoviePilot API 客户端未注入'))
  }
}

/** 拆掉统一响应信封 / axios 包装，返回业务数据；success=false 时抛出 message */
function unwrap(response) {
  const body = response?.data ?? response;
  if (body?.success === false) throw new Error(body.message || '请求失败')
  return body?.data ?? body ?? {}
}

/** 取出响应体（不拆信封），用于 plugin/form 这类直接返回结构的接口 */
function bodyOf(response) {
  return response?.data ?? response ?? {}
}

function formatSize(size) {
  const value = Number(size || 0);
  if (!value) return '-'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(0)} KB`
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`
  return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`
}

/** 歌曲可用音质列表（服务端 types 字段） */
function qualitiesOf(song) {
  return (song?.types || []).map(item => item.type).filter(Boolean)
}

function singerOf(song) {
  return song?.singer || '未知歌手'
}

/** 歌曲稳定 key，用于下载中状态与列表渲染 */
function songKey(song) {
  return `${song?.source || ''}:${song?.songmid || ''}:${song?.name || ''}`
}

/** 封面地址（服务端直接给的是第三方 CDN 公网 URL） */
function coverOf(song) {
  return song?.img || ''
}

const _export_sfc = (sfc, props) => {
  const target = sfc.__vccOpts || sfc;
  for (const [key, val] of props) {
    target[key] = val;
  }
  return target;
};

export { QUALITIES as Q, SOURCES as S, _export_sfc as _, singerOf as a, bodyOf as b, coverOf as c, formatSize as f, makeApiCall as m, qualitiesOf as q, songKey as s, unwrap as u };
