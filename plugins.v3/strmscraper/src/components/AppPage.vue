<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import ConfigPanel from './Config.vue'
import {
  bodyOf, coverUrl, formatSize, makeApiCall, posterStyle, statusOf, unwrap, versionLabel,
} from '../lib/strm.js'

const props = defineProps({
  api: { type: Object, default: () => ({}) },
  pluginId: { type: String, default: 'StrmScraper' },
  navKey: { type: String, default: 'main' },
  nativeSubscribe: { type: Function, default: null },
  sourcePluginId: { type: String, default: '' },
})

const loading = ref(true)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const overview = ref({})
const items = ref([])
const keyword = ref('')
const typeFilter = ref('all')

const drawer = ref(false)
const current = ref(null)
const detail = ref(null)
const seasonKey = ref('')
const selected = ref(new Set())

const task = ref(null)
let taskTimer = null

const GRADIENTS = [
  ['#667EEA', '#764BA2'], ['#F093FB', '#F5576C'], ['#4FACFE', '#00C6FB'],
  ['#43E97B', '#38F9D7'], ['#FA709A', '#FBC2A0'], ['#30CFD0', '#330867'],
  ['#FF9A9E', '#FECFEF'], ['#A18CD1', '#FBC2EB'],
]

const apiCall = makeApiCall(props.api, props.pluginId)

// 海报加载失败（无本地海报且 TMDB 未命中）时回退渐变占位图
const posterFailed = reactive({})

function markPosterFailed(path) {
  posterFailed[path] = true
}

// 页面内设置面板：直接复用设置弹窗的 Config 组件
const showSettings = ref(false)
const settingsModel = ref({})

async function openSettings() {
  showSettings.value = true
  try {
    const raw = await props.api.get(`plugin/form/${props.pluginId}`)
    settingsModel.value = bodyOf(raw)?.model || {}
  } catch (readError) {
    error.value = `读取配置失败：${readError?.message || readError}`
  }
}

async function saveSettings(config) {
  try {
    await props.api.put(`plugin/${props.pluginId}`, config)
    showSettings.value = false
    notice.value = '配置已保存'
    await loadEverything()
  } catch (saveError) {
    error.value = `保存配置失败：${saveError?.message || saveError}`
  }
}

const counts = computed(() => ({
  all: items.value.length,
  movie: items.value.filter(i => i.type === 'movie').length,
  tv: items.value.filter(i => i.type === 'tv').length,
}))

const visibleItems = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  return items.value.filter(item => {
    if (typeFilter.value !== 'all' && item.type !== typeFilter.value) return false
    if (kw && !`${item.title} ${item.path}`.toLowerCase().includes(kw)) return false
    return true
  })
})

const seasons = computed(() => {
  if (!detail.value || detail.value.type !== 'tv') return []
  const map = new Map()
  for (const file of detail.value.files || []) {
    const key = String(file.season_no ?? 1)
    if (!map.has(key)) map.set(key, { no: file.season_no ?? 1, name: file.season_name || `Season ${file.season_no ?? 1}`, files: [] })
    map.get(key).files.push(file)
  }
  return [...map.values()].sort((a, b) => a.no - b.no)
})

const currentSeason = computed(() => seasons.value.find(s => String(s.no) === seasonKey.value) || seasons.value[0] || null)

const rows = computed(() => {
  if (!current.value) return []
  if (current.value.type === 'tv') return currentSeason.value?.files || []
  return (detail.value?.files || []).map(file => ({ ...file, label: versionLabel(file.name) }))
})

const selectedRows = computed(() => rows.value.filter(row => selected.value.has(row.path)))

async function loadEverything() {
  loading.value = true
  error.value = ''
  try {
    const [overviewResponse, itemsResponse] = await Promise.all([
      apiCall('get', '/overview'),
      apiCall('get', '/items'),
    ])
    overview.value = unwrap(overviewResponse)
    items.value = unwrap(itemsResponse) || []
  } catch (loadError) {
    error.value = loadError?.message || '加载媒体清单失败'
  } finally {
    loading.value = false
  }
}

async function refreshItems() {
  const response = await apiCall('get', '/items?refresh=true')
  items.value = unwrap(response) || []
  const overviewResponse = await apiCall('get', '/overview')
  overview.value = unwrap(overviewResponse)
}

async function triggerFullScan(force) {
  busy.value = true
  try {
    unwrap(await apiCall('get', force ? '/scan?force=true' : '/scan'))
    notice.value = force ? '已在后台启动强制全量扫描' : '已在后台启动全量扫描（跳过已刮削）'
  } catch (scanError) {
    error.value = scanError?.message || '触发全量扫描失败'
  } finally {
    busy.value = false
  }
}

async function openDrawer(item) {
  current.value = item
  drawer.value = true
  selected.value = new Set()
  seasonKey.value = ''
  detail.value = null
  try {
    const response = await apiCall('get', `/files?path=${encodeURIComponent(item.path)}`)
    detail.value = unwrap(response)
    if (item.type === 'tv' && seasons.value.length) seasonKey.value = String(seasons.value[0].no)
  } catch (detailError) {
    error.value = detailError?.message || '读取文件明细失败'
  }
}

function closeDrawer() {
  drawer.value = false
  current.value = null
  detail.value = null
  selected.value = new Set()
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

function pollTask(taskId) {
  clearInterval(taskTimer)
  taskTimer = setInterval(async () => {
    try {
      const data = unwrap(await apiCall('get', `/tasks?task_id=${taskId}`))
      task.value = data
      if (data && data.status !== 'running') {
        clearInterval(taskTimer)
        taskTimer = null
        busy.value = false
        notice.value = `刮削完成：成功 ${data.success || 0} 个，失败 ${data.failed || 0} 个`
        try {
          await refreshItems()
        } catch (refreshError) {
          error.value = refreshError?.message || '刷新列表失败'
        }
        setTimeout(() => { task.value = null }, 6000)
      }
    } catch (pollError) {
      clearInterval(taskTimer)
      taskTimer = null
      busy.value = false
      error.value = pollError?.message || '查询任务状态失败'
    }
  }, 1500)
}

async function submitScrape(paths, target) {
  if (!paths.length) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const data = unwrap(await apiCall('post', '/scrape', { paths, target }))
    if (!data?.task_id) throw new Error('未返回任务 ID')
    task.value = { id: data.task_id, status: 'running', total: paths.length, done: 0, success: 0, failed: 0 }
    pollTask(data.task_id)
  } catch (submitError) {
    busy.value = false
    error.value = submitError?.message || '提交刮削任务失败'
  }
}

function scrapeSelected() {
  submitScrape(selectedRows.value.map(row => row.path), 'file')
}

function scrapeWhole() {
  if (!current.value) return
  submitScrape([current.value.path], 'dir')
}

function scrapeOne(row) {
  submitScrape([row.path], 'file')
}

function scrapeCard(item) {
  submitScrape([item.path], 'dir')
}

onMounted(() => { loadEverything() })
onBeforeUnmount(() => { clearInterval(taskTimer) })
</script>

<template>
  <div class="strm-page">
    <div class="strm-head">
      <div class="strm-logo">S</div>
      <div class="strm-heading">
        <h1>STRM 刮削</h1>
        <p>监控目录内的 .strm 文件，按媒体聚合、按单集或版本补齐元数据</p>
      </div>
      <div class="strm-head-chips">
        <span :class="['strm-chip', overview.monitoring ? 'is-ok' : 'is-muted']">
          {{ overview.monitoring ? '监控运行中' : '监控未启动' }}
        </span>
        <span class="strm-chip is-muted">{{ (overview.monitor_dirs || []).length }} 个监控目录</span>
      </div>
      <div class="strm-head-actions">
        <button class="strm-btn ghost" @click="openSettings">设置</button>
        <button class="strm-btn ghost" :disabled="loading || busy" @click="loadEverything">刷新</button>
        <button class="strm-btn ghost" :disabled="busy" @click="triggerFullScan(false)">全量扫描</button>
        <button class="strm-btn" :disabled="busy" @click="triggerFullScan(true)">强制全量</button>
      </div>
    </div>

    <div v-if="error" class="strm-alert is-error">{{ error }}</div>
    <div v-else-if="notice" class="strm-alert is-ok">{{ notice }}</div>

    <div v-if="task" class="strm-task">
      <span class="strm-task-text">
        刮削中 {{ task.done || 0 }}/{{ task.total || 0 }} · 成功 {{ task.success || 0 }} · 失败 {{ task.failed || 0 }}
      </span>
      <span class="strm-task-bar">
        <i :style="{ width: `${task.total ? Math.round(((task.done || 0) / task.total) * 100) : 0}%` }"></i>
      </span>
    </div>

    <div class="strm-stats">
      <div class="strm-stat"><b>{{ overview.total || 0 }}</b><span>媒体总数</span></div>
      <div class="strm-stat is-movie"><b>{{ overview.movie || 0 }}</b><span>电影</span></div>
      <div class="strm-stat is-tv"><b>{{ overview.tv || 0 }}</b><span>电视剧</span></div>
      <div class="strm-stat is-ok"><b>{{ overview.dir_scraped || 0 }}</b><span>已刮削目录</span></div>
      <div class="strm-stat is-fail"><b>{{ overview.unscraped_items || 0 }}</b><span>待刮削</span></div>
    </div>

    <div class="strm-toolbar">
      <div class="strm-seg">
        <button :class="['strm-seg-item', typeFilter === 'all' && 'active']" @click="typeFilter = 'all'">
          全部 <em>{{ counts.all }}</em>
        </button>
        <button :class="['strm-seg-item', typeFilter === 'movie' && 'active mv']" @click="typeFilter = 'movie'">
          电影 <em>{{ counts.movie }}</em>
        </button>
        <button :class="['strm-seg-item', typeFilter === 'tv' && 'active tv']" @click="typeFilter = 'tv'">
          电视剧 <em>{{ counts.tv }}</em>
        </button>
      </div>
      <input v-model="keyword" class="strm-search" type="text" placeholder="搜索标题或路径">
    </div>

    <div v-if="loading" class="strm-empty">正在扫描监控目录…</div>
    <div v-else-if="!visibleItems.length" class="strm-empty">没有匹配的媒体</div>
    <div v-else class="strm-grid">
      <div v-for="item in visibleItems" :key="item.path" class="strm-card">
        <div class="strm-poster" :style="posterStyle(item.title)">
          <img
            v-if="!posterFailed[item.path]"
            class="strm-poster-img"
            :src="coverUrl(props.api, props.pluginId, item)"
            :alt="item.title"
            loading="lazy"
            @error="markPosterFailed(item.path)"
          >
          <span :class="['strm-type', item.type === 'tv' ? 'is-tv' : 'is-mv']">{{ item.type === 'tv' ? '电视剧' : '电影' }}</span>
          <span :class="['strm-dot', `is-${statusOf(item)}`]"></span>
          <div class="strm-poster-foot">
            <template v-if="item.type === 'tv'">
              <span class="strm-pill">{{ item.total_episodes || item.total_files }} 集 · {{ (item.seasons || []).length }} 季</span>
            </template>
            <template v-else-if="item.multi_version">
              <span class="strm-pill is-blue">{{ item.total_files }} 版本</span>
            </template>
            <template v-else>
              <span class="strm-pill is-blue">单版本</span>
            </template>
            <em v-if="item.unscraped">{{ item.unscraped }} 待刮</em>
          </div>
        </div>
        <div class="strm-card-body">
          <div class="strm-card-title" :title="item.path">{{ item.title }}</div>
          <div class="strm-card-meta">{{ item.path }}</div>
          <div class="strm-card-actions">
            <button
              v-if="item.type === 'tv'"
              class="strm-btn small is-tv"
              @click="openDrawer(item)"
            >剧集预览</button>
            <button
              v-else-if="item.multi_version"
              class="strm-btn small is-mv"
              @click="openDrawer(item)"
            >版本预览</button>
            <button v-else class="strm-btn small" :disabled="busy" @click="scrapeCard(item)">刮削</button>
            <button
              v-if="item.type === 'tv' || item.multi_version"
              class="strm-btn small ghost"
              :disabled="busy"
              @click="scrapeCard(item)"
            >整部重刮</button>
          </div>
        </div>
      </div>
    </div>

    <div v-if="showSettings" class="strm-cfg-mask" @click.self="showSettings = false">
      <div class="strm-cfg-wrap">
        <ConfigPanel
          :initial-config="settingsModel"
          :api="props.api"
          :plugin-id="props.pluginId"
          @save="saveSettings"
          @close="showSettings = false"
        />
      </div>
    </div>

    <div :class="['strm-mask', drawer && 'is-open']" @click="closeDrawer"></div>
    <div :class="['strm-drawer', drawer && 'is-open']">
      <template v-if="current">
        <div class="strm-drawer-head">
          <div class="strm-drawer-poster" :style="posterStyle(current.title)">
            <img
              v-if="!posterFailed[current.path]"
              class="strm-poster-img"
              :src="coverUrl(props.api, props.pluginId, current)"
              :alt="current.title"
              @error="markPosterFailed(current.path)"
            >
          </div>
          <div class="strm-drawer-info">
            <h2>{{ current.title }}</h2>
            <div class="strm-drawer-sub">
              <span>{{ current.type === 'tv' ? '电视剧' : '电影' }}</span>
              <span>{{ current.total_files }} 个文件</span>
              <span>待刮削 {{ current.unscraped }}</span>
            </div>
          </div>
          <button class="strm-close" @click="closeDrawer">×</button>
        </div>

        <div v-if="current.type === 'tv'" class="strm-season-bar">
          <button
            v-for="season in seasons"
            :key="season.no"
            :class="['strm-season-chip', String(season.no) === seasonKey && 'active']"
            @click="seasonKey = String(season.no)"
          >{{ season.name }} <em>{{ season.files.length }}</em></button>
          <div class="strm-season-right">
            <button class="strm-mini" @click="selectAll">全选本季</button>
            <button class="strm-mini" @click="selectUnscraped">选中未刮削</button>
            <button class="strm-mini" @click="clearSelection">清空</button>
          </div>
        </div>

        <div class="strm-list">
          <div v-if="!rows.length" class="strm-empty">暂无文件</div>
          <div
            v-for="row in rows"
            :key="row.path"
            :class="['strm-row', selected.has(row.path) && 'is-selected']"
          >
            <input type="checkbox" :checked="selected.has(row.path)" @change="toggleRow(row.path)">
            <span class="strm-row-no">
              {{ current.type === 'tv' ? `E${String(row.episode ?? 0).padStart(2, '0')}` : (row.label || '') }}
            </span>
            <span class="strm-row-name" :title="row.name">{{ row.name }}</span>
            <span class="strm-row-size">{{ formatSize(row.size) }}</span>
            <span :class="['strm-row-state', row.scraped ? 'is-ok' : 'is-none']">
              <i></i>{{ row.scraped ? '已刮削' : '未刮削' }}
            </span>
            <button class="strm-mini" :disabled="busy" @click="scrapeOne(row)">刮削</button>
          </div>
        </div>

        <div class="strm-drawer-foot">
          <span class="strm-sel">已选 <b>{{ selectedRows.length }}</b> 项</span>
          <div class="strm-foot-right">
            <button class="strm-btn ghost" :disabled="busy || !selectedRows.length" @click="scrapeSelected">
              刮削选中{{ current.type === 'tv' ? '单集' : '版本' }}
            </button>
            <button class="strm-btn" :disabled="busy" @click="scrapeWhole">
              {{ current.type === 'tv' ? '整剧重新刮削' : '整部重新刮削' }}
            </button>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.strm-page {
  color: rgb(var(--v-theme-on-surface, 27, 29, 41));
  width: 100%;
  max-width: 1320px;
  margin: 0 auto;
  padding: 24px 24px 40px;
  box-sizing: border-box;
  font-family: inherit;
}
.strm-head { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; margin-bottom: 20px; }
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
.strm-chip.is-ok {
  background: rgba(22, 163, 74, .12); color: #16A34A;
}
.strm-head-actions { margin-left: auto; display: flex; gap: 8px; }

.strm-btn {
  padding: 8px 15px; border-radius: 10px; font-size: 13px; font-weight: 600; font-family: inherit;
  cursor: pointer; border: none; transition: .15s;
  background: rgb(var(--v-theme-primary, 124, 92, 252));
  color: rgb(var(--v-theme-on-primary, 255, 255, 255));
}
.strm-btn:hover { filter: brightness(1.08); }
.strm-btn:disabled { opacity: .45; cursor: not-allowed; filter: none; }
.strm-btn.ghost {
  background: rgb(var(--v-theme-surface, 255, 255, 255));
  color: rgb(var(--v-theme-primary, 124, 92, 252));
  border: 1px solid rgba(var(--v-theme-primary, 124, 92, 252), .35);
}
.strm-btn.small { flex: 1; padding: 7px 0; font-size: 12px; }
.strm-btn.small.is-tv { background: #EC4899; color: #fff; }
.strm-btn.small.is-mv { background: #3B82F6; color: #fff; }

.strm-alert { border-radius: 10px; padding: 10px 14px; font-size: 13px; margin-bottom: 16px; }
.strm-alert.is-error { background: rgba(229, 72, 77, .12); color: #E5484D; }
.strm-alert.is-ok { background: rgba(22, 163, 74, .12); color: #16A34A; }

.strm-task {
  display: flex; align-items: center; gap: 12px; margin-bottom: 16px;
  padding: 10px 14px; border-radius: 10px;
  background: rgba(var(--v-theme-primary, 124, 92, 252), .10);
}
.strm-task-text { font-size: 12.5px; white-space: nowrap; }
.strm-task-bar {
  flex: 1; height: 6px; border-radius: 999px; overflow: hidden;
  background: rgba(var(--v-theme-on-surface, 27, 29, 41), .10);
}
.strm-task-bar i { display: block; height: 100%; background: rgb(var(--v-theme-primary, 124, 92, 252)); transition: width .3s; }

.strm-stats { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; margin-bottom: 18px; }
.strm-stat {
  background: rgb(var(--v-theme-surface, 255, 255, 255));
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .10);
  border-radius: 12px; padding: 13px 15px;
}
.strm-stat b { display: block; font-size: 21px; font-weight: 700; line-height: 1.2; font-variant-numeric: tabular-nums; }
.strm-stat span {
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .62));
}
.strm-stat.is-movie b { color: #3B82F6; }
.strm-stat.is-tv b { color: #EC4899; }
.strm-stat.is-ok b { color: #16A34A; }
.strm-stat.is-fail b { color: #E5484D; }

.strm-toolbar { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-bottom: 18px; }
.strm-seg {
  display: flex; gap: 2px; padding: 3px; border-radius: 11px;
  background: rgb(var(--v-theme-surface, 255, 255, 255));
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .10);
}
.strm-seg-item {
  display: flex; align-items: center; gap: 5px; padding: 7px 15px; border: none; border-radius: 8px;
  font-size: 13px; font-family: inherit; cursor: pointer; background: transparent;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .7));
}
.strm-seg-item em { font-style: normal; font-size: 11px; opacity: .75; }
.strm-seg-item.active { background: rgb(var(--v-theme-primary, 124, 92, 252)); color: #fff; font-weight: 600; }
.strm-seg-item.active.mv { background: #3B82F6; }
.strm-seg-item.active.tv { background: #EC4899; }
.strm-search {
  flex: 1; min-width: 200px; max-width: 340px; padding: 9px 13px; font-size: 13px; font-family: inherit;
  border-radius: 11px; outline: none;
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .14);
  background: rgb(var(--v-theme-surface, 255, 255, 255));
  color: rgb(var(--v-theme-on-surface, 27, 29, 41));
}
.strm-search:focus { border-color: rgb(var(--v-theme-primary, 124, 92, 252)); }

.strm-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(178px, 1fr)); gap: 16px; }
.strm-card {
  display: flex; flex-direction: column; overflow: hidden; border-radius: 14px;
  background: rgb(var(--v-theme-surface, 255, 255, 255));
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .10);
  transition: transform .18s, border-color .18s;
}
.strm-card:hover { transform: translateY(-3px); border-color: rgba(var(--v-theme-primary, 124, 92, 252), .35); }
.strm-poster { position: relative; aspect-ratio: 2 / 3; overflow: hidden; }
.strm-poster-img {
  position: absolute; inset: 0; width: 100%; height: 100%;
  object-fit: cover; display: block; background: transparent;
}
.strm-type {
  position: absolute; top: 9px; left: 9px; padding: 3px 9px; border-radius: 999px;
  font-size: 11px; font-weight: 600; background: rgba(255, 255, 255, .92);
}
.strm-type.is-tv { color: #EC4899; }
.strm-type.is-mv { color: #3B82F6; }
.strm-dot { position: absolute; top: 12px; right: 12px; width: 9px; height: 9px; border-radius: 50%; box-shadow: 0 0 0 3px rgba(255, 255, 255, .5); }
.strm-dot.is-ok { background: #16A34A; }
.strm-dot.is-pend { background: #EA8A1F; }
.strm-dot.is-fail { background: #E5484D; }
.strm-poster-foot {
  position: absolute; left: 0; right: 0; bottom: 0; padding: 24px 9px 8px;
  display: flex; align-items: center; gap: 6px; font-size: 11px; font-weight: 600; color: #fff;
  background: linear-gradient(transparent, rgba(10, 11, 20, .82));
}
.strm-pill { background: #EC4899; padding: 2px 7px; border-radius: 6px; }
.strm-pill.is-blue { background: #3B82F6; }
.strm-poster-foot em { font-style: normal; margin-left: auto; color: #FDBA74; }
.strm-card-body { padding: 10px 12px 11px; display: flex; flex-direction: column; gap: 6px; flex: 1; }
.strm-card-title { font-size: 13px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.strm-card-meta {
  font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .5));
}
.strm-card-actions { margin-top: auto; padding-top: 9px; display: flex; gap: 7px; }
.strm-card-actions .strm-btn.small { padding: 7px 8px; }

.strm-empty {
  text-align: center; padding: 48px 0; font-size: 13px;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .5));
}

/* 详情抽屉：层级必须高于 MP 顶栏/侧栏（1000），否则顶部会被顶栏盖住；
   但仍保持在 v-overlay（2000）/ v-dialog（2400）之下，弹窗类界面才能盖住它 */
.strm-mask {
  position: fixed; inset: 0; z-index: 1300; background: rgba(10, 11, 20, .45);
  opacity: 0; pointer-events: none; transition: opacity .25s;
}
.strm-mask.is-open { opacity: 1; pointer-events: auto; }
.strm-drawer {
  position: fixed; top: 0; right: -640px; z-index: 1400; width: 620px; max-width: 95vw; height: 100vh;
  display: flex; flex-direction: column; transition: right .28s cubic-bezier(.4, 0, .2, 1);
  background: rgb(var(--v-theme-surface, 255, 255, 255));
  box-shadow: -14px 0 44px rgba(10, 11, 20, .18);
}
.strm-drawer.is-open { right: 0; }
.strm-drawer-head {
  display: flex; gap: 14px; align-items: flex-start; padding: 16px 20px 14px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .10);
}
.strm-drawer-poster { position: relative; width: 58px; height: 87px; border-radius: 10px; flex-shrink: 0; overflow: hidden; }

/* 页面内设置面板 */
.strm-cfg-mask {
  /* 必须高于 MP 顶栏/侧栏（1000）与 v-overlay（2000），对齐 v-dialog 的 2400；
     原值 60 会让遮罩被顶栏和侧栏盖住，弹窗也就被"压"在内容区里 */
  position: fixed; inset: 0; z-index: 2400;
  background: rgba(0, 0, 0, .45);
  display: flex; align-items: center; justify-content: center; padding: 24px;
}
.strm-cfg-wrap {
  width: min(760px, 100%); max-height: 86vh; overflow: auto; border-radius: 16px;
  background: rgb(var(--v-theme-surface, 255, 255, 255));
  box-shadow: 0 18px 48px rgba(0, 0, 0, .28);
}
.strm-drawer-info { flex: 1; min-width: 0; }
.strm-drawer-info h2 { font-size: 16px; font-weight: 700; margin: 0; line-height: 1.35; }
.strm-drawer-sub {
  display: flex; gap: 12px; flex-wrap: wrap; margin-top: 6px; font-size: 12px;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .62));
}
.strm-close {
  width: 30px; height: 30px; border-radius: 9px; flex-shrink: 0; cursor: pointer; font-size: 18px; line-height: 1;
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .14);
  background: transparent; color: inherit;
}
.strm-season-bar {
  display: flex; gap: 8px; align-items: center; flex-wrap: wrap; padding: 12px 20px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .10);
}
.strm-season-chip {
  padding: 6px 13px; border-radius: 9px; font-size: 12.5px; font-family: inherit; cursor: pointer; white-space: nowrap;
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .14);
  background: transparent; color: rgba(var(--v-theme-on-surface, 27, 29, 41), .72);
}
.strm-season-chip em { font-style: normal; font-size: 11px; opacity: .75; }
.strm-season-chip.active {
  background: rgb(var(--v-theme-primary, 124, 92, 252));
  border-color: rgb(var(--v-theme-primary, 124, 92, 252));
  color: #fff; font-weight: 600;
}
.strm-season-right { margin-left: auto; display: flex; gap: 8px; }
.strm-mini {
  padding: 5px 11px; border-radius: 9px; font-size: 12px; font-family: inherit; cursor: pointer;
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .14);
  background: transparent; color: rgba(var(--v-theme-on-surface, 27, 29, 41), .72);
}
.strm-mini:hover { border-color: rgba(var(--v-theme-primary, 124, 92, 252), .4); color: rgb(var(--v-theme-primary, 124, 92, 252)); }
.strm-list { flex: 1; overflow-y: auto; padding: 10px 20px 16px; }
.strm-row {
  display: flex; align-items: center; gap: 10px; padding: 8px; border-radius: 10px;
  border: 1px solid transparent;
}
.strm-row:hover { background: rgba(var(--v-theme-on-surface, 27, 29, 41), .04); }
.strm-row.is-selected {
  background: rgba(var(--v-theme-primary, 124, 92, 252), .10);
  border-color: rgba(var(--v-theme-primary, 124, 92, 252), .28);
}
.strm-row input { width: 15px; height: 15px; flex-shrink: 0; accent-color: rgb(var(--v-theme-primary, 124, 92, 252)); cursor: pointer; }
.strm-row-no { width: 46px; flex-shrink: 0; font-size: 12px; font-weight: 700; color: rgb(var(--v-theme-primary, 124, 92, 252)); }
.strm-row-name {
  flex: 1; min-width: 0; font-size: 12.5px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.strm-row-size {
  width: 68px; flex-shrink: 0; text-align: right; font-size: 11.5px; font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .55));
}
.strm-row-state {
  width: 74px; flex-shrink: 0; display: flex; align-items: center; gap: 5px; font-size: 11.5px;
}
.strm-row-state i { width: 6px; height: 6px; border-radius: 50%; }
.strm-row-state.is-ok { color: #16A34A; }
.strm-row-state.is-ok i { background: #16A34A; }
.strm-row-state.is-none {
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .55));
}
.strm-row-state.is-none i { background: rgba(var(--v-theme-on-surface, 27, 29, 41), .28); }
.strm-drawer-foot {
  display: flex; align-items: center; gap: 10px; padding: 13px 20px;
  border-top: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .10);
}
.strm-sel { font-size: 12.5px; color: rgba(var(--v-theme-on-surface, 27, 29, 41), .72); }
.strm-sel b { font-size: 14px; color: rgb(var(--v-theme-primary, 124, 92, 252)); }
.strm-foot-right { margin-left: auto; display: flex; gap: 9px; }

@media (max-width: 900px) {
  .strm-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
