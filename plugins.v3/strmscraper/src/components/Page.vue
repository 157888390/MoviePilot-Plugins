<script setup>
// 插件详情弹窗（联邦暴露名 ./Page）。
//
// 本组件收敛了原侧栏全页 AppPage 与精简详情页的全部能力：统计、海报墙、类型与二级分类
// 筛选、搜索、单集/版本刮削、任务进度与刮削记录。宿主 PluginDataDialog 在 Vue 模式下只提供
// 一个空 VCard，因此标题栏、关闭按钮与设置入口都由本组件自行渲染。
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import StrmIcon from './StrmIcon.vue'
import {
  coverUrl,
  errorLabel,
  formatDate,
  formatSize,
  formatTime,
  makeApiCall,
  posterStyle,
  STATUS_LABELS,
  statusClass,
  statusOf,
  typeClass,
  typeLabel,
  unwrap,
  versionLabel,
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
const displayMode = ref('poster')
const sortBy = ref('recent')
const lastRefreshed = ref(null)
const refreshedLabel = computed(() =>
  lastRefreshed.value
    ? new Intl.DateTimeFormat('zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      }).format(lastRefreshed.value)
    : '尚未刷新',
)
const typeFilter = ref('all')
// 条目状态筛选：all / scraped / failed / skipped / pending（数字来自 overview.status）
const statusFilter = ref('all')
// 分类多选（组合键 `${父分类路径}::${分类名}`；一级分类回退项为 `::${分类名}`），空数组 = 不过滤
const subFilters = ref([])
// 队列面板：点顶部状态区展开，不单独开 Tab（F8）
const queuePanelOpen = ref(false)
const queuePanelEl = ref(null)
let queueReturnFocus = null

/** 抽屉打开后接管焦点，关闭时回到触发按钮。 */
watch(queuePanelOpen, async (open) => {
  if (open) {
    queueReturnFocus = document.activeElement
    scanMenu.value = false
    await nextTick()
    queuePanelEl.value?.querySelector('button')?.focus()
  } else if (queueReturnFocus?.isConnected) {
    await nextTick()
    queueReturnFocus.focus()
  }
})

/** 任务抽屉的键盘焦点始终留在当前对话框内。 */
function trapQueueFocus(event) {
  if (event.key !== 'Tab') return
  const controls = [
    ...(queuePanelEl.value?.querySelectorAll(
      'button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), [tabindex="0"]',
    ) || []),
  ]
  const first = controls[0]
  const last = controls[controls.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last?.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first?.focus()
  }
}

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
const queue = ref({
  running: null,
  queued: [],
  queued_total: 0,
  stats: {},
  recent: [],
  busy: false,
})
const busy = computed(() => Boolean(queue.value.busy))
const scanMenu = ref(false)
let queueTimer = null
// 记录上一轮队列是否忙碌，用于识别「刚跑完」这一瞬间并触发清单重算
let queueWasBusy = false
let queueCompletionKey = null
let disposed = false

// 海报加载失败（无本地海报且 TMDB 未命中）时回退渐变占位图
const posterFailed = reactive({})

/** 记录某条目的海报已加载失败，模板据此切换到渐变占位图。 */
function markPosterFailed(path) {
  posterFailed[path] = true
}

const counts = computed(() => ({
  all: items.value.length,
  movie: items.value.filter((item) => item.type === 'movie').length,
  tv: items.value.filter((item) => item.type === 'tv').length,
  music: items.value.filter((item) => item.type === 'music').length,
}))

// 状态过滤 chip：数字来自 overview.status，key 与 item.status 一一对应。
// 按用户要求只保留「待刮削 / 已刮 / 失败」三项（去掉「跳过」与类型计数）。
const STATUS_FILTERS = [
  { key: 'scraped', label: '已刮削', icon: 'checkCircle', hint: '元数据与海报已就绪' },
  { key: 'pending', label: '待刮削', icon: 'clock', hint: '等待补全媒体资料' },
  { key: 'failed', label: '刮削失败', icon: 'alert', hint: '查看并重试失败项' },
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
  return (queue.value.queued || []).some((item) => item.target === path)
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
  if (item.type === 'tv')
    return `${item.total_episodes || item.total_files} 集 · ${(item.seasons || []).length} 季`
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
    const subs = (group.children || []).filter((child) => !child.loose && child[type])
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
    const loose = (group.children || []).find((child) => child.loose && child[type])
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
    const duplicated = children.filter((entry) => entry.name === child.name).length > 1
    child.label =
      duplicated && child.parentName ? `${child.name} · ${child.parentName}` : child.name
  }
  return children
})

/** 当前选中的二级分类对象（多选）。 */
const activeSubs = computed(() => {
  const map = new Map(typeChildren.value.map((child) => [child.key, child]))
  return subFilters.value.map((key) => map.get(key)).filter(Boolean)
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
      .filter((child) => !child.loose && child[type])
      .map((child) => ({
        ...child,
        type,
        parent: group.path,
        parentName: group.name,
        key: `${group.path}::${child.name}`,
        pending: pendingByCat.value[`${type}|${child.name}`] || 0,
      }))
    const loose = (group.children || []).find((child) => child.loose && child[type])
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
      loose: loose
        ? {
            ...loose,
            type,
            parent: group.path,
            parentName: group.name,
            key: `${group.path}::${loose.name}`,
          }
        : null,
    })
  }
  // 音乐兜底：有音乐但后端未产出分类组时，仍给一个类型入口
  if ((overview.value.music || 0) > 0 && !seen.has('music')) {
    groups.push({
      name: '音乐',
      path: '',
      type: 'music',
      movie: 0,
      tv: 0,
      music: overview.value.music || 0,
      total: overview.value.music || 0,
      unscraped: 0,
      children: [],
      loose: null,
    })
  }
  return groups
})

/** 窄屏分类选择复用侧栏数据，保留所有分类入口。 */
const mobileCategories = computed(() =>
  sidebarGroups.value
    .flatMap((group) =>
      [...group.children, ...(group.loose ? [group.loose] : [])].map((child) => ({
        ...child,
        label: typeFilter.value === 'all' ? group.name + ' / ' + child.name : child.name,
      })),
    )
    .filter((child) => typeFilter.value === 'all' || child.type === typeFilter.value),
)

/** 在窄屏选择分类，并切换到相应媒体类型。 */
function selectMobileCategory(key) {
  const child = mobileCategories.value.find((item) => item.key === key)
  if (child) selectSubCat(child)
  else subFilters.value = []
}

/** 右侧内容标题：当前选中分类名，无则回退类型名 / 全部媒体。 */
const sectionTitle = computed(() => {
  if (activeSubs.value.length) return activeSubs.value.map((sub) => sub.label).join(' / ')
  const tab = TYPE_TABS.find((item) => item.key === typeFilter.value)
  return tab ? tab.label : '全部媒体'
})

const visibleItems = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  const subSet = new Set(subFilters.value)
  const childMap = new Map(typeChildren.value.map((child) => [child.key, child]))
  const result = items.value.filter((item) => {
    if (typeFilter.value !== 'all' && item.type !== typeFilter.value) return false
    if (statusFilter.value !== 'all' && displayStatus(item) !== statusFilter.value) return false
    if (subSet.size) {
      const keys = categoryKeysOf(item)
      const base = item.category_dir || item.root || ''
      const sub = subOf(item) || '未分类'
      const matched = [...subSet].some((key) => {
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
  if (sortBy.value === 'title')
    return result.sort((a, b) =>
      String(a.title).localeCompare(String(b.title), 'zh-CN', { numeric: true }),
    )
  const field = sortBy.value === 'scraped' ? 'last_scrape' : 'last_file_change'
  return result.sort((a, b) => Number(b[field] || 0) - Number(a[field] || 0))
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
  () =>
    seasons.value.find((item) => String(item.no) === seasonKey.value) || seasons.value[0] || null,
)

const rows = computed(() => {
  if (!current.value) return []
  if (current.value.type === 'tv') return currentSeason.value?.files || []
  // 电影按版本列出，音乐按曲目列出；两者的数据形状一致（后端统一放在 versions 里）
  return (detail.value?.files || []).map((file) => ({ ...file, label: versionLabel(file.name) }))
})

const selectedRows = computed(() => rows.value.filter((row) => selected.value.has(row.path)))

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
  view.value = 'grid'
  typeFilter.value = 'all'
  subFilters.value = []
}

/** 侧边栏：只按类型筛选（全部电影 / 全部电视剧 / 全部音乐）。 */
function selectTypeOnly(key) {
  view.value = 'grid'
  typeFilter.value = key
  subFilters.value = []
}

/** 侧边栏：单选二级分类，再点一次取消。 */
function selectSubCat(child) {
  view.value = 'grid'
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
    if (force) Object.keys(posterFailed).forEach((path) => delete posterFailed[path])
    if (view.value === 'detail' && current.value) {
      const updated = items.value.find((item) => item.path === current.value.path)
      if (updated) {
        current.value = updated
        await loadDetailFiles(updated)
      } else {
        closeDetail()
      }
    }
    lastRefreshed.value = new Date()
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
      ? `?scope=category&paths=${encodeURIComponent(subs.map((sub) => sub.path).join(','))}&overwrite=${overwrite}`
      : `?overwrite=${overwrite}`
    const result = unwrap(await apiCall('get', `/scan${query}`))
    const label = overwrite ? '覆盖重刮' : '仅补缺失'
    const where = subs.length
      ? `分类「${subs.map((sub) => sub.label).join('、')}」`
      : '全部监控目录'
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
    await loadDetailFiles(item, true)
  } catch (detailError) {
    error.value = detailError?.message || '读取文件明细失败'
  }
}

/** 刷新打开的详情，保留仍然有效的季与文件选择。 */
async function loadDetailFiles(item, resetSeason = false) {
  const response = await apiCall('get', `/files?path=${encodeURIComponent(item.path)}`)
  if (view.value !== 'detail' || current.value?.path !== item.path) return
  detail.value = unwrap(response)
  if (
    item.type === 'tv' &&
    seasons.value.length &&
    (resetSeason || !seasons.value.some((season) => String(season.no) === seasonKey.value))
  ) {
    seasonKey.value = String(seasons.value[0].no)
  }
  const paths = new Set((detail.value?.files || []).map((file) => file.path))
  selected.value = new Set([...selected.value].filter((path) => paths.has(path)))
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
  selected.value = new Set(rows.value.map((row) => row.path))
}

function selectUnscraped() {
  selected.value = new Set(rows.value.filter((row) => !row.scraped).map((row) => row.path))
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
    if (data) {
      queue.value = data
      queueWasBusy = queueWasBusy || Boolean(data.busy)
    }
  } catch (queueError) {
    // 轮询类请求失败不弹错，下一轮自动重试
  }
}

/** 完成计数也能识别两次轮询之间已经结束的短任务。 */
function completionKey(data) {
  const stats = data.stats || {}
  return [stats.done || 0, stats.failed || 0, stats.canceled || 0, data.recent?.[0]?.time || 0].join(':')
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
    const nextCompletionKey = completionKey(queue.value)
    const completed = queueCompletionKey !== null && queueCompletionKey !== nextCompletionKey
    queueCompletionKey = nextCompletionKey
    if ((queueWasBusy && !nowBusy) || completed) {
      // 重算清单、详情与统计；短任务也通过完成快照触发刷新。
      await loadAll(true, true)
      if (view.value === 'records') await loadRecords()
    }
    queueWasBusy = nowBusy
  } catch (pollError) {
    // 忽略单次轮询失败，下一轮继续
  }
  if (!disposed) queueTimer = setTimeout(pollQueue, queue.value.busy ? POLL_BUSY_MS : POLL_IDLE_MS)
}

/** 启动常驻队列轮询（幂等）：重复调用不会叠出两条循环。 */
function startQueuePolling() {
  if (disposed) return
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
  postScrape(
    selectedRows.value.map((row) => row.path),
    'file',
  )
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
  Boolean(
    recordFilter.success ||
      recordFilter.type ||
      recordFilter.category ||
      recordFilter.since ||
      recordFilter.until,
  ),
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
  if (height > 320) el.style.setProperty('--strm-h', `${Math.max(320, height - 20)}px`)
  else el.style.removeProperty('--strm-h')
}

onMounted(async () => {
  // 详情弹窗默认 80rem，海报墙需要更宽的可视区
  emit('layout', { maxWidth: '96rem' })
  syncRootHeight()
  requestAnimationFrame(syncRootHeight)
  setTimeout(syncRootHeight, 350)
  window.addEventListener('resize', syncRootHeight)
  loadAll(true)
  await loadQueue()
  startQueuePolling()
})

onBeforeUnmount(() => {
  disposed = true
  clearTimeout(queueTimer)
  window.removeEventListener('resize', syncRootHeight)
})
/** Escape 关闭当前浮层。 */
function closeOverlays() {
  queuePanelOpen.value = false
  scanMenu.value = false
}
</script>

<template>
  <div ref="rootEl" class="strm" @keydown.esc="closeOverlays">
    <aside class="strm-side" :inert="queuePanelOpen || undefined">
      <div class="strm-brand">
        <span class="strm-logo"><StrmIcon name="library" :size="23" /></span>
        <div>
          <strong>
            STRM
            <span>Scraper</span>
          </strong>
          <small>让每一份收藏，都有封面。</small>
        </div>
      </div>
      <div class="strm-workspace">
        <span class="strm-workspace-mark"><StrmIcon name="folder" :size="17" /></span>
        <span>
          我的媒体空间
          <small>STRM 媒体管理</small>
        </span>
        <span class="strm-online-dot" :class="{ off: !overview.monitoring }"></span>
      </div>
      <div class="strm-side-label">媒体管理</div>
      <nav class="strm-side-nav" aria-label="媒体导航">
        <button
          :class="[
            'strm-sopt',
            view !== 'records' && typeFilter === 'all' && !subFilters.length && 'on',
          ]"
          @click="selectAllMedia"
        >
          <StrmIcon name="library" :size="18" />
          <span>全部媒体</span>
          <em>{{ counts.all }}</em>
        </button>
        <button
          v-for="type in TYPE_TABS.slice(1)"
          :key="type.key"
          :class="[
            'strm-sopt',
            view !== 'records' && typeFilter === type.key && !subFilters.length && 'on',
          ]"
          @click="selectTypeOnly(type.key)"
        >
          <StrmIcon
            :name="type.key === 'movie' ? 'film' : type.key === 'tv' ? 'tv' : 'music'"
            :size="18"
          />
          <span>{{ type.label }}</span>
          <em>{{ counts[type.key] }}</em>
        </button>
        <button :class="['strm-sopt', view === 'records' && 'on']" @click="switchView('records')">
          <StrmIcon name="history" :size="18" />
          <span>刮削记录</span>
          <StrmIcon name="chevron" :size="14" class="strm-nav-arrow" />
        </button>
      </nav>
      <div class="strm-side-divider"></div>
      <div class="strm-side-label">
        媒体分类
        <span>{{ sidebarGroups.reduce((sum, group) => sum + group.children.length, 0) }}</span>
      </div>
      <nav class="strm-category-nav" aria-label="分类筛选">
        <template v-for="group in sidebarGroups" :key="group.path || group.name">
          <div v-if="group.children.length || group.loose" class="strm-sgroup">
            {{ group.name }}
          </div>
          <button
            v-for="child in group.children"
            :key="child.key"
            :class="[
              'strm-sopt',
              'is-child',
              view === 'grid' &&
                typeFilter === group.type &&
                subFilters.includes(child.key) &&
                'on',
            ]"
            @click="selectSubCat(child)"
          >
            <i class="strm-category-dot" :class="group.type"></i>
            <span>{{ child.name }}</span>
            <em>{{ child[group.type] || 0 }}</em>
            <b v-if="child.pending" :title="child.pending + ' 项待刮削'">{{ child.pending }}</b>
          </button>
          <button
            v-if="group.loose"
            :class="['strm-sopt', 'is-child', subFilters.includes(group.loose.key) && 'on']"
            @click="selectSubCat(group.loose)"
          >
            <i class="strm-category-dot"></i>
            <span>{{ group.loose.name }}</span>
            <em>{{ group.loose[group.type] || 0 }}</em>
          </button>
        </template>
        <p v-if="!sidebarGroups.length" class="strm-side-empty">扫描后显示媒体分类</p>
      </nav>
      <div class="strm-side-bottom">
        <div class="strm-monitor-card">
          <span class="strm-monitor-icon"><StrmIcon name="activity" :size="18" /></span>
          <div>
            <b>{{ overview.monitoring ? '目录监控已开启' : '目录监控未开启' }}</b>
            <small>
              {{
                overview.monitoring
                  ? '新文件会自动加入刮削队列'
                  : overview.monitor_dirs?.length
                    ? '已配置目录，可手动刷新与刮削'
                    : '前往设置配置监控目录'
              }}
            </small>
          </div>
          <i :class="['strm-online-dot', !overview.monitoring && 'off']"></i>
        </div>
        <button v-if="props.show_switch" class="strm-sopt strm-settings" @click="openSettings">
          <StrmIcon name="settings" :size="18" />
          <span>插件设置</span>
          <span class="strm-settings-arrow">↗</span>
        </button>
        <div class="strm-side-caption">
          STRM SCRAPER
          <span>MoviePilot 插件</span>
        </div>
      </div>
    </aside>

    <main class="strm-main" :inert="queuePanelOpen || undefined">
      <header class="strm-head">
        <div class="strm-crumb">
          <StrmIcon name="folder" :size="16" />
          <span>我的媒体空间</span>
          <span class="strm-crumb-slash">/</span>
          <b>{{ view === 'records' ? '刮削记录' : view === 'detail' ? '媒体详情' : '媒体库' }}</b>
        </div>
        <div class="strm-head-actions">
          <span :class="['strm-monitor-badge', !overview.monitoring && 'is-muted']">
            <i class="strm-online-dot" :class="{ off: !overview.monitoring }"></i>
            {{ overview.monitoring ? '监控运行中' : '监控已暂停' }}
          </span>
          <button
            :class="['strm-queue-trigger', busy && 'is-busy']"
            :aria-expanded="queuePanelOpen"
            @click="queuePanelOpen = !queuePanelOpen"
          >
            <StrmIcon name="queue" :size="18" />
            <span>任务队列</span>
            <b v-if="busy">{{ (queue.queued_total || 0) + (queue.running ? 1 : 0) }}</b>
          </button>
          <span class="strm-head-divider"></span>
          <button
            v-if="props.show_switch"
            class="strm-icon-btn"
            title="插件设置"
            aria-label="插件设置"
            @click="openSettings"
          >
            <StrmIcon name="settings" :size="18" />
          </button>
          <button
            class="strm-icon-btn strm-close"
            title="关闭"
            aria-label="关闭"
            @click="emit('close')"
          >
            <StrmIcon name="close" :size="18" />
          </button>
        </div>
      </header>
      <nav class="strm-mobile-nav" aria-label="移动端媒体导航">
        <button
          v-for="type in TYPE_TABS"
          :key="type.key"
          :class="{ active: view === 'grid' && typeFilter === type.key }"
          @click="selectTypeOnly(type.key)"
        >
          {{ type.label }}
        </button>
        <button :class="{ active: view === 'records' }" @click="switchView('records')">
          刮削记录
        </button>
      </nav>
      <div :class="['strm-body', view === 'grid' && 'is-grid']">
        <div v-if="error" class="strm-alert is-error" role="alert">
          <StrmIcon name="alert" :size="18" />
          <span>{{ error }}</span>
          <button class="strm-icon-btn" aria-label="关闭提示" @click="error = ''">
            <StrmIcon name="close" :size="16" />
          </button>
        </div>
        <div v-else-if="notice" class="strm-alert is-ok" role="status">
          <StrmIcon name="checkCircle" :size="18" />
          <span>{{ notice }}</span>
          <button class="strm-icon-btn" aria-label="关闭提示" @click="notice = ''">
            <StrmIcon name="close" :size="16" />
          </button>
        </div>
        <template v-if="view === 'grid'">
          <div class="strm-page-heading">
            <div>
              <div class="strm-eyebrow">YOUR PERSONAL MEDIA LIBRARY</div>
              <h1>
                媒体库
                <span class="strm-title-dot">.</span>
              </h1>
              <p>一处管理你的 STRM 收藏，让媒体资料保持完整。</p>
            </div>
            <div class="strm-page-actions">
              <button class="strm-btn ghost" :disabled="loading" @click="refreshAll">
                <StrmIcon name="refresh" :size="16" :class="{ 'strm-spinning': loading }" />
                刷新媒体
              </button>
              <div class="strm-scan">
                <button
                  class="strm-btn"
                  :disabled="busy"
                  :aria-expanded="scanMenu"
                  @click="scanMenu = !scanMenu"
                >
                  <StrmIcon name="sparkles" :size="17" />
                  开始扫描
                  <StrmIcon name="chevron" :size="15" />
                </button>
                <template v-if="scanMenu">
                  <button
                    class="strm-menu-dismiss"
                    aria-label="关闭扫描菜单"
                    @click="scanMenu = false"
                  ></button>
                  <div class="strm-scan-menu">
                    <span class="strm-menu-label">扫描所有监控目录</span>
                    <button @click="runScan(false)">
                      <StrmIcon name="checkCircle" :size="17" />
                      <span>
                        <b>仅补缺失</b>
                        <em>保留已有资料，补全未刮削媒体</em>
                      </span>
                    </button>
                    <button @click="runScopedScan('incremental')">
                      <StrmIcon name="activity" :size="17" />
                      <span>
                        <b>增量扫描</b>
                        <em>只处理新增和未刮削的目录</em>
                      </span>
                    </button>
                    <button @click="runScopedScan('unscraped')">
                      <StrmIcon name="search" :size="17" />
                      <span>
                        <b>补漏扫描</b>
                        <em>重新检查尚未完成的媒体</em>
                      </span>
                    </button>
                    <button class="strm-scan-overwrite" @click="runScan(true)">
                      <StrmIcon name="refresh" :size="17" />
                      <span>
                        <b>覆盖重刮</b>
                        <em>重新下载全部 NFO 与图片</em>
                      </span>
                    </button>
                    <template v-if="activeSubs.length">
                      <span class="strm-menu-label">
                        当前分类：{{ activeSubs.map((sub) => sub.label).join('、') }}
                      </span>
                      <button @click="runScan(false, true)">
                        <span><b>补全当前分类</b></span>
                      </button>
                      <button @click="runScan(true, true)">
                        <span><b>覆盖重刮当前分类</b></span>
                      </button>
                    </template>
                  </div>
                </template>
              </div>
            </div>
          </div>
          <div class="strm-stats">
            <button
              v-for="sf in STATUS_FILTERS"
              :key="sf.key"
              :class="['strm-stat', 'is-' + sf.key, statusFilter === sf.key && 'active']"
              :aria-pressed="statusFilter === sf.key"
              @click="statusFilter = statusFilter === sf.key ? 'all' : sf.key"
            >
              <div class="strm-stat-top">
                <span>{{ sf.label }}</span>
                <span class="strm-stat-icon"><StrmIcon :name="sf.icon" :size="19" /></span>
              </div>
              <div class="strm-stat-number">
                {{ statusCounts[sf.key] }}
                <small>项媒体</small>
              </div>
              <div class="strm-stat-bottom">
                <span>
                  <i></i>
                  {{ sf.hint }}
                </span>
                <StrmIcon name="arrow" :size="15" />
              </div>
            </button>
          </div>
          <div class="strm-library-bar">
            <div class="strm-library-title">
              <h2>{{ sectionTitle === '全部' ? '全部媒体' : sectionTitle }}</h2>
              <span>{{ visibleItems.length }}</span>
              <button
                v-if="statusFilter !== 'all'"
                class="strm-filter-chip"
                @click="statusFilter = 'all'"
              >
                {{ STATUS_LABELS[statusFilter] }}
                <StrmIcon name="close" :size="12" />
              </button>
            </div>
            <div class="strm-library-meta">
              <label v-if="mobileCategories.length" class="strm-mobile-categories">
                <select
                  :value="subFilters[0] || ''"
                  aria-label="媒体分类"
                  @change="selectMobileCategory($event.target.value)"
                >
                  <option value="">全部分类</option>
                  <option
                    v-for="category in mobileCategories"
                    :key="category.key"
                    :value="category.key"
                  >
                    {{ category.label }}
                  </option>
                </select>
                <StrmIcon name="chevron" :size="12" />
              </label>
              <span class="strm-updated">
                <i></i>
                {{ refreshedLabel }} 更新
              </span>
            </div>
          </div>
          <div class="strm-toolbar">
            <label class="strm-search-wrap">
              <StrmIcon name="search" :size="18" />
              <input
                v-model="keyword"
                class="strm-search"
                type="search"
                aria-label="搜索媒体"
                placeholder="搜索媒体名称或文件路径…"
              />
              <span class="strm-search-key">搜索</span>
            </label>
            <div class="strm-toolbar-right">
              <label class="strm-sort">
                <StrmIcon name="sort" :size="17" />
                <select v-model="sortBy" aria-label="媒体排序">
                  <option value="recent">最近更新</option>
                  <option value="title">名称排序</option>
                  <option value="scraped">最近刮削</option>
                </select>
                <StrmIcon name="chevron" :size="13" />
              </label>
              <div class="strm-view-toggle" aria-label="显示方式">
                <button
                  :class="{ active: displayMode === 'poster' }"
                  :aria-pressed="displayMode === 'poster'"
                  title="海报视图"
                  aria-label="海报视图"
                  @click="displayMode = 'poster'"
                >
                  <StrmIcon name="grid" :size="17" />
                </button>
                <button
                  :class="{ active: displayMode === 'list' }"
                  :aria-pressed="displayMode === 'list'"
                  title="列表视图"
                  aria-label="列表视图"
                  @click="displayMode = 'list'"
                >
                  <StrmIcon name="list" :size="18" />
                </button>
              </div>
            </div>
          </div>
          <div class="strm-content">
            <div v-if="loading" class="strm-skel-grid">
              <div v-for="n in 10" :key="n" class="strm-skel">
                <div class="strm-skel-poster"></div>
                <div class="strm-skel-line"></div>
                <div class="strm-skel-line is-short"></div>
              </div>
            </div>
            <div v-else-if="!items.length" class="strm-empty">
              <span class="strm-empty-icon"><StrmIcon name="library" :size="32" /></span>
              <h3>收藏，从这里开始</h3>
              <p>将 .strm 文件放入监控目录，然后刷新媒体库。</p>
              <button class="strm-btn ghost" @click="refreshAll">刷新媒体库</button>
            </div>
            <div v-else-if="!visibleItems.length" class="strm-empty">
              <span class="strm-empty-icon"><StrmIcon name="search" :size="32" /></span>
              <h3>没有找到匹配的媒体</h3>
              <p>试试其他关键词，或清除当前筛选。</p>
              <button class="strm-btn ghost" @click="resetFilters">清除筛选</button>
            </div>
            <div v-else :class="['strm-grid', displayMode === 'list' && 'is-list']">
              <article v-for="item in visibleItems" :key="item.path" class="strm-card">
                <div class="strm-poster-wrap">
                  <button
                    class="strm-poster"
                    :style="posterStyle(item.title)"
                    :aria-label="'查看 ' + item.title + ' 的详情'"
                    @click="openDetail(item)"
                  >
                    <img
                      v-if="!posterFailed[item.path]"
                      class="strm-poster-img"
                      :src="coverUrl(props.api, props.pluginId, item)"
                      :alt="item.title"
                      loading="lazy"
                      @error="markPosterFailed(item.path)"
                    />
                    <span v-else class="strm-poster-fallback">
                      <StrmIcon
                        :name="item.type === 'tv' ? 'tv' : item.type === 'music' ? 'music' : 'film'"
                        :size="38"
                      />
                      <b>{{ item.title }}</b>
                    </span>
                    <span :class="['strm-poster-status', 'is-' + statusClass(displayStatus(item))]">
                      <StrmIcon v-if="displayStatus(item) === 'scraped'" name="check" :size="12" />
                      <i v-else></i>
                      {{ STATUS_LABELS[displayStatus(item)] }}
                    </span>
                    <span :class="['strm-type', typeClass(item.type)]">
                      {{ typeLabel(item.type) }}
                    </span>
                    <span class="strm-poster-open"><StrmIcon name="arrow" :size="23" /></span>
                    <span
                      v-if="displayStatus(item) === 'failed'"
                      class="strm-fail-strip"
                      :title="item.error_message"
                    >
                      <StrmIcon name="alert" :size="13" />
                      {{ errorLabel(item.error_code) }}
                    </span>
                  </button>
                </div>
                <div class="strm-card-info">
                  <div class="strm-card-heading">
                    <button class="strm-card-title" :title="item.path" @click="openDetail(item)">
                      {{ item.title }}
                    </button>
                    <button
                      class="strm-card-go"
                      :disabled="busy"
                      :title="'重新刮削 ' + item.title + '（覆盖元数据）'"
                      :aria-label="'重新刮削 ' + item.title"
                      @click="scrapeItem(item)"
                    >
                      <StrmIcon name="sparkles" :size="16" />
                    </button>
                  </div>
                  <div class="strm-card-sub">
                    <span>{{ subLabel(item) || item.category || typeLabel(item.type) }}</span>
                    <i></i>
                    {{ fileSummary(item) }}
                  </div>
                  <span v-if="displayMode === 'list'" class="strm-card-path">{{ item.path }}</span>
                </div>
                <div v-if="displayMode === 'list'" class="strm-list-card-meta">
                  <span :class="['strm-tag', statusTagClass(displayStatus(item))]">
                    {{ STATUS_LABELS[displayStatus(item)] }}
                  </span>
                  <small>{{ formatDate(item.last_file_change) }} 更新</small>
                </div>
              </article>
            </div>
          </div>
          <footer class="strm-library-foot">
            <span>
              共 {{ items.length }} 项媒体
              <span class="strm-footer-dot">·</span>
              当前显示 {{ visibleItems.length }} 项
            </span>
            <span>
              <StrmIcon name="checkCircle" :size="13" />
              媒体资料，由你掌控
            </span>
          </footer>
        </template>
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
              />
              <span class="strm-dhero-scrim"></span>

              <button class="strm-back" @click="closeDetail">
                <span class="strm-back-arrow">←</span>
                返回媒体库
              </button>

              <div class="strm-dhero-body">
                <div class="strm-dhero-poster" :style="posterStyle(current.title)">
                  <img
                    v-if="!posterFailed[current.path]"
                    class="strm-poster-img"
                    :src="coverUrl(props.api, props.pluginId, current)"
                    :alt="current.title"
                    @error="markPosterFailed(current.path)"
                  />
                </div>
                <div class="strm-dhero-info">
                  <div class="strm-dhero-kicker">
                    {{ typeLabel(current.type) }}
                    <template v-if="subLabel(current)">· {{ subLabel(current) }}</template>
                  </div>
                  <h2>{{ current.title }}</h2>
                  <div class="strm-dhero-tags">
                    <span class="strm-tag is-plain">{{ current.total_files }} 个文件</span>
                    <span
                      :class="['strm-tag', statusTagClass(displayStatus(current))]"
                      :title="STATUS_LABELS[displayStatus(current)]"
                    >
                      <i class="strm-status-dot"></i>
                      {{ STATUS_LABELS[displayStatus(current)] }}
                    </span>
                    <span
                      v-if="displayStatus(current) === 'failed' && current.error_code"
                      class="strm-tag is-fail"
                      :title="current.error_message"
                    >
                      {{ errorLabel(current.error_code) }}
                    </span>
                  </div>
                  <!-- F6 两行时间：文件最后变更 与 真实刮削时间分开显示 -->
                  <div class="strm-dhero-times">
                    <span>
                      <b>文件更新</b>
                      {{ formatDate(current.last_file_change) }}
                    </span>
                    <span class="strm-sep">·</span>
                    <span>
                      <b>上次刮削</b>
                      {{ statusOf(current) === 'pending' ? '尚未刮削' : formatTime(current.last_scrape) }}
                    </span>
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
              >
                {{ season.name }}
                <em>{{ season.files.length }}</em>
              </button>
            </div>

            <div class="strm-list-actions">
              <button class="strm-mini" @click="selectAll">全选本页</button>
              <button class="strm-mini" @click="selectUnscraped">选中未刮削</button>
              <button class="strm-mini" @click="clearSelection">清空</button>
              <span class="strm-list-sum">
                共
                <b>{{ rows.length }}</b>
                项
                <template v-if="selectedRows.length">
                  · 已选
                  <b>{{ selectedRows.length }}</b>
                </template>
              </span>
            </div>

            <div class="strm-list">
              <div v-if="!rows.length" class="strm-empty">暂无文件</div>
              <div
                v-for="row in rows"
                :key="row.path"
                :class="[
                  'strm-row',
                  `is-${statusClass(displayStatus(row))}`,
                  selected.has(row.path) && 'is-selected',
                ]"
              >
                <span class="strm-row-bar"></span>
                <input
                  type="checkbox"
                  :aria-label="'选择 ' + row.name"
                  :checked="selected.has(row.path)"
                  @change="toggleRow(row.path)"
                />
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
                  <i></i>
                  {{ STATUS_LABELS[displayStatus(row)] }}
                </span>
                <button
                  class="strm-row-go"
                  :disabled="busy"
                  title="重下该条目的 NFO 与图片（覆盖）"
                  @click="scrapeOne(row)"
                >
                  刮削
                </button>
              </div>
            </div>

            <!-- 收尾操作条：只保留「按选中执行」，整目录/整剧的主 CTA 已上移到 Hero，
               避免同一个动作在上下两处重复出现 -->
            <div class="strm-detail-foot">
              <span class="strm-sel">
                已选
                <b>{{ selectedRows.length }}</b>
                项
              </span>
              <div class="strm-foot-right">
                <button
                  class="strm-btn ghost"
                  :disabled="busy || !selectedRows.length"
                  @click="scrapeSelected"
                >
                  刮削选中{{
                    current.type === 'tv' ? '单集' : current.type === 'music' ? '曲目' : '版本'
                  }}
                </button>
              </div>
            </div>
          </div>
        </template>

        <!-- ============================ 刮削记录 ============================ -->
        <template v-else-if="view === 'records'">
          <div class="strm-rec-head">
            <span class="strm-rec-count">
              最近
              <b>{{ records.length }}</b>
              条
            </span>
            <div class="strm-foot-right">
              <button class="strm-btn ghost" :disabled="recordsLoading" @click="loadRecords">
                刷新记录
              </button>
              <button class="strm-btn ghost" :disabled="!records.length" @click="clearRecords">
                清空记录
              </button>
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
            <input
              v-model="recordFilter.since"
              type="date"
              @change="loadRecords"
              title="起始日期"
            />
            <span class="strm-rec-filter-sep">至</span>
            <input
              v-model="recordFilter.until"
              type="date"
              @change="loadRecords"
              title="结束日期"
            />
            <button v-if="hasRecordFilter" class="strm-mini" @click="resetRecordFilter">
              重置
            </button>
            <button
              class="strm-btn small ghost"
              :disabled="!statusCounts.failed"
              @click="retryFailed"
            >
              重试失败项（{{ statusCounts.failed }}）
            </button>
          </div>

          <div v-if="recordsLoading" class="strm-empty">正在读取记录…</div>
          <div v-else-if="!records.length" class="strm-empty">
            <p>{{ hasRecordFilter ? '没有符合筛选条件的记录' : '暂无刮削记录' }}</p>
            <button v-if="hasRecordFilter" class="strm-mini" @click="resetRecordFilter">
              清除筛选
            </button>
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
                  <span v-if="record.category" class="strm-tag is-plain">
                    {{ record.category }}
                  </span>
                  <span v-if="record.exists === false" class="strm-tag is-warn">目录已删除</span>
                  <!-- F9 错误码中文映射：失败记录标出结构化原因 -->
                  <span
                    v-if="!record.success && record.error_code"
                    class="strm-tag is-fail"
                    :title="record.message"
                  >
                    {{ errorLabel(record.error_code) }}
                  </span>
                </div>
                <div v-if="record.message" class="strm-rec-msg" :title="record.message">
                  {{ record.message }}
                </div>
              </div>
              <span :class="['strm-rec-badge', record.success ? 'is-ok' : 'is-fail']">
                {{ record.success ? '成功' : '失败' }}
              </span>
              <span class="strm-rec-time">{{ record.time }}</span>
            </div>
          </div>
        </template>
      </div>
    </main>
    <Transition name="strm-drawer">
      <div v-if="queuePanelOpen" class="strm-queue-layer">
        <button
          class="strm-queue-backdrop"
          aria-label="关闭任务队列"
          @click="queuePanelOpen = false"
        ></button>
        <aside
          ref="queuePanelEl"
          class="strm-queue-panel"
          @keydown="trapQueueFocus"
          role="dialog"
          aria-modal="true"
          aria-label="任务队列"
        >
          <div class="strm-drawer-head">
            <span class="strm-drawer-icon"><StrmIcon name="queue" :size="23" /></span>
            <div>
              <h2>任务队列</h2>
              <p>{{ busy ? '正在处理你的媒体资料' : '所有任务已就绪' }}</p>
            </div>
            <button class="strm-icon-btn" aria-label="关闭任务队列" @click="queuePanelOpen = false">
              <StrmIcon name="close" :size="19" />
            </button>
          </div>
          <div class="strm-queue-stats">
            <div>
              <b>{{ queue.stats.done || 0 }}</b>
              <span>已完成</span>
            </div>
            <div>
              <b>{{ queue.stats.failed || 0 }}</b>
              <span>失败</span>
            </div>
            <div>
              <b>{{ queue.queued_total || 0 }}</b>
              <span>排队中</span>
            </div>
          </div>
          <div class="strm-queue-scroll">
            <div v-if="queue.running" class="strm-queue-sec">
              <span class="strm-queue-sec-title">正在执行</span>
              <div class="strm-queue-item is-busy">
                <StrmIcon name="refresh" :size="18" class="strm-spinning" />
                <div>
                  <b>{{ queueTargetLabel(queue.running) }}</b>
                  <small>{{ kindLabel(queue.running.kind) }} · 处理中</small>
                </div>
              </div>
            </div>
            <div v-if="queue.queued?.length" class="strm-queue-sec">
              <span class="strm-queue-sec-title">接下来 · {{ queue.queued_total }}</span>
              <div v-for="item in queue.queued" :key="item.key" class="strm-queue-item">
                <StrmIcon name="clock" :size="17" />
                <div>
                  <b>{{ queueTargetLabel(item) }}</b>
                  <small>{{ kindLabel(item.kind) }}</small>
                </div>
                <button class="strm-mini" @click="cancelQueue(item.target)">取消</button>
              </div>
            </div>
            <div v-if="queue.recent?.length" class="strm-queue-sec">
              <span class="strm-queue-sec-title">最近完成</span>
              <div
                v-for="(item, index) in queue.recent.slice(0, 8)"
                :key="index"
                :class="['strm-queue-item', item.success ? 'is-ok' : 'is-fail']"
              >
                <StrmIcon :name="item.success ? 'checkCircle' : 'alert'" :size="17" />
                <div>
                  <b>{{ queueTargetLabel(item) }}</b>
                  <small>{{ item.success ? '媒体资料已补全' : item.message || '刮削失败' }}</small>
                </div>
              </div>
            </div>
            <div v-if="!busy && !queue.recent?.length" class="strm-empty">
              <StrmIcon name="checkCircle" :size="36" />
              <h3>暂时没有任务</h3>
              <p>扫描媒体或手动刮削后，进度会显示在这里。</p>
            </div>
          </div>
          <div class="strm-queue-actions">
            <button
              class="strm-btn ghost"
              :disabled="!statusCounts.failed || busy"
              @click="retryFailed"
            >
              重试失败项
            </button>
            <button class="strm-btn ghost" :disabled="!busy" @click="cancelQueue()">
              取消全部排队
            </button>
          </div>
        </aside>
      </div>
    </Transition>
  </div>
</template>
<style scoped>
/* 色彩跟随宿主主题；预览页只提供同名主题变量。 */
.strm {
  --strm-primary: rgb(var(--v-theme-primary, 45, 113, 86));
  --strm-surface: rgb(var(--v-theme-surface, 255, 255, 255));
  --strm-text: rgb(var(--v-theme-on-surface, 39, 48, 45));
  --strm-muted: rgba(var(--v-theme-on-surface, 39, 48, 45), 0.49);
  --strm-line: rgba(var(--v-theme-on-surface, 39, 48, 45), 0.085);
  --strm-wash: color-mix(in srgb, var(--strm-surface) 97%, var(--strm-text));
  --strm-tint: color-mix(in srgb, var(--strm-primary) 8%, var(--strm-surface));
  width: 100%;
  height: var(--strm-h, 90vh);
  min-height: 380px;
  display: flex;
  position: relative;
  overflow: hidden;
  background: var(--strm-wash);
  color: var(--strm-text);
  font-size: 13px;
  line-height: 1.55;
  font-family: inherit;
  text-align: left;
}
.strm *,
.strm *::before,
.strm *::after {
  box-sizing: border-box;
}
.strm button,
.strm input,
.strm select {
  font: inherit;
  color: inherit;
}
.strm button {
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}
.strm button:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}
.strm button:focus-visible,
.strm input:focus-visible,
.strm select:focus-visible {
  outline: 2px solid var(--strm-primary);
  outline-offset: 3px;
}
.strm h1,
.strm h2,
.strm h3,
.strm p {
  margin: 0;
}
.strm-side {
  display: flex;
  flex-direction: column;
  width: 224px;
  flex: 0 0 224px;
  min-height: 0;
  background: var(--strm-surface);
  border-right: 1px solid var(--strm-line);
  padding: 28px 15px 13px;
  overflow: hidden;
}
.strm-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 7px;
  margin-bottom: 30px;
}
.strm-logo {
  display: grid;
  place-items: center;
  width: 38px;
  height: 40px;
  border-radius: 11px;
  background: var(--strm-primary);
  color: white;
  box-shadow: 0 4px 9px color-mix(in srgb, var(--strm-primary) 15%, transparent);
}
.strm-brand strong {
  display: block;
  font-size: 20px;
  line-height: 1.15;
  letter-spacing: -0.7px;
  font-weight: 750;
}
.strm-brand strong span {
  font-weight: 400;
}
.strm-brand small {
  font-size: 10px;
  color: var(--strm-muted);
  display: block;
  margin-top: 5px;
  letter-spacing: 0.6px;
}
.strm-workspace {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 10px;
  border: 1px solid var(--strm-line);
  border-radius: 9px;
  margin-bottom: 27px;
  font-size: 13px;
  font-weight: 600;
}
.strm-workspace small {
  display: block;
  font-size: 11px;
  font-weight: 400;
  color: var(--strm-muted);
  margin-top: 1px;
}
.strm-workspace-mark {
  background: var(--strm-wash);
  border: 1px solid var(--strm-line);
  width: 30px;
  height: 32px;
  display: grid;
  place-items: center;
  border-radius: 6px;
}
.strm-online-dot {
  display: inline-block;
  flex-shrink: 0;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #4d9b76;
  box-shadow: 0 0 0 3px #4d9b7610;
}
.strm-online-dot.off {
  background: #9da5a1;
  box-shadow: none;
}
.strm-workspace > .strm-online-dot {
  margin-left: auto;
}
.strm-side-label {
  display: flex;
  justify-content: space-between;
  color: var(--strm-muted);
  font-size: 11px;
  letter-spacing: 0.8px;
  padding: 0 12px;
  margin-bottom: 10px;
}
.strm-side-label > span {
  font-variant-numeric: tabular-nums;
  opacity: 0.65;
}
.strm-side-nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.strm-sopt {
  border: 0;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  width: 100%;
  min-height: 38px;
  background: transparent;
  border-radius: 7px;
  color: var(--strm-muted) !important;
  text-align: left;
  font-size: 13px !important;
  transition:
    background 0.15s,
    color 0.15s;
}
.strm-sopt:hover {
  color: var(--strm-primary) !important;
  background: var(--strm-wash);
}
.strm-sopt.on {
  color: var(--strm-primary) !important;
  background: var(--strm-tint);
  font-weight: 600 !important;
}
.strm-sopt em {
  font-size: 11px;
  font-weight: 400;
  font-style: normal;
  margin-left: auto;
  font-variant-numeric: tabular-nums;
  color: var(--strm-muted);
}
.strm-sopt.on em {
  color: var(--strm-primary);
  background: color-mix(in srgb, var(--strm-primary) 9%, transparent);
  border-radius: 4px;
  padding: 0 5px;
}
.strm-nav-arrow {
  margin-left: auto;
  transform: rotate(-90deg);
  opacity: 0.5;
}
.strm-side-divider {
  height: 1px;
  flex-shrink: 0;
  background: var(--strm-line);
  margin: 23px 9px 21px;
}
.strm-category-nav {
  overflow-y: auto;
  min-height: 60px;
  scrollbar-width: thin;
  flex: 1;
  padding-bottom: 15px;
}
.strm-sgroup {
  color: var(--strm-muted);
  font-size: 11px;
  padding: 5px 12px 6px;
  opacity: 0.7;
}
.strm-sgroup:not(:first-child) {
  margin-top: 9px;
}
.strm-sopt.is-child {
  min-height: 32px;
  padding: 6px 12px;
  gap: 12px;
  font-size: 12px !important;
}
.strm-category-dot {
  width: 5px;
  height: 5px;
  border-radius: 2px;
  background: #8c9cac;
  margin-left: 4px;
}
.strm-category-dot.tv {
  background: #99a68b;
}
.strm-category-dot.movie {
  background: #b3a188;
}
.strm-sopt b {
  font-size: 10px;
  font-weight: 500;
  color: #a37435;
  background: #c9974f12;
  padding: 0 4px;
  border-radius: 3px;
}
.strm-side-empty {
  color: var(--strm-muted);
  padding: 8px 12px;
  font-size: 12px;
}
.strm-side-bottom {
  margin-top: auto;
  padding-top: 12px;
}
.strm-monitor-card {
  display: flex;
  align-items: center;
  gap: 9px;
  background: var(--strm-wash);
  border-radius: 8px;
  padding: 11px 9px;
  margin-bottom: 10px;
}
.strm-monitor-card b {
  display: block;
  font-size: 11px;
  font-weight: 600;
}
.strm-monitor-card small {
  display: block;
  font-size: 9px;
  color: var(--strm-muted);
  margin-top: 2px;
}
.strm-monitor-icon {
  color: var(--strm-primary);
}
.strm-monitor-card > i {
  margin-left: auto;
  width: 5px;
  height: 5px;
}
.strm-settings {
  margin-bottom: 10px;
}
.strm-settings-arrow {
  margin-left: auto;
  opacity: 0.55;
}
.strm-side-caption {
  padding: 0 7px;
  color: var(--strm-muted);
  opacity: 0.55;
  font-size: 9px;
  letter-spacing: 0.8px;
  display: flex;
  justify-content: space-between;
}
.strm-side-caption span {
  letter-spacing: 0;
}
.strm-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.strm-head {
  height: 65px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  padding: 0 32px;
  border-bottom: 1px solid var(--strm-line);
  background: var(--strm-surface);
  gap: 12px;
}
.strm-crumb {
  display: flex;
  align-items: center;
  gap: 11px;
  font-size: 12px;
  color: var(--strm-muted);
  white-space: nowrap;
}
.strm-crumb > svg {
  opacity: 0.7;
}
.strm-crumb b {
  font-weight: 500;
  color: var(--strm-text);
}
.strm-crumb-slash {
  opacity: 0.4;
}
.strm-head-actions {
  display: flex;
  align-items: center;
  gap: 13px;
  margin-left: auto;
}
.strm-monitor-badge {
  color: var(--strm-muted);
  font-size: 11px;
  display: flex;
  align-items: center;
  gap: 7px;
  white-space: nowrap;
}
.strm-queue-trigger {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 12px !important;
  background: transparent;
  border: 0;
  padding: 7px 2px;
}
.strm-queue-trigger:hover {
  color: var(--strm-primary);
}
.strm-queue-trigger b {
  background: var(--strm-tint);
  color: var(--strm-primary);
  font-size: 10px;
  border-radius: 4px;
  padding: 0 5px;
}
.strm-queue-trigger.is-busy > svg {
  color: #bc8b36;
}
.strm-head-divider {
  height: 17px;
  width: 1px;
  background: var(--strm-line);
}
.strm-icon-btn {
  width: 29px;
  height: 29px;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 0;
  border-radius: 6px;
  color: var(--strm-muted) !important;
}
.strm-icon-btn:hover {
  background: var(--strm-wash);
  color: var(--strm-primary) !important;
}
.strm-body {
  padding: 27px 32px 0;
  overflow-y: auto;
  flex: 1;
  min-height: 0;
  scrollbar-width: thin;
}
.strm-body.is-grid {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.strm-page-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 25px;
  flex-shrink: 0;
}
.strm-eyebrow {
  font-size: 9px;
  font-weight: 550;
  letter-spacing: 1.9px;
  color: var(--strm-muted);
  opacity: 0.85;
  margin-bottom: 5px;
}
.strm-page-heading h1 {
  font-size: 27px;
  letter-spacing: -1px;
  line-height: 1.55;
  font-weight: 650;
}
.strm-title-dot {
  color: var(--strm-primary);
  font-size: 33px;
  margin-left: 3px;
}
.strm-page-heading p {
  margin-top: 3px;
  color: var(--strm-muted);
  font-size: 12px;
}
.strm-page-actions {
  display: flex;
  align-items: center;
  gap: 9px;
  padding-top: 17px;
}
.strm-btn {
  display: inline-flex;
  justify-content: center;
  align-items: center;
  gap: 7px;
  min-height: 36px;
  padding: 8px 14px;
  border: 1px solid transparent;
  background: var(--strm-primary);
  border-radius: 6px;
  color: rgb(var(--v-theme-on-primary, 255, 255, 255)) !important;
  font-size: 12px !important;
  font-weight: 550 !important;
  white-space: nowrap;
  transition:
    filter 0.15s,
    box-shadow 0.15s;
}
.strm-btn:hover:not(:disabled) {
  filter: brightness(0.95);
  box-shadow: 0 2px 7px #0000000b;
}
.strm-btn.ghost {
  color: var(--strm-text) !important;
  background: var(--strm-surface);
  border-color: var(--strm-line);
}
.strm-btn.small {
  min-height: 29px;
  font-size: 11px !important;
  padding: 5px 10px;
}
.strm-scan {
  position: relative;
}
.strm-menu-dismiss {
  position: fixed;
  inset: 0;
  border: 0;
  background: transparent;
  z-index: 31;
  cursor: default !important;
}
.strm-scan-menu {
  position: absolute;
  right: 0;
  top: calc(100% + 9px);
  width: 260px;
  background: var(--strm-surface);
  box-shadow: 0 12px 45px #0000001c;
  border: 1px solid var(--strm-line);
  border-radius: 10px;
  padding: 7px;
  z-index: 32;
}
.strm-menu-label {
  color: var(--strm-muted);
  font-size: 10px;
  display: block;
  padding: 7px 9px;
}
.strm-scan-menu > button {
  width: 100%;
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 10px 9px;
  background: transparent;
  border: 0;
  border-radius: 5px;
  text-align: left;
}
.strm-scan-menu > button:hover {
  background: var(--strm-wash);
}
.strm-scan-menu b {
  font-size: 12px;
  font-weight: 550;
  display: block;
}
.strm-scan-menu em {
  font-size: 10px;
  color: var(--strm-muted);
  font-style: normal;
  display: block;
  margin-top: 2px;
}
.strm-scan-menu .strm-scan-overwrite {
  border-top: 1px solid var(--strm-line);
  border-radius: 0 0 5px 5px;
  margin-top: 3px;
  padding-top: 13px;
}
.strm-stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 15px;
  flex-shrink: 0;
  margin-bottom: 28px;
}
.strm-stat {
  text-align: left;
  padding: 15px 18px 12px;
  border: 1px solid var(--strm-line);
  border-radius: 9px;
  background: var(--strm-surface);
  position: relative;
  overflow: hidden;
  transition:
    border 0.15s,
    box-shadow 0.15s;
}
.strm-stat:hover,
.strm-stat.active {
  border-color: color-mix(in srgb, var(--strm-primary) 45%, var(--strm-line));
  box-shadow: 0 2px 9px #00000005;
}
.strm-stat-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: var(--strm-muted);
  font-size: 12px;
}
.strm-stat-icon {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border-radius: 7px;
  color: var(--strm-primary);
  background: var(--strm-tint);
}
.strm-stat.is-pending .strm-stat-icon {
  background: #bf8e3b0d;
  color: #ba8d45;
}
.strm-stat.is-failed .strm-stat-icon {
  background: #c66e680d;
  color: #bc746d;
}
.strm-stat-number {
  font-size: 31px;
  line-height: 1.3;
  font-weight: 600;
  letter-spacing: -1px;
  font-variant-numeric: tabular-nums;
  margin: 0 0 10px;
}
.strm-stat-number small {
  font-size: 10px;
  font-weight: 400;
  letter-spacing: 0;
  color: var(--strm-muted);
  margin-left: 8px;
}
.strm-stat-bottom {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: var(--strm-muted);
  font-size: 10px;
  padding-top: 9px;
  border-top: 1px solid var(--strm-line);
}
.strm-stat-bottom span {
  display: flex;
  align-items: center;
  gap: 5px;
}
.strm-stat-bottom i {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: #7aa48c;
}
.strm-stat.is-pending .strm-stat-bottom i {
  background: #bc9b67;
}
.strm-stat.is-failed .strm-stat-bottom i {
  background: #bd817b;
}
.strm-stat-bottom > svg {
  opacity: 0.5;
}
.strm-library-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
  flex-shrink: 0;
}
.strm-library-title {
  display: flex;
  align-items: center;
  gap: 9px;
}
.strm-library-title h2 {
  font-size: 14px;
  font-weight: 600;
  letter-spacing: -0.25px;
}
.strm-library-title > span {
  font-size: 10px;
  border: 1px solid var(--strm-line);
  padding: 0 6px;
  border-radius: 4px;
  color: var(--strm-muted);
}
.strm-filter-chip {
  display: flex;
  gap: 5px;
  align-items: center;
  font-size: 10px !important;
  border: 0;
  border-radius: 4px;
  color: var(--strm-primary) !important;
  padding: 3px 6px;
  background: var(--strm-tint);
}
.strm-updated {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 10px;
  color: var(--strm-muted);
}
.strm-updated i {
  width: 4px;
  height: 4px;
  background: #a2aaa5;
  border-radius: 50%;
}
.strm-toolbar {
  display: flex;
  align-items: center;
  gap: 15px;
  margin-bottom: 20px;
  flex-shrink: 0;
}
.strm-search-wrap {
  min-width: 0;
  width: min(390px, 52%);
  display: flex;
  align-items: center;
  gap: 9px;
  background: var(--strm-surface);
  border: 1px solid var(--strm-line);
  padding: 0 11px;
  height: 35px;
  border-radius: 6px;
  color: var(--strm-muted);
}
.strm-search-wrap:focus-within {
  border-color: color-mix(in srgb, var(--strm-primary) 60%, var(--strm-line));
}
.strm-search {
  width: 100%;
  min-width: 0;
  background: transparent;
  padding: 0;
  border: 0;
  outline: none !important;
  font-size: 11px !important;
}
.strm-search::placeholder {
  color: var(--strm-muted);
  opacity: 0.8;
}
.strm-search-key {
  font-size: 9px;
  border: 1px solid var(--strm-line);
  padding: 0 3px;
  border-radius: 3px;
  white-space: nowrap;
  opacity: 0.65;
}
.strm-toolbar-right {
  display: flex;
  align-items: center;
  margin-left: auto;
  gap: 12px;
}
.strm-sort {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--strm-muted);
}
.strm-sort select {
  appearance: none;
  border: 0;
  background: transparent;
  padding: 5px 0;
  outline: none;
  cursor: pointer;
  font-size: 11px;
}
.strm-sort select option {
  background: var(--strm-surface);
  color: var(--strm-text);
}
.strm-view-toggle {
  display: flex;
  padding: 3px;
  gap: 2px;
  border-radius: 6px;
  background: color-mix(in srgb, var(--strm-surface) 92%, var(--strm-text));
}
.strm-view-toggle button {
  border: 0;
  background: transparent;
  border-radius: 4px;
  display: grid;
  place-items: center;
  width: 28px;
  height: 25px;
  color: var(--strm-muted);
}
.strm-view-toggle button.active {
  background: var(--strm-surface);
  box-shadow: 0 1px 3px #0000000e;
  color: var(--strm-primary);
}
.strm-content {
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  flex: 1;
  scrollbar-width: thin;
  padding: 1px 2px 20px;
  margin: -1px -2px 0;
}
.strm-grid,
.strm-skel-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(149px, 1fr));
  gap: 24px 18px;
}
.strm-card {
  min-width: 0;
}
.strm-poster-wrap {
  position: relative;
}
.strm-poster {
  width: 100%;
  aspect-ratio: 2 / 3;
  border: 0;
  padding: 0;
  display: block;
  position: relative;
  border-radius: 8px;
  overflow: hidden;
  text-align: left;
  box-shadow: 0 2px 5px #00000008;
  isolation: isolate;
}
.strm-poster::after {
  content: '';
  position: absolute;
  inset: 0;
  z-index: -1;
  background: linear-gradient(180deg, #00000012, transparent 45%, #00000057);
  pointer-events: none;
}
.strm-poster-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  position: absolute;
  inset: 0;
  z-index: -2;
  transition:
    transform 0.35s,
    filter 0.35s;
}
.strm-poster:hover .strm-poster-img {
  transform: scale(1.045);
  filter: brightness(0.72);
}
.strm-poster-fallback {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  padding: 20px;
  gap: 15px;
  text-align: center;
  color: #ffffffbd;
}
.strm-poster-fallback b {
  font-size: 16px;
  font-weight: 500;
}
.strm-poster-status {
  position: absolute;
  top: 9px;
  right: 9px;
  display: flex;
  align-items: center;
  gap: 3px;
  padding: 3px 6px;
  border: 1px solid #ffffff25;
  font-size: 9px;
  color: white;
  background: #20302bd9;
  border-radius: 4px;
  backdrop-filter: blur(12px);
}
.strm-poster-status.is-none {
  background: #3d3629cc;
}
.strm-poster-status.is-fail {
  background: #a24e47e8;
}
.strm-poster-status.is-busy {
  background: #aa8733e8;
}
.strm-poster-status i {
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: #e6c993;
  margin: 0 2px;
}
.strm-type {
  position: absolute;
  bottom: 10px;
  left: 10px;
  color: white;
  background: #ffffff20;
  border: 1px solid #ffffff33;
  font-size: 9px;
  padding: 2px 5px;
  border-radius: 3px;
  backdrop-filter: blur(9px);
}
.strm-poster-open {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  opacity: 0;
  transition: opacity 0.2s;
}
.strm-poster-open > svg {
  box-sizing: content-box;
  padding: 14px;
  border: 1px solid #ffffff55;
  background: #ffffff15;
  border-radius: 50%;
  backdrop-filter: blur(6px);
}
.strm-poster:hover .strm-poster-open,
.strm-poster:focus-visible .strm-poster-open {
  opacity: 1;
}
.strm-fail-strip {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  padding: 6px 9px;
  background: #9a5549e0;
  color: white;
  font-size: 9px;
  display: flex;
  align-items: center;
  gap: 5px;
}
.strm-poster:has(.strm-fail-strip) .strm-type {
  bottom: 36px;
}
.strm-card-info {
  padding-top: 9px;
}
.strm-card-heading {
  display: flex;
  align-items: center;
  gap: 4px;
}
.strm-card-title {
  display: block;
  text-align: left;
  padding: 0;
  background: transparent;
  border: 0;
  font-size: 12px !important;
  font-weight: 550 !important;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  min-width: 0;
  flex: 1;
  letter-spacing: 0.1px;
}
.strm-card-title:hover {
  color: var(--strm-primary);
}
.strm-card-go {
  flex-shrink: 0;
  display: grid;
  place-items: center;
  width: 23px;
  height: 23px;
  border: 0;
  background: transparent;
  border-radius: 4px;
  color: var(--strm-muted) !important;
  opacity: 0.3;
}
.strm-card:hover .strm-card-go,
.strm-card-go:focus-visible {
  opacity: 1;
}
.strm-card-go:hover {
  background: var(--strm-tint);
  color: var(--strm-primary) !important;
}
.strm-card-sub {
  display: flex;
  gap: 5px;
  align-items: center;
  font-size: 10px;
  color: var(--strm-muted);
  overflow: hidden;
  white-space: nowrap;
  margin-top: 1px;
}
.strm-card-sub i {
  width: 2px;
  height: 2px;
  background: currentColor;
  opacity: 0.6;
  border-radius: 50%;
}
.strm-grid.is-list {
  display: flex;
  flex-direction: column;
  gap: 0;
  background: var(--strm-surface);
  border: 1px solid var(--strm-line);
  border-radius: 8px;
}
.strm-grid.is-list .strm-card {
  display: flex;
  gap: 15px;
  align-items: center;
  padding: 13px 16px;
  border-bottom: 1px solid var(--strm-line);
}
.strm-grid.is-list .strm-card:last-child {
  border: 0;
}
.strm-grid.is-list .strm-poster-wrap {
  width: 44px;
  flex: 0 0 44px;
}
.strm-grid.is-list .strm-poster {
  border-radius: 4px;
}
.strm-grid.is-list .strm-type,
.strm-grid.is-list .strm-poster-status,
.strm-grid.is-list .strm-fail-strip,
.strm-grid.is-list .strm-poster-open {
  display: none;
}
.strm-grid.is-list .strm-card-info {
  flex: 1;
  min-width: 0;
  padding: 0;
}
.strm-grid.is-list .strm-card-heading {
  justify-content: flex-start;
}
.strm-grid.is-list .strm-card-title {
  flex: initial;
  max-width: calc(100% - 28px);
  font-size: 13px !important;
}
.strm-grid.is-list .strm-card-go {
  margin-left: 5px;
  opacity: 0.6;
}
.strm-card-path {
  display: block;
  font-size: 10px;
  color: var(--strm-muted);
  opacity: 0.6;
  margin-top: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.strm-list-card-meta {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 5px;
  flex-shrink: 0;
}
.strm-list-card-meta small {
  color: var(--strm-muted);
  font-size: 10px;
}
.strm-library-foot {
  margin-top: auto;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: var(--strm-muted);
  font-size: 9px;
  padding: 13px 0;
  border-top: 1px solid var(--strm-line);
}
.strm-library-foot > span:last-child {
  display: flex;
  align-items: center;
  gap: 5px;
  opacity: 0.7;
}
.strm-footer-dot {
  margin: 0 8px;
  opacity: 0.4;
}
.strm-skel-poster {
  aspect-ratio: 2 / 3;
  border-radius: 8px;
  background: linear-gradient(
    110deg,
    var(--strm-line) 30%,
    var(--strm-surface) 50%,
    var(--strm-line) 70%
  );
  background-size: 220% 100%;
  animation: strm-shimmer 1.6s infinite;
}
.strm-skel-line {
  height: 10px;
  width: 75%;
  margin-top: 10px;
  border-radius: 3px;
  background: var(--strm-line);
}
.strm-skel-line.is-short {
  width: 50%;
  height: 6px;
  margin-top: 8px;
}
.strm-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  min-height: 240px;
  color: var(--strm-muted);
  text-align: center;
  padding: 25px;
  font-size: 12px;
}
.strm-empty h3 {
  font-size: 15px;
  font-weight: 500;
  color: var(--strm-text);
}
.strm-empty p {
  font-size: 12px;
}
.strm-empty-icon {
  width: 66px;
  height: 66px;
  border-radius: 18px;
  display: grid;
  place-items: center;
  background: var(--strm-tint);
  color: var(--strm-primary);
  margin-bottom: 4px;
}
.strm-alert {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 9px 12px;
  margin-bottom: 16px;
  border-radius: 7px;
  font-size: 12px;
  flex-shrink: 0;
  border: 1px solid;
}
.strm-alert > span {
  flex: 1;
}
.strm-alert.is-error {
  color: #b55b53;
  background: color-mix(in srgb, #b55b53 6%, var(--strm-surface));
  border-color: #b55b5325;
}
.strm-alert.is-ok {
  color: var(--strm-primary);
  background: var(--strm-tint);
  border-color: color-mix(in srgb, var(--strm-primary) 15%, transparent);
}
.strm-mini {
  padding: 4px 7px;
  border: 0;
  border-radius: 4px;
  font-size: 11px !important;
  background: transparent;
  color: var(--strm-primary) !important;
  white-space: nowrap;
}
.strm-mini:hover {
  background: var(--strm-tint);
}
/* 详情：保留单季、单集与多版本操作。 */
.strm-detail {
  padding-bottom: 25px;
}
.strm-dhero {
  position: relative;
  border-radius: 12px;
  overflow: hidden;
  color: white;
  background: #273b33;
  padding: 19px 24px 25px;
  isolation: isolate;
}
.strm-dhero-bg {
  position: absolute;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center 40%;
  inset: 0;
  filter: blur(25px);
  opacity: 0.23;
  z-index: -2;
}
.strm-dhero-scrim {
  position: absolute;
  inset: 0;
  background: linear-gradient(90deg, #172b25d9, #1a2827b0);
  z-index: -1;
}
.strm-back {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px !important;
  color: #ffffffb3 !important;
  background: transparent;
  border: 0;
  padding: 0 0 20px;
}
.strm-back:hover {
  color: white !important;
}
.strm-back-arrow {
  font-size: 17px;
}
.strm-dhero-body {
  display: flex;
  align-items: center;
  gap: 25px;
}
.strm-dhero-poster {
  width: 112px;
  flex-shrink: 0;
  aspect-ratio: 2 / 3;
  position: relative;
  border-radius: 6px;
  overflow: hidden;
  box-shadow: 0 7px 20px #00000033;
  isolation: isolate;
}
.strm-dhero-info {
  min-width: 0;
  flex: 1;
}
.strm-dhero-kicker {
  font-size: 11px;
  color: #ffffff8a;
  letter-spacing: 1px;
  margin-bottom: 7px;
}
.strm-dhero-info h2 {
  font-size: 27px;
  font-weight: 600;
  letter-spacing: -0.5px;
  margin-bottom: 13px;
}
.strm-dhero-tags {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}
.strm-tag {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border: 1px solid var(--strm-line);
  border-radius: 4px;
  padding: 2px 6px;
  color: var(--strm-muted);
  font-size: 10px;
  white-space: nowrap;
}
.strm-tag.is-ok {
  background: #529b7310;
  color: #649879;
  border-color: #529b7325;
}
.strm-tag.is-fail {
  background: #bc746d10;
  color: #b46a64;
  border-color: #bc746d25;
}
.strm-tag.is-busy,
.strm-tag.is-warn {
  background: #bf9b5610;
  color: #b18e4c;
  border-color: #bf9b5625;
}
.strm-dhero .strm-tag {
  color: #ffffffba;
  border-color: #ffffff22;
  background: #ffffff0b;
}
.strm-dhero .strm-tag.is-ok {
  color: #a8d6bb;
}
.strm-dhero .strm-tag.is-fail {
  color: #efb4ad;
}
.strm-status-dot {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: currentColor;
}
.strm-dhero-times {
  display: flex;
  align-items: center;
  gap: 12px;
  color: #ffffff79;
  font-size: 10px;
  margin-top: 15px;
  flex-wrap: wrap;
}
.strm-dhero-times b {
  font-weight: 400;
  margin-right: 7px;
  color: #ffffffad;
}
.strm-dhero-path {
  color: #ffffff62;
  font-size: 10px;
  margin-top: 9px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.strm-dhero-actions {
  align-self: flex-end;
  margin-left: auto;
}
.strm-dhero-actions .strm-btn {
  background: #ffffff16;
  border-color: #ffffff4a;
}
.strm-season-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 22px 0 16px;
  overflow-x: auto;
}
.strm-season-label {
  font-size: 10px;
  color: var(--strm-muted);
  letter-spacing: 1px;
  margin-right: 8px;
}
.strm-season-chip {
  border: 1px solid var(--strm-line);
  background: var(--strm-surface);
  border-radius: 5px;
  white-space: nowrap;
  padding: 6px 11px;
  font-size: 11px !important;
}
.strm-season-chip.active {
  background: var(--strm-tint);
  border-color: color-mix(in srgb, var(--strm-primary) 25%, transparent);
  color: var(--strm-primary);
}
.strm-season-chip em {
  font-size: 9px;
  font-style: normal;
  margin-left: 8px;
  opacity: 0.5;
}
.strm-list-actions {
  display: flex;
  align-items: center;
  gap: 3px;
  padding: 15px 0 12px;
}
.strm-list-sum {
  margin-left: auto;
  font-size: 11px;
  color: var(--strm-muted);
}
.strm-list-sum b {
  font-weight: 500;
  color: var(--strm-text);
}
.strm-list {
  background: var(--strm-surface);
  border: 1px solid var(--strm-line);
  border-radius: 8px;
  overflow: hidden;
}
.strm-row {
  display: flex;
  gap: 13px;
  align-items: center;
  padding: 14px 17px;
  position: relative;
  border-bottom: 1px solid var(--strm-line);
}
.strm-row:last-child {
  border: 0;
}
.strm-row.is-selected {
  background: var(--strm-tint);
}
.strm-row input {
  accent-color: var(--strm-primary);
  width: 13px;
  height: 13px;
  flex-shrink: 0;
}
.strm-row-bar {
  width: 2px;
  height: 16px;
  position: absolute;
  left: 0;
  border-radius: 2px;
  background: #729c81;
  opacity: 0.7;
}
.strm-row.is-fail .strm-row-bar {
  background: #b4776e;
}
.strm-row.is-none .strm-row-bar {
  background: #d2bd8e;
}
.strm-row-no {
  font: 10px monospace;
  color: var(--strm-muted);
  width: 30px;
  flex-shrink: 0;
}
.strm-row-no.is-plain {
  width: 64px;
}
.strm-row-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  font-size: 12px;
}
.strm-row-size {
  color: var(--strm-muted);
  font-size: 10px;
  width: 50px;
  text-align: right;
  flex-shrink: 0;
}
.strm-row-state {
  font-size: 10px;
  color: var(--strm-muted);
  display: flex;
  align-items: center;
  gap: 5px;
  width: 56px;
  flex-shrink: 0;
}
.strm-row-state i {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: currentColor;
}
.strm-row-state.is-ok {
  color: #729c81;
}
.strm-row-state.is-fail {
  color: #b4776e;
}
.strm-row-go {
  font-size: 11px !important;
  padding: 4px 10px;
  border: 1px solid var(--strm-line);
  background: transparent;
  border-radius: 4px;
  color: var(--strm-primary) !important;
}
.strm-detail-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 17px;
}
.strm-sel {
  color: var(--strm-muted);
  font-size: 11px;
}
.strm-sel b {
  color: var(--strm-primary);
  font-weight: 500;
}
.strm-foot-right {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-left: auto;
}
/* 记录：用统一的边框、标签与时间列降低阅读噪音。 */
.strm-rec-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 1px 0 19px;
}
.strm-rec-count {
  font-size: 17px;
  font-weight: 550;
}
.strm-rec-count b {
  font-variant-numeric: tabular-nums;
  font-weight: inherit;
}
.strm-rec-filter {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 7px;
  background: var(--strm-surface);
  border: 1px solid var(--strm-line);
  padding: 12px;
  border-radius: 8px;
  margin-bottom: 18px;
}
.strm-rec-filter select,
.strm-rec-filter input {
  background: var(--strm-wash);
  border: 1px solid var(--strm-line);
  border-radius: 4px;
  padding: 6px 7px;
  font-size: 11px;
  min-width: 0;
  color-scheme: normal;
}
.strm-rec-filter select option {
  background: var(--strm-surface);
}
.strm-rec-filter-sep {
  font-size: 10px;
  color: var(--strm-muted);
}
.strm-rec-filter > button:last-child {
  margin-left: auto;
}
.strm-rec-list {
  border: 1px solid var(--strm-line);
  border-radius: 8px;
  overflow: hidden;
  margin-bottom: 25px;
  background: var(--strm-surface);
}
.strm-rec {
  display: flex;
  align-items: center;
  gap: 15px;
  padding: 18px;
  border-bottom: 1px solid var(--strm-line);
}
.strm-rec:last-child {
  border: 0;
}
.strm-rec-dot {
  width: 7px;
  height: 7px;
  background: #81a98e;
  box-shadow: 0 0 0 4px #81a98e10;
  border-radius: 50%;
  flex-shrink: 0;
}
.strm-rec.is-fail .strm-rec-dot {
  background: #bb7b73;
  box-shadow: 0 0 0 4px #bb7b7310;
}
.strm-rec-main {
  min-width: 0;
  flex: 1;
}
.strm-rec-top {
  display: flex;
  align-items: center;
  gap: 7px;
}
.strm-rec-title {
  font-size: 12px;
  font-weight: 550;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.strm-rec-msg {
  font-size: 10px;
  color: var(--strm-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-top: 4px;
}
.strm-rec-badge {
  font-size: 10px;
  color: #729c81;
  border: 1px solid #729c8125;
  border-radius: 4px;
  padding: 2px 7px;
}
.strm-rec-badge.is-fail {
  color: #b4776e;
  border-color: #b4776e25;
}
.strm-rec-time {
  font-size: 10px;
  color: var(--strm-muted);
  font-variant-numeric: tabular-nums;
  min-width: 118px;
  text-align: right;
}
/* 任务抽屉始终留在组件内，联邦挂载不改变宿主滚动层级。 */
.strm-queue-layer {
  position: absolute;
  z-index: 50;
  inset: 0;
  display: flex;
  justify-content: flex-end;
}
.strm-queue-backdrop {
  position: absolute;
  inset: 0;
  border: 0;
  background: #10221b38;
  backdrop-filter: blur(2px);
}
.strm-queue-panel {
  position: relative;
  width: min(425px, 100%);
  background: var(--strm-surface);
  display: flex;
  flex-direction: column;
  box-shadow: -10px 0 50px #00000010;
}
.strm-drawer-head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 25px 23px;
  border-bottom: 1px solid var(--strm-line);
}
.strm-drawer-icon {
  width: 39px;
  height: 39px;
  background: var(--strm-tint);
  color: var(--strm-primary);
  border-radius: 10px;
  display: grid;
  place-items: center;
}
.strm-drawer-head h2 {
  font-size: 17px;
  font-weight: 550;
}
.strm-drawer-head p {
  font-size: 11px;
  color: var(--strm-muted);
  margin-top: 3px;
}
.strm-drawer-head > button {
  margin-left: auto;
}
.strm-queue-stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  padding: 23px;
  gap: 10px;
}
.strm-queue-stats div {
  background: var(--strm-wash);
  border-radius: 7px;
  padding: 12px;
}
.strm-queue-stats b {
  display: block;
  font-size: 23px;
  font-weight: 500;
}
.strm-queue-stats span {
  font-size: 10px;
  color: var(--strm-muted);
}
.strm-queue-scroll {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 0 23px;
  scrollbar-width: thin;
}
.strm-queue-sec {
  margin-bottom: 25px;
}
.strm-queue-sec-title {
  font-size: 11px;
  color: var(--strm-muted);
  display: block;
  margin-bottom: 10px;
}
.strm-queue-item {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 13px 0;
  border-bottom: 1px solid var(--strm-line);
}
.strm-queue-item > svg {
  color: var(--strm-muted);
}
.strm-queue-item.is-busy > svg {
  color: var(--strm-primary);
}
.strm-queue-item.is-ok > svg {
  color: #729c81;
}
.strm-queue-item.is-fail > svg {
  color: #b4776e;
}
.strm-queue-item > div {
  min-width: 0;
  flex: 1;
}
.strm-queue-item b {
  display: block;
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.strm-queue-item small {
  display: block;
  color: var(--strm-muted);
  font-size: 10px;
  margin-top: 3px;
}
.strm-queue-actions {
  display: flex;
  gap: 9px;
  padding: 18px 23px;
  border-top: 1px solid var(--strm-line);
}
.strm-queue-actions button {
  flex: 1;
}
.strm-drawer-enter-active,
.strm-drawer-leave-active {
  transition: opacity 0.2s;
}
.strm-drawer-enter-active .strm-queue-panel,
.strm-drawer-leave-active .strm-queue-panel {
  transition: transform 0.2s;
}
.strm-drawer-enter-from,
.strm-drawer-leave-to {
  opacity: 0;
}
.strm-drawer-enter-from .strm-queue-panel,
.strm-drawer-leave-to .strm-queue-panel {
  transform: translateX(100%);
}
.strm-mobile-nav,
.strm-mobile-categories {
  display: none;
}
.strm-library-meta {
  display: flex;
  align-items: center;
  gap: 12px;
}
@media (max-width: 760px) {
  .strm-mobile-categories {
    display: flex;
    align-items: center;
    gap: 3px;
    color: var(--strm-muted);
    font-size: 10px;
  }
  .strm-mobile-categories select {
    appearance: none;
    border: 0;
    background: transparent;
    color: inherit;
    font: inherit;
    max-width: 125px;
    padding: 3px 0;
  }
  .strm-mobile-categories select option {
    background: var(--strm-surface);
    color: var(--strm-text);
  }
}
@media (max-width: 480px) {
  .strm-library-meta .strm-updated {
    display: none;
  }
}
.strm-spinning {
  animation: strm-spin 1.5s linear infinite;
}
@keyframes strm-spin {
  to {
    transform: rotate(360deg);
  }
}
@keyframes strm-shimmer {
  to {
    background-position: -220% 0;
  }
}
@media (min-width: 1600px) {
  .strm-grid,
  .strm-skel-grid {
    grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
  }
}
@media (max-width: 1100px) {
  .strm-side {
    width: 198px;
    flex-basis: 198px;
    padding-inline: 11px;
  }
  .strm-head {
    padding-inline: 23px;
  }
  .strm-body {
    padding: 25px 23px 0;
  }
  .strm-brand strong {
    font-size: 18px;
  }
  .strm-brand small {
    font-size: 9px;
  }
  .strm-brand {
    gap: 8px;
    padding-inline: 4px;
  }
  .strm-monitor-card small {
    font-size: 8px;
  }
  .strm-dhero-body {
    flex-wrap: wrap;
  }
  .strm-dhero-actions {
    display: flex;
    width: 100%;
    margin-left: 137px;
  }
  .strm-detail-foot .strm-foot-right::before {
    content: '';
  }
  .strm-rec-time {
    min-width: 95px;
    font-size: 9px;
  }
}
@media (max-width: 820px) {
  .strm-side {
    width: 175px;
    flex-basis: 175px;
    padding-inline: 8px;
  }
  .strm-brand {
    gap: 6px;
  }
  .strm-brand strong {
    font-size: 15px;
  }
  .strm-logo {
    width: 31px;
    height: 34px;
  }
  .strm-brand small {
    letter-spacing: 0;
  }
  .strm-head {
    padding-inline: 18px;
  }
  .strm-body {
    padding: 22px 18px 0;
  }
  .strm-head-actions {
    gap: 6px;
  }
  .strm-monitor-badge {
    display: none;
  }
  .strm-page-heading h1 {
    font-size: 24px;
  }
  .strm-page-heading p {
    font-size: 10px;
  }
  .strm-stats {
    gap: 9px;
  }
  .strm-stat {
    padding: 12px;
  }
  .strm-stat-number {
    font-size: 27px;
  }
  .strm-stat-bottom {
    font-size: 9px;
  }
  .strm-stat-bottom > svg {
    display: none;
  }
  .strm-workspace {
    padding: 8px;
    font-size: 11px;
  }
  .strm-workspace small {
    font-size: 10px;
  }
  .strm-monitor-card {
    padding: 8px 5px;
    gap: 5px;
  }
  .strm-monitor-card small,
  .strm-side-caption span {
    display: none;
  }
  .strm-rec-top {
    flex-wrap: wrap;
  }
  .strm-rec {
    gap: 10px;
    padding: 13px;
  }
  .strm-rec-time {
    min-width: 75px;
    max-width: 75px;
  }
  .strm-toolbar {
    gap: 8px;
  }
  .strm-toolbar-right {
    gap: 8px;
  }
  .strm-search-key {
    display: none;
  }
}
@media (max-width: 760px) {
  .strm-side {
    display: none;
  }
  .strm-head {
    height: 53px;
    padding-inline: 16px;
  }
  .strm-crumb {
    font-size: 10px;
    gap: 6px;
  }
  .strm-head-actions {
    gap: 6px;
  }
  .strm-queue-trigger {
    font-size: 11px !important;
  }
  .strm-head-divider {
    display: none;
  }
  .strm-body {
    padding: 20px 16px 0;
  }
  .strm-page-heading {
    gap: 10px;
    align-items: flex-start;
    margin-bottom: 18px;
  }
  .strm-page-heading h1 {
    font-size: 25px;
  }
  .strm-page-heading p {
    max-width: 185px;
    line-height: 1.6;
  }
  .strm-eyebrow {
    font-size: 6px;
    letter-spacing: 1.2px;
  }
  .strm-page-actions {
    padding-top: 18px;
    gap: 6px;
  }
  .strm-page-actions > .strm-btn.ghost {
    padding: 7px;
    font-size: 0 !important;
    gap: 0;
  }
  .strm-btn {
    padding: 8px 10px;
    font-size: 11px !important;
  }
  .strm-stats {
    margin-bottom: 22px;
    gap: 7px;
  }
  .strm-stat {
    border-radius: 7px;
    padding: 10px 9px;
  }
  .strm-stat-top {
    font-size: 10px;
  }
  .strm-stat-icon {
    width: 22px;
    height: 22px;
  }
  .strm-stat-icon > svg {
    width: 15px;
  }
  .strm-stat-number {
    font-size: 26px;
    margin-top: 6px;
    margin-bottom: 7px;
  }
  .strm-stat-number small {
    font-size: 8px;
    margin-left: 4px;
  }
  .strm-stat-bottom {
    font-size: 8px;
  }
  .strm-stat-bottom i {
    display: none;
  }
  .strm-grid,
  .strm-skel-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 19px 13px;
  }
  .strm-search-wrap {
    width: 100%;
  }
  .strm-search {
    font-size: 10px !important;
  }
  .strm-toolbar-right {
    gap: 8px;
  }
  .strm-sort > svg {
    display: none;
  }
  .strm-sort select {
    max-width: 62px;
    font-size: 10px;
  }
  .strm-view-toggle button {
    width: 25px;
  }
  .strm-toolbar {
    margin-bottom: 16px;
    gap: 10px;
  }
  .strm-updated {
    font-size: 9px;
  }
  .strm-library-title h2 {
    font-size: 13px;
  }
  .strm-card-title {
    font-size: 12px !important;
  }
  .strm-card-sub {
    font-size: 9px;
  }
  .strm-card-go {
    opacity: 0.6;
  }
  .strm-library-foot {
    font-size: 8px;
  }
  .strm-library-foot > span:last-child {
    display: none;
  }
  .strm-mobile-nav {
    display: flex;
    align-items: center;
    gap: 5px;
    overflow-x: auto;
    border-bottom: 1px solid var(--strm-line);
    padding: 10px 16px;
    background: var(--strm-surface);
    flex-shrink: 0;
  }
  .strm-mobile-nav button {
    white-space: nowrap;
    font-size: 11px !important;
    border: 0;
    background: transparent;
    padding: 5px 9px;
    border-radius: 5px;
    color: var(--strm-muted);
  }
  .strm-mobile-nav button.active {
    color: var(--strm-primary);
    background: var(--strm-tint);
  }
  .strm-dhero {
    padding: 15px;
  }
  .strm-dhero-body {
    align-items: flex-start;
    gap: 15px;
    flex-wrap: wrap;
  }
  .strm-dhero-poster {
    width: 75px;
  }
  .strm-dhero-info h2 {
    font-size: 20px;
  }
  .strm-dhero-times {
    gap: 4px;
    font-size: 9px;
    margin-top: 10px;
  }
  .strm-dhero-times .strm-sep {
    display: none;
  }
  .strm-dhero-times > span {
    display: block;
    width: 100%;
  }
  .strm-dhero-actions {
    display: flex;
    width: 100%;
    margin: 0;
  }
  .strm-row {
    gap: 9px;
    padding: 12px 10px;
  }
  .strm-row-name {
    font-size: 10px;
  }
  .strm-row-size {
    display: none;
  }
  .strm-row-state {
    font-size: 9px;
    width: 42px;
  }
  .strm-row-no {
    width: 22px;
    font-size: 9px;
  }
  .strm-row-no.is-plain {
    width: 40px;
  }
  .strm-row-go {
    font-size: 9px !important;
    padding: 3px 5px;
  }
  .strm-rec-head {
    flex-wrap: wrap;
  }
  .strm-rec-count {
    font-size: 15px;
  }
  .strm-rec {
    gap: 8px;
    flex-wrap: wrap;
  }
  .strm-rec-main {
    flex-basis: calc(100% - 25px);
  }
  .strm-rec-title {
    font-size: 11px;
  }
  .strm-rec-time {
    max-width: initial;
    margin-left: auto;
  }
  .strm-rec-badge {
    margin-left: 15px;
  }
  .strm-grid.is-list .strm-card {
    padding: 10px;
    gap: 10px;
  }
  .strm-list-card-meta small {
    display: none;
  }
  .strm-grid.is-list .strm-card-title {
    font-size: 12px !important;
  }
  .strm-list-card-meta .strm-tag {
    font-size: 8px;
    padding: 2px 4px;
  }
  .strm-card-path {
    font-size: 8px;
  }
}
/* 短窗口优先让出海报空间，避免首行封面被固定统计区截断。 */
@media (max-height: 820px) {
  .strm-body {
    padding-top: 19px;
  }
  .strm-page-heading {
    margin-bottom: 18px;
  }
  .strm-page-heading h1 {
    line-height: 1.25;
    font-size: 25px;
  }
  .strm-eyebrow {
    font-size: 7px;
    margin-bottom: 4px;
  }
  .strm-page-heading p {
    margin-top: 4px;
  }
  .strm-page-actions {
    padding-top: 12px;
  }
  .strm-stats {
    margin-bottom: 21px;
  }
  .strm-stat {
    padding: 11px 15px 12px;
  }
  .strm-stat-number {
    font-size: 29px;
    margin: 1px 0 0;
    line-height: 1.15;
  }
  .strm-stat-icon {
    width: 24px;
    height: 24px;
  }
  .strm-stat-bottom {
    display: none;
  }
  .strm-library-bar {
    margin-bottom: 10px;
  }
  .strm-toolbar {
    margin-bottom: 15px;
  }
  .strm-library-foot {
    padding: 10px 0;
  }
}
@media (max-width: 760px) and (max-height: 820px) {
  .strm-stat {
    padding: 10px;
  }
  .strm-eyebrow {
    display: none;
  }
  .strm-page-actions {
    padding-top: 9px;
  }
  .strm-page-heading p {
    max-width: 215px;
  }
}
@media (prefers-reduced-motion: reduce) {
  .strm *,
  .strm *::before,
  .strm *::after {
    transition-duration: 0.01ms !important;
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
  }
}
</style>
