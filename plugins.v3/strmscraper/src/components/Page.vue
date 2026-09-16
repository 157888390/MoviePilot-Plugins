<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { coverUrl, makeApiCall, posterStyle, statusOf, unwrap } from '../lib/strm.js'

const props = defineProps({
  api: { type: Object, default: () => ({}) },
  pluginId: { type: String, default: 'StrmScraper' },
})

const apiCall = makeApiCall(props.api, props.pluginId)

const loading = ref(true)
const error = ref('')
const notice = ref('')
const overview = ref({})
const items = ref([])
const keyword = ref('')
const typeFilter = ref('all')
const posterFailed = reactive({})

function markPosterFailed(path) {
  posterFailed[path] = true
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

async function loadData() {
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

onMounted(() => { loadData() })
</script>

<template>
  <div class="pg">
    <div class="pg-head">
      <div :class="['pg-chip', overview.monitoring ? 'is-ok' : 'is-muted']">
        {{ overview.monitoring ? '监控运行中' : '监控未启动' }}
      </div>
      <div class="pg-chip is-muted">{{ (overview.monitor_dirs || []).length }} 个监控目录</div>
      <div class="pg-actions">
        <button class="pg-btn" :disabled="loading" @click="loadData">刷新</button>
      </div>
    </div>

    <div v-if="error" class="pg-alert">{{ error }}</div>
    <div v-else-if="notice" class="pg-alert is-ok">{{ notice }}</div>

    <div class="pg-stats">
      <div class="pg-stat"><b>{{ overview.total || 0 }}</b><span>媒体总数</span></div>
      <div class="pg-stat is-movie"><b>{{ overview.movie || 0 }}</b><span>电影</span></div>
      <div class="pg-stat is-tv"><b>{{ overview.tv || 0 }}</b><span>电视剧</span></div>
      <div class="pg-stat is-ok"><b>{{ overview.dir_scraped || 0 }}</b><span>已刮削目录</span></div>
      <div class="pg-stat is-fail"><b>{{ overview.unscraped_items || 0 }}</b><span>待刮削</span></div>
    </div>

    <div class="pg-toolbar">
      <div class="pg-seg">
        <button :class="['pg-seg-item', typeFilter === 'all' && 'active']" @click="typeFilter = 'all'">
          全部 <em>{{ counts.all }}</em>
        </button>
        <button :class="['pg-seg-item', typeFilter === 'movie' && 'active']" @click="typeFilter = 'movie'">
          电影 <em>{{ counts.movie }}</em>
        </button>
        <button :class="['pg-seg-item', typeFilter === 'tv' && 'active']" @click="typeFilter = 'tv'">
          电视剧 <em>{{ counts.tv }}</em>
        </button>
      </div>
      <input v-model="keyword" class="pg-search" type="text" placeholder="搜索标题或路径">
    </div>

    <div v-if="loading" class="pg-empty">正在扫描监控目录…</div>
    <div v-else-if="!visibleItems.length" class="pg-empty">没有匹配的媒体</div>
    <div v-else class="pg-list">
      <div v-for="item in visibleItems" :key="item.path" class="pg-item">
        <div class="pg-poster" :style="posterFailed[item.path] ? posterStyle(item.title) : ''">
          <img
            v-if="!posterFailed[item.path]"
            :src="coverUrl(props.api, props.pluginId, item)"
            :alt="item.title"
            loading="lazy"
            @error="markPosterFailed(item.path)"
          >
          <span :class="['pg-dot', `is-${statusOf(item)}`]"></span>
        </div>
        <div class="pg-info">
          <div class="pg-title" :title="item.path">{{ item.title }}</div>
          <div class="pg-path">{{ item.path }}</div>
          <div class="pg-tags">
            <span :class="['pg-tag', item.type === 'tv' ? 'is-tv' : 'is-mv']">{{ item.type === 'tv' ? '电视剧' : '电影' }}</span>
            <template v-if="item.type === 'tv'">
              <span class="pg-tag is-plain">{{ item.total_episodes || item.total_files }} 集 · {{ (item.seasons || []).length }} 季</span>
            </template>
            <template v-else-if="item.multi_version">
              <span class="pg-tag is-blue">{{ item.total_files }} 版本</span>
            </template>
            <template v-else>
              <span class="pg-tag is-blue">单版本</span>
            </template>
            <span v-if="item.unscraped" class="pg-tag is-fail">{{ item.unscraped }} 待刮</span>
            <span v-else class="pg-tag is-ok">已刮削</span>
          </div>
        </div>
      </div>
    </div>

    <div class="pg-foot">完整刮削操作（单集/版本重刮、强制全量）请使用左侧导航的「STRM 刮削」页面。</div>
  </div>
</template>

<style scoped>
.pg {
  padding: 10px 4px 4px;
  font-size: 13px;
  font-family: inherit;
  color: rgb(var(--v-theme-on-surface, 27, 29, 41));
}
.pg-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
.pg-chip {
  border-radius: 999px; padding: 3px 10px; font-size: 12px;
  background: rgba(var(--v-theme-on-surface, 27, 29, 41), .06);
}
.pg-chip.is-ok { background: rgba(22, 163, 74, .12); color: #16A34A; }
.pg-chip.is-muted { color: rgba(var(--v-theme-on-surface, 27, 29, 41), .65); }
.pg-actions { margin-left: auto; display: flex; gap: 8px; }
.pg-btn {
  padding: 5px 12px; border-radius: 9px; font-size: 12.5px; font-weight: 600; font-family: inherit; cursor: pointer;
  border: 1px solid rgba(var(--v-theme-primary, 124, 92, 252), .35);
  background: transparent; color: rgb(var(--v-theme-primary, 124, 92, 252));
}
.pg-btn:disabled { opacity: .45; cursor: not-allowed; }

.pg-alert {
  border-radius: 10px; padding: 8px 12px; font-size: 12.5px; margin-bottom: 10px;
  background: rgba(229, 72, 77, .12); color: #E5484D;
}
.pg-alert.is-ok { background: rgba(22, 163, 74, .12); color: #16A34A; }

.pg-stats {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(88px, 1fr));
  gap: 8px; margin-bottom: 12px;
}
.pg-stat {
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .08);
  border-radius: 12px; padding: 9px 10px;
}
.pg-stat b { display: block; font-size: 17px; font-weight: 700; }
.pg-stat span { font-size: 11px; color: rgba(var(--v-theme-on-surface, 27, 29, 41), .62); }
.pg-stat.is-movie b { color: #4F8CFF; }
.pg-stat.is-tv b { color: #B37BFF; }
.pg-stat.is-ok b { color: #16A34A; }
.pg-stat.is-fail b { color: #E5484D; }

.pg-toolbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
.pg-seg {
  display: inline-flex; border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .12);
  border-radius: 10px; overflow: hidden;
}
.pg-seg-item {
  border: none; background: transparent; color: inherit; font-family: inherit;
  font-size: 12.5px; padding: 6px 12px; cursor: pointer;
}
.pg-seg-item + .pg-seg-item { border-left: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .12); }
.pg-seg-item.active { background: rgb(var(--v-theme-primary, 124, 92, 252)); color: rgb(var(--v-theme-on-primary, 255, 255, 255)); }
.pg-seg-item em { font-style: normal; opacity: .75; margin-left: 3px; }
.pg-search {
  flex: 1; min-width: 140px; padding: 7px 11px; font-size: 12.5px; font-family: inherit;
  border-radius: 10px; outline: none;
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .16);
  background: rgb(var(--v-theme-surface, 255, 255, 255));
  color: rgb(var(--v-theme-on-surface, 27, 29, 41));
}

.pg-empty { padding: 18px 0; text-align: center; color: rgba(var(--v-theme-on-surface, 27, 29, 41), .55); }
.pg-list { display: flex; flex-direction: column; gap: 8px; max-height: 46vh; overflow-y: auto; padding-right: 2px; }
.pg-item {
  display: flex; gap: 10px; padding: 8px; border-radius: 12px;
  border: 1px solid rgba(var(--v-theme-on-surface, 27, 29, 41), .08);
}
.pg-poster {
  position: relative; width: 56px; aspect-ratio: 2 / 3; border-radius: 9px; overflow: hidden; flex-shrink: 0;
  background: rgba(var(--v-theme-on-surface, 27, 29, 41), .08);
}
.pg-poster img { width: 100%; height: 100%; object-fit: cover; display: block; }
.pg-dot {
  position: absolute; top: 5px; right: 5px; width: 8px; height: 8px; border-radius: 50%;
  box-shadow: 0 0 0 2px rgba(0, 0, 0, .18);
}
.pg-dot.is-ok { background: #16A34A; }
.pg-dot.is-pend { background: #F59E0B; }
.pg-dot.is-fail { background: #E5484D; }

.pg-info { min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.pg-title { font-weight: 600; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pg-path {
  font-size: 11px; color: rgba(var(--v-theme-on-surface, 27, 29, 41), .55);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.pg-tags { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 2px; }
.pg-tag {
  border-radius: 999px; padding: 1.5px 8px; font-size: 11px;
  background: rgba(var(--v-theme-on-surface, 27, 29, 41), .07);
}
.pg-tag.is-mv { background: rgba(79, 140, 255, .14); color: #4F8CFF; }
.pg-tag.is-tv { background: rgba(179, 123, 255, .16); color: #B37BFF; }
.pg-tag.is-blue { background: rgba(79, 172, 254, .14); color: #2E9BE6; }
.pg-tag.is-ok { background: rgba(22, 163, 74, .13); color: #16A34A; }
.pg-tag.is-fail { background: rgba(229, 72, 77, .13); color: #E5484D; }

.pg-foot {
  margin-top: 10px; font-size: 11.5px;
  color: rgba(var(--v-theme-on-surface, 27, 29, 41), .55);
}
</style>
