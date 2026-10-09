<script setup>
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import ConfigPanel from './Config.vue'
import {
  SOURCES,
  QUALITIES,
  supportsSongList,
  makeApiCall,
  formatSize,
  qualitiesOf,
  singerOf,
  songKey,
  coverOf,
  playlistCoverOf,
  playlistIdOf,
  playlistAuthorOf,
  playlistCountOf,
  bodyOf,
  unwrap,
} from '../lib/helper.js'

const props = defineProps({
  api: { type: Object, default: () => ({}) },
  pluginId: { type: String, default: 'LxMusicDownloader' },
  navKey: { type: String, default: 'main' },
  nativeSubscribe: { type: Function, default: null },
  sourcePluginId: { type: String, default: '' },
})

const apiCall = makeApiCall(props.api, props.pluginId)

const loading = ref(false)
const searching = ref(false)
const error = ref('')
const notice = ref('')
let noticeTimer = null

const keyword = ref('')
const source = ref('kw')
const quality = ref('320k')
const limit = ref(10)

const songs = ref([])
const overview = ref({})
const stats = ref(null)

const downloading = reactive({})
const coverFailed = reactive({})
const lastResolved = ref(null)

// ---------------------------------------------------------------- 歌单
const tab = ref('song')
const plKeyword = ref('')
const playlists = ref([])
const playlistLoading = ref(false)
const playlistCoverFailed = reactive({})
const currentPlaylist = ref(null)
const playlistDetail = ref(null)
const detailLoading = ref(false)
const batchRunning = ref(false)
const batchResult = ref(null)
const batchSelected = reactive({})

// 歌单并发数取自插件配置；设置面板保存后回写，避免每次都要重开页面
const batchConcurrency = ref(3)

function flash(text, isError = false) {
  clearTimeout(noticeTimer)
  if (isError) { error.value = text; notice.value = ''; }
  else { notice.value = text; error.value = ''; }
  noticeTimer = setTimeout(() => { notice.value = ''; error.value = ''; }, 8000)
}

function markCoverFailed(song) {
  coverFailed[songKey(song)] = true
}

async function loadOverview() {
  try {
    overview.value = unwrap(await apiCall('get', '/overview'))
    if (overview.value?.source) source.value = overview.value.source
    if (overview.value?.quality) quality.value = overview.value.quality
    if (overview.value?.playlist_concurrency) batchConcurrency.value = overview.value.playlist_concurrency
  } catch (loadError) {
    error.value = loadError?.message || '读取运行状态失败'
  }
}

async function loadStats() {
  try {
    stats.value = unwrap(await apiCall('get', '/stats'))
  } catch (loadError) {
    stats.value = null
  }
}

async function loadEverything() {
  loading.value = true
  error.value = ''
  try {
    await Promise.all([loadOverview(), loadStats()])
  } finally {
    loading.value = false
  }
}

async function doSearch() {
  const kw = keyword.value.trim()
  if (!kw) { flash('请输入歌曲名或歌手', true); return }
  searching.value = true
  error.value = ''
  notice.value = ''
  songs.value = []
  lastResolved.value = null
  try {
    const path = `/search?keyword=${encodeURIComponent(kw)}&limit=${limit.value}&source=${source.value}`
    songs.value = unwrap(await apiCall('get', path)) || []
    if (!songs.value.length) flash(`「${source.value}」没有搜索到与「${kw}」相关的歌曲`, true)
  } catch (searchError) {
    error.value = searchError?.message || '搜索失败'
  } finally {
    searching.value = false
  }
}

async function doDownload(song) {
  const key = songKey(song)
  downloading[key] = true
  error.value = ''
  notice.value = ''
  try {
    const response = await apiCall('post', '/download', { song, quality: quality.value })
    const body = response?.data ?? response
    if (body?.success === false) throw new Error(body.message || '下载失败')
    flash(body?.message || '下载完成')
    loadStats()
  } catch (downloadError) {
    error.value = downloadError?.message || '下载失败'
  } finally {
    downloading[key] = false
  }
}

async function resolveSong(song) {
  error.value = ''
  notice.value = ''
  try {
    const data = unwrap(await apiCall('post', '/resolve', { song, quality: quality.value }))
    lastResolved.value = { ...data, name: song?.name, singer: singerOf(song), key: songKey(song) }
    flash(`解析成功：实际音质 ${data.type}`)
  } catch (resolveError) {
    error.value = resolveError?.message || '解析直链失败'
  }
}

function copyUrl(url) {
  if (!url) return
  navigator.clipboard?.writeText(url).then(
    () => flash('直链已复制到剪贴板'),
    () => flash('复制失败，请手动选择', true),
  )
}

// ---------------------------------------------------------------- 歌单逻辑

/** 把歌单标识（URL 或纯数字 ID）与搜索关键词区分开 */
function looksLikePlaylistId(text) {
  const value = String(text || '').trim()
  if (!value) return false
  if (/^\d+$/.test(value)) return true
  return value.startsWith('http://') || value.startsWith('https://')
    || value.includes('/playlist/') || value.includes('/songlist/')
}

function playlistSupported(method) {
  return supportsSongList(source.value, method)
}

/** 取热门歌单（也是歌单页的默认内容） */
async function loadPlaylists() {
  const kw = plKeyword.value.trim()
  if (kw && !looksLikePlaylistId(kw) && !playlistSupported('search')) {
    flash(`「${source.value}」不支持歌单搜索，请换平台或直接粘贴歌单链接`, true)
    return
  }
  playlistLoading.value = true
  error.value = ''
  notice.value = ''
  currentPlaylist.value = null
  playlistDetail.value = null
  batchResult.value = null
  try {
    // 粘贴链接/ID 时直接进详情，不用先列一遍
    if (looksLikePlaylistId(kw)) {
      await openPlaylist({ id: kw, name: '', source: source.value }, true)
      return
    }
    const path = kw
      ? `/playlist/list?keyword=${encodeURIComponent(kw)}&source=${source.value}`
      : `/playlist/list?source=${source.value}&sort_id=hot&page=1`
    playlists.value = unwrap(await apiCall('get', path)) || []
    if (!playlists.value.length) flash(kw ? `没有搜到与「${kw}」相关的歌单` : `「${source.value}」没有取到热门歌单`, true)
  } catch (loadError) {
    error.value = loadError?.message || '读取歌单失败'
  } finally {
    playlistLoading.value = false
  }
}

async function openPlaylist(playlist, keepList = false) {
  const id = playlistIdOf(playlist)
  if (!id) { flash('该歌单没有返回 id，无法打开', true); return }
  detailLoading.value = true
  error.value = ''
  notice.value = ''
  batchResult.value = null
  Object.keys(batchSelected).forEach(key => { delete batchSelected[key] })
  if (!keepList) playlists.value = []
  try {
    const path = `/playlist/detail?playlist_id=${encodeURIComponent(id)}&source=${playlist.source || source.value}`
    const data = unwrap(await apiCall('get', path))
    playlistDetail.value = data
    currentPlaylist.value = { ...playlist, ...(data.info || {}), id }
  } catch (detailError) {
    error.value = detailError?.message || '读取歌单详情失败'
    currentPlaylist.value = null
  } finally {
    detailLoading.value = false
  }
}

function closePlaylist() {
  currentPlaylist.value = null
  playlistDetail.value = null
  batchResult.value = null
}

/** 全选/全不选：默认只勾选还没下载过的（服务端不去重，由前端按已存在结果提示） */
function toggleAllSongs(checked) {
  const list = playlistDetail.value?.songs || []
  Object.keys(batchSelected).forEach(key => { delete batchSelected[key] })
  if (checked) list.forEach(song => { batchSelected[songKey(song)] = true })
}

async function downloadPlaylist() {
  const list = playlistDetail.value?.songs || []
  // 勾选了曲目就只下载选中的；一个都没勾才按「整单」处理（不传 songs，服务端重新拉全量）
  const selected = list.filter(song => batchSelected[songKey(song)])
  batchRunning.value = true
  error.value = ''
  notice.value = ''
  batchResult.value = null
  try {
    const payload = {
      playlist_id: playlistIdOf(currentPlaylist.value) || playlistDetail.value?.id,
      source: currentPlaylist.value?.source || source.value,
      quality: quality.value,
      concurrency: batchConcurrency.value,
      skip_existing: true,
    }
    if (selected.length) {
      payload.songs = selected
      payload.name = currentPlaylist.value?.name || ''
    }
    const body = bodyOf(await apiCall('post', '/playlist/download', payload))
    if (body?.success === false) throw new Error(body.message || '整单下载失败')
    batchResult.value = body?.data || null
    flash(body?.message || '整单下载完成')
    loadStats()
  } catch (batchError) {
    error.value = batchError?.message || '整单下载失败'
  } finally {
    batchRunning.value = false
  }
}

function batchSummary() {
  const data = batchResult.value
  if (!data) return ''
  return `成功 ${data.success}｜跳过 ${data.skipped}（已存在）｜失败 ${data.failed}｜共 ${data.total} 首`
}

// 设置面板
const showSettings = ref(false)
const loadingSettings = ref(false)
const settingsModel = ref({})

/*
 * MP 会给插件页面套一层「包含块祖先」（容器查询 / transform / contain 之类），
 * 于是 position:fixed 不再是相对窗口定位，而是被关进「内容区」这一个盒子里：
 *   - 顶栏与侧栏在盒子之外 → 无论 z-index 多大都盖不住它们
 *   - 弹窗按内容区居中，而不是按窗口居中
 * 把浮层 Teleport 出去才能跳出这层上下文。优先挂到 .v-application（保留 Vuetify 的
 * CSS 变量），兜底 body。
 */
const overlayTarget = (typeof document !== 'undefined' && document.querySelector('.v-application'))
  ? '.v-application'
  : 'body'

// 必须先拿到配置再挂载面板：Config 只在初值到达后才渲染，避免先闪一屏默认值
async function openSettings() {
  if (loadingSettings.value) return
  loadingSettings.value = true
  try {
    const raw = await props.api.get(`plugin/form/${props.pluginId}`)
    settingsModel.value = bodyOf(raw)?.model || {}
    showSettings.value = true
  } catch (readError) {
    error.value = `读取配置失败：${readError?.message || readError}`
  } finally {
    loadingSettings.value = false
  }
}

async function saveSettings(config) {
  try {
    await props.api.put(`plugin/${props.pluginId}`, config)
    showSettings.value = false
    flash('配置已保存')
    await loadEverything()
  } catch (saveError) {
    error.value = `保存配置失败：${saveError?.message || saveError}`
  }
}

const cacheInfo = () => stats.value || {}

onMounted(() => { loadEverything() })
onBeforeUnmount(() => { clearTimeout(noticeTimer) })
</script>

<template>
  <div class="lx-page">
    <div class="lx-head">
      <div class="lx-logo">L</div>
      <div class="lx-heading">
        <h1>LX 音源下载</h1>
        <p>调用自建 LX Sync Server 搜索歌曲、浏览歌单，解析直链并下载到指定目录</p>
      </div>
      <div class="lx-head-chips">
        <span class="lx-chip" :class="overview.auth_state === 'ok' ? 'is-ok' : 'is-warn'">
          {{ overview.auth_state === 'ok' ? '鉴权正常' : (overview.auth_state === 'anonymous' ? '未配置凭据' : '鉴权异常') }}
        </span>
        <span class="lx-chip is-muted">{{ overview.source_name || '-' }} · {{ overview.quality || '-' }}</span>
      </div>
      <div class="lx-head-actions">
        <button class="lx-btn ghost" :disabled="loadingSettings" @click="openSettings">{{ loadingSettings ? '读取中…' : '设置' }}</button>
        <button class="lx-btn ghost" :disabled="loading" @click="loadEverything">刷新</button>
      </div>
    </div>

    <div class="lx-tabs">
      <button class="lx-tab" :class="{ 'is-active': tab === 'song' }" @click="tab = 'song'">单曲搜索</button>
      <button class="lx-tab" :class="{ 'is-active': tab === 'playlist' }" @click="tab = 'playlist'">歌单</button>
    </div>

    <div v-if="error" class="lx-alert is-error">{{ error }}</div>
    <div v-else-if="notice" class="lx-alert is-ok">{{ notice }}</div>

    <div class="lx-stats">
      <div class="lx-stat is-ok">
        <b>{{ formatSize(cacheInfo().totalSize) }}</b>
        <span>服务端缓存占用</span>
      </div>
      <div class="lx-stat">
        <b>{{ cacheInfo().fileCount || 0 }}</b>
        <span>缓存文件数</span>
      </div>
      <div class="lx-stat">
        <b>{{ tab === 'song' ? songs.length : playlists.length }}</b>
        <span>{{ tab === 'song' ? '本次搜索候选' : '歌单候选' }}</span>
      </div>
      <div class="lx-stat is-mv">
        <b>{{ overview.max_results || 10 }}</b>
        <span>搜索上限</span>
      </div>
    </div>

    <!-- 单曲搜索 -->
    <template v-if="tab === 'song'">
      <div class="lx-search">
        <input v-model="keyword" class="lx-input" type="text" placeholder="输入歌曲名或「歌手 - 歌名」，回车搜索" @keyup.enter="doSearch">
        <select v-model="source" class="lx-select">
          <option v-for="item in SOURCES" :key="item.value" :value="item.value">{{ item.label }}</option>
        </select>
        <select v-model="quality" class="lx-select">
          <option v-for="item in QUALITIES" :key="item.value" :value="item.value">{{ item.label }}</option>
        </select>
        <input v-model.number="limit" class="lx-input is-num" type="number" min="1" max="50">
        <button class="lx-btn" :disabled="searching" @click="doSearch">{{ searching ? '搜索中…' : '搜索' }}</button>
      </div>
      <p class="lx-tip">
        下载目录：<code>{{ overview.download_dir || '-' }}</code>
        <span v-if="overview.source === 'tx'" class="lx-warn">（酷狗/酷我等平台可用；QQ 音乐搜索在服务端长期故障）</span>
      </p>
      <div v-if="searching" class="lx-empty">正在搜索…</div>
      <div v-else-if="!songs.length" class="lx-empty">输入关键词开始搜索</div>
      <div v-else class="lx-list">
        <div v-for="song in songs" :key="songKey(song)" class="lx-row">
          <div class="lx-cover" :class="{ 'is-fallback': coverFailed[songKey(song)] }">
            <img v-if="coverOf(song) && !coverFailed[songKey(song)]" :src="coverOf(song)" :alt="song.name" loading="lazy" referrerpolicy="no-referrer" @error="markCoverFailed(song)">
            <span v-else class="lx-cover-ph">♪</span>
          </div>
          <div class="lx-info">
            <div class="lx-title" :title="song.name">{{ song.name }}</div>
            <div class="lx-sub">{{ singerOf(song) }} · {{ song.albumName || '未知专辑' }}</div>
            <div class="lx-tags">
              <span v-if="song.interval" class="lx-tag is-plain">{{ song.interval }}</span>
              <span v-for="q in qualitiesOf(song)" :key="q" class="lx-tag">{{ q }}</span>
            </div>
          </div>
          <div class="lx-row-actions">
            <button class="lx-btn small ghost" @click="resolveSong(song)">解析</button>
            <button class="lx-btn small" :disabled="downloading[songKey(song)]" @click="doDownload(song)">{{ downloading[songKey(song)] ? '下载中…' : '下载' }}</button>
          </div>
        </div>
      </div>
    </template>

    <!-- 歌单 -->
    <template v-else>
      <div class="lx-search">
        <input v-model="plKeyword" class="lx-input" type="text" placeholder="歌单名，或直接粘贴歌单链接 / 歌单 ID，回车查询" @keyup.enter="loadPlaylists">
        <select v-model="source" class="lx-select">
          <option v-for="item in SOURCES" :key="item.value" :value="item.value">{{ item.label }}</option>
        </select>
        <select v-model="quality" class="lx-select">
          <option v-for="item in QUALITIES" :key="item.value" :value="item.value">{{ item.label }}</option>
        </select>
        <button class="lx-btn" :disabled="playlistLoading" @click="loadPlaylists">{{ playlistLoading ? '查询中…' : (plKeyword.trim() ? '搜索歌单' : '热门歌单') }}</button>
      </div>
      <p class="lx-tip">
        歌单能力由服务端内置 SDK 提供，与自定义音源脚本无关；整单下载落到<code>{{ overview.download_dir || '-' }}</code>，目录整理交给 MoviePilot 本体。
        <span v-if="!playlistSupported('search')" class="lx-warn">（{{ overview.source_name || source }} 不支持歌单搜索，可粘贴链接或换平台）</span>
      </p>

      <!-- 歌单详情 -->
      <div v-if="currentPlaylist" class="lx-pl-detail">
        <div class="lx-pl-detail-head">
          <button class="lx-mini" @click="closePlaylist">← 返回列表</button>
          <div class="lx-pl-detail-title">
            <b>{{ currentPlaylist.name || '未命名歌单' }}</b>
            <span class="lx-sub-inline">{{ playlistAuthorOf(currentPlaylist) }}</span>
            <span v-if="playlistCountOf(currentPlaylist)" class="lx-tag is-plain">{{ playlistCountOf(currentPlaylist) }} 首</span>
            <span v-if="playlistDetail?.truncated" class="lx-tag is-warn">已截断</span>
          </div>
        </div>

        <div class="lx-pl-batch">
          <label class="lx-check">
            <input type="checkbox" @change="toggleAllSongs($event.target.checked)">
            <span>全选（{{ Object.keys(batchSelected).length }} / {{ (playlistDetail?.songs || []).length }}）</span>
          </label>
          <div class="lx-pl-batch-right">
            <span class="lx-pl-hint">并发 {{ batchConcurrency }} · 跳过已存在</span>
            <button class="lx-btn" :disabled="batchRunning || detailLoading" @click="downloadPlaylist">{{ batchRunning ? '下载中…' : (Object.keys(batchSelected).length ? `下载选中 ${Object.keys(batchSelected).length} 首` : '整单下载') }}</button>
          </div>
        </div>

        <div v-if="batchResult" class="lx-batch-result">
          <div class="lx-batch-result-head">
            <b>《{{ batchResult.name }}》</b>
            <span class="lx-tag is-ok">成功 {{ batchResult.success }}</span>
            <span class="lx-tag is-plain">跳过 {{ batchResult.skipped }}</span>
            <span v-if="batchResult.failed" class="lx-tag is-err">失败 {{ batchResult.failed }}</span>
            <button class="lx-mini" @click="batchResult = null">关闭</button>
          </div>
          <div class="lx-batch-result-sub">{{ batchSummary() }}</div>
          <div v-if="batchResult.failed" class="lx-batch-fails">
            <div v-for="item in batchResult.items.filter(i => i.status === 'failed').slice(0, 10)" :key="item.index" class="lx-fail-line">
              ✗ {{ item.name }}<span>{{ item.message }}</span>
            </div>
          </div>
        </div>

        <div v-if="detailLoading" class="lx-empty">正在读取歌单曲目…</div>
        <div v-else-if="!(playlistDetail?.songs || []).length" class="lx-empty">该歌单没有曲目</div>
        <div v-else class="lx-list">
          <div v-for="song in playlistDetail.songs" :key="songKey(song)" class="lx-row">
            <label class="lx-check is-square">
              <input v-model="batchSelected[songKey(song)]" type="checkbox">
            </label>
            <div class="lx-cover" :class="{ 'is-fallback': coverFailed[songKey(song)] }">
              <img v-if="coverOf(song) && !coverFailed[songKey(song)]" :src="coverOf(song)" :alt="song.name" loading="lazy" referrerpolicy="no-referrer" @error="markCoverFailed(song)">
              <span v-else class="lx-cover-ph">♪</span>
            </div>
            <div class="lx-info">
              <div class="lx-title" :title="song.name">{{ song.name }}</div>
              <div class="lx-sub">{{ singerOf(song) }} · {{ song.albumName || '未知专辑' }}</div>
              <div class="lx-tags">
                <span v-if="song.interval" class="lx-tag is-plain">{{ song.interval }}</span>
                <span v-for="q in qualitiesOf(song)" :key="q" class="lx-tag">{{ q }}</span>
              </div>
            </div>
            <div class="lx-row-actions">
              <button class="lx-btn small" :disabled="downloading[songKey(song)]" @click="doDownload(song)">{{ downloading[songKey(song)] ? '下载中…' : '下载' }}</button>
            </div>
          </div>
        </div>
      </div>

      <!-- 歌单列表 -->
      <div v-else-if="playlistLoading" class="lx-empty">正在读取歌单…</div>
      <div v-else-if="!playlists.length" class="lx-empty">点「热门歌单」浏览，或输入歌单名 / 粘贴歌单链接查询</div>
      <div v-else class="lx-pl-list">
        <div v-for="item in playlists" :key="playlistIdOf(item) || item.name" class="lx-pl-card" @click="openPlaylist(item)">
          <div class="lx-pl-cover">
            <img v-if="playlistCoverOf(item) && !playlistCoverFailed[playlistIdOf(item)]" :src="playlistCoverOf(item)" :alt="item.name" loading="lazy" referrerpolicy="no-referrer" @error="playlistCoverFailed[playlistIdOf(item)] = true">
            <span v-else class="lx-cover-ph">♫</span>
          </div>
          <div class="lx-pl-meta">
            <div class="lx-pl-name" :title="item.name">{{ item.name || '未命名歌单' }}</div>
            <div class="lx-pl-sub">{{ playlistAuthorOf(item) }}</div>
            <div v-if="playlistCountOf(item)" class="lx-pl-count">{{ playlistCountOf(item) }} 首</div>
          </div>
        </div>
      </div>
    </template>

    <!-- 解析直链 -->
    <div v-if="lastResolved" class="lx-resolved">
      <div class="lx-resolved-head">
        <b>{{ lastResolved.name }}</b>
        <span class="lx-tag is-ok">{{ lastResolved.type }}</span>
        <span v-if="lastResolved.source_name" class="lx-tag is-plain">{{ lastResolved.source_name }}</span>
        <button class="lx-mini" @click="lastResolved = null">关闭</button>
      </div>
      <div class="lx-resolved-url">
        <code>{{ lastResolved.url }}</code>
        <button class="lx-mini" @click="copyUrl(lastResolved.url)">复制</button>
      </div>
    </div>

    <!-- 设置弹窗 -->
    <Teleport :to="overlayTarget">
      <div v-if="showSettings" class="lx-cfg-mask" @click.self="showSettings = false">
        <div class="lx-cfg-wrap">
          <ConfigPanel
            :initial-config="settingsModel"
            :api="props.api"
            :plugin-id="props.pluginId"
            @save="saveSettings"
            @close="showSettings = false"
          />
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.lx-page {
  color: rgb(var(--v-theme-on-surface, 27, 29, 41));
  width: 100%;
  max-width: 1120px;
  margin: 0 auto;
  padding: 24px 24px 40px;
  box-sizing: border-box;
  font-family: inherit;
}
.lx-head { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; margin-bottom: 20px; }
.lx-logo {
  width: 40px; height: 40px; border-radius: 12px; flex-shrink: 0;
  background: linear-gradient(135deg, #10B981, #059669);
  color: #fff; display: flex; align-items: center; justify-content: center; font-size: 19px; font-weight: 700;
}
.lx-heading h1 { font-size: 19px; font-weight: 700; margin: 0; }
.lx-heading p {
  font-size: 12.5px; margin: 2px 0 0;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .62));
}
.lx-head-chips { display: flex; gap: 8px; flex-wrap: wrap; margin-left: 8px; }
.lx-chip {
  border-radius: 999px; padding: 4px 11px; font-size: 12px;
  background: rgba(var(--v-theme-on-surface, 27, 29, 41), .06);
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .7));
}
.lx-chip.is-ok { background: rgba(22, 163, 74, .12); color: #16A34A; }
.lx-chip.is-warn { background: rgba(234, 138, 31, .15); color: #EA8A1F; }
.lx-head-actions { margin-left: auto; display: flex; gap: 8px; }
.lx-btn {
  padding: 8px 15px; border-radius: 10px; font-size: 13px; font-weight: 600; font-family: inherit;
  cursor: pointer; border: none; transition: .15s;
  background: rgb(var(--v-theme-primary, 124, 92, 252));
  color: rgb(var(--v-theme-on-primary, 255, 255, 255));
}
.lx-btn:hover { filter: brightness(1.08); }
.lx-btn:disabled { opacity: .45; cursor: not-allowed; filter: none; }
.lx-btn.ghost {
  background: rgb(var(--v-theme-surface, 255, 255, 255));
  color: rgb(var(--v-theme-primary, 124, 92, 252));
  border: 1px solid rgba(var(--v-theme-primary, 124, 92, 252), .35);
}
.lx-btn.small { padding: 7px 13px; font-size: 12px; }
.lx-alert { border-radius: 10px; padding: 10px 14px; font-size: 13px; margin-bottom: 16px; white-space: pre-wrap; }
.lx-alert.is-error { background: rgba(229, 72, 77, .12); color: #E5484D; }
.lx-alert.is-ok { background: rgba(22, 163, 74, .12); color: #16A34A; }
.lx-stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 18px; }
.lx-stat {
  background: rgb(var(--v-theme-surface, 255, 255, 255));
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .10);
  border-radius: 12px; padding: 13px 15px;
}
.lx-stat b { display: block; font-size: 20px; font-weight: 700; line-height: 1.2; font-variant-numeric: tabular-nums; }
.lx-stat span { font-size: 12px; color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .62)); }
.lx-stat.is-ok b { color: #16A34A; }
.lx-stat.is-mv b { color: #3B82F6; }
.lx-search { display: flex; gap: 9px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; }
.lx-input, .lx-select {
  padding: 9px 12px; font-size: 13px; font-family: inherit; border-radius: 10px; outline: none;
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .16);
  background: rgb(var(--v-theme-surface, 255, 255, 255));
  color: rgb(var(--v-theme-on-surface, 27, 29, 41));
}
.lx-input { flex: 1; min-width: 200px; }
.lx-input.is-num { flex: 0 0 76px; min-width: 76px; }
.lx-input:focus, .lx-select:focus { border-color: rgb(var(--v-theme-primary, 124, 92, 252)); }
.lx-tip {
  font-size: 12px; margin: 0 0 16px;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .6));
}
.lx-tip code {
  background: rgba(var(--v-theme-on-surface, 27, 29, 41), .07);
  padding: 1px 6px; border-radius: 5px; font-size: 11.5px;
}
.lx-warn { color: #EA8A1F; }
.lx-empty {
  text-align: center; padding: 48px 0; font-size: 13px;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .5));
}
.lx-list { display: flex; flex-direction: column; gap: 8px; }
.lx-row {
  display: flex; align-items: center; gap: 12px; padding: 10px 12px; border-radius: 12px;
  background: rgb(var(--v-theme-surface, 255, 255, 255));
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .10);
}
.lx-row:hover { border-color: rgba(var(--v-theme-primary, 124, 92, 252), .32); }
.lx-cover {
  width: 46px; height: 46px; border-radius: 9px; overflow: hidden; flex-shrink: 0;
  background: linear-gradient(135deg, #10B981, #059669);
  display: flex; align-items: center; justify-content: center;
}
.lx-cover img { width: 100%; height: 100%; object-fit: cover; display: block; }
.lx-cover-ph { color: #fff; font-size: 20px; }
.lx-info { flex: 1; min-width: 0; }
.lx-title { font-size: 13.5px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.lx-sub {
  font-size: 11.5px; margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .6));
}
.lx-tags { display: flex; gap: 5px; flex-wrap: wrap; margin-top: 5px; }
.lx-tag {
  font-size: 10.5px; padding: 2px 7px; border-radius: 6px; font-weight: 600;
  background: rgba(var(--v-theme-primary, 124, 92, 252), .12);
  color: rgb(var(--v-theme-primary, 124, 92, 252));
}
.lx-tag.is-plain { background: rgba(var(--v-theme-on-surface, 27, 29, 41), .07); color: rgba(var(--v-theme-on-surface, 27, 29, 41), .7); }
.lx-tag.is-ok { background: rgba(22, 163, 74, .14); color: #16A34A; }
.lx-row-actions { display: flex; gap: 7px; flex-shrink: 0; }
.lx-resolved {
  margin-top: 18px; padding: 14px 16px; border-radius: 12px;
  background: rgba(var(--v-theme-primary, 124, 92, 252), .07);
  border: 1px solid rgba(var(--v-theme-primary, 124, 92, 252), .22);
}
.lx-resolved-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 13px; }
.lx-resolved-head b { font-size: 14px; }
.lx-resolved-url { display: flex; align-items: center; gap: 8px; margin-top: 9px; }
.lx-resolved-url code {
  flex: 1; min-width: 0; font-size: 11.5px; word-break: break-all;
  background: rgba(var(--v-theme-on-surface, 27, 29, 41), .06);
  padding: 7px 10px; border-radius: 8px;
}
.lx-mini {
  padding: 5px 11px; border-radius: 8px; font-size: 12px; font-family: inherit; cursor: pointer; flex-shrink: 0;
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .14);
  background: transparent; color: rgba(var(--v-theme-on-surface, 27, 29, 41), .72);
}
.lx-mini:hover { border-color: rgba(var(--v-theme-primary, 124, 92, 252), .4); color: rgb(var(--v-theme-primary, 124, 92, 252)); }

/* ------------------------------- 标签页 ------------------------------- */
.lx-tabs { display: flex; gap: 4px; margin-bottom: 16px; border-bottom: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .10); }
.lx-tab {
  padding: 9px 16px; font-size: 13px; font-weight: 600; font-family: inherit; cursor: pointer;
  background: transparent; border: none; border-bottom: 2px solid transparent; margin-bottom: -1px;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .65));
}
.lx-tab.is-active { color: rgb(var(--v-theme-primary, 124, 92, 252)); border-bottom-color: rgb(var(--v-theme-primary, 124, 92, 252)); }

/* ------------------------------- 歌单列表 ------------------------------ */
.lx-pl-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 14px; }
.lx-pl-card {
  cursor: pointer; border-radius: 12px; overflow: hidden; padding: 10px;
  background: rgb(var(--v-theme-surface, 255, 255, 255));
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .10);
  transition: .15s;
}
.lx-pl-card:hover { border-color: rgba(var(--v-theme-primary, 124, 92, 252), .35); transform: translateY(-2px); }
.lx-pl-cover {
  width: 100%; aspect-ratio: 1 / 1; border-radius: 9px; overflow: hidden; margin-bottom: 9px;
  background: linear-gradient(135deg, #10B981, #059669);
  display: flex; align-items: center; justify-content: center;
}
.lx-pl-cover img { width: 100%; height: 100%; object-fit: cover; display: block; }
.lx-pl-meta { min-width: 0; }
.lx-pl-name {
  font-size: 13px; font-weight: 600; line-height: 1.35;
  display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.lx-pl-sub {
  font-size: 11.5px; margin-top: 3px;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .6));
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.lx-pl-count { font-size: 11px; margin-top: 2px; color: rgba(var(--v-theme-on-surface, 27, 29, 41), .55); }

/* ------------------------------- 歌单详情 ------------------------------ */
.lx-pl-detail { margin-top: 4px; }
.lx-pl-detail-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.lx-pl-detail-title { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 13.5px; min-width: 0; }
.lx-pl-detail-title b { font-size: 15px; }
.lx-sub-inline { font-size: 12px; color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .6)); }
.lx-tag.is-warn { background: rgba(234, 138, 31, .15); color: #EA8A1F; }
.lx-tag.is-err { background: rgba(229, 72, 77, .14); color: #E5484D; }
.lx-pl-batch {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  padding: 10px 13px; border-radius: 11px; margin-bottom: 12px;
  background: rgba(var(--v-theme-primary, 124, 92, 252), .06);
  border: 1px solid rgba(var(--v-theme-primary, 124, 92, 252), .18);
}
.lx-pl-batch-right { margin-left: auto; display: flex; align-items: center; gap: 12px; }
.lx-pl-hint { font-size: 11.5px; color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .62)); }
.lx-check { display: flex; align-items: center; gap: 7px; font-size: 12.5px; cursor: pointer; }
.lx-check input { width: 15px; height: 15px; accent-color: rgb(var(--v-theme-primary, 124, 92, 252)); cursor: pointer; }
.lx-check.is-square { flex-shrink: 0; }
.lx-batch-result {
  border-radius: 11px; padding: 12px 14px; margin-bottom: 14px; font-size: 12.5px;
  background: rgba(22, 163, 74, .08); border: 1px solid rgba(22, 163, 74, .22);
}
.lx-batch-result-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.lx-batch-result-head b { font-size: 13.5px; }
.lx-batch-result-sub { margin-top: 6px; color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .75)); }
.lx-batch-fails { margin-top: 8px; display: flex; flex-direction: column; gap: 3px; }
.lx-fail-line { display: flex; gap: 8px; font-size: 11.5px; color: #E5484D; }
.lx-fail-line span { color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .6)); }
.lx-cfg-mask {
  /* 必须高于 MP 顶栏/侧栏（1000）与 v-overlay（2000），对齐 v-dialog 的 2400；
     原值 60 会让遮罩被顶栏和侧栏盖住，弹窗也就被"压"在内容区里 */
  position: fixed; inset: 0; z-index: 2400;
  background: rgba(0, 0, 0, .45);
  display: flex; align-items: center; justify-content: center; padding: 24px;
}
.lx-cfg-wrap {
  width: min(760px, 100%); max-height: 86vh; overflow: auto; border-radius: 16px;
  background: rgb(var(--v-theme-surface, 255, 255, 255));
  box-shadow: 0 18px 48px rgba(0, 0, 0, .28);
}
@media (max-width: 820px) {
  .lx-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
