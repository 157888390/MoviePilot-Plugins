<script setup>
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import ConfigPanel from './Config.vue'
import {
  QUALITIES, SOURCES, bodyOf, coverOf, formatSize, makeApiCall, qualitiesOf, singerOf, songKey, unwrap,
} from '../lib/lx.js'

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

function flash(text, isError = false) {
  clearTimeout(noticeTimer)
  if (isError) { error.value = text; notice.value = '' }
  else { notice.value = text; error.value = '' }
  noticeTimer = setTimeout(() => { notice.value = ''; error.value = '' }, 8000)
}

function markCoverFailed(song) {
  coverFailed[songKey(song)] = true
}

async function loadOverview() {
  try {
    overview.value = unwrap(await apiCall('get', '/overview'))
    if (overview.value?.source) source.value = overview.value.source
    if (overview.value?.quality) quality.value = overview.value.quality
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

// 设置面板
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
        <p>调用自建 LX Sync Server 搜索歌曲，解析直链并下载到指定目录</p>
      </div>
      <div class="lx-head-chips">
        <span :class="['lx-chip', overview.auth_state === 'ok' ? 'is-ok' : 'is-warn']">
          {{ overview.auth_state === 'ok' ? '鉴权正常' : (overview.auth_state === 'anonymous' ? '未配置凭据' : '鉴权异常') }}
        </span>
        <span class="lx-chip is-muted">{{ overview.source_name || '-' }} · {{ overview.quality || '-' }}</span>
      </div>
      <div class="lx-head-actions">
        <button class="lx-btn ghost" @click="openSettings">设置</button>
        <button class="lx-btn ghost" :disabled="loading" @click="loadEverything">刷新</button>
      </div>
    </div>

    <div v-if="error" class="lx-alert is-error">{{ error }}</div>
    <div v-else-if="notice" class="lx-alert is-ok">{{ notice }}</div>

    <div class="lx-stats">
      <div class="lx-stat is-ok"><b>{{ formatSize(cacheInfo().totalSize) }}</b><span>服务端缓存占用</span></div>
      <div class="lx-stat"><b>{{ cacheInfo().fileCount || 0 }}</b><span>缓存文件数</span></div>
      <div class="lx-stat"><b>{{ songs.length }}</b><span>本次搜索候选</span></div>
      <div class="lx-stat is-mv"><b>{{ overview.max_results || 10 }}</b><span>搜索上限</span></div>
    </div>

    <div class="lx-search">
      <input
        v-model="keyword"
        class="lx-input"
        type="text"
        placeholder="输入歌曲名或「歌手 - 歌名」，回车搜索"
        @keyup.enter="doSearch"
      >
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
        <div class="lx-cover" :class="coverFailed[songKey(song)] ? 'is-fallback' : ''">
          <img
            v-if="coverOf(song) && !coverFailed[songKey(song)]"
            :src="coverOf(song)"
            :alt="song.name"
            loading="lazy"
            referrerpolicy="no-referrer"
            @error="markCoverFailed(song)"
          >
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
          <button class="lx-btn small" :disabled="downloading[songKey(song)]" @click="doDownload(song)">
            {{ downloading[songKey(song)] ? '下载中…' : '下载' }}
          </button>
        </div>
      </div>
    </div>

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
.lx-stat span {
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .62));
}
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

.lx-cfg-mask {
  position: fixed; inset: 0; z-index: 60;
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
