<script setup>
import { onMounted, ref } from 'vue'
import { formatSize, makeApiCall, unwrap } from '../lib/lx.js'

const props = defineProps({
  api: { type: Object, default: () => ({}) },
  pluginId: { type: String, default: 'LxMusicDownloader' },
})

const apiCall = makeApiCall(props.api, props.pluginId)

const loading = ref(true)
const error = ref('')
const overview = ref({})
const stats = ref(null)

async function loadData() {
  loading.value = true
  error.value = ''
  try {
    const [overviewResponse, statsResponse] = await Promise.all([
      apiCall('get', '/overview'),
      apiCall('get', '/stats').catch(() => null),
    ])
    overview.value = unwrap(overviewResponse)
    stats.value = statsResponse ? unwrap(statsResponse) : null
  } catch (loadError) {
    error.value = loadError?.message || '读取运行状态失败'
  } finally {
    loading.value = false
  }
}

onMounted(() => { loadData() })
</script>

<template>
  <div class="pg">
    <div class="pg-head">
      <div :class="['pg-chip', overview.auth_state === 'ok' ? 'is-ok' : 'is-warn']">
        {{ overview.auth_state === 'ok' ? '鉴权正常' : (overview.auth_state === 'anonymous' ? '未配置凭据' : '鉴权异常') }}
      </div>
      <div :class="['pg-chip', overview.enabled ? 'is-ok' : 'is-muted']">
        {{ overview.enabled ? '插件已启用' : '插件未启用' }}
      </div>
      <div class="pg-actions">
        <button class="pg-btn" :disabled="loading" @click="loadData">刷新</button>
      </div>
    </div>

    <div v-if="error" class="pg-alert">{{ error }}</div>

    <div class="pg-stats">
      <div class="pg-stat is-ok"><b>{{ formatSize(stats?.totalSize) }}</b><span>服务端缓存</span></div>
      <div class="pg-stat"><b>{{ stats?.fileCount || 0 }}</b><span>缓存文件</span></div>
      <div class="pg-stat is-mv"><b>{{ overview.source_name || '-' }}</b><span>音源平台</span></div>
      <div class="pg-stat"><b>{{ overview.quality || '-' }}</b><span>下载音质</span></div>
    </div>

    <div class="pg-table">
      <div class="pg-line"><span>服务端地址</span><b>{{ overview.host || '未配置' }}</b></div>
      <div class="pg-line"><span>鉴权状态</span><b>{{ overview.auth_text || '-' }}</b></div>
      <div class="pg-line"><span>下载位置</span><b>{{ overview.download_dir || '-' }}</b></div>
      <div class="pg-line"><span>搜索结果数</span><b>{{ overview.max_results || '-' }}</b></div>
      <div class="pg-line"><span>侧栏入口</span><b>{{ overview.sidebar_enabled ? '已开启' : '已关闭' }}</b></div>
    </div>

    <div class="pg-commands">
      <div class="pg-commands-title">远程命令</div>
      <div class="pg-cmd"><code>/lx_search 歌曲名</code><span>搜索并列出候选</span></div>
      <div class="pg-cmd"><code>/lx_download 序号</code><span>按上次搜索序号下载</span></div>
      <div class="pg-cmd"><code>/lx_download 歌手 - 歌名</code><span>直接搜索首条并下载</span></div>
      <div class="pg-cmd"><code>/lx_stats</code><span>查看服务端缓存统计</span></div>
    </div>
  </div>
</template>

<style scoped>
.pg {
  color: rgb(var(--v-theme-on-surface, 27, 29, 41));
  padding: 18px 20px 8px;
  font-family: inherit;
}
.pg-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; }
.pg-chip {
  border-radius: 999px; padding: 4px 11px; font-size: 12px;
  background: rgba(var(--v-theme-on-surface, 27, 29, 41), .06);
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .7));
}
.pg-chip.is-ok { background: rgba(22, 163, 74, .12); color: #16A34A; }
.pg-chip.is-warn { background: rgba(234, 138, 31, .15); color: #EA8A1F; }
.pg-actions { margin-left: auto; }
.pg-btn {
  padding: 7px 14px; border-radius: 9px; font-size: 12.5px; font-weight: 600; font-family: inherit; cursor: pointer;
  border: 1px solid rgba(var(--v-theme-primary, 124, 92, 252), .35); background: transparent;
  color: rgb(var(--v-theme-primary, 124, 92, 252));
}
.pg-btn:disabled { opacity: .5; cursor: not-allowed; }
.pg-alert {
  border-radius: 10px; padding: 9px 13px; font-size: 13px; margin-bottom: 14px;
  background: rgba(229, 72, 77, .12); color: #E5484D;
}
.pg-stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-bottom: 16px; }
.pg-stat {
  background: rgb(var(--v-theme-surface, 255, 255, 255));
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .10);
  border-radius: 11px; padding: 11px 13px;
}
.pg-stat b {
  display: block; font-size: 16px; font-weight: 700; line-height: 1.25;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.pg-stat span { font-size: 11.5px; color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .62)); }
.pg-stat.is-ok b { color: #16A34A; }
.pg-stat.is-mv b { color: #3B82F6; }
.pg-table {
  border-radius: 11px; overflow: hidden; margin-bottom: 16px;
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .10);
}
.pg-line {
  display: flex; gap: 12px; padding: 9px 13px; font-size: 12.5px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .07);
}
.pg-line:last-child { border-bottom: none; }
.pg-line span { width: 90px; flex-shrink: 0; color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .6)); }
.pg-line b { font-weight: 600; word-break: break-all; }
.pg-commands {
  border-radius: 11px; padding: 12px 14px;
  background: rgba(var(--v-theme-primary, 124, 92, 252), .06);
  border: 1px solid rgba(var(--v-theme-primary, 124, 92, 252), .18);
}
.pg-commands-title { font-size: 12px; font-weight: 700; margin-bottom: 8px; color: rgb(var(--v-theme-primary, 124, 92, 252)); }
.pg-cmd { display: flex; align-items: center; gap: 10px; font-size: 12px; padding: 3px 0; }
.pg-cmd code {
  background: rgba(var(--v-theme-on-surface, 27, 29, 41), .07);
  padding: 2px 7px; border-radius: 6px; font-size: 11.5px; flex-shrink: 0;
}
.pg-cmd span { color: rgba(var(--v-theme-on-surface, 27, 29, 41), var(--v-medium-emphasis-opacity, .65)); }
@media (max-width: 820px) {
  .pg-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
