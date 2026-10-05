<script setup>
// 插件详情弹窗（联邦暴露名 ./Page）。
//
// 本组件收敛了原侧栏全页 AppPage 与精简详情页的全部能力：统计、海报墙、类型与二级分类
// 筛选、搜索、单集/版本刮削、任务进度与刮削记录。宿主 PluginDataDialog 在 Vue 模式下只提供
// 一个空 VCard，因此标题栏、关闭按钮与设置入口都由本组件自行渲染。
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import {
  coverUrl, errorLabel, formatDate, formatSize, formatTime, makeApiCall,
  posterStyle, STATUS_LABELS, statusClass, statusOf, typeClass, typeLabel, unwrap, versionLabel,
} from '../lib/strm.js'

const props = defineProps({
  api: { type: Object, default: () => ({}) },
  pluginId: { type: String, default: 'StrmScraper' },
  sourcePluginId: { type: String, default: '' },
  nativeSubscribe: { type: Function, default: null },
  // 宿主用 snake_case 透传（PluginDataDialog 绑定的是 :show_switch），必须按原样声明；
  // 若声明成 showSwitch，Vue 的 prop 归一化匹配不上，设置入口会永远不显示。
  show_switch: { type: Boolean, default: true },
})

// action=让宿主重载详情数据；switch=切换到配置页；close=关闭弹窗；layout=声明弹窗宽度
const emit = defineEmits(['action', 'switch', 'close', 'layout'])

const TYPE_TABS = [
  { key: 'all', label: '全部' },
  { key: 'movie', label: '电影' },
  { key: 'tv', label: '电视剧' },
  { key: 'music', label: '音乐' },
]

// 队列轮询间隔：刮削进行中要跟得紧，空闲时没必要反复打接口
const POLL_BUSY_MS = 2000
const POLL_IDLE_MS = 8000

const apiCall = makeApiCall(props.api, props.pluginId)

// view：grid=媒体库海报墙 / detail=某个媒体的内联详情 / records=刮削记录
const view = ref('grid')
const loading = ref(true)
const error = ref('')
const notice = ref('')

const overview = ref({})
const items = ref([])
const categories = ref([])
const keyword = ref('')
const typeFilter = ref('all')
// 条目状态筛选：all / scraped / failed / skipped / pending（数字来自 overview.status）
const statusFilter = ref('all')
// 分类多选（组合键 `${父分类路径}::${分类名}`；一级分类回退项为 `::${分类名}`），空数组 = 不过滤
const subFilters = ref([])
// 队列面板：点顶部状态区展开，不单独开 Tab（F8）
const queuePanelOpen = ref(false)

const current = ref(null)
const detail = ref(null)
const seasonKey = ref('')
const selected = ref(new Set())

const records = ref([])
const recordsLoading = ref(false)
// 记录筛选：状态 / 类型 / 分类 / 时间范围（F10），空串 = 不过滤
const recordFilter = reactive({ success: '', type: '', category: '', since: '', until: '' })

// 队列状态：界面唯一的「正在做什么」来源。文件监控触发的刮削与用户点出来的刮削
// 都会出现在这里，因此不再需要「扫描进度」和「任务进度」两套互不相干的状态。
const queue = ref({ running: null, queued: [], queued_total: 0, stats: {}, recent: [], busy: false })
const busy = computed(() => Boolean(queue.value.busy))
const scanMenu = ref(false)
let queueTimer = null
// 记录上一轮队列是否忙碌，用于识别「刚跑完」这一瞬间并触发清单重算
let queueWasBusy = false

// 海报加载失败（无本地海报且 TMDB 未命中）时回退渐变占位图
const posterFailed = reactive({})

/** 记录某条目的海报已加载失败，模板据此切换到渐变占位图。 */
function markPosterFailed(path) {
  posterFailed[path] = true
}

const counts = computed(() => ({
  all: items.value.length,
  movie: items.value.filter(item => item.type === 'movie').length,
  tv: items.value.filter(item => item.type === 'tv').length,
  music: items.value.filter(item => item.type === 'music').length,
}))

// 状态过滤 chip：数字来自 overview.status，key 与 item.status 一一对应。
// 按用户要求只保留「待刮削 / 已刮 / 失败」三项（去掉「跳过」与类型计数）。
const STATUS_FILTERS = [
  { key: 'pending', label: '待刮削' },
  { key: 'scraped', label: '已刮' },
  { key: 'failed', label: '失败' },
]
const statusCounts = computed(() => {
  const dist = overview.value.status || {}
  return {
    pending: dist.pending || 0,
    failed: dist.failed || 0,
    scraped: dist.scraped || 0,
    skipped: dist.skipped || 0,
  }
})

/** 目标路径是否正在队列里（执行中或排队中），用于把状态叠成「刮削中」。 */
function isInQueue(path) {
  const running = queue.value.running
  if (running && running.target === path) return true
  return (queue.value.queued || []).some(item => item.target === path)
}

/** 展示用状态：item.status 四态之上叠加「刮削中」。 */
function displayStatus(item) {
  if (isInQueue(item.path)) return 'busy'
  return statusOf(item)
}

/**
 * 取媒体相对其分类目录的下一级目录名，即二级分类名。
 *
 * 媒体直接躺在分类目录下（中间只差一层）时不构成二级分类，返回空串。
 */
function subOf(item) {
  const base = item.category_dir || item.root || ''
  const path = item.path || ''
  if (!base || !path.startsWith(`${base}/`)) return ''
  const rest = path.slice(base.length + 1)
  const index = rest.indexOf('/')
  return index === -1 ? '' : rest.slice(0, index)
}

/**
 * 媒体归属的分类名集合：末级分类名 + 一级分类名。
 *
 * ``item.category`` 是后端算出的末级分类目录名（如「日番」「国产剧」「动画电影」），
 * ``item.category_dir`` 的目录名是一级分类名（如「电视剧」「电影」）。一级与二级
 * 筛选项都用这个集合匹配，不必再靠路径前缀推导。
 */
function categoryKeysOf(item) {
  const keys = new Set()
  const deepest = String(item?.category || '').trim()
  if (deepest) keys.add(deepest)
  const base = String(item?.category_dir || item?.root || '')
    .replace(/[/\\]+$/, '')
    .split(/[/\\]/)
    .pop()
  if (base) keys.add(base)
  return keys
}

/** 分类分组数据源：优先 /overview 的 categories，回落 /categories 的独立结果。 */
const categoryGroups = computed(() => overview.value.categories || categories.value)

/** 后端枚举出的二级分类全名集合，用于判断卡片上推出的分类名是否可信。 */
const knownSubs = computed(() => {
  const keys = new Set()
  for (const category of categoryGroups.value) {
    for (const child of category.children || []) {
      if (!child.loose) keys.add(`${category.path}::${child.name}`)
    }
  }
  return keys
})

/** 卡片与详情上的分类标签：优先后端给出的末级分类名（如 日番），路径推导作兜底。 */
function subLabel(item) {
  const deepest = String(item?.category || '').trim()
  if (deepest) return deepest
  const base = item.category_dir || item.root || ''
  const sub = subOf(item)
  if (sub && knownSubs.value.has(`${base}::${sub}`)) return sub
  return ''
}

/** 卡片上的一句话文件摘要：剧集集数 / 曲目数 / 版本数。 */
function fileSummary(item) {
  if (item.type === 'tv') return `${item.total_episodes || item.total_files} 集 · ${(item.seasons || []).length} 季`
  if (item.type === 'music') return `${item.total_files} 首`
  if (item.multi_version) return `${item.total_files} 版本`
  return '单版本'
}

/**
 * 当前类型下可点击的分类筛选项（一级分类 + 二级分类）。
 *
 * 取 /overview 的 categories：「电视剧」这类一级分类会展开成它下面的二级分类
 * （国产剧 / 国漫 / 日番 / 欧美剧…），同名分类追加父分类名区分。媒体直接躺在
 * 分类目录下（没有二级分类）时，把一级分类本身也做成筛选项，否则这类布局在界面
 * 上完全没有可点的分类入口。只保留该类型确有媒体的项，空分类对筛选没有意义。
 * 「全部」与「音乐」不下钻，返回空数组即不渲染分类行。
 */
const typeChildren = computed(() => {
  const type = typeFilter.value
  if (type !== 'movie' && type !== 'tv') return []
  const children = []
  for (const group of categoryGroups.value) {
    const subs = (group.children || []).filter(child => !child.loose && child[type])
    if (!subs.length) {
      if (!group[type]) continue
      children.push({
        name: group.name,
        path: group.path,
        parent: '',
        parentName: '',
        level: 1,
        [type]: group[type],
        total: group.total,
        unscraped: group.unscraped,
        key: `::${group.name}`,
      })
      continue
    }
    for (const child of subs) {
      children.push({
        ...child,
        parent: group.path,
        parentName: group.name,
        level: 2,
        key: `${group.path}::${child.name}`,
      })
    }
    // 散装在分类目录下、没进任何二级分类的媒体仍留「未分类」入口
    const loose = (group.children || []).find(child => child.loose && child[type])
    if (loose) {
      children.push({
        ...loose,
        parent: group.path,
        parentName: group.name,
        level: 2,
        key: `${group.path}::${loose.name}`,
      })
    }
  }
  for (const child of children) {
    const duplicated = children.filter(entry => entry.name === child.name).length > 1
    child.label = duplicated && child.parentName
      ? `${child.name} · ${child.parentName}`
      : child.name
  }
  return children
})

/** 当前选中的二级分类对象（多选）。 */
const activeSubs = computed(() => {
  const map = new Map(typeChildren.value.map(child => [child.key, child]))
  return subFilters.value.map(key => map.get(key)).filter(Boolean)
})

/**
 * 分类内「待刮」计数，与顶部统计条同口径：按 item.status === 'pending' 聚合。
 * 不用后端 categories.unscraped（那是「目录里存在未刮文件」的口径，已刮目录补了新集
 * 也会计数），否则会出现侧边栏「1 待刮」而统计条「0 待刮」的对不上。
 */
const pendingByCat = computed(() => {
  const map = {}
  for (const item of items.value) {
    if ((item.status || 'pending') !== 'pending') continue
    const key = `${item.type}|${item.category || ''}`
    map[key] = (map[key] || 0) + 1
  }
  return map
})

/**
 * 侧边栏分类树：全部媒体 + 各类型分组（组标题）+ 全部X + 二级分类（单选）。
 *
 * 数据源仍是 /overview 的 categories，但不再按当前 typeFilter 下钻，而是把电影/电视剧
 * （以及有媒体时的音乐）完整平铺，让用户能在侧边栏直接跳任意类型/分类。
 */
const sidebarGroups = computed(() => {
  const groups = []
  const seen = new Set()
  for (const group of categoryGroups.value) {
    const type = group.movie ? 'movie' : group.tv ? 'tv' : group.music ? 'music' : ''
    if (!type) continue
    seen.add(type)
    const children = (group.children || [])
      .filter(child => !child.loose && child[type])
      .map(child => ({
        ...child,
        type,
        parent: group.path,
        parentName: group.name,
        key: `${group.path}::${child.name}`,
        pending: pendingByCat.value[`${type}|${child.name}`] || 0,
      }))
    const loose = (group.children || []).find(child => child.loose && child[type])
    groups.push({
      name: group.name,
      path: group.path,
      type,
      movie: group.movie,
      tv: group.tv,
      music: group.music,
      total: group.total,
      unscraped: group.unscraped,
      children,
      loose: loose ? {
        ...loose, type, parent: group.path, parentName: group.name, key: `${group.path}::${loose.name}`,
      } : null,
    })
  }
  // 音乐兜底：有音乐但后端未产出分类组时，仍给一个类型入口
  if ((overview.value.music || 0) > 0 && !seen.has('music')) {
    groups.push({
      name: '音乐', path: '', type: 'music',
      movie: 0, tv: 0, music: overview.value.music || 0,
      total: overview.value.music || 0, unscraped: 0, children: [], loose: null,
    })
  }
  return groups
})

/** 右侧内容标题：当前选中分类名，无则回退类型名 / 全部媒体。 */
const sectionTitle = computed(() => {
  if (activeSubs.value.length) return activeSubs.value.map(sub => sub.label).join(' / ')
  const tab = TYPE_TABS.find(item => item.key === typeFilter.value)
  return tab ? tab.label : '全部媒体'
})

const visibleItems = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  const subSet = new Set(subFilters.value)
  const childMap = new Map(typeChildren.value.map(child => [child.key, child]))
  return items.value.filter(item => {
    if (typeFilter.value !== 'all' && item.type !== typeFilter.value) return false
    if (statusFilter.value !== 'all' && displayStatus(item) !== statusFilter.value) return false
    if (subSet.size) {
      const keys = categoryKeysOf(item)
      const base = item.category_dir || item.root || ''
      const sub = subOf(item) || '未分类'
      const matched = [...subSet].some(key => {
        const child = childMap.get(key)
        if (!child) return false
        // 二级分类连同父分类一起比对，避免不同父分类下的同名分类互相串台；
        // 一级分类回退项没有父分类（parent 为空串），只按分类名匹配
        if (child.parent && child.parent !== base) return false
        return keys.has(child.name) || sub === child.name
      })
      if (!matched) return false
    }
    if (kw && !`${item.title} ${item.path}`.toLowerCase().includes(kw)) return false
    return true
  })
})

const seasons = computed(() => {
  if (!detail.value || detail.value.type !== 'tv') return []
  const map = new Map()
  for (const file of detail.value.files || []) {
    const key = String(file.season_no ?? 1)
    if (!map.has(key)) {
      map.set(key, {
        no: file.season_no ?? 1,
        name: file.season_name || `Season ${file.season_no ?? 1}`,
        files: [],
      })
    }
    map.get(key).files.push(file)
  }
  return [...map.values()].sort((a, b) => a.no - b.no)
})

const currentSeason = computed(
  () => seasons.value.find(item => String(item.no) === seasonKey.value) || seasons.value[0] || null,
)

const rows = computed(() => {
  if (!current.value) return []
  if (current.value.type === 'tv') return currentSeason.value?.files || []
  // 电影按版本列出，音乐按曲目列出；两者的数据形状一致（后端统一放在 versions 里）
  return (detail.value?.files || []).map(file => ({ ...file, label: versionLabel(file.name) }))
})

const selectedRows = computed(() => rows.value.filter(row => selected.value.has(row.path)))

/** 正在刮削的目标名：目录/文件取路径最后一段，扫描项直接用后端给的标签。 */
const runningLabel = computed(() => {
  const item = queue.value.running
  if (!item) return ''
  const target = String(item.target || '')
  if (item.kind === 'scan') return target
  const parts = target.replace(/[/\\]+$/, '').split(/[/\\]/)
  return parts[parts.length - 1] || target
})

/** 队列项/最近完成项的目标名：目录/文件取路径最后一段，扫描项直接用后端标签。 */
function queueTargetLabel(item) {
  if (!item) return ''
  const target = String(item.target || '')
  if (item.kind === 'scan') return target
  const parts = target.replace(/[/\\]+$/, '').split(/[/\\]/)
  return parts[parts.length - 1] || target
}

/** 状态 → strm-tag 样式类（ok/fail/busy/skip/none），供详情页状态标签与集列表圆点共用。 */
function statusTagClass(status) {
  return `is-${statusClass(status)}`
}

/** 侧边栏：切到「全部媒体」。 */
function selectAllMedia() {
  typeFilter.value = 'all'
  subFilters.value = []
}

/** 侧边栏：只按类型筛选（全部电影 / 全部电视剧 / 全部音乐）。 */
function selectTypeOnly(key) {
  typeFilter.value = key
  subFilters.value = []
}

/** 侧边栏：单选二级分类，再点一次取消。 */
function selectSubCat(child) {
  typeFilter.value = child.type
  if (subFilters.value.includes(child.key)) subFilters.value = []
  else subFilters.value = [child.key]
}

/** 队列项类型 → 中文标签。 */
function kindLabel(kind) {
  if (kind === 'scan') return '扫描'
  if (kind === 'dir') return '目录'
  return '单集'
}

/**
 * 全量加载媒体库。
 *
 * 先带 refresh 强制重扫清单，再取统计与分类：后端清单有 60 秒缓存，若这里不复用同一次
 * 强刷，删除目录后重开弹窗仍会看到已不存在的条目。
 *
 * :param silent: True 时不置位 loading。队列跑完后要重算清单，但整页「正在扫描监控目录…」
 *     闪一下很打断视线，这种情况走静默刷新。
 */
async function loadAll(force = true, silent = false) {
  if (!silent) loading.value = true
  if (!silent) error.value = ''
  try {
    const itemsResponse = await apiCall('get', force ? '/items?refresh=true' : '/items')
    items.value = unwrap(itemsResponse) || []
    const [overviewResponse, categoryResponse] = await Promise.all([
      apiCall('get', '/overview'),
      apiCall('get', '/categories'),
    ])
    overview.value = unwrap(overviewResponse) || {}
    categories.value = unwrap(categoryResponse) || []
  } catch (loadError) {
    error.value = loadError?.message || '加载媒体清单失败'
  } finally {
    if (!silent) loading.value = false
  }
}

/**
 * 顶部「刷新」：只重建清单，不触发任何刮削。
 *
 * 刮削是重活（覆盖重刮等于把每个媒体的 8~9 种图片全部重下），所以刷新刻意保持廉价：
 * 只重扫目录、刷新统计与队列快照。真要刮削一律走「全量扫描」或条目上的刮削按钮，
 * 避免误点一次刷新就把整个媒体库重刮一遍。
 */
async function refreshAll() {
  await loadAll(true)
  await loadQueue()
  if (view.value === 'records') await loadRecords()
}

/**
 * 触发一次扫描并入队，立即返回不阻塞界面。
 *
 * :param overwrite: True=覆盖重刮（重下 NFO 与图片）；False=只补缺失，已有元数据不动
 * :param scoped: True 时只扫当前选中的二级分类目录，False 扫全部监控目录
 */
async function runScan(overwrite, scoped = false) {
  const subs = scoped ? activeSubs.value : []
  if (scoped && !subs.length) return
  scanMenu.value = false
  error.value = ''
  notice.value = ''
  try {
    const query = subs.length
      ? `?scope=category&paths=${encodeURIComponent(subs.map(sub => sub.path).join(','))}&overwrite=${overwrite}`
      : `?overwrite=${overwrite}`
    const result = unwrap(await apiCall('get', `/scan${query}`))
    const label = overwrite ? '覆盖重刮' : '仅补缺失'
    const where = subs.length ? `分类「${subs.map(sub => sub.label).join('、')}」` : '全部监控目录'
    notice.value = result?.queued
      ? `已加入队列：${where} · ${label}`
      : `无需重复入队：${where} 的同类扫描已在队列中`
    await loadQueue()
  } catch (scanError) {
    error.value = scanError?.message || '触发扫描失败'
  }
}

/** 按 scope 触发增量/补漏扫描（B9）：incremental 只补新增，unscraped 只刮未刮。 */
async function runScopedScan(scope) {
  scanMenu.value = false
  error.value = ''
  notice.value = ''
  try {
    const result = unwrap(await apiCall('get', `/scan?scope=${scope}&overwrite=false`))
    const label = scope === 'incremental' ? '增量扫描' : '补漏扫描'
    notice.value = result?.queued ? `已加入队列：${label}` : `无需重复入队：${label}`
    await loadQueue()
  } catch (scanError) {
    error.value = scanError?.message || '触发扫描失败'
  }
}

/** 清空全部筛选（类型/状态/分类/关键词），空态引导用（F11）。 */
function resetFilters() {
  keyword.value = ''
  typeFilter.value = 'all'
  statusFilter.value = 'all'
  subFilters.value = []
}

/** 打开某个媒体的内联详情，并拉取单集/版本明细。 */
async function openDetail(item) {
  current.value = item
  detail.value = null
  selected.value = new Set()
  seasonKey.value = ''
  view.value = 'detail'
  try {
    const response = await apiCall('get', `/files?path=${encodeURIComponent(item.path)}`)
    detail.value = unwrap(response)
    if (item.type === 'tv' && seasons.value.length) {
      seasonKey.value = String(seasons.value[0].no)
    }
  } catch (detailError) {
    error.value = detailError?.message || '读取文件明细失败'
  }
}

/** 关闭详情并回到海报墙。 */
function closeDetail() {
  view.value = 'grid'
  current.value = null
  detail.value = null
  selected.value = new Set()
}

/** 切换视图；进入记录页时按需拉取记录。 */
async function switchView(next) {
  view.value = next
  if (next === 'records') await loadRecords()
}

function toggleRow(path) {
  const next = new Set(selected.value)
  if (next.has(path)) next.delete(path)
  else next.add(path)
  selected.value = next
}

function selectAll() {
  selected.value = new Set(rows.value.map(row => row.path))
}

function selectUnscraped() {
  selected.value = new Set(rows.value.filter(row => !row.scraped).map(row => row.path))
}

function clearSelection() {
  selected.value = new Set()
}

/**
 * 拉取一次队列快照。
 *
 * 失败时保留上一份数据而不是清空：队列接口偶发失败不该让界面从「正在刮削」闪回空闲。
 */
async function loadQueue() {
  try {
    const data = unwrap(await apiCall('get', '/queue'))
    if (data) queue.value = data
  } catch (queueError) {
    // 轮询类请求失败不弹错，下一轮自动重试
  }
}

/**
 * 队列轮询循环：忙时 2s、闲时 8s，队列由忙转闲的那一次顺带重算清单。
 *
 * 文件监控触发的刮削也会出现在这个队列里，所以界面不需要额外的事件推送通道；
 * 只靠这一条轮询就能同时反映「用户点的刮削」和「监控自动刮削」。
 */
async function pollQueue() {
  clearTimeout(queueTimer)
  try {
    const data = unwrap(await apiCall('get', '/queue'))
    if (data) queue.value = data
    const nowBusy = Boolean(queue.value.busy)
    if (queueWasBusy && !nowBusy) {
      // worker 刚清空：被改动的目录要重算清单与统计，但不要闪整页 loading
      await loadAll(true, true)
      if (view.value === 'records') await loadRecords()
    }
    queueWasBusy = nowBusy
  } catch (pollError) {
    // 忽略单次轮询失败，下一轮继续
  }
  queueTimer = setTimeout(pollQueue, queue.value.busy ? POLL_BUSY_MS : POLL_IDLE_MS)
}

/** 启动常驻队列轮询（幂等）：重复调用不会叠出两条循环。 */
function startQueuePolling() {
  clearTimeout(queueTimer)
  queueWasBusy = Boolean(queue.value.busy)
  pollQueue()
}

/**
 * 提交刮削并入队。target=dir 走目录级（递归整棵子树），target=file 走单集/单版本。
 *
 * 手动操作一律带 overwrite:true —— 与宿主原生手动刮削一致，点一次就把目标范围内
 * 已存在的 NFO 与图片重下一遍，不依赖配置页的「覆盖已有元数据」开关（那个只决定
 * 全量扫描的默认值与重试失败项）。入队后立即拉一次队列快照，让进度提示即时出现。
 */
async function postScrape(paths, target) {
  if (!paths.length) return
  error.value = ''
  notice.value = ''
  try {
    const result = unwrap(await apiCall('post', '/scrape', { paths, target, overwrite: true }))
    const queued = result?.queued || 0
    const deduped = result?.deduped || 0
    notice.value = queued
      ? `已加入刮削队列：${queued} 项${deduped ? `，${deduped} 项已在队列中` : ''}`
      : '目标已在刮削队列中，未重复入队'
    await loadQueue()
  } catch (submitError) {
    error.value = submitError?.message || '提交刮削失败'
  }
}

function scrapeSelected() {
  postScrape(selectedRows.value.map(row => row.path), 'file')
}

function scrapeWhole() {
  if (!current.value) return
  postScrape([current.value.path], 'dir')
}

function scrapeOne(row) {
  postScrape([row.path], 'file')
}

function scrapeItem(item) {
  postScrape([item.path], 'dir')
}

/** 读取刮削记录，支持状态/类型/分类/时间范围筛选（F10）。 */
async function loadRecords() {
  recordsLoading.value = true
  try {
    const query = new URLSearchParams()
    query.set('limit', '200')
    if (recordFilter.success) query.set('success', recordFilter.success)
    if (recordFilter.type) query.set('type', recordFilter.type)
    if (recordFilter.category) query.set('category', recordFilter.category)
    if (recordFilter.since) query.set('since', recordFilter.since)
    if (recordFilter.until) query.set('until', recordFilter.until)
    records.value = unwrap(await apiCall('get', `/records?${query.toString()}`)) || []
  } catch (recordsError) {
    error.value = recordsError?.message || '读取刮削记录失败'
  } finally {
    recordsLoading.value = false
  }
}

/** 清空刮削记录文件。 */
async function clearRecords() {
  try {
    unwrap(await apiCall('get', '/records/clear'))
    records.value = []
    notice.value = '刮削记录已清空'
  } catch (clearError) {
    error.value = clearError?.message || '清空刮削记录失败'
  }
}

/** 记录里出现过的分类名（去重排序），供记录页分类下拉（F10）。 */
const recordCategories = computed(() => {
  const set = new Set()
  for (const record of records.value) {
    if (record.category) set.add(record.category)
  }
  return [...set].sort()
})

/** 是否已设任何记录筛选（决定「重置」按钮是否显示）。 */
const hasRecordFilter = computed(() =>
  Boolean(recordFilter.success || recordFilter.type || recordFilter.category || recordFilter.since || recordFilter.until),
)

/** 清空记录筛选并重新拉取。 */
function resetRecordFilter() {
  recordFilter.success = ''
  recordFilter.type = ''
  recordFilter.category = ''
  recordFilter.since = ''
  recordFilter.until = ''
  loadRecords()
}

/** 批量重试失败项（F10 顶部「重试所有失败」）。 */
async function retryFailed() {
  error.value = ''
  notice.value = ''
  try {
    const result = unwrap(await apiCall('post', '/retry_failed', {}))
    notice.value = result?.queued ? `已重试 ${result.queued} 个失败项` : '没有可重试的失败项'
    await loadQueue()
  } catch (retryError) {
    error.value = retryError?.message || '重试失败项失败'
  }
}

/** 取消队列项：target 为空时取消全部，否则只取消该目标（F8）。 */
async function cancelQueue(target = '') {
  const mode = target ? 'one' : 'all'
  try {
    const result = unwrap(await apiCall('post', '/queue/cancel', { mode, target }))
    notice.value = result?.canceled ? `已取消 ${result.canceled} 项` : '队列已清空'
    await loadQueue()
  } catch (cancelError) {
    error.value = cancelError?.message || '取消失败'
  }
}

/** 设置入口：交给主应用打开插件配置弹窗，不自行渲染配置表单。 */
function openSettings() {
  emit('switch')
}

/** 根节点引用：用于计算视口剩余高度，实现「头部/侧边栏固定、仅海报区滚动」。 */
const rootEl = ref(null)

/**
 * 让插件页占满视口剩余高度（--strm-h 交给 CSS 布局）。
 * 宿主头部高度未知，只能运行时测量组件顶边到视口底部的距离；
 * 挂载后连测多次以兜住宿主布局/字体晚加载造成的偏移。
 */
function syncRootHeight() {
  const el = rootEl.value
  if (!el) return
  const height = Math.floor(window.innerHeight - el.getBoundingClientRect().top)
  if (height > 320) el.style.setProperty('--strm-h', `${height}px`)
  else el.style.removeProperty('--strm-h')
}

onMounted(() => {
  // 详情弹窗默认 80rem，海报墙需要更宽的可视区
  emit('layout', { maxWidth: '96rem' })
  syncRootHeight()
  requestAnimationFrame(syncRootHeight)
  setTimeout(syncRootHeight, 350)
  window.addEventListener('resize', syncRootHeight)
  loadAll(true)
  loadQueue()
  startQueuePolling()
})

onBeforeUnmount(() => {
  clearTimeout(queueTimer)
  window.removeEventListener('resize', syncRootHeight)
})
</script>

<template>
  <div ref="rootEl" class="strm">
    <header class="strm-head">
      <div class="strm-logo">S</div>
      <div class="strm-heading">
        <div class="strm-crumb">STRM 监控刮削<template v-if="view !== 'records'"> / <b>{{ sectionTitle }}</b></template></div>
        <h1>{{ view === 'records' ? '刮削记录' : (view === 'detail' ? '媒体详情' : '媒体库') }}</h1>
      </div>
      <div class="strm-seg">
        <button :class="['strm-seg-item', view !== 'records' && 'active']" @click="switchView('grid')">媒体库</button>
        <button :class="['strm-seg-item', view === 'records' && 'active']" @click="switchView('records')">刮削记录</button>
      </div>
      <div class="strm-head-chips">
        <span :class="['strm-chip', overview.monitoring ? 'is-ok' : 'is-muted']">
          <i class="strm-chip-dot"></i>{{ overview.monitoring ? '监控运行中' : '监控未启动' }}
        </span>
        <!-- 队列状态 chip：可点击展开队列面板（F8），不再单独开 Tab -->
        <button :class="['strm-chip', 'is-queue', busy && 'is-busy']" @click="queuePanelOpen = !queuePanelOpen">
          <template v-if="busy">队列处理中 · 排队 {{ queue.queued_total || 0 }}</template>
          <template v-else>队列空闲</template>
        </button>
      </div>
      <div class="strm-head-actions">
        <button v-if="props.show_switch" class="strm-btn ghost" @click="openSettings">设置</button>
        <button class="strm-btn ghost" :disabled="loading || busy" @click="refreshAll">刷新</button>
        <!-- 全量扫描的唯一入口：是否覆盖交给下拉选，避免「全量扫描 / 强制全量」两个按钮
             只差一个布尔值却并排出现，用户极易点错并触发全库重刮 -->
        <div class="strm-scan" @mouseleave="scanMenu = false">
          <button class="strm-btn" :disabled="busy" @click="scanMenu = !scanMenu">
            全量扫描 <span :class="['strm-caret', scanMenu && 'is-open']">▼</span>
          </button>
          <div v-show="scanMenu" class="strm-scan-menu">
            <button @click="runScan(false)">
              <b>仅补缺失</b><em>跳过已有元数据，只刮未刮的</em>
            </button>
            <button @click="runScan(true)">
              <b>覆盖重刮</b><em>重下全部 NFO 与图片</em>
            </button>
            <button @click="runScopedScan('incremental')">
              <b>增量扫描</b><em>只补新增与未刮的目录</em>
            </button>
            <button @click="runScopedScan('unscraped')">
              <b>补漏扫描</b><em>只刮状态非「已刮」的目录</em>
            </button>
            <template v-if="activeSubs.length">
              <div class="strm-scan-sep">仅分类「{{ activeSubs.map(sub => sub.label).join('、') }}」</div>
              <button @click="runScan(false, true)"><b>仅补缺失</b></button>
              <button @click="runScan(true, true)"><b>覆盖重刮</b></button>
            </template>
          </div>
        </div>
        <button class="strm-close" title="关闭" @click="emit('close')">×</button>
      </div>
    </header>

    <div :class="['strm-body', view === 'grid' && 'is-grid']">
      <div v-if="error" class="strm-alert is-error">
        <span>{{ error }}</span>
        <button class="strm-mini" @click="error = ''">知道了</button>
      </div>
      <div v-else-if="notice" class="strm-alert is-ok">
        <span>{{ notice }}</span>
        <button class="strm-mini" @click="notice = ''">知道了</button>
      </div>

      <!-- F8 队列面板：唯一反映「后台正在干什么」的地方。文件监控自动触发的刮削与
           用户点出来的刮削都在这里，点顶部队列 chip 或此状态行展开/收起详情 -->
      <div :class="['strm-queue', queuePanelOpen && 'is-open']">
        <button class="strm-queue-bar" @click="queuePanelOpen = !queuePanelOpen">
          <span :class="['strm-queue-state', busy ? 'is-busy' : 'is-idle']"></span>
          <span class="strm-task-text">
            <template v-if="busy">
              <template v-if="queue.running && queue.running.kind === 'scan'">
                正在枚举 {{ queueTargetLabel(queue.running) }}
              </template>
              <template v-else-if="queue.running">正在刮削 {{ runningLabel }}</template>
              <template v-else>等待队列调度</template>
              <template v-if="queue.queued_total"> · 排队 {{ queue.queued_total }}</template>
            </template>
            <template v-else>队列空闲</template>
          </span>
          <span :class="['strm-task-bar', busy && 'is-live']"><i></i></span>
          <span :class="['strm-caret', queuePanelOpen && 'is-open']">▼</span>
        </button>

        <div v-if="queuePanelOpen" class="strm-queue-panel">
          <!-- 统计胶囊行：与顶部三卡片同一语言（彩色圆点 + 等宽数字） -->
          <div class="strm-queue-stats">
            <div class="strm-queue-stat is-ok"><i></i><b>{{ queue.stats.done || 0 }}</b><span>成功</span></div>
            <div class="strm-queue-stat is-fail"><i></i><b>{{ queue.stats.failed || 0 }}</b><span>失败</span></div>
            <div class="strm-queue-stat is-cancel"><i></i><b>{{ queue.stats.canceled || 0 }}</b><span>已取消</span></div>
            <div class="strm-queue-stat is-pend"><i></i><b>{{ queue.queued_total || 0 }}</b><span>排队</span></div>
          </div>

          <div v-if="queue.running" class="strm-queue-sec">
            <span class="strm-queue-sec-title">正在执行</span>
            <div class="strm-queue-item is-busy">
              <span class="strm-queue-dot is-busy"></span>
              <span class="strm-queue-target">{{ queueTargetLabel(queue.running) }}</span>
              <span class="strm-queue-kind">{{ kindLabel(queue.running.kind) }}</span>
            </div>
          </div>

          <div v-if="queue.queued && queue.queued.length" class="strm-queue-sec">
            <span class="strm-queue-sec-title">排队中（{{ queue.queued_total }}）</span>
            <div v-for="item in queue.queued" :key="item.key" class="strm-queue-item">
              <span class="strm-queue-dot is-pend"></span>
              <span class="strm-queue-target">{{ queueTargetLabel(item) }}</span>
              <span class="strm-queue-kind">{{ kindLabel(item.kind) }}</span>
              <button class="strm-mini" @click="cancelQueue(item.target)">取消</button>
            </div>
          </div>

          <div v-if="queue.recent && queue.recent.length" class="strm-queue-sec">
            <span class="strm-queue-sec-title">最近完成</span>
            <div v-for="(item, index) in queue.recent.slice(0, 6)" :key="`${item.time}-${index}`" class="strm-queue-item">
              <span :class="['strm-queue-dot', item.success ? 'is-ok' : 'is-fail']"></span>
              <span class="strm-queue-target">{{ queueTargetLabel(item) }}</span>
              <span v-if="!item.success" class="strm-queue-err" :title="item.message">{{ item.message }}</span>
            </div>
          </div>

          <div class="strm-queue-actions">
            <button class="strm-btn small ghost" :disabled="!statusCounts.failed" @click="retryFailed">
              重试失败项（{{ statusCounts.failed }}）
            </button>
            <button class="strm-btn small ghost" :disabled="!busy" @click="cancelQueue()">取消全部</button>
          </div>
        </div>
      </div>

      <!-- ============================ 媒体库 ============================ -->
      <template v-if="view === 'grid'">
        <div class="strm-grid-wrap">
          <!-- 左侧固定分类侧边栏：全部媒体 + 各类型分组 + 二级分类（单选） -->
          <aside class="strm-side">
            <div class="strm-side-head">分类筛选</div>
            <nav class="strm-side-nav">
              <button :class="['strm-sopt', typeFilter === 'all' && !subFilters.length && 'on']" @click="selectAllMedia">
                <span>全部媒体</span><em>{{ overview.total || 0 }}</em>
              </button>
              <template v-for="group in sidebarGroups" :key="group.path || group.name">
                <div class="strm-sgroup">{{ group.name }}</div>
                <button :class="['strm-sopt', typeFilter === group.type && !subFilters.length && 'on']" @click="selectTypeOnly(group.type)">
                  <span>全部{{ group.name }}</span><em>{{ group[group.type] || 0 }}</em>
                </button>
                <button
                  v-for="child in group.children"
                  :key="child.key"
                  :class="['strm-sopt', 'is-child', typeFilter === group.type && subFilters.includes(child.key) && 'on']"
                  :title="child.pending ? `${child.pending} 项待刮削` : child.path"
                  @click="selectSubCat(child)"
                >
                  <span>{{ child.name }}</span><em>{{ child[group.type] || 0 }}</em>
                  <b v-if="child.pending">{{ child.pending }} 待刮</b>
                </button>
                <button
                  v-if="group.loose"
                  :class="['strm-sopt', 'is-child', typeFilter === group.type && subFilters.includes(group.loose.key) && 'on']"
                  @click="selectSubCat(group.loose)"
                >
                  <span>{{ group.loose.name }}</span><em>{{ group.loose[group.type] || 0 }}</em>
                </button>
              </template>
            </nav>
          </aside>

          <!-- 右侧内容：随页面滚动 -->
          <div class="strm-content">
            <!-- 统计：只保留 待刮削 / 已刮 / 失败（可点击筛选） -->
            <div class="strm-stats">
              <button
                v-for="sf in STATUS_FILTERS"
                :key="sf.key"
                :class="['strm-stat', `is-${sf.key}`, statusFilter === sf.key && 'active']"
                @click="statusFilter = statusFilter === sf.key ? 'all' : sf.key"
              >
                <b>{{ statusCounts[sf.key] }}</b>
                <span>{{ sf.label }}</span>
                <i class="strm-stat-dot"></i>
              </button>
            </div>

            <!-- 标题 + 搜索 + 计数 -->
            <div class="strm-toolbar">
              <h2 class="strm-section-title">{{ sectionTitle }}</h2>
              <input v-model="keyword" class="strm-search" type="text" placeholder="搜索标题或路径">
              <span class="strm-count">共 {{ visibleItems.length }} 项</span>
            </div>

            <!-- 加载态：骨架卡网格（shimmer），比一行文字更能预告即将出现的内容 -->
            <div v-if="loading" class="strm-skel-grid">
              <div v-for="n in 10" :key="n" class="strm-skel">
                <div class="strm-skel-poster"></div>
                <div class="strm-skel-line"></div>
                <div class="strm-skel-line is-short"></div>
              </div>
            </div>
            <!-- F11 空态引导：区分「库里没东西」与「筛选后没结果」两种，都给出下一步 -->
            <div v-else-if="!items.length" class="strm-empty">
              <p>监控目录里还没有任何媒体</p>
              <p class="strm-empty-hint">把 .strm 文件放进监控目录后，点右上角「刷新」即可识别</p>
              <button class="strm-btn ghost" @click="refreshAll">立即刷新</button>
            </div>
            <div v-else-if="!visibleItems.length" class="strm-empty">
              <p>没有匹配的媒体</p>
              <p class="strm-empty-hint">当前类型 / 状态 / 分类 / 关键词组合下没有结果</p>
              <button class="strm-mini" @click="resetFilters">清除筛选</button>
            </div>
            <div v-else class="strm-grid">
              <!-- Plex 式海报卡：整卡可点进详情，hover 浮层承载元信息与操作，海报是第一主角 -->
              <div v-for="item in visibleItems" :key="item.path" class="strm-card" @click="openDetail(item)">
                <div class="strm-poster" :style="posterStyle(item.title)">
                  <img
                    v-if="!posterFailed[item.path]"
                    class="strm-poster-img"
                    :src="coverUrl(props.api, props.pluginId, item)"
                    :alt="item.title"
                    loading="lazy"
                    @error="markPosterFailed(item.path)"
                  >
                  <span :class="['strm-type', typeClass(item.type)]">{{ typeLabel(item.type) }}</span>
                  <!-- F4 状态圆点四态：scraped/failed/skipped/pending，叠加队列「刮削中」 -->
                  <span
                    :class="['strm-dot', `is-${statusClass(displayStatus(item))}`]"
                    :title="STATUS_LABELS[displayStatus(item)]"
                  ></span>
                  <!-- 失败红条：海报底部直接标出原因 -->
                  <div v-if="displayStatus(item) === 'failed'" class="strm-fail-strip" :title="item.error_message">
                    ⚠ {{ errorLabel(item.error_code) }}
                  </div>
                  <!-- hover 浮层 -->
                  <div class="strm-overlay">
                    <div class="strm-overlay-title">{{ item.title }}</div>
                    <div class="strm-overlay-meta">{{ subLabel(item) || item.category }} · {{ fileSummary(item) }}</div>
                    <div class="strm-overlay-status">
                      <i :class="['strm-overlay-dot', `is-${statusClass(displayStatus(item))}`]"></i>
                      {{ STATUS_LABELS[displayStatus(item)] }}
                    </div>
                    <div class="strm-overlay-actions">
                      <button class="strm-overlay-btn" @click.stop="openDetail(item)">
                        {{ item.type === 'tv' ? '剧集预览' : (item.type === 'music' ? '曲目列表' : '版本预览') }}
                      </button>
                      <button class="strm-overlay-btn is-primary" :disabled="busy" @click.stop="scrapeItem(item)">整目录重刮</button>
                    </div>
                  </div>
                </div>
                <div class="strm-card-title" :title="item.path">{{ item.title }}</div>
                <div class="strm-card-sub">{{ subLabel(item) || item.category }} · {{ fileSummary(item) }}</div>
              </div>
            </div>
          </div>
        </div>
      </template>

      <!-- ============================ 内联详情 ============================ -->
      <template v-else-if="view === 'detail' && current">
        <div class="strm-detail">
          <!-- Hero：Plex 式沉浸横幅 —— 海报当背景氛围层，主色渐变 + 底部压暗做底，
               前景左侧浮大张海报（2:3）、右侧大标题与元信息，主 CTA 直接落在横幅里。
               横幅恒定深色（与设计稿一致），所以内部文字用白色系，不跟随宿主主题 -->
          <div class="strm-dhero">
            <img
              v-if="!posterFailed[current.path]"
              class="strm-dhero-bg"
              :src="coverUrl(props.api, props.pluginId, current)"
              alt=""
              aria-hidden="true"
            >
            <span class="strm-dhero-scrim"></span>

            <button class="strm-back" @click="closeDetail">
              <span class="strm-back-arrow">←</span>返回媒体库
            </button>

            <div class="strm-dhero-body">
              <div class="strm-dhero-poster" :style="posterStyle(current.title)">
                <img
                  v-if="!posterFailed[current.path]"
                  class="strm-poster-img"
                  :src="coverUrl(props.api, props.pluginId, current)"
                  :alt="current.title"
                  @error="markPosterFailed(current.path)"
                >
              </div>
              <div class="strm-dhero-info">
                <div class="strm-dhero-kicker">
                  {{ typeLabel(current.type) }}<template v-if="subLabel(current)"> · {{ subLabel(current) }}</template>
                </div>
                <h2>{{ current.title }}</h2>
                <div class="strm-dhero-tags">
                  <span class="strm-tag is-plain">{{ current.total_files }} 个文件</span>
                  <span :class="['strm-tag', statusTagClass(displayStatus(current))]" :title="STATUS_LABELS[displayStatus(current)]">
                    <i class="strm-status-dot"></i>{{ STATUS_LABELS[displayStatus(current)] }}
                  </span>
                  <span v-if="displayStatus(current) === 'failed' && current.error_code" class="strm-tag is-fail" :title="current.error_message">
                    {{ errorLabel(current.error_code) }}
                  </span>
                </div>
                <!-- F6 两行时间：文件最后变更 与 真实刮削时间分开显示 -->
                <div class="strm-dhero-times">
                  <span><b>文件更新</b>{{ formatDate(current.last_file_change) }}</span>
                  <span class="strm-sep">·</span>
                  <span><b>上次刮削</b>{{ formatTime(current.last_scrape) }}</span>
                </div>
                <div class="strm-dhero-path" :title="current.path">{{ current.path }}</div>
              </div>
              <div class="strm-dhero-actions">
                <button class="strm-btn" :disabled="busy" @click="scrapeWhole">
                  {{ current.type === 'tv' ? '整剧重新刮削' : '整目录重新刮削' }}
                </button>
              </div>
            </div>
          </div>

          <div v-if="current.type === 'tv'" class="strm-season-bar">
            <span class="strm-season-label">SEASON</span>
            <button
              v-for="season in seasons"
              :key="season.no"
              :class="['strm-season-chip', String(season.no) === seasonKey && 'active']"
              @click="seasonKey = String(season.no)"
            >{{ season.name }}<em>{{ season.files.length }}</em></button>
          </div>

          <div class="strm-list-actions">
            <button class="strm-mini" @click="selectAll">全选本页</button>
            <button class="strm-mini" @click="selectUnscraped">选中未刮削</button>
            <button class="strm-mini" @click="clearSelection">清空</button>
            <span class="strm-list-sum">
              共 <b>{{ rows.length }}</b> 项<template v-if="selectedRows.length"> · 已选 <b>{{ selectedRows.length }}</b></template>
            </span>
          </div>

          <div class="strm-list">
            <div v-if="!rows.length" class="strm-empty">暂无文件</div>
            <div
              v-for="row in rows"
              :key="row.path"
              :class="['strm-row', `is-${statusClass(displayStatus(row))}`, selected.has(row.path) && 'is-selected']"
            >
              <span class="strm-row-bar"></span>
              <input type="checkbox" :checked="selected.has(row.path)" @change="toggleRow(row.path)">
              <span v-if="current.type === 'tv'" class="strm-row-no">
                E{{ String(row.episode ?? 0).padStart(2, '0') }}
              </span>
              <span v-else class="strm-row-no is-plain">{{ row.label }}</span>
              <span class="strm-row-name" :title="row.name">{{ row.name }}</span>
              <span class="strm-row-size">{{ formatSize(row.size) }}</span>
              <span
                :class="['strm-row-state', `is-${statusClass(displayStatus(row))}`]"
                :title="row.error_message || STATUS_LABELS[displayStatus(row)]"
              >
                <i></i>{{ STATUS_LABELS[displayStatus(row)] }}
              </span>
              <button
                class="strm-row-go"
                :disabled="busy"
                title="重下该条目的 NFO 与图片（覆盖）"
                @click="scrapeOne(row)"
              >刮削</button>
            </div>
          </div>

          <!-- 收尾操作条：只保留「按选中执行」，整目录/整剧的主 CTA 已上移到 Hero，
               避免同一个动作在上下两处重复出现 -->
          <div class="strm-detail-foot">
            <span class="strm-sel">已选 <b>{{ selectedRows.length }}</b> 项</span>
            <div class="strm-foot-right">
              <button class="strm-btn ghost" :disabled="busy || !selectedRows.length" @click="scrapeSelected">
                刮削选中{{ current.type === 'tv' ? '单集' : (current.type === 'music' ? '曲目' : '版本') }}
              </button>
            </div>
          </div>
        </div>
      </template>

      <!-- ============================ 刮削记录 ============================ -->
      <template v-else-if="view === 'records'">
        <div class="strm-rec-head">
          <span class="strm-rec-count">最近 <b>{{ records.length }}</b> 条</span>
          <div class="strm-foot-right">
            <button class="strm-btn ghost" :disabled="recordsLoading" @click="loadRecords">刷新记录</button>
            <button class="strm-btn ghost" :disabled="!records.length" @click="clearRecords">清空记录</button>
          </div>
        </div>

        <!-- F10 记录筛选：状态 / 类型 / 分类 / 时间范围，改完即重新拉取 -->
        <div class="strm-rec-filter">
          <select v-model="recordFilter.success" @change="loadRecords">
            <option value="">全部结果</option>
            <option value="true">仅成功</option>
            <option value="false">仅失败</option>
          </select>
          <select v-model="recordFilter.type" @change="loadRecords">
            <option value="">全部类型</option>
            <option value="dir">目录</option>
            <option value="file">单集</option>
          </select>
          <select v-model="recordFilter.category" @change="loadRecords">
            <option value="">全部分类</option>
            <option v-for="cat in recordCategories" :key="cat" :value="cat">{{ cat }}</option>
          </select>
          <input v-model="recordFilter.since" type="date" @change="loadRecords" title="起始日期">
          <span class="strm-rec-filter-sep">至</span>
          <input v-model="recordFilter.until" type="date" @change="loadRecords" title="结束日期">
          <button v-if="hasRecordFilter" class="strm-mini" @click="resetRecordFilter">重置</button>
          <button class="strm-btn small ghost" :disabled="!statusCounts.failed" @click="retryFailed">
            重试失败项（{{ statusCounts.failed }}）
          </button>
        </div>

        <div v-if="recordsLoading" class="strm-empty">正在读取记录…</div>
        <div v-else-if="!records.length" class="strm-empty">
          <p>{{ hasRecordFilter ? '没有符合筛选条件的记录' : '暂无刮削记录' }}</p>
          <button v-if="hasRecordFilter" class="strm-mini" @click="resetRecordFilter">清除筛选</button>
        </div>
        <!-- 时间轴式记录列表：左侧状态点 + 标题/标签一行 + 消息一行，时间与结果徽章靠右 -->
        <div v-else class="strm-rec-list">
          <div
            v-for="(record, index) in records"
            :key="`${record.time}-${index}`"
            :class="['strm-rec', record.success ? 'is-ok' : 'is-fail']"
          >
            <span class="strm-rec-dot"></span>
            <div class="strm-rec-main">
              <div class="strm-rec-top">
                <span class="strm-rec-title" :title="record.target">{{ record.title }}</span>
                <span v-if="record.category" class="strm-tag is-plain">{{ record.category }}</span>
                <span v-if="record.exists === false" class="strm-tag is-warn">目录已删除</span>
                <!-- F9 错误码中文映射：失败记录标出结构化原因 -->
                <span v-if="!record.success && record.error_code" class="strm-tag is-fail" :title="record.message">
                  {{ errorLabel(record.error_code) }}
                </span>
              </div>
              <div v-if="record.message" class="strm-rec-msg" :title="record.message">{{ record.message }}</div>
            </div>
            <span :class="['strm-rec-badge', record.success ? 'is-ok' : 'is-fail']">
              {{ record.success ? '成功' : '失败' }}
            </span>
            <span class="strm-rec-time">{{ record.time }}</span>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.strm {
  width: 100%;
  /* --strm-h 由 syncRootHeight 运行时注入（视口高度 - 组件顶边）：
     有值时整页锁高，头部/侧边栏固定、只有海报区滚动；无值时回退页面自然滚动 */
  height: var(--strm-h, auto);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  color: rgb(var(--v-theme-on-surface, 27, 29, 41));
  font-family: inherit;
}
.strm-head {
  position: relative;
  z-index: 30; /* 高于海报卡/浮层（z-index 2），扫描下拉不被遮挡 */
  display: flex;
  align-items: center;
  gap: 13px;
  flex-wrap: wrap;
  padding: 14px 22px 13px;
  background: rgb(var(--v-theme-surface, 255, 255, 255));
  border-bottom: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .10);
}
.strm-crumb {
  font-size: 11px; font-weight: 500; line-height: 1.3;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .5));
}
.strm-crumb b { color: rgb(var(--v-theme-primary, 124, 92, 252)); font-weight: 700; }
.strm-logo {
  width: 40px; height: 40px; border-radius: 12px; flex-shrink: 0;
  background: rgb(var(--v-theme-primary, 124, 92, 252));
  color: rgb(var(--v-theme-on-primary, 255, 255, 255));
  display: flex; align-items: center; justify-content: center; font-size: 19px; font-weight: 700;
}
.strm-heading h1 { font-size: 19px; font-weight: 700; margin: 0; }
.strm-heading p {
  font-size: 12.5px; margin: 2px 0 0;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .62));
}
.strm-head-chips { display: flex; gap: 8px; flex-wrap: wrap; margin-left: 8px; }
.strm-chip {
  border-radius: 999px; padding: 4px 11px; font-size: 12px;
  background: rgba(var(--v-theme-on-surface, 27, 29, 41), .06);
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .7));
}
.strm-chip-dot {
  display: inline-block; width: 7px; height: 7px; border-radius: 50%;
  background: currentColor; margin-right: 6px; vertical-align: 1px;
}
.strm-chip.is-ok { background: rgba(22, 163, 74, .12); color: #16A34A; }
.strm-chip.is-ok .strm-chip-dot { animation: strm-pulse 2s ease-in-out infinite; }
.strm-chip.is-warn { background: rgba(234, 138, 31, .14); color: #EA8A1F; }
.strm-chip.is-queue { border: none; cursor: pointer; font-family: inherit; }
.strm-chip.is-busy { background: rgba(234, 138, 31, .14); color: #EA8A1F; }
.strm-head-actions { margin-left: auto; display: flex; gap: 8px; align-items: center; }
.strm-close {
  width: 30px; height: 30px; border-radius: 9px; flex-shrink: 0; cursor: pointer;
  font-size: 18px; line-height: 1;
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .14);
  background: transparent; color: inherit;
}
.strm-body {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 16px 22px 26px;
}
/* 媒体库视图：body 不再自身滚动，把滚动权下放给右栏内容 */
.strm-body.is-grid {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* 媒体库双栏：左侧固定分类侧边栏 + 右侧内容（仅右栏滚动） */
.strm-grid-wrap {
  flex: 1 1 auto;
  min-height: 0;
  display: grid;
  grid-template-columns: 232px minmax(0, 1fr);
  align-items: stretch;
}
.strm-side {
  min-height: 0;
  overflow-y: auto;
  padding: 8px 10px 18px;
  /* 边界感：侧栏垫一层浅底色 + 分隔线，与内容区明确分区 */
  background: rgba(var(--v-theme-on-surface, 27, 29, 41), .035);
  border-right: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .10);
  border-radius: 12px 0 0 0;
}
.strm-side-head {
  font-size: 11px; letter-spacing: .6px; padding: 4px 12px 10px;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .5));
}
.strm-sgroup {
  font-size: 11px; letter-spacing: .4px; padding: 14px 12px 6px;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .5));
}
.strm-sopt {
  display: flex; align-items: center; gap: 10px; width: 100%;
  padding: 8px 12px; border: none; border-radius: 9px;
  background: transparent; cursor: pointer; text-align: left; font-family: inherit; font-size: 13px;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .72));
  box-shadow: inset 2px 0 0 transparent;
  transition: background .12s, color .12s;
}
.strm-sopt:hover {
  background: rgba(var(--v-theme-primary, 124, 92, 252), .09);
  color: rgb(var(--v-theme-on-surface, 27, 29, 41));
}
.strm-sopt.on {
  background: linear-gradient(135deg, rgb(var(--v-theme-primary, 124, 92, 252)), color-mix(in srgb, rgb(var(--v-theme-primary, 124, 92, 252)) 62%, #fff));
  color: #fff; font-weight: 600;
  box-shadow: 0 10px 22px -10px rgba(var(--v-theme-primary, 124, 92, 252), .72);
}
.strm-sopt em { margin-left: auto; font-style: normal; font-size: 11px; font-weight: 500; opacity: .7; }
.strm-sopt.on em { opacity: .88; color: #fff; }
.strm-sopt b { font-weight: 700; font-size: 10.5px; color: #B45309; background: rgba(234, 138, 31, .16); padding: 1px 7px; border-radius: 99px; }
.strm-sopt.on b { background: rgba(255, 255, 255, .24); color: #fff; }
.strm-sopt.is-child { padding-left: 28px; font-size: 12.5px; font-weight: 400; }
.strm-content {
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  padding: 2px 8px 24px 20px;
}
.strm-section-title { font-size: 16px; font-weight: 700; margin: 0; flex: 0 0 auto; white-space: nowrap; }

.strm-btn {
  padding: 8px 15px; border-radius: 10px; font-size: 13px; font-weight: 600; font-family: inherit;
  cursor: pointer; border: none; transition: transform .15s, box-shadow .15s, filter .15s;
  background: rgb(var(--v-theme-primary, 124, 92, 252));
  color: rgb(var(--v-theme-on-primary, 255, 255, 255));
}
/* hover 上浮 + 同色投影，按压时回落：给按钮物理反馈 */
.strm-btn:hover:not(:disabled) { filter: brightness(1.08); transform: translateY(-1px); box-shadow: 0 6px 16px rgba(var(--v-theme-primary, 124, 92, 252), .32); }
.strm-btn:active:not(:disabled) { transform: translateY(0); box-shadow: none; }
.strm-btn:disabled { opacity: .45; cursor: not-allowed; filter: none; }
.strm-btn.ghost {
  background: rgb(var(--v-theme-surface, 255, 255, 255));
  color: rgb(var(--v-theme-primary, 124, 92, 252));
  border: 1px solid rgba(var(--v-theme-primary, 124, 92, 252), .35);
}
.strm-btn.ghost:hover:not(:disabled) { box-shadow: 0 6px 16px rgba(var(--v-theme-primary, 124, 92, 252), .18); }
.strm-btn.small { flex: 1; padding: 7px 8px; font-size: 12px; }

/* 全量扫描下拉：把「是否覆盖」从两个并排按钮收进一个菜单，降低误点全库重刮的概率。
   padding-bottom 桥接按钮与菜单之间的 6px 空隙（负 margin 抵消布局影响），
   否则鼠标从按钮滑向菜单必经空隙触发 @mouseleave，菜单秒关 */
.strm-scan { position: relative; padding-bottom: 6px; margin-bottom: -6px; }
.strm-scan-menu {
  position: absolute; top: 100%; right: 0; z-index: 5;
  min-width: 220px; padding: 6px;
  display: flex; flex-direction: column; gap: 2px;
  border-radius: 12px;
  background: rgb(var(--v-theme-surface, 255, 255, 255));
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .14);
  box-shadow: 0 12px 32px rgba(10, 11, 20, .18);
}
.strm-scan-menu button {
  display: flex; flex-direction: column; gap: 2px; align-items: flex-start;
  padding: 8px 11px; border: none; border-radius: 9px; cursor: pointer;
  font-size: 13px; font-family: inherit; text-align: left;
  background: transparent; color: rgb(var(--v-theme-on-surface, 27, 29, 41));
}
.strm-scan-menu button:hover { background: rgba(var(--v-theme-primary, 124, 92, 252), .10); }
.strm-scan-menu button b { font-weight: 600; }
.strm-scan-menu button em {
  font-style: normal; font-size: 11.5px;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .55));
}
.strm-scan-sep {
  margin: 5px 4px 3px; padding-top: 6px; font-size: 11px;
  border-top: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .10);
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .6));
}

.strm-alert {
  display: flex; align-items: center; gap: 10px;
  flex-shrink: 0; /* 同队列条：flex 列容器里不能被压扁 */
  border-radius: 10px; padding: 10px 14px; font-size: 13px; margin-bottom: 14px;
}
.strm-alert span { flex: 1; min-width: 0; }
.strm-alert.is-error { background: rgba(229, 72, 77, .12); color: #E5484D; }
.strm-alert.is-ok { background: rgba(22, 163, 74, .12); color: #16A34A; }

.strm-task {
  display: flex; align-items: center; gap: 12px; margin-bottom: 14px;
  padding: 10px 14px; border-radius: 10px;
  background: rgba(var(--v-theme-primary, 124, 92, 252), .10);
}
.strm-task-text { font-size: 12.5px; white-space: nowrap; }
.strm-task-bar {
  flex: 1; height: 6px; border-radius: 999px; overflow: hidden;
  background: rgba(var(--v-theme-on-surface, 27, 29, 41), .10);
}
.strm-task-bar i {
  display: block; height: 100%;
  background: linear-gradient(90deg, #8B5CF6, rgb(var(--v-theme-primary, 124, 92, 252)), #3B82F6);
  transition: width .3s;
}
/* 队列长度不可预知（事件会持续灌入），因此这里用不确定进度条而不是百分比 */
.strm-task-bar.is-live i {
  width: 35%; transition: none;
  animation: strm-slide 1.15s ease-in-out infinite;
}
@keyframes strm-slide {
  0% { margin-left: -35%; }
  100% { margin-left: 100%; }
}

.strm-tabs { display: flex; gap: 6px; margin-bottom: 14px; }
.strm-tab {
  padding: 6px 14px; border-radius: 9px; font-size: 13px; font-family: inherit; cursor: pointer;
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .12);
  background: transparent;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .72));
}
.strm-tab.active {
  background: rgb(var(--v-theme-primary, 124, 92, 252));
  border-color: rgb(var(--v-theme-primary, 124, 92, 252));
  color: #fff; font-weight: 600;
}

.strm-stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 16px; }
.strm-stat {
  position: relative; overflow: hidden;
  display: flex; align-items: center; gap: 12px;
  padding: 14px 16px; border-radius: 13px;
  background: rgb(var(--v-theme-surface, 255, 255, 255));
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .10);
  cursor: pointer; font-family: inherit; text-align: left;
  transition: border-color .18s, transform .18s, box-shadow .18s;
}
/* 顶部彩条：hover 时从左向右展开，提示「这张卡可点」 */
.strm-stat::before {
  content: ""; position: absolute; top: 0; left: 0; right: 0; height: 3px;
  background: linear-gradient(90deg, var(--strm-accent, rgb(var(--v-theme-primary, 124, 92, 252))), transparent);
  transform: scaleX(0); transform-origin: left; opacity: 0;
  transition: transform .22s ease, opacity .22s ease;
}
.strm-stat:hover { transform: translateY(-2px); box-shadow: 0 8px 22px rgba(10, 11, 20, .14); }
.strm-stat:hover::before { transform: scaleX(1); opacity: 1; }
.strm-stat.active { border-color: rgb(var(--v-theme-primary, 124, 92, 252)); box-shadow: 0 0 0 3px rgba(var(--v-theme-primary, 124, 92, 252), .14); }
/* KPI 渐变数字：同色系深→浅渐变，视觉重量高于文字标签 */
.strm-stat b {
  font-size: 24px; font-weight: 800; line-height: 1; letter-spacing: -.5px;
  font-variant-numeric: tabular-nums;
  background: linear-gradient(135deg, var(--strm-accent2, currentColor) 0%, var(--strm-accent, currentColor) 100%);
  -webkit-background-clip: text; background-clip: text;
  -webkit-text-fill-color: transparent;
}
.strm-stat span {
  font-size: 12px; font-weight: 600;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .62));
}
.strm-stat-dot {
  margin-left: auto; width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0;
  background: var(--strm-accent, rgb(var(--v-theme-primary, 124, 92, 252)));
  box-shadow: 0 0 0 4px color-mix(in srgb, var(--strm-accent, #7C5CFC) 16%, transparent);
}
.strm-stat.is-pending { --strm-accent: #EA8A1F; --strm-accent2: #FBBF24; }
.strm-stat.is-scraped { --strm-accent: #16A34A; --strm-accent2: #4ADE80; }
.strm-stat.is-failed { --strm-accent: #E5484D; --strm-accent2: #FB7185; }

/* 骨架屏：渐变扫光，扫描监控目录时不留大片空白 */
.strm-skel-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(168px, 1fr)); gap: 18px 16px;
}
.strm-skel { display: flex; flex-direction: column; gap: 8px; }
.strm-skel-poster {
  aspect-ratio: 2 / 3; border-radius: 14px;
  background: linear-gradient(90deg,
    rgba(var(--v-theme-on-surface, 27, 29, 41), .06) 25%,
    rgba(var(--v-theme-on-surface, 27, 29, 41), .12) 37%,
    rgba(var(--v-theme-on-surface, 27, 29, 41), .06) 63%);
  background-size: 400% 100%;
  animation: strm-shimmer 1.4s ease infinite;
}
.strm-skel-line {
  height: 10px; border-radius: 6px;
  background: linear-gradient(90deg,
    rgba(var(--v-theme-on-surface, 27, 29, 41), .06) 25%,
    rgba(var(--v-theme-on-surface, 27, 29, 41), .12) 37%,
    rgba(var(--v-theme-on-surface, 27, 29, 41), .06) 63%);
  background-size: 400% 100%;
  animation: strm-shimmer 1.4s ease infinite;
}
.strm-skel-line.is-short { width: 55%; }
@keyframes strm-shimmer {
  0% { background-position: 100% 50%; }
  100% { background-position: 0 50%; }
}

/* F2 分类筛选行：一级分类 + 二级分类常显 chip，与搜索框分行，点击即过滤海报墙 */
.strm-cats { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 10px; }
.strm-cats-label {
  font-size: 12px; flex-shrink: 0;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .6));
}
.strm-cats .strm-cat:hover { border-color: rgba(var(--v-theme-primary, 124, 92, 252), .4); }
/* 折叠箭头：全量扫描菜单与队列面板共用 */
.strm-caret { display: inline-block; font-size: 9px; line-height: 1; transition: transform .18s; }
.strm-caret.is-open { transform: rotate(180deg); }
.strm-cat {
  display: flex; align-items: center; gap: 6px;
  padding: 6px 12px; border-radius: 999px; font-size: 12.5px; font-family: inherit; cursor: pointer;
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .12);
  background: transparent;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .78));
}
.strm-cat i { width: 8px; height: 8px; border-radius: 50%; background: var(--cat-color, currentColor); }
.strm-cat em { font-style: normal; opacity: .7; font-size: 11px; }
.strm-cat b { font-weight: 600; font-size: 11px; color: #EA8A1F; }
.strm-cat.active {
  background: rgb(var(--v-theme-primary, 124, 92, 252));
  border-color: rgb(var(--v-theme-primary, 124, 92, 252));
  color: #fff; font-weight: 600;
}
.strm-cat.active i { background: #fff; }
.strm-cat.active b { color: #FDE68A; }

.strm-toolbar { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }
.strm-seg {
  display: flex; gap: 2px; padding: 3px; border-radius: 11px;
  background: rgba(var(--v-theme-on-surface, 27, 29, 41), .05);
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .10);
  margin-left: 2px; flex-shrink: 0;
}
.strm-seg-item {
  padding: 6px 14px; border: none; border-radius: 8px;
  font-size: 13px; font-weight: 600; font-family: inherit; cursor: pointer; background: transparent;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .68));
  transition: background .15s, color .15s, box-shadow .15s;
}
.strm-seg-item.active {
  background: rgb(var(--v-theme-surface, 255, 255, 255));
  color: rgb(var(--v-theme-on-surface, 27, 29, 41));
  box-shadow: 0 1px 3px rgba(0, 0, 0, .12);
}
.strm-search {
  flex: 1; min-width: 180px; padding: 9px 13px; font-size: 13px; font-family: inherit;
  border-radius: 11px; outline: none;
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .14);
  background: rgb(var(--v-theme-surface, 255, 255, 255));
  color: rgb(var(--v-theme-on-surface, 27, 29, 41));
  transition: border-color .15s, box-shadow .15s;
}
.strm-search:hover { border-color: rgba(var(--v-theme-primary, 124, 92, 252), .4); }
.strm-search:focus {
  border-color: rgb(var(--v-theme-primary, 124, 92, 252));
  box-shadow: 0 0 0 3px rgba(var(--v-theme-primary, 124, 92, 252), .15);
}
.strm-count {
  font-size: 12.5px;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .6));
}

.strm-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(168px, 1fr)); gap: 18px 16px; }
.strm-card { display: flex; flex-direction: column; cursor: pointer; }
.strm-poster {
  position: relative; aspect-ratio: 2 / 3; overflow: hidden; border-radius: 14px;
  background: rgb(var(--v-theme-surface, 255, 255, 255));
  transition: transform .18s ease, box-shadow .18s ease;
}
.strm-card:hover .strm-poster { transform: translateY(-5px); box-shadow: 0 18px 40px rgba(10, 11, 20, .34), 0 0 0 1px rgba(var(--v-theme-primary, 124, 92, 252), .35); }
.strm-poster-img {
  position: absolute; inset: 0; width: 100%; height: 100%;
  object-fit: cover; display: block; background: transparent;
}
.strm-type {
  position: absolute; top: 9px; left: 9px; padding: 3px 9px; border-radius: 999px;
  font-size: 11px; font-weight: 600; background: rgba(255, 255, 255, .92);
}
.strm-type.is-mv { color: #3B82F6; }
.strm-type.is-tv { color: #EC4899; }
.strm-type.is-mu { color: #8B5CF6; }
.strm-dot { position: absolute; top: 12px; right: 12px; width: 10px; height: 10px; border-radius: 50%; box-shadow: 0 0 0 3px rgba(0, 0, 0, .32); }
.strm-dot.is-ok { background: #16A34A; }
.strm-dot.is-fail { background: #E5484D; }
.strm-dot.is-busy { background: #EA8A1F; animation: strm-pulse 1s ease-in-out infinite; }
.strm-dot.is-skip { background: #94A3B8; }
.strm-dot.is-none { background: transparent; border: 1.5px solid rgba(255, 255, 255, .9); }
/* 失败红条：贴海报底边，一眼看出「这条刮挂了」 */
.strm-fail-strip {
  position: absolute; left: 0; right: 0; bottom: 0; padding: 5px 10px;
  font-size: 11px; font-weight: 600; color: #fff; z-index: 2;
  background: rgba(229, 72, 77, .92);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
/* hover 浮层：覆盖海报，承载元信息与操作，Plex 式「悬停才知道更多」 */
.strm-overlay {
  position: absolute; inset: 0; z-index: 2; display: flex; flex-direction: column;
  justify-content: flex-end; padding: 12px;
  background: linear-gradient(180deg, transparent 35%, rgba(10, 11, 20, .88) 100%);
  opacity: 0; transition: opacity .18s ease;
}
.strm-card:hover .strm-overlay { opacity: 1; }
.strm-overlay-title { font-size: 14px; font-weight: 700; color: #fff; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.strm-overlay-meta { font-size: 11.5px; color: rgba(255, 255, 255, .72); margin-top: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.strm-overlay-status { display: flex; align-items: center; gap: 6px; font-size: 11.5px; color: #fff; margin-top: 8px; }
.strm-overlay-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.strm-overlay-dot.is-ok { background: #34D399; }
.strm-overlay-dot.is-fail { background: #F87171; }
.strm-overlay-dot.is-busy { background: #FBBF24; }
.strm-overlay-dot.is-skip { background: #94A3B8; }
.strm-overlay-dot.is-none { background: transparent; border: 1.5px solid #fff; }
.strm-overlay-actions { display: flex; gap: 7px; margin-top: 10px; }
.strm-overlay-btn {
  flex: 1; padding: 7px 8px; border-radius: 8px; font-size: 12px; font-weight: 600;
  font-family: inherit; cursor: pointer; border: none;
  background: rgba(255, 255, 255, .18); color: #fff;
}
.strm-overlay-btn:hover { background: rgba(255, 255, 255, .3); }
.strm-overlay-btn.is-primary { background: rgb(var(--v-theme-primary, 124, 92, 252)); color: #fff; }
.strm-overlay-btn.is-primary:hover { filter: brightness(1.1); }
.strm-overlay-btn:disabled { opacity: .45; cursor: not-allowed; }
.strm-card-title { margin-top: 9px; font-size: 13.5px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.strm-card-sub {
  margin-top: 2px; font-size: 11.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .5));
}

.strm-empty {
  text-align: center; padding: 48px 0; font-size: 13px;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .5));
}
/* 空态图标：径向渐变圆底 + 主题色描边 emoji，给「暂无数据」一个视觉落点 */
.strm-empty::before {
  content: "🎞️"; display: grid; place-items: center;
  width: 52px; height: 52px; margin: 0 auto 12px;
  border-radius: 50%; font-size: 22px;
  background: radial-gradient(circle at 30% 25%,
    rgba(var(--v-theme-primary, 124, 92, 252), .22), rgba(var(--v-theme-primary, 124, 92, 252), .06));
  box-shadow: inset 0 0 0 1px rgba(var(--v-theme-primary, 124, 92, 252), .18);
}

/* 内联详情：Plex 式 Hero + 季切换 + 集列表（不用 fixed 抽屉，避免与宿主 VDialog 层叠上下文冲突） */
.strm-detail {
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .10);
  border-radius: 16px; overflow: hidden;
  /* 显式声明前景色：本容器内的文字一律按自己的主题变量求值，
     不依赖宿主 .v-card 的继承结果（毛玻璃/纯白/暗色三主题下才不会出现浅字压浅底） */
  color: rgb(var(--v-theme-on-surface, 27, 29, 41));
  background: rgb(var(--v-theme-surface, 255, 255, 255));
}

/* 详情 Hero：Plex 式沉浸横幅。海报既是海报也是背景氛围层，底下垫一层主色渐变，
   再叠底部压暗 scrim 保证白字可读。横幅恒定深色（对齐设计稿），内部一律白色系，
   不跟随宿主三主题 —— 深色底 + 白字在任何主题下都是同一份视觉 */
.strm-dhero {
  position: relative; overflow: hidden; min-height: 232px;
  display: flex; flex-direction: column; justify-content: flex-end;
  padding: 48px 20px 20px;
  background: linear-gradient(120deg, rgb(var(--v-theme-primary, 124, 92, 252)) -40%, #1b2740 58%, #0d1119 100%);
  color: #fff;
}
.strm-dhero-bg {
  position: absolute; inset: 0; width: 100%; height: 100%;
  object-fit: cover; transform: scale(1.24);
  filter: blur(24px) saturate(1.35); opacity: .58;
  pointer-events: none;
}
.strm-dhero-scrim {
  position: absolute; inset: 0; pointer-events: none;
  background: linear-gradient(180deg,
    rgba(8, 10, 16, .10) 0%,
    rgba(8, 10, 16, .54) 56%,
    rgba(8, 10, 16, .86) 100%);
}
.strm-back {
  position: absolute; top: 14px; left: 16px; z-index: 3;
  display: inline-flex; align-items: center; gap: 5px; cursor: pointer;
  padding: 5px 13px 5px 9px; border-radius: 999px;
  font-family: inherit; font-size: 12px;
  border: 1px solid rgba(255, 255, 255, .26);
  background: rgba(10, 12, 18, .42);
  color: rgba(255, 255, 255, .92);
  transition: background .15s, border-color .15s, color .15s;
}
.strm-back:hover { background: rgba(10, 12, 18, .64); border-color: rgba(255, 255, 255, .46); color: #fff; }
.strm-back-arrow { font-size: 13px; line-height: 1; }
.strm-dhero-body { position: relative; z-index: 1; display: flex; gap: 18px; align-items: flex-end; flex-wrap: wrap; }
.strm-dhero-poster {
  position: relative; width: 128px; height: 192px; flex: none;
  border-radius: 12px; overflow: hidden;
  background: rgba(255, 255, 255, .08);
  outline: 1px solid rgba(255, 255, 255, .2);
  box-shadow: 0 18px 44px rgba(0, 0, 0, .5);
}
.strm-dhero-info { flex: 1; min-width: 240px; }
.strm-dhero-kicker {
  font-size: 11px; font-weight: 600; letter-spacing: 1.4px; text-transform: uppercase;
  color: rgba(255, 255, 255, .68); margin-bottom: 9px;
}
/* 显式声明 color：宿主对 h2 有全局样式，不会继承插件容器的主题变量 */
.strm-dhero-info h2 {
  font-size: 27px; font-weight: 800; letter-spacing: -.6px; line-height: 1.15;
  margin: 0 0 12px; color: #fff; text-shadow: 0 2px 20px rgba(0, 0, 0, .35);
}
.strm-dhero-tags { display: flex; gap: 7px; flex-wrap: wrap; align-items: center; }
.strm-dhero-tags .strm-tag {
  background: rgba(255, 255, 255, .16); color: #fff;
  border: 1px solid rgba(255, 255, 255, .16);
}
.strm-dhero-tags .strm-tag.is-ok { background: rgba(52, 211, 153, .22); color: #b7f4da; border-color: rgba(52, 211, 153, .32); }
.strm-dhero-tags .strm-tag.is-fail { background: rgba(248, 113, 113, .22); color: #ffd2d2; border-color: rgba(248, 113, 113, .34); }
.strm-dhero-tags .strm-tag.is-busy { background: rgba(251, 191, 36, .22); color: #ffe6ae; border-color: rgba(251, 191, 36, .34); }
.strm-dhero-tags .strm-tag.is-skip,
.strm-dhero-tags .strm-tag.is-none { background: rgba(255, 255, 255, .14); color: rgba(255, 255, 255, .84); }
.strm-dhero-times {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  margin-top: 11px; font-size: 12px; font-variant-numeric: tabular-nums;
  color: rgba(255, 255, 255, .74);
}
.strm-dhero-times b { font-weight: 600; margin-right: 5px; color: rgba(255, 255, 255, .94); }
.strm-sep { opacity: .38; }
.strm-dhero-path {
  margin-top: 8px; font-size: 11.5px;
  color: rgba(255, 255, 255, .6);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.strm-dhero-actions { display: flex; gap: 8px; align-items: center; margin-left: auto; padding-bottom: 2px; }
.strm-dhero-actions .strm-btn { flex: 0 0 auto; }
/* 横幅内的主 CTA 走白底（Plex / 设计稿口径），与页面主色按钮区分层级 */
.strm-dhero-actions .strm-btn:not(.ghost) {
  background: #fff; border-color: #fff; color: #0c1220; font-weight: 600;
}
.strm-dhero-actions .strm-btn.ghost {
  background: rgba(255, 255, 255, .16); border-color: rgba(255, 255, 255, .3); color: #fff;
}

.strm-tag {
  display: inline-flex; align-items: center;
  border-radius: 999px; padding: 3px 10px; font-size: 11.5px;
  background: rgba(var(--v-theme-on-surface, 27, 29, 41), .07);
}
.strm-tag.is-mv { background: rgba(59, 130, 246, .14); color: #3B82F6; }
.strm-tag.is-tv { background: rgba(236, 72, 153, .14); color: #EC4899; }
.strm-tag.is-mu { background: rgba(139, 92, 246, .16); color: #8B5CF6; }
.strm-tag.is-ok { background: rgba(22, 163, 74, .13); color: #16A34A; }
.strm-tag.is-fail { background: rgba(229, 72, 77, .13); color: #E5484D; }
.strm-tag.is-warn { background: rgba(234, 138, 31, .16); color: #EA8A1F; }
.strm-tag.is-busy { background: rgba(234, 138, 31, .16); color: #EA8A1F; }
.strm-tag.is-skip { background: rgba(var(--v-theme-on-surface, 27, 29, 41), .08); color: rgba(var(--v-theme-on-surface, 27, 29, 41), .6); }
.strm-tag.is-none { background: rgba(var(--v-theme-on-surface, 27, 29, 41), .07); color: rgba(var(--v-theme-on-surface, 27, 29, 41), .55); }
.strm-status-dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: currentColor; margin-right: 4px; vertical-align: 1px; }
.strm-tag.is-plain { color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .7)); }

/* 季切换：胶囊 + 计数角标，激活态走宿主主色 */
.strm-season-bar {
  display: flex; gap: 8px; align-items: center; flex-wrap: wrap; padding: 12px 18px;
  border-top: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .07);
  border-bottom: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .09);
}
.strm-season-label {
  font-size: 10.5px; font-weight: 600; letter-spacing: 1px; margin-right: 2px;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .5));
}
.strm-season-chip {
  display: inline-flex; align-items: center; gap: 7px;
  padding: 6px 12px; border-radius: 999px; font-size: 12.5px; font-family: inherit; cursor: pointer; white-space: nowrap;
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .14);
  background: transparent; color: rgba(var(--v-theme-on-surface, 27, 29, 41), .72);
  transition: border-color .15s, color .15s, background .15s;
}
.strm-season-chip:hover { border-color: rgba(var(--v-theme-primary, 124, 92, 252), .45); }
.strm-season-chip em {
  font-style: normal; font-size: 10.5px; font-variant-numeric: tabular-nums;
  padding: 1px 6px; border-radius: 999px;
  background: rgba(var(--v-theme-on-surface, 27, 29, 41), .09);
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), .62);
}
.strm-season-chip.active {
  background: rgb(var(--v-theme-primary, 124, 92, 252));
  border-color: rgb(var(--v-theme-primary, 124, 92, 252));
  color: #fff; font-weight: 600;
  box-shadow: 0 4px 14px rgba(var(--v-theme-primary, 124, 92, 252), .3);
}
.strm-season-chip.active em { background: rgba(255, 255, 255, .24); color: #fff; }
.strm-list-actions { display: flex; align-items: center; gap: 8px; padding: 11px 18px 2px; }
.strm-list-sum {
  margin-left: auto; font-size: 12px; font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .6));
}
.strm-list-sum b { font-weight: 700; }
.strm-mini {
  padding: 5px 11px; border-radius: 9px; font-size: 12px; font-family: inherit; cursor: pointer;
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .14);
  background: transparent; color: rgba(var(--v-theme-on-surface, 27, 29, 41), .72);
}
.strm-mini:hover { border-color: rgba(var(--v-theme-primary, 124, 92, 252), .4); color: rgb(var(--v-theme-primary, 124, 92, 252)); }
.strm-mini:disabled { opacity: .45; cursor: not-allowed; }
.strm-list { max-height: 46vh; overflow-y: auto; padding: 8px 18px 14px; }
.strm-row {
  position: relative;
  display: flex; align-items: center; gap: 10px; padding: 9px 10px 9px 7px; border-radius: 11px;
  border: 1px solid transparent;
  transition: background .14s, border-color .14s;
}
/* 行首状态色条：一眼扫出哪几集有问题，颜色跟状态语义走 */
.strm-row-bar {
  width: 3px; height: 22px; border-radius: 999px; flex-shrink: 0;
  background: rgba(var(--v-theme-on-surface, 27, 29, 41), .16);
}
.strm-row.is-ok .strm-row-bar { background: #16A34A; }
.strm-row.is-fail .strm-row-bar { background: #E5484D; }
.strm-row.is-busy .strm-row-bar { background: #EA8A1F; animation: strm-pulse 1s ease-in-out infinite; }
.strm-row.is-skip .strm-row-bar { background: #94A3B8; }
.strm-row:hover { background: rgba(var(--v-theme-on-surface, 27, 29, 41), .045); }
.strm-row.is-selected {
  background: rgba(var(--v-theme-primary, 124, 92, 252), .10);
  border-color: rgba(var(--v-theme-primary, 124, 92, 252), .30);
}
.strm-row input { width: 15px; height: 15px; flex-shrink: 0; accent-color: rgb(var(--v-theme-primary, 124, 92, 252)); cursor: pointer; }
.strm-row-no {
  width: 52px; flex-shrink: 0; font-size: 12.5px; font-weight: 700;
  font-variant-numeric: tabular-nums; color: rgb(var(--v-theme-primary, 124, 92, 252));
}
.strm-row-no.is-plain { color: rgba(var(--v-theme-on-surface, 27, 29, 41), .62); }
.strm-row-name {
  flex: 1; min-width: 0; font-size: 12.5px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.strm-row-size {
  width: 68px; flex-shrink: 0; text-align: right; font-size: 11.5px; font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .55));
}
.strm-row-state { width: 74px; flex-shrink: 0; display: flex; align-items: center; gap: 5px; font-size: 11.5px; }
.strm-row-state i { width: 6px; height: 6px; border-radius: 50%; }
.strm-row-state.is-ok { color: #16A34A; }
.strm-row-state.is-ok i { background: #16A34A; }
.strm-row-state.is-fail { color: #E5484D; }
.strm-row-state.is-fail i { background: #E5484D; }
.strm-row-state.is-busy { color: #EA8A1F; }
.strm-row-state.is-busy i { background: #EA8A1F; animation: strm-pulse 1s ease-in-out infinite; }
.strm-row-state.is-skip { color: #94A3B8; }
.strm-row-state.is-skip i { background: #94A3B8; }
.strm-row-state.is-none { color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .55)); }
.strm-row-state.is-none i { background: rgba(var(--v-theme-on-surface, 27, 29, 41), .28); }
/* 行内「刮削」：常驻但压到背景层，指针扫过该行才提亮成主色，避免整列按钮抢视线 */
.strm-row-go {
  flex-shrink: 0; padding: 4px 10px; border-radius: 8px; font-size: 11.5px; font-family: inherit; cursor: pointer;
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .14);
  background: transparent;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), .6);
  transition: color .14s, border-color .14s, background .14s;
}
.strm-row:hover .strm-row-go {
  border-color: rgba(var(--v-theme-primary, 124, 92, 252), .45);
  color: rgb(var(--v-theme-primary, 124, 92, 252));
}
.strm-row-go:hover { background: rgba(var(--v-theme-primary, 124, 92, 252), .12); }
.strm-row-go:disabled { opacity: .35; cursor: not-allowed; }
.strm-detail-foot {
  display: flex; align-items: center; gap: 10px; padding: 13px 18px;
  border-top: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .10);
  background: rgba(var(--v-theme-on-surface, 27, 29, 41), .02);
}
.strm-sel { font-size: 12.5px; color: rgba(var(--v-theme-on-surface, 27, 29, 41), .72); }
.strm-sel b { font-size: 14px; color: rgb(var(--v-theme-primary, 124, 92, 252)); }
.strm-foot-right { margin-left: auto; display: flex; gap: 9px; }

.strm-rec-head { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.strm-rec-count { font-size: 13px; color: rgba(var(--v-theme-on-surface, 27, 29, 41), .65); }
.strm-rec-count b { color: rgb(var(--v-theme-on-surface, 27, 29, 41)); font-variant-numeric: tabular-nums; }
/* 时间轴式记录列表（v4）：左状态点 + 主内容两行，右侧结果徽章与时间 */
.strm-rec-list {
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .10);
  border-radius: 14px; overflow: hidden;
  background: rgb(var(--v-theme-surface, 255, 255, 255));
  max-height: 56vh; overflow-y: auto;
}
.strm-rec {
  position: relative;
  display: flex; align-items: flex-start; gap: 12px; padding: 11px 16px 11px 18px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .07);
  font-size: 12.5px; transition: background .15s;
}
/* 左侧状态色条：与状态点同色，扫一眼就知道这行结果 */
.strm-rec::before {
  content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
  background: #16A34A; opacity: .55;
}
.strm-rec.is-fail::before { background: #E5484D; }
.strm-rec:hover { background: rgba(var(--v-theme-primary, 124, 92, 252), .05); }
.strm-rec:last-child { border-bottom: none; }
.strm-rec-dot {
  flex-shrink: 0; width: 8px; height: 8px; border-radius: 50%; margin-top: 5px;
}
.strm-rec.is-ok .strm-rec-dot { background: #16A34A; box-shadow: 0 0 0 3px rgba(22, 163, 74, .13); }
.strm-rec.is-fail .strm-rec-dot { background: #E5484D; box-shadow: 0 0 0 3px rgba(229, 72, 77, .13); }
.strm-rec-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.strm-rec-top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-width: 0; }
.strm-rec-title {
  max-width: 320px; font-weight: 600; font-size: 13px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.strm-rec-badge {
  flex-shrink: 0; width: 44px; text-align: center; margin-top: 1px;
  border-radius: 999px; padding: 2px 0; font-size: 11px; font-weight: 600;
}
.strm-rec-badge.is-ok { background: rgba(22, 163, 74, .13); color: #16A34A; }
.strm-rec-badge.is-fail { background: rgba(229, 72, 77, .13); color: #E5484D; }
.strm-rec-time {
  flex-shrink: 0; width: 132px; text-align: right; margin-top: 1px; font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .6));
}
.strm-rec-msg {
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .55));
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}

/* Hero 横幅：待处理概览 + 行动号召，Plex 首页横幅的克制版 */
.strm-hero {
  display: flex; align-items: center; gap: 18px; flex-wrap: wrap;
  margin-bottom: 16px; padding: 20px 22px; border-radius: 14px;
  background:
    linear-gradient(120deg, rgba(var(--v-theme-primary, 124, 92, 252), .16), transparent 55%),
    rgb(var(--v-theme-surface, 255, 255, 255));
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .10);
}
.strm-hero-body { flex: 1; min-width: 240px; }
.strm-hero-kicker { font-size: 11px; letter-spacing: .6px; color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .55)); }
.strm-hero-title { font-size: 19px; font-weight: 700; margin-top: 3px; }
.strm-hero-title b { font-size: 22px; color: rgb(var(--v-theme-primary, 124, 92, 252)); font-variant-numeric: tabular-nums; }
.strm-hero-title b.is-fail { color: #E5484D; }
.strm-hero-sub { font-size: 12.5px; margin-top: 5px; color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .6)); }
.strm-hero-actions { display: flex; gap: 9px; align-items: center; }

/* F1/F5 统计 + 类型 + 状态筛选合并成一行可点击 chip */
.strm-chips { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 14px; }
.strm-chip-sep {
  width: 1px; height: 18px; flex-shrink: 0;
  background: rgba(var(--v-theme-on-surface, 27, 29, 41), .14);
}
button.strm-chip {
  border: 1px solid transparent; cursor: pointer; font-family: inherit;
  display: inline-flex; align-items: center; gap: 5px; line-height: 1.5;
}
button.strm-chip b { font-weight: 700; font-variant-numeric: tabular-nums; }
button.strm-chip:hover { border-color: rgba(var(--v-theme-primary, 124, 92, 252), .4); }
button.strm-chip.is-count.is-movie { color: #3B82F6; }
button.strm-chip.is-count.is-tv { color: #EC4899; }
button.strm-chip.is-count.is-music { color: #8B5CF6; }
button.strm-chip.is-status.is-scraped { background: rgba(22, 163, 74, .12); color: #16A34A; }
button.strm-chip.is-status.is-failed { background: rgba(229, 72, 77, .12); color: #E5484D; }
button.strm-chip.is-status.is-skipped { background: rgba(var(--v-theme-on-surface, 27, 29, 41), .08); color: rgba(var(--v-theme-on-surface, 27, 29, 41), .6); }
button.strm-chip.is-status.is-pending { background: rgba(234, 138, 31, .14); color: #EA8A1F; }
button.strm-chip.is-count.active,
button.strm-chip.is-status.active {
  background: rgb(var(--v-theme-primary, 124, 92, 252));
  border-color: rgb(var(--v-theme-primary, 124, 92, 252));
  color: #fff;
}
/* 零值 chip 降透明度，让行重心落在非零信息上（0 音乐/0 失败不再抢色底） */
.strm-chip.is-zero:not(.active) { opacity: .42; }

/* F2 面包屑：选中分类时的导航回退 */
.strm-breadcrumb {
  display: flex; align-items: center; gap: 10px; margin-bottom: 12px; font-size: 12.5px;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), .7);
}
.strm-breadcrumb span { min-width: 0; }

/* F8 队列面板（v4）：状态点 + 胶囊统计 + 分组卡片，与顶部统计卡同一语言 */
.strm-queue {
  /* body 在媒体库视图是 flex 列容器，下面的 grid-wrap 又是 flex:1；
     不锁 shrink 的话空间不足时本条会被压成一条细缝（内容裁切） */
  flex-shrink: 0;
  margin-bottom: 14px; border-radius: 14px; overflow: hidden;
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .10);
  background: rgb(var(--v-theme-surface, 255, 255, 255));
}
.strm-queue-bar {
  width: 100%; display: flex; align-items: center; gap: 12px;
  padding: 11px 16px; border: none; cursor: pointer; font-family: inherit;
  background: transparent;
  color: rgb(var(--v-theme-on-surface, 27, 29, 41));
  transition: background .15s;
}
.strm-queue-bar:hover { background: rgba(var(--v-theme-primary, 124, 92, 252), .07); }
.strm-queue-state {
  flex-shrink: 0; width: 9px; height: 9px; border-radius: 50%;
  background: rgba(var(--v-theme-on-surface, 27, 29, 41), .28);
  transition: background .2s;
}
.strm-queue-state.is-busy {
  background: #EA8A1F;
  animation: strm-pulse 1s ease-in-out infinite;
  box-shadow: 0 0 0 4px rgba(234, 138, 31, .15);
}
/* 展开时把状态点挪到面板上方做「当前状态」标题色 */
.strm-queue.is-open .strm-queue-state.is-busy { box-shadow: 0 0 0 5px rgba(234, 138, 31, .20); }
.strm-queue-bar .strm-task-text { flex-shrink: 0; }
.strm-queue-panel {
  padding: 14px 16px 16px; display: flex; flex-direction: column; gap: 14px;
  border-top: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .08);
}
.strm-queue-stats { display: flex; gap: 10px; flex-wrap: wrap; }
.strm-queue-stat {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 14px; border-radius: 11px;
  background: rgba(var(--v-theme-on-surface, 27, 29, 41), .05);
}
.strm-queue-stat i { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.strm-queue-stat.is-ok i { background: #16A34A; }
.strm-queue-stat.is-fail i { background: #E5484D; }
.strm-queue-stat.is-cancel i { background: rgba(var(--v-theme-on-surface, 27, 29, 41), .35); }
.strm-queue-stat.is-pend i { background: #EA8A1F; }
.strm-queue-stat b { font-size: 15px; font-weight: 700; line-height: 1; font-variant-numeric: tabular-nums; }
.strm-queue-stat span { font-size: 12px; color: rgba(var(--v-theme-on-surface, 27, 29, 41), .65); }
.strm-queue-sec { display: flex; flex-direction: column; gap: 7px; }
.strm-queue-sec-title {
  font-size: 11px; font-weight: 600; letter-spacing: .3px;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), .5);
}
.strm-queue-item {
  display: flex; align-items: center; gap: 9px; font-size: 12.5px;
  padding: 7px 11px; border-radius: 10px;
  background: rgba(var(--v-theme-on-surface, 27, 29, 41), .04);
}
.strm-queue-item.is-busy { background: rgba(var(--v-theme-primary, 124, 92, 252), .08); }
.strm-queue-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.strm-queue-dot.is-busy { background: #EA8A1F; animation: strm-pulse 1s ease-in-out infinite; }
.strm-queue-dot.is-ok { background: #16A34A; }
.strm-queue-dot.is-fail { background: #E5484D; }
.strm-queue-dot.is-pend { background: rgba(var(--v-theme-on-surface, 27, 29, 41), .28); }
.strm-queue-target { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.strm-queue-kind { flex-shrink: 0; font-size: 11px; color: rgba(var(--v-theme-on-surface, 27, 29, 41), .5); }
.strm-queue-err {
  flex-shrink: 0; max-width: 200px; font-size: 11px; color: #E5484D;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.strm-queue-actions { display: flex; gap: 9px; padding-top: 2px; }
@keyframes strm-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }

/* F10 记录筛选（v4） */
.strm-rec-filter { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; }
.strm-rec-filter select,
.strm-rec-filter input[type="date"] {
  padding: 7px 12px; border-radius: 11px; font-size: 12.5px; font-family: inherit; cursor: pointer;
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .14);
  background: rgb(var(--v-theme-surface, 255, 255, 255));
  color: rgb(var(--v-theme-on-surface, 27, 29, 41));
  transition: border-color .15s, box-shadow .15s;
}
.strm-rec-filter select:hover,
.strm-rec-filter input[type="date"]:hover { border-color: rgba(var(--v-theme-primary, 124, 92, 252), .5); }
.strm-rec-filter select:focus,
.strm-rec-filter input[type="date"]:focus {
  outline: none; border-color: rgb(var(--v-theme-primary, 124, 92, 252));
  box-shadow: 0 0 0 3px rgba(var(--v-theme-primary, 124, 92, 252), .15);
}
.strm-rec-filter-sep { font-size: 12px; color: rgba(var(--v-theme-on-surface, 27, 29, 41), .5); }

/* F11 空态引导 */
.strm-empty p { margin: 0 0 4px; }
.strm-empty-hint { font-size: 12px; color: rgba(var(--v-theme-on-surface, 27, 29, 41), .45); }
.strm-empty .strm-btn, .strm-empty .strm-mini { margin-top: 12px; }

@media (max-width: 900px) {
  .strm-head, .strm-body { padding-left: 14px; padding-right: 14px; }
  .strm-rec-time { display: none; }
  .strm-dhero { min-height: 0; padding: 42px 14px 16px; }
  .strm-dhero-body { gap: 13px; }
  .strm-dhero-poster { width: 92px; height: 138px; }
  .strm-dhero-info h2 { font-size: 20px; letter-spacing: -.3px; }
  /* 窄屏下主 CTA 落回下一行、占满宽度，不与标题抢横向空间 */
  .strm-dhero-actions { width: 100%; margin-left: 0; padding-top: 4px; }
  .strm-dhero-actions .strm-btn { flex: 1; }
  .strm-row-size { display: none; }
  /* 窄屏双栏收成单栏：回退整页滚动，侧栏作为内容上方的一条 */
  .strm-grid-wrap { grid-template-columns: 1fr; }
  .strm-body.is-grid { display: block; overflow-y: auto; }
  .strm-body.is-grid .strm-content { overflow: visible; padding-right: 0; }
  .strm-side {
    max-height: none;
    overflow: visible;
    border-right: none; border-bottom: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .10);
    border-radius: 12px 12px 0 0;
    margin-bottom: 14px;
  }
  .strm-content { padding-left: 0; }
}
</style>
